"""
coder.py — Agentic coding loop using claude-agent-sdk.

The agent runs inside the target repo directory, with access to native
Read / Write / Edit / Bash tools (from Claude Code). A custom `submit_fix`
MCP tool signals when the agent is satisfied with its changes.

Auth: uses the system `claude` CLI (logged in via Claude Max on this machine).
No ANTHROPIC_API_KEY needed — set CLI_PATH env var to override the binary path.
"""
import anyio
import os
import shutil
import subprocess
import sys
import time

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    tool,
    create_sdk_mcp_server,
)

REPO = os.environ.get("TARGET_REPO_PATH", "./arrow")

# Default turn budget: tight enough to prevent Haiku-style 60-turn
# flailing we saw in Phase A, loose enough for careful fixes with
# reviewer feedback. Override via `max_turns` arg or MAX_TURNS env var.
DEFAULT_MAX_TURNS = int(os.environ.get("MAX_TURNS", "25"))

# Use the system claude CLI (already logged in via Claude Max).
# Falls back to whatever the SDK bundles if not found.
_CLAUDE_BIN = os.environ.get("CLI_PATH") or shutil.which("claude") or None


def _build_system_prompt(
    extra_context: str = "",
    memory_block: str = "",
    turn_budget: int = DEFAULT_MAX_TURNS,
) -> str:
    base = (
        "You are a coding agent fixing bugs in a Python project. "
        "Be efficient: read the relevant file, make the fix, run tests, call submit_fix.\n\n"
        f"TURN BUDGET: You have {turn_budget} turns total. Each message from you "
        "(whether tool use or text) counts as one turn. Spend them wisely:\n"
        "- ~3 turns to locate the bug (read 1-2 files)\n"
        "- ~2 turns to apply the fix with Edit\n"
        "- ~3 turns to verify with Bash\n"
        "- 1 turn to call submit_fix\n"
        f"If you are past turn {turn_budget // 2} and have not called Edit yet, "
        "you are flailing. STOP reading and commit to a fix based on the best "
        "evidence you have. It's better to submit an imperfect fix than to run "
        "out of turns with nothing.\n\n"
        "Workflow:\n"
        "1. Read the file(s) likely to contain the bug (1-3 turns max)\n"
        "2. Make the minimal fix with Edit (1-2 turns)\n"
        "3. Run the relevant tests with Bash (1-2 turns)\n"
        "4. Call submit_fix with a one-sentence summary\n\n"
        "The bug is very likely in arrow/arrow.py, arrow/locales.py, or "
        "arrow/parser.py. Do NOT explore other directories or run more than "
        "5 Bash commands in a row without an Edit.\n\n"
        "STRICT RULES (violating these voids the round):\n"
        "- Do NOT run `git checkout`, `git switch`, `git reset`, `git pull`, "
        "  `git fetch`, `git stash`, `git rebase`, or anything that moves HEAD "
        "  or changes which commit is checked out. The repo is pinned at a "
        "  specific baseline commit and your fix must apply to THAT commit.\n"
        "- Do NOT run `pip install` or modify the Python environment.\n"
        "- Read-only git is fine: `git status`, `git diff`, `git log`, `git show`."
    )
    # Memory block (lessons learned from past traces) goes before the
    # round-specific reviewer feedback so it serves as durable guidance.
    if memory_block:
        base += f"\n\n{memory_block}"
    if extra_context:
        base += f"\n\n{extra_context}"
    return base


def _log(msg: str):
    print(f"    [coder] {msg}", flush=True)


