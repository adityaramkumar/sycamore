"""
distill.py: Mine completed traces for memory updates.

After each issue runs, this module decides what (if anything) to add
to the coder's lesson store and the reviewer's calibration store.

Reviewer updates are purely structural: every round contributes to the
2x2 win/loss table from DESIGN.md sec 4.2. Each round produces one
calibration item tagged by its (verdict, oracle) outcome.

Coder updates require an LLM call: given a successful (oracle-passed)
diff, we ask a small distillation model to emit 1-2 short, generalizable
bullets. We deliberately use a separate AGENT/MODEL env var
(DISTILL_MODEL, defaults to AGENT_MODEL or haiku) so the distillation
quality can be raised independently of the loop agents (DESIGN.md sec
'Risks').

Scheduling (alternating updates per DESIGN.md sec 4.3) is applied by
the loop driver, NOT here. update_from_trace honors a `schedule` dict
of {"update_coder": bool, "update_reviewer": bool}; defaults to both.
"""
from __future__ import annotations

import os
import shutil

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)

try:
    from harness.memory import (
        CoderMemory,
        ReviewerMemory,
        categorize,
        make_item,
    )
except ImportError:
    from memory import (  # type: ignore
        CoderMemory,
        ReviewerMemory,
        categorize,
        make_item,
    )

_CLAUDE_BIN = os.environ.get("CLI_PATH") or shutil.which("claude") or None
DISTILL_MODEL = os.environ.get("DISTILL_MODEL") or os.environ.get("AGENT_MODEL", "haiku")
MAX_LESSONS_PER_ISSUE = 2
MAX_LESSON_CHARS = 200


def _log(msg: str) -> None:
    print(f"    [distill] {msg}", flush=True)


def alternating_schedule(stream_index: int) -> dict:
    """DESIGN.md sec 4.3 alternating-update schedule.

    `stream_index` is 1-based position in the training stream (NOT the
    issue.number). Held-out issues should not call this at all.
    """
    return {
        "update_coder": stream_index % 2 == 1,
        "update_reviewer": stream_index % 2 == 0,
    }


def _classify_round(approved: bool, oracle_passed: bool) -> str:
    """Map (verdict, oracle) to one of the four reviewer outcomes."""
    if approved and oracle_passed:
        return "true_approval"
    if approved and not oracle_passed:
        return "false_approval"
    if not approved and not oracle_passed:
        return "true_rejection"
    return "false_rejection"


_REVIEWER_NOTE_TEMPLATES = {
    "true_approval":
        "Approved this kind of fix and tests passed. Pattern looks correct.",
    "true_rejection":
        "Rejected this kind of fix and tests confirmed it was wrong. "
        "Comment that triggered: {comment}",
    "false_approval":
        "Approved this fix but tests FAILED ({n_failed} failures, e.g. {first_fail}). "
        "Look harder for this kind of bug.",
    "false_rejection":
        "Rejected this fix but tests PASSED. Possibly over-asking. "
        "Comment was: {comment}",
}


def _build_reviewer_note(outcome: str, round_data: dict, oracle: dict) -> str:
    template = _REVIEWER_NOTE_TEMPLATES[outcome]
    comment = ""
    comments = round_data.get("comments") or []
    if comments:
        comment = str(comments[0])[:160]
    failing = oracle.get("failing_tests") or []
    return template.format(
        comment=comment or "(no comment)",
        n_failed=oracle.get("n_failed", 0),
        first_fail=(failing[0] if failing else "n/a"),
    )


def _select_coder_lesson_round(trace: dict) -> dict | None:
    """Pick the best round to distill a coder lesson from.

    Prefer round 1 (cleanest signal; coder solved it without reviewer
    contamination). Otherwise the earliest oracle-passed round.
    """
    rounds = trace.get("comments_per_round") or []
    for r in rounds:
        oracle = r.get("oracle") or {}
        if oracle.get("passed"):
            return r
    return None


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "..."


