#!/usr/bin/env bash
# compress_context.sh — Summarize a file with phi3:mini to reduce Claude token usage
#
# Usage:
#   bash scripts/compress_context.sh <file_path> [--mode summary|functions|deps|bugs]
#   bash scripts/compress_context.sh agents/risk_intelligence/agent.py
#   bash scripts/compress_context.sh agents/risk_intelligence/agent.py --mode bugs
#
# Output: compressed summary to stdout
# Pipe to clipboard: bash scripts/compress_context.sh file.py | pbcopy

set -euo pipefail

# ─── Args ────────────────────────────────────────────────────────────────────
FILE=""
MODE="summary"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    -*) echo "Unknown flag: $1" >&2; exit 1 ;;
    *) FILE="$1"; shift ;;
  esac
done

if [[ -z "$FILE" ]]; then
  echo "Usage: bash scripts/compress_context.sh <file_path> [--mode summary|functions|deps|bugs]" >&2
  exit 1
fi

if [[ ! -f "$FILE" ]]; then
  echo "Error: file not found: $FILE" >&2
  exit 1
fi

# ─── Check Ollama running ─────────────────────────────────────────────────────
if ! curl -sf http://localhost:11434/ &>/dev/null; then
  echo "Error: Ollama is not running. Start it: ollama serve &" >&2
  exit 1
fi

# ─── Check phi3:mini installed ────────────────────────────────────────────────
if ! ollama list 2>/dev/null | grep -q "phi3:mini"; then
  echo "Error: phi3:mini not installed. Run: ollama pull phi3:mini" >&2
  exit 1
fi

# ─── Select prompt by mode ────────────────────────────────────────────────────
FILE_CONTENT=$(cat "$FILE")
FILENAME=$(basename "$FILE")
LINES=$(wc -l < "$FILE" | tr -d ' ')

case "$MODE" in
  summary)
    PROMPT="Summarize this file for an AI assistant (Claude).
Include:
- Purpose: what this file does (one sentence)
- Key functions: each function name and its job (one line per function)
- Dependencies: what it imports from other project files (not stdlib)
- Potential issues: bugs, missing error handling, or design problems
Limit to 150-200 words. Be specific, not generic.

File: ${FILENAME} (${LINES} lines)
---
${FILE_CONTENT}"
    ;;
  functions)
    PROMPT="List every function and class in this file.
For each: name, parameters, return type (if visible), one-sentence description.
No code. No explanation. Just the list.

File: ${FILENAME}
---
${FILE_CONTENT}"
    ;;
  deps)
    PROMPT="List what this file imports from other project files (not Python stdlib or third-party packages).
Output format: import_path → what is used from it
One line per dependency. No explanation.

File: ${FILENAME}
---
${FILE_CONTENT}"
    ;;
  bugs)
    PROMPT="Scan this file for potential issues.
Look specifically for:
- Missing error handling on external calls
- Hardcoded values that should be config
- Empty required fields (e.g. uncertainty_notes = [])
- Policy checks bypassed or missing
- Cross-project data leaks
- Bare 'allow' used where 'allow_with_audit' is required
List only real issues, not style suggestions. Limit to 150 words.

File: ${FILENAME}
---
${FILE_CONTENT}"
    ;;
  *)
    echo "Unknown mode: $MODE. Choose: summary, functions, deps, bugs" >&2
    exit 1
    ;;
esac

# ─── Run compression ─────────────────────────────────────────────────────────
echo "=== COMPRESSED CONTEXT: ${FILE} (mode: ${MODE}) ===" >&2
echo "" >&2

ollama run phi3:mini "$PROMPT"

echo "" >&2
echo "=== END ===" >&2
echo "(Pipe to clipboard: bash scripts/compress_context.sh ${FILE} | pbcopy)" >&2
