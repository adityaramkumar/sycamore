"""
reviewer.py — Code diff reviewer using claude-agent-sdk.

Uses ClaudeSDKClient with a custom submit_review MCP tool to get a structured
approval decision from Claude.

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

SYSTEM = (
    "You are a strict code reviewer. Given a GitHub issue and a diff, decide whether "
    "the fix correctly addresses the issue. Check for: correctness, edge cases, test "
    "coverage, and code style. Call submit_review with your decision."
)


def _log(msg: str):
    print(f"    [reviewer] {msg}", flush=True)


async def _run_reviewer_async(issue: dict, diff: str) -> dict:
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
        system_prompt=SYSTEM,
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


def run_reviewer(issue: dict, diff: str) -> dict:
    """
    Review a diff for a given issue.
    Returns {"approved": bool, "comments": [str, ...]}
    """
    return anyio.run(_run_reviewer_async, issue, diff)