# Coder-lesson distillation LLM call. Uses an MCP tool to capture
# structured output rather than parsing free-form text.
async def _distill_coder_lessons_async(
    issue: dict, category: str, diff: str
) -> list[str]:
    captured: dict[str, list[str]] = {"lessons": []}

    @tool(
        "submit_lessons",
        "Submit 1 or 2 short, generalizable lessons learned from this fix.",
        {
            "lessons": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Up to 2 single-sentence lessons, each <= 200 chars. Generalize. "
                    "Do not include specific values from this issue."
                ),
            }
        },
    )
    async def submit_lessons(args):
        items = args.get("lessons") or []
        if isinstance(items, str):
            items = [items]
        captured["lessons"] = [str(x).strip() for x in items if str(x).strip()]
        return {"content": [{"type": "text", "text": "Recorded."}]}

    mcp_server = create_sdk_mcp_server(
        name="distill-tools",
        version="1.0.0",
        tools=[submit_lessons],
    )

    system = (
        "You distill GENERALIZABLE engineering lessons from a successful bug fix. "
        "Each lesson must be ONE sentence, <=200 characters, focused on FILE "
        "LOCATIONS, FUNCTION NAMES, or APPROACH. Never include the specific "
        "values, dates, locales, or numbers from this issue. Aim for guidance "
        "that helps with FUTURE bugs in the same area. Output 1-2 lessons."
    )
    user = (
        f"Bug category: {category}\n"
        f"Issue title: {issue.get('title', '')}\n\n"
        f"Successful fix diff:\n```diff\n{_truncate(diff, 3500)}\n```\n\n"
        "Call submit_lessons with up to 2 bullet lessons."
    )

    options = ClaudeAgentOptions(
        model=DISTILL_MODEL,
        system_prompt=system,
        max_turns=3,
        allowed_tools=["mcp__distill-tools__submit_lessons"],
        permission_mode="default",
        mcp_servers={"distill-tools": mcp_server},
        **({"cli_path": _CLAUDE_BIN} if _CLAUDE_BIN else {}),
    )

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(user)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            pass  # tool body already captured args
                elif isinstance(msg, ResultMessage):
                    break
    except Exception as e:  # noqa: BLE001 - distillation is best-effort
        _log(f"distillation LLM call failed: {e!r}")
        return []

    lessons = [_truncate(x, MAX_LESSON_CHARS) for x in captured["lessons"][:MAX_LESSONS_PER_ISSUE]]
    return [x for x in lessons if x]


def _distill_coder_lessons(issue: dict, category: str, diff: str) -> list[str]:
    if not diff.strip():
        return []
    return anyio.run(_distill_coder_lessons_async, issue, category, diff)


def update_from_trace(
    trace: dict,
    issue: dict,
    coder_memory: CoderMemory | None = None,
    reviewer_memory: ReviewerMemory | None = None,
    schedule: dict | None = None,
) -> dict:
    """Walk a finished trace and update both memories per the schedule.

    Returns a summary dict {coder_lessons, reviewer_cases} of what
    was added (for logging/inspection).
    """
    schedule = schedule or {"update_coder": True, "update_reviewer": True}
    summary = {"coder_lessons": [], "reviewer_cases": []}

    rounds = trace.get("comments_per_round") or []
    issue_number = int(trace.get("issue_number") or issue.get("number") or 0)

    if reviewer_memory is not None and schedule.get("update_reviewer"):
        for r in rounds:
            oracle = r.get("oracle") or {}
            if not oracle:
                continue  # pre-oracle traces - nothing to calibrate against
            # Empty-diff rounds are degenerate: oracle trivially passes
            # on a clean baseline and reviewer trivially rejects "no
            # changes". Skipping these matches metrics._reviewer_confusion.
            if int(r.get("diff_length", 0)) <= 0:
                continue
            outcome = _classify_round(
                bool(r.get("approved")),
                bool(oracle.get("passed")),
            )
            note = _build_reviewer_note(outcome, r, oracle)
            diff_snippet = (r.get("diff") or "")[:1500]
            item = make_item(
                text=note,
                tag=outcome,
                source_issue=issue_number,
                diff_snippet=diff_snippet,
            )
            reviewer_memory.add(item)
            summary["reviewer_cases"].append({"outcome": outcome, "id": item.id})
        if summary["reviewer_cases"]:
            _log(
                f"reviewer memory: +{len(summary['reviewer_cases'])} cases "
                f"({', '.join(c['outcome'] for c in summary['reviewer_cases'])})"
            )

    if coder_memory is not None and schedule.get("update_coder"):
        passed_round = _select_coder_lesson_round(trace)
        if passed_round is not None:
            category = categorize(issue)
            lessons = _distill_coder_lessons(
                issue,
                category,
                passed_round.get("diff", ""),
            )
            for text in lessons:
                item = make_item(
                    text=text,
                    tag=category,
                    source_issue=issue_number,
                )
                coder_memory.add(item)
                summary["coder_lessons"].append({"category": category, "id": item.id, "text": text})
            if lessons:
                _log(f"coder memory: +{len(lessons)} lesson(s) in [{category}]")

    return summary
