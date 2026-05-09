#!/bin/bash
# Ralph loop — Bibliotype triage variant.
#
# Differences from the upstream Ralph scaffolding:
#   - Uses RALPH_PROMPT.md so it doesn't collide with the project's CLAUDE.md
#   - prd.json lives next to this script (scripts/ralph/prd.json)
#   - progress.txt lives at the project root (per PRD US-000) so AGENTS.md and
#     progress.txt sit next to each other where the agent expects them.
#   - Claude is invoked with the project root as CWD so it can edit code freely.

set -e

TOOL="claude"
MAX_ITERATIONS=10

while [[ $# -gt 0 ]]; do
  case $1 in
    --tool) TOOL="$2"; shift 2 ;;
    --tool=*) TOOL="${1#*=}"; shift ;;
    *) [[ "$1" =~ ^[0-9]+$ ]] && MAX_ITERATIONS="$1"; shift ;;
  esac
done

if [[ "$TOOL" != "amp" && "$TOOL" != "claude" ]]; then
  echo "Error: Invalid tool '$TOOL'. Must be 'amp' or 'claude'." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PRD_FILE="$SCRIPT_DIR/prd.json"
PROMPT_FILE="$SCRIPT_DIR/RALPH_PROMPT.md"
PROGRESS_FILE="$PROJECT_ROOT/progress.txt"
LAST_BRANCH_FILE="$SCRIPT_DIR/.last-branch"

if [ ! -f "$PRD_FILE" ]; then
  echo "Missing $PRD_FILE — run scripts/ralph/convert_prd.py first." >&2
  exit 1
fi
if [ ! -f "$PROMPT_FILE" ]; then
  echo "Missing $PROMPT_FILE — copy from the ralph plugin." >&2
  exit 1
fi

if command -v jq >/dev/null 2>&1; then
  CURRENT_BRANCH=$(jq -r '.branchName // empty' "$PRD_FILE" 2>/dev/null || echo "")
  [ -n "$CURRENT_BRANCH" ] && echo "$CURRENT_BRANCH" > "$LAST_BRANCH_FILE"
fi

if [ ! -f "$PROGRESS_FILE" ]; then
  {
    echo "# Bibliotype triage progress log"
    echo ""
    echo "## Codebase Patterns"
    echo "- (populated as Ralph iterations discover them)"
    echo ""
    echo "---"
  } > "$PROGRESS_FILE"
fi

cd "$PROJECT_ROOT"
echo "Starting Ralph — tool=$TOOL max_iterations=$MAX_ITERATIONS cwd=$PROJECT_ROOT"

for i in $(seq 1 "$MAX_ITERATIONS"); do
  echo ""
  echo "=============================================================="
  echo "  Ralph iteration $i / $MAX_ITERATIONS  ($TOOL)"
  echo "=============================================================="

  if [[ "$TOOL" == "amp" ]]; then
    OUTPUT=$(cat "$PROMPT_FILE" | amp --dangerously-allow-all 2>&1 | tee /dev/stderr) || true
  else
    OUTPUT=$(claude --dangerously-skip-permissions --print < "$PROMPT_FILE" 2>&1 | tee /dev/stderr) || true
  fi

  if echo "$OUTPUT" | grep -q "<promise>COMPLETE</promise>"; then
    echo ""
    echo "Ralph completed all stories at iteration $i."
    exit 0
  fi

  echo "Iteration $i complete; continuing..."
  sleep 2
done

echo ""
echo "Ralph reached max iterations ($MAX_ITERATIONS) without completing all stories."
echo "Inspect $PROGRESS_FILE for status; relaunch ralph.sh to keep going."
exit 0
