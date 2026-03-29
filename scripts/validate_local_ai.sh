#!/usr/bin/env bash
# validate_local_ai.sh — Run all 4 validation tests for local AI setup
# Usage: bash scripts/validate_local_ai.sh

set -uo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

PASS=0
FAIL=0
declare -a REPORT=()

result() {
  local status="$1" test="$2" detail="$3"
  if [[ "$status" == "PASS" ]]; then
    REPORT+=("${GREEN}PASS${NC}  ${test}  —  ${detail}")
    ((PASS++))
  else
    REPORT+=("${RED}FAIL${NC}  ${test}  —  ${detail}")
    ((FAIL++))
  fi
}

echo -e "\n${BOLD}━━━ Local AI Validation Checklist ━━━${NC}\n"

# ──────────────────────────────────────────────
# Pre-check: Ollama running
# ──────────────────────────────────────────────
if ! curl -sf http://localhost:11434/ &>/dev/null; then
  echo -e "${RED}Ollama is not running. Start it first:${NC}"
  echo "  ollama serve &"
  echo "  Then re-run: bash scripts/validate_local_ai.sh"
  exit 1
fi
echo -e "${GREEN}Ollama service: running${NC}\n"

# ──────────────────────────────────────────────
# TEST 1 — Small code edit (qwen2.5-coder:3b)
# Expected: <10 sec, correct Python output
# ──────────────────────────────────────────────
echo "Test 1: Small code edit  [qwen2.5-coder:3b]"
START=$(date +%s)
RESPONSE=$(ollama run qwen2.5-coder:3b \
  "Fix this Python function (return corrected code only, no explanation):
def add(a b):
    return a + b" 2>&1) || true
END=$(date +%s)
ELAPSED=$((END - START))

if echo "$RESPONSE" | grep -q "def add" && [[ "$ELAPSED" -lt 30 ]]; then
  echo -e "  ${GREEN}✓ Response in ${ELAPSED}s${NC}"
  result "PASS" "Small code edit" "${ELAPSED}s — model responded with corrected function"
else
  echo -e "  ${RED}✗ Failed or slow (${ELAPSED}s)${NC}"
  result "FAIL" "Small code edit" "${ELAPSED}s — check model is loaded: ollama list"
fi

# ──────────────────────────────────────────────
# TEST 2 — Generate docstring (qwen2.5-coder:3b)
# Expected: properly formatted docstring
# ──────────────────────────────────────────────
echo ""
echo "Test 2: Generate docstring  [qwen2.5-coder:3b]"
START=$(date +%s)
RESPONSE=$(ollama run qwen2.5-coder:3b \
  "Write a Python docstring for this function (docstring only, no other text):
def calculate_risk_score(tasks_blocked, milestone_date, today):
    pass" 2>&1) || true
END=$(date +%s)
ELAPSED=$((END - START))

if echo "$RESPONSE" | grep -qi '"""' || echo "$RESPONSE" | grep -qi "Args\|Returns\|param\|:param"; then
  echo -e "  ${GREEN}✓ Docstring generated in ${ELAPSED}s${NC}"
  result "PASS" "Generate docstring" "${ELAPSED}s — formatted docstring returned"
else
  echo -e "  ${RED}✗ No docstring detected in response${NC}"
  echo "  Response: $(echo "$RESPONSE" | head -c 80)"
  result "FAIL" "Generate docstring" "response did not contain docstring format"
fi

# ──────────────────────────────────────────────
# TEST 3 — Summarize file (phi3:mini)
# Expected: captures purpose, key functions, dependencies
# ──────────────────────────────────────────────
echo ""
echo "Test 3: Summarize file  [phi3:mini]"

# Use a real project file if available, else a synthetic snippet
if [[ -f "context_assembly/assembler.py" ]]; then
  TARGET_FILE="context_assembly/assembler.py"
  CONTENT=$(head -60 "$TARGET_FILE")
else
  TARGET_FILE="(synthetic)"
  CONTENT='import json
from state.canonical_state import CanonicalState
from knowledge_graph.query_service import query_graph

def assemble_context(project_id: str, event_type: str) -> dict:
    """Builds scoped context for an agent invocation."""
    state_slice = CanonicalState.get_slice(project_id)
    graph_ctx = query_graph(project_id, hops=2)
    return {"state": state_slice, "graph": graph_ctx, "project_id": project_id}'
fi

START=$(date +%s)
RESPONSE=$(ollama run phi3:mini \
  "Summarize this Python file for an AI assistant.
Include: purpose, key functions, dependencies, potential issues.
Limit to 150-200 words. Be specific.

---
${CONTENT}" 2>&1) || true
END=$(date +%s)
ELAPSED=$((END - START))
WORD_COUNT=$(echo "$RESPONSE" | wc -w | tr -d ' ')

if [[ "$WORD_COUNT" -gt 30 ]] && [[ "$ELAPSED" -lt 60 ]]; then
  echo -e "  ${GREEN}✓ Summary generated in ${ELAPSED}s (${WORD_COUNT} words)${NC}"
  result "PASS" "Summarize file (${TARGET_FILE})" "${ELAPSED}s — ${WORD_COUNT} word summary"
else
  echo -e "  ${RED}✗ Summary too short or too slow (${ELAPSED}s, ${WORD_COUNT} words)${NC}"
  result "FAIL" "Summarize file" "${ELAPSED}s, ${WORD_COUNT} words — expected >30 words in <60s"
fi

# ──────────────────────────────────────────────
# TEST 4 — OpenAI-compatible API endpoint
# Expected: valid JSON response via HTTP
# ──────────────────────────────────────────────
echo ""
echo "Test 4: OpenAI-compatible API endpoint"
START=$(date +%s)
HTTP_RESPONSE=$(curl -sf http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ollama" \
  -d '{"model":"phi3:mini","messages":[{"role":"user","content":"Reply with only the word: VALID"}],"max_tokens":5}' \
  2>&1) || true
END=$(date +%s)
ELAPSED=$((END - START))

if echo "$HTTP_RESPONSE" | grep -qi "VALID\|choices\|message"; then
  echo -e "  ${GREEN}✓ API responded in ${ELAPSED}s${NC}"
  result "PASS" "OpenAI-compatible API" "${ELAPSED}s — valid JSON response from /v1/chat/completions"
else
  echo -e "  ${RED}✗ API did not return expected response${NC}"
  result "FAIL" "OpenAI-compatible API" "check ollama serve is running and phi3:mini is installed"
fi

# ──────────────────────────────────────────────
# SUMMARY
# ──────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━ Results ━━━${NC}"
echo ""
for line in "${REPORT[@]}"; do
  echo -e "  $line"
done
echo ""

TOTAL=$((PASS + FAIL))
if [[ "$FAIL" -eq 0 ]]; then
  echo -e "${GREEN}${BOLD}All ${TOTAL} tests passed.${NC} Local AI stack is ready."
else
  echo -e "${YELLOW}${BOLD}${PASS}/${TOTAL} tests passed.${NC} ${FAIL} failed."
  echo ""
  echo "  Troubleshooting:"
  echo "  - Is Ollama running?   pgrep -x ollama"
  echo "  - Models installed?    ollama list"
  echo "  - Memory pressure?     open -a 'Activity Monitor'"
  echo "  - Server logs?         tail -50 /tmp/ollama.log"
fi
echo ""