async def _run_coder_async(
    issue: dict,
    extra_context: str = "",
    max_turns: int = DEFAULT_MAX_TURNS,
    memory_block: str = "",
) -> str:
    """Async impl. Returns the git diff produced by the agent."""

    start = time.time()
    turn = 0
    edit_count = 0
    bash_run_length = 0  # consecutive Bash calls without an Edit
    fix_submitted = {"done": False, "summary": ""}
    stopped_early = ""

    @tool(
        "submit_fix",
        "Submit your fix when all edits are complete and tests pass.",
        {"summary": str},
    )
    async def submit_fix(args):
        fix_submitted["done"] = True
        fix_submitted["summary"] = args.get("summary", "")
        return {"content": [{"type": "text", "text": "Fix submitted. Thank you."}]}

    mcp_server = create_sdk_mcp_server(
        name="worktrial-tools",
        version="1.0.0",
        tools=[submit_fix],
    )

    user_prompt = (
        f"Issue #{issue['number']}: {issue['title']}\n\n{issue['body_summary']}"
    )
    if extra_context:
        user_prompt += f"\n\n---\nPrevious reviewer feedback:\n{extra_context}"

    model = os.environ.get("AGENT_MODEL", "haiku")

    options = ClaudeAgentOptions(
        model=model,
        system_prompt=_build_system_prompt(extra_context, memory_block, max_turns),
        cwd=os.path.abspath(REPO),
        max_turns=max_turns,
        allowed_tools=["Read", "Write", "Edit", "Bash", "mcp__worktrial-tools__submit_fix"],
        permission_mode="acceptEdits",
        mcp_servers={"worktrial-tools": mcp_server},
        **({"cli_path": _CLAUDE_BIN} if _CLAUDE_BIN else {}),
    )

    _log(f"Starting on issue #{issue['number']} (budget: {max_turns} turns)...")

    # The SDK's max_turns parameter did not actually cap assistant turns
    # in Phase A (saw 60+ turns with max_turns=30). We enforce our own
    # budget here by breaking the response stream when turn >= max_turns
    # or when flailing heuristics trip.
    async with ClaudeSDKClient(options=options) as client:
        await client.query(user_prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                turn += 1
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        first_line = block.text.strip().split("\n")[0][:120]
                        _log(f"[turn {turn}, {time.time() - start:.0f}s] {first_line}")
                    elif isinstance(block, ToolUseBlock):
                        _log(f"[turn {turn}, {time.time() - start:.0f}s] tool: {block.name}")
                        if block.name == "Edit" or block.name == "Write":
                            edit_count += 1
                            bash_run_length = 0
                        elif block.name == "Bash":
                            bash_run_length += 1
                        else:
                            bash_run_length = 0
                # Hard turn budget
                if turn >= max_turns:
                    stopped_early = f"turn_budget_exhausted({turn}/{max_turns})"
                    _log(f"[turn {turn}] STOP: turn budget exhausted")
                    break
                # Flailing heuristic 1: halfway through budget with no edits
                if turn >= max_turns // 2 and edit_count == 0 and not fix_submitted["done"]:
                    stopped_early = f"flailing_no_edits_by_turn_{turn}"
                    _log(f"[turn {turn}] STOP: halfway through budget with no Edit yet")
                    break
                # Flailing heuristic 2: too many Bash calls in a row
                if bash_run_length >= 8:
                    stopped_early = f"bash_streak({bash_run_length})"
                    _log(f"[turn {turn}] STOP: {bash_run_length} Bash calls in a row without an Edit")
                    break
            elif isinstance(msg, ResultMessage):
                break  # conversation finished

    elapsed = time.time() - start
    _log(
        f"Done ({turn} turns, {edit_count} edits, {elapsed:.0f}s). "
        f"fix_submitted={fix_submitted['done']}"
        + (f" stopped={stopped_early}" if stopped_early else "")
    )

    # Capture whatever changes were made
    diff_result = subprocess.run(
        ["git", "diff"],
        cwd=os.path.abspath(REPO),
        capture_output=True,
        text=True,
    )
    return diff_result.stdout


def run_coder(
    issue: dict,
    extra_context: str = "",
    max_turns: int = DEFAULT_MAX_TURNS,
    memory_block: str = "",
) -> str:
    """
    Run the coder agent on an issue.
    Returns the git diff string of all changes made.

    `memory_block` is an optional preamble of distilled lessons from
    past traces, injected into the system prompt by the loop layer.
    The coder does not load memory itself - the loop owns that.
    """
    return anyio.run(_run_coder_async, issue, extra_context, max_turns, memory_block)
