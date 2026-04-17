#!/usr/bin/env bash
# run_parallel_ablation.sh: launch a parallel FULL-vs-ABLATE ablation.
#
# For each issue in $ISSUES, spawns one FULL-arm worker (memory on,
# git-history retrieval on, distillation on) and one ABLATE-arm worker
# (--ablate --no-history). Each worker gets its own git worktree and
# its own TRACES_DIR / MEMORY_DIR so they can't step on each other.
#
# Caveats (see docs/RESULTS.md for details):
#   - In parallel mode, each FULL worker starts with an empty memory
#     dir. Within-arm memory accumulation is NOT exercised. To measure
#     that, run sequentially with `python -m harness.loop --issues ...`.
#   - 10 concurrent Sonnet sessions on one API key is close to tier-1
#     rate limits. If you hit Overloaded errors, reduce $ISSUES or
#     batch the two arms rather than running them concurrently.
#
# Usage:
#   ANTHROPIC_API_KEY=sk-... AGENT_MODEL=sonnet \
#     scripts/run_parallel_ablation.sh 1056 815 1224 1240 607
#
# After the run:
#   mkdir -p traces-full traces-ablate
#   cp traces-full-*/*.json  traces-full/
#   cp traces-ablate-*/*.json traces-ablate/
#   python harness/eval.py traces-full
#   python harness/eval.py traces-ablate
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <issue_num> [<issue_num> ...]" >&2
    exit 2
fi

if [[ ! -d arrow/.git ]]; then
    echo "error: ./arrow not found. git clone https://github.com/arrow-py/arrow ./arrow first." >&2
    exit 1
fi

: "${AGENT_MODEL:=sonnet}"
: "${MAX_ROUNDS:=3}"

ISSUES=("$@")
BASELINE=$(python -c "import json; print(json.load(open('data/issues.json'))['baseline_commit'])")

echo ">>> Issues to run: ${ISSUES[*]}"
echo ">>> Model: $AGENT_MODEL"
echo ">>> max-rounds: $MAX_ROUNDS"
echo ">>> Creating ${#ISSUES[@]} full-arm + ${#ISSUES[@]} ablate-arm worktrees..."

for i in "${!ISSUES[@]}"; do
    (
        cd arrow
        git worktree add --force "../arrow-$i"         "$BASELINE" >/dev/null 2>&1 || true
        git worktree add --force "../arrow-ablate-$i"  "$BASELINE" >/dev/null 2>&1 || true
    )
done

pids=()

for i in "${!ISSUES[@]}"; do
    issue="${ISSUES[$i]}"

    # FULL arm worker
    (
        TRACES_DIR="./traces-full-$i" \
        MEMORY_DIR="./memory-$i" \
        TARGET_REPO_PATH="./arrow-$i" \
        AGENT_MODEL="$AGENT_MODEL" \
        python -m harness.loop --issue "$issue" --max-rounds "$MAX_ROUNDS" \
            >"/tmp/ablation-full-$i.log" 2>&1
    ) &
    pids+=($!)

    # ABLATE arm worker
    (
        TRACES_DIR="./traces-ablate-$i" \
        TARGET_REPO_PATH="./arrow-ablate-$i" \
        AGENT_MODEL="$AGENT_MODEL" \
        python -m harness.loop --issue "$issue" --max-rounds "$MAX_ROUNDS" \
            --ablate --no-history \
            >"/tmp/ablation-ablate-$i.log" 2>&1
    ) &
    pids+=($!)
done

echo ">>> Launched ${#pids[@]} workers. Tail logs at /tmp/ablation-*.log"
echo ">>> Waiting for all workers to complete..."

for p in "${pids[@]}"; do
    wait "$p" || true
done

echo ">>> All workers finished. Aggregating traces..."
mkdir -p traces-full traces-ablate
for i in "${!ISSUES[@]}"; do
    cp "traces-full-$i"/*.json   traces-full/   2>/dev/null || true
    cp "traces-ablate-$i"/*.json traces-ablate/ 2>/dev/null || true
done

echo
echo "========================= FULL ARM ========================="
python harness/eval.py ./traces-full
echo
echo "========================= ABLATE ARM ========================="
python harness/eval.py ./traces-ablate
