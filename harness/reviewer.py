"""
reviewer.py: Code diff reviewer using claude-agent-sdk.

Uses ClaudeSDKClient with a custom submit_review MCP tool to get a
structured approval decision from Claude.

Auth: uses the system `claude` CLI (logged in via Claude Max on this machine).
No ANTHROPIC_API_KEY needed.
"""
import anyio
import os
import shutil
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

_CLAUDE_BIN = os.environ.get("CLI_PATH") or shutil.which("claude") or None

# Phase B showed the reviewer chronically over-asks: precision 100%,
# recall 50-57%, with every rejection being of an oracle-passing diff.
# The old prompt listed "correctness, edge cases, test coverage, code
# style" with equal weight, which primed the model to reject any
# correct fix that lacked new tests. This rewrite makes correctness
# the primary criterion and explicitly tells the reviewer not to reject
# for missing tests when the fix itself is right.
SYSTEM_BASE = (
    "You are a code reviewer. Given a GitHub issue and a diff, decide "
    "whether the fix correctly addresses the issue.\n\n"
    "PRIMARY CRITERION: correctness. Does the diff actually make the "
    "bug go away? If yes, lean toward approve.\n\n"
    "Secondary considerations (mention in comments but do NOT block "
    "approval on these alone):\n"
    "- edge cases the fix might miss\n"
    "- consistency with the repo's existing style and patterns\n"
    "- risk of regressions in adjacent code\n\n"
    "TEST COVERAGE: do NOT reject a correct fix just for missing new "
    "tests. Many valid bug fixes in this repo ship without new tests. "
    "You may note that tests would be nice-to-have in your comments, "
    "but if the fix is small, correct, and consistent with the repo's "
    "patterns, approve it.\n\n"
    "Call submit_review with your decision."
)


def _build_system_prompt(memory_block: str = "", history_block: str = "") -> str:
    """Compose the reviewer system prompt.

    Blocks are appended in order: base rubric, then calibration memory
    (past wins and losses vs the oracle), then git-history context
    (what past similar fixes in this repo looked like).
    """
    parts = [SYSTEM_BASE]
    if memory_block:
        parts.append(memory_block)
    if history_block:
        parts.append(history_block)
    return "\n\n".join(parts)


def _log(msg: str):
    print(f"    [reviewer] {msg}", flush=True)


async def _run_reviewer_async(
    issue: dict,
    diff: str,
    memory_block: str = "",
    history_block: str = "",
) -> dict:
    if not diff.strip():
        _log("No diff to review, rejecting.")
        return {"approved": False, "comments": ["No changes were made."]}

    review_result = {"approved": False, "comments": []}

    @tool(
        "submit_review",
        "Submit your review decision.",
        {
            "approved": bool,
            "comments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of specific change requests. Empty list if approved.",
            },
        },
    )
    async def submit_review(args):
        review_result["approved"] = bool(args.get("approved", False))
        comments = args.get("comments", [])
        # Coerce to list[str] in case Claude returns a plain string
        if isinstance(comments, str):
            comments = [c.strip() for c in comments.split("\n") if c.strip()]
        review_result["comments"] = comments
        return {"content": [{"type": "text", "text": "Review recorded."}]}

    mcp_server = create_sdk_mcp_server(
        name="review-tools",
        version="1.0.0",
        tools=[submit_review],
    )

    user_prompt = (
        f"Issue #{issue['number']}: {issue['title']}\n\n"
        f"Description: {issue['body_summary']}\n\n"
        f"Diff:\n```diff\n{diff[:8000]}\n```\n\n"
        "Review the diff and call submit_review with your decision."
    )

    model = os.environ.get("AGENT_MODEL", "haiku")

    options = ClaudeAgentOptions(
        model=model,
        system_prompt=_build_system_prompt(memory_block, history_block),
        max_turns=5,
        allowed_tools=["mcp__review-tools__submit_review"],
        permission_mode="default",
        mcp_servers={"review-tools": mcp_server},
        **({"cli_path": _CLAUDE_BIN} if _CLAUDE_BIN else {}),
    )

    start = time.time()
    _log(f"Reviewing diff ({len(diff)} chars)...")

    async with ClaudeSDKClient(options=options) as client:
        await client.query(user_prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        first_line = block.text.strip().split("\n")[0][:120]
                        _log(f"[{time.time() - start:.0f}s] {first_line}")
                    elif isinstance(block, ToolUseBlock):
                        _log(f"[{time.time() - start:.0f}s] tool: {block.name}")
            elif isinstance(msg, ResultMessage):
                break

    verdict = "APPROVED" if review_result["approved"] else f"REJECTED ({len(review_result['comments'])} comments)"
    _log(f"Done ({time.time() - start:.0f}s): {verdict}")

    return review_result


def run_reviewer(
    issue: dict,
    diff: str,
    memory_block: str = "",
    history_block: str = "",
) -> dict:
    """
    Review a diff for a given issue.
    Returns {"approved": bool, "comments": [str, ...]}.

    `memory_block`  optional calibration preamble (win/loss exemplars).
    `history_block` optional git-history context: up to 3 pre-baseline
                    commits that touched the same files. Tells the
                    reviewer what a typical fix in this repo looks
                    like, so it does not over-ask relative to the
                    repo's own patterns.
    """
    return anyio.run(_run_reviewer_async, issue, diff, memory_block, history_block)
