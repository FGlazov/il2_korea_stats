#!/usr/bin/env bash
# Summarize design_doc/ changes that haven't been reviewed yet.
# Usage: design_doc_changes.sh          show changes since the last recorded review
#        design_doc_changes.sh --mark   record HEAD as reviewed
set -euo pipefail

root=$(git rev-parse --show-toplevel)
cd "$root"
marker=".claude/.design-doc-last-reviewed"

if [ "${1:-}" = "--mark" ]; then
  git rev-parse HEAD > "$marker"
  echo "Recorded design_doc review at $(git rev-parse --short HEAD)."
  exit 0
fi

last=""
[ -f "$marker" ] && last=$(tr -d '[:space:]' < "$marker")

if [ -n "$last" ] && git cat-file -e "${last}^{commit}" 2>/dev/null; then
  echo "== Commits touching design_doc/ since last review ($(git rev-parse --short "$last")):"
  commits=$(git log --oneline "${last}..HEAD" -- design_doc/)
  if [ -n "$commits" ]; then
    echo "$commits"
    echo
    echo "== Files changed since last review:"
    git diff --stat "$last" HEAD -- design_doc/
  else
    echo "(none)"
  fi
else
  echo "== No review recorded yet. Last 10 commits touching design_doc/:"
  git log --oneline -10 -- design_doc/
fi

echo
echo "== Uncommitted changes in design_doc/ (the maintainer may have edited directly):"
uncommitted=$(git status --short -- design_doc/)
if [ -n "$uncommitted" ]; then
  echo "$uncommitted"
  git diff HEAD --stat -- design_doc/
else
  echo "(none)"
fi

echo
echo "== Open questions:"
grep -E '^\*\*OQ-' design_doc/11_open_questions.md || echo "(none)"
