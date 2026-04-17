"""
eval.py: CLI wrapper over harness.metrics.

Usage:
    python harness/eval.py ./traces
    python harness/eval.py ./traces --json
    python harness/eval.py ./traces --held-out 1015,541,686

The substance lives in harness.metrics; this file just parses args,
loads traces, and prints. Kept as a separate entrypoint to preserve
the README's documented CLI.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Allow `python harness/eval.py` (script invocation) to find the harness package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.metrics import compute, compute_split, load_traces, print_summary


def _parse_held_out(s: str | None) -> set[int] | None:
    if not s:
        return None
    out: set[int] = set()
    for tok in s.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.add(int(tok))
        except ValueError:
            print(f"WARNING: ignoring non-integer held-out token {tok!r}", file=sys.stderr)
    return out or None


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute metrics from trace files.")
    parser.add_argument("traces_dir", help="Directory containing issue_*.json traces")
    parser.add_argument("--json", action="store_true", help="Also print full metrics JSON")
    parser.add_argument(
        "--held-out",
        help="Comma-separated issue numbers to report as a held-out split",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.traces_dir):
        print(f"Not a directory: {args.traces_dir}", file=sys.stderr)
        sys.exit(1)

    traces = load_traces(args.traces_dir)
    if not traces:
        print(f"No issue_*.json traces found in {args.traces_dir}")
        sys.exit(0)

    held_out = _parse_held_out(args.held_out)
    metrics = compute_split(traces, held_out) if held_out else compute(traces)

    print_summary(metrics)

    if args.json:
        print("\n--- Full JSON ---")
        print(json.dumps(metrics, indent=2, default=str))


if __name__ == "__main__":
    main()
