"""
coder.py: Agentic coding loop using claude-agent-sdk.

The agent runs inside the target repo directory, with access to native
Read / Write / Edit / Bash tools (from Claude Code). A custom `submit_fix`
MCP tool signals when the agent is satisfied with its changes.

Auth: uses the system `claude` CLI (logged in via Claude Max on this machine).
No ANTHROPIC_API_KEY needed; set CLI_PATH env var to override the binary path.
"""
import os
import shutil
import subprocess
import time

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)

REPO = os.environ.get("TARGET_REPO_PATH", "./arrow")

# Default turn budget. Phase A with Haiku at 60+ turns was obviously
# flailing; Phase B with Sonnet showed 12 thorough Grep/Read turns is
# normal pre-edit exploration, not flailing. 40 leaves room for
# Sonnet's more careful style while still bounding Haiku-style loops.
# Override via `max_turns` arg or MAX_TURNS env var.
DEFAULT_MAX_TURNS = int(os.environ.get("MAX_TURNS", "40"))

# Use the system claude CLI (already logged in via Claude Max).
# Falls back to whatever the SDK bundles if not found.
_CLAUDE_BIN = os.environ.get("CLI_PATH") or shutil.which("claude") or None


def _build_system_prompt(
    extra_context: str = "",
    memory_block: str = "",
    history_block: str = "",
    turn_budget: int = DEFAULT_MAX_TURNS,
) -> str:
    base = (
        "You are a coding agent fixing bugs in a Python project. "
        "Each fix should include both the source change AND a regression test.\n\n"
        f"TURN BUDGET: You have {turn_budget} turns total. Each message from you "
        "(whether tool use or text) counts as one turn. Typical budget:\n"
        "- ~5-10 turns to locate the bug (read files, grep)\n"
        "- ~2-5 turns to apply the source fix with Edit\n"
        "- ~2-4 turns to add a regression test in the matching tests/ file\n"
        "- ~3-5 turns to verify with Bash\n"
        "- 1 turn to call submit_fix\n"
        "Reserve at least 5 turns at the end to finish the edits and submit_fix. "
        "It is better to submit an imperfect fix than to run out of turns with "
        "nothing.\n\n"
        "Workflow:\n"
        "1. Read the file(s) likely to contain the bug, plus the matching test file.\n"
        "2. Make the source fix with Edit.\n"
        "3. Add a regression test for the bug you just fixed. Put it in the "
        "   tests/ file that matches the module you changed "
        "   (arrow/arrow.py -> tests/arrow_tests.py, arrow/locales.py -> "
        "   tests/locales_tests.py, arrow/parser.py -> tests/parser_tests.py). "
        "   Follow the existing test class and assertion patterns in that file. "
        "   The test should FAIL on the old code and PASS on your fix. Skip the "
        "   test only if the change is genuinely trivial (e.g. a typo fix) or "
        "   if writing a test would require infrastructure the repo does not have.\n"
        "4. Run the relevant tests with Bash to confirm both old coverage and "
        "   your new test pass.\n"
        "5. Call submit_fix with a one-sentence summary.\n\n"
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
    # Ordering: concrete examples from this repo's git history first
    # (tied to the current issue), then distilled cross-issue lessons
    # from the agent's own past traces, then the round-specific
    # reviewer feedback. History + memory together approximate
    # "context the agent would have if it worked on this repo full-time".
    if history_block:
        base += f"\n\n{history_block}"
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
    history_block: str = "",
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
        name="coder-tools",
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
        system_prompt=_build_system_prompt(
            extra_context=extra_context,
            memory_block=memory_block,
            history_block=history_block,
            turn_budget=max_turns,
        ),
        cwd=os.path.abspath(REPO),
        max_turns=max_turns,
        allowed_tools=["Read", "Write", "Edit", "Bash", "mcp__coder-tools__submit_fix"],
        permission_mode="acceptEdits",
        mcp_servers={"coder-tools": mcp_server},
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
                # Flailing heuristic: excessive Bash-only streak. Not Read/Grep,
                # because Sonnet's thorough exploration is often Grep-heavy
                # pre-edit and that's normal, not flailing. Bash streaks usually
                # indicate "keep running tests, not edit yet" loops.
                if bash_run_length >= 12:
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
    history_block: str = "",
) -> str:
    """
    Run the coder agent on an issue.
    Returns the git diff string of all changes made.

    `memory_block`  - distilled cross-issue lessons from past traces.
    `history_block` - concrete pre-baseline commits retrieved from the
                      repo's git log that look related to this issue.

    Both are optional and injected by the loop layer, which owns
    memory state and retrieval. The coder itself has no persistence.
    """
    return anyio.run(
        _run_coder_async,
        issue,
        extra_context,
        max_turns,
        memory_block,
        history_block,
    )
