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

# Use the system claude CLI (already logged in via Claude Max).
# Falls back to whatever the SDK bundles if not found.
_CLAUDE_BIN = os.environ.get("CLI_PATH") or shutil.which("claude") or None


def _build_system_prompt(extra_context: str = "", memory_block: str = "") -> str:
    base = (
        "You are a coding agent fixing bugs in a Python project. "
        "Be efficient: read the relevant file, make the fix, run tests, call submit_fix.\n\n"
        "Workflow:\n"
        "1. Read the file(s) likely to contain the bug\n"
        "2. Make the minimal fix\n"
        "3. Run the relevant tests with Bash\n"
        "4. Call submit_fix with a one-sentence summary\n\n"
        "Do NOT explore the repo structure extensively. The bug is likely in "
        "arrow/arrow.py, arrow/locales.py, or arrow/parser.py.\n\n"
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
    max_turns: int = 30,
    memory_block: str = "",
) -> str:
    """Async impl. Returns the git diff produced by the agent."""

    start = time.time()
    turn = 0
    fix_submitted = {"done": False, "summary": ""}

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
        system_prompt=_build_system_prompt(extra_context, memory_block),
        cwd=os.path.abspath(REPO),
        max_turns=max_turns,
        allowed_tools=["Read", "Write", "Edit", "Bash", "mcp__worktrial-tools__submit_fix"],
        permission_mode="acceptEdits",
        mcp_servers={"worktrial-tools": mcp_server},
        **({"cli_path": _CLAUDE_BIN} if _CLAUDE_BIN else {}),
    )

    _log(f"Starting on issue #{issue['number']}...")

    async with ClaudeSDKClient(options=options) as client:
        await client.query(user_prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                turn += 1
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        # Show first line of narration as a status update
                        first_line = block.text.strip().split("\n")[0][:120]
                        _log(f"[turn {turn}, {time.time() - start:.0f}s] {first_line}")
                    elif isinstance(block, ToolUseBlock):
                        _log(f"[turn {turn}, {time.time() - start:.0f}s] tool: {block.name}")
            elif isinstance(msg, ResultMessage):
                break  # conversation finished

    elapsed = time.time() - start
    _log(f"Done ({turn} turns, {elapsed:.0f}s). fix_submitted={fix_submitted['done']}")

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
    max_turns: int = 30,
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
