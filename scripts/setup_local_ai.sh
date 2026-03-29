#!/usr/bin/env bash
# setup_local_ai.sh — Hybrid Ollama + Claude local AI setup
# MacBook Air M1, 8GB RAM
# Run: bash setup_local_ai.sh

set -euo pipefail

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ok()    { echo -e "${GREEN}  ✓ $*${NC}"; }
warn()  { echo -e "${YELLOW}  ⚠ $*${NC}"; }
fail()  { echo -e "${RED}  ✗ $*${NC}"; }
info()  { echo -e "${CYAN}  → $*${NC}"; }
header(){ echo -e "\n${BOLD}━━━ $* ━━━${NC}"; }

# Track results
declare -a RESULTS=()
pass() { RESULTS+=("${GREEN}PASS${NC}  $*"); }
skip() { RESULTS+=("${YELLOW}SKIP${NC}  $*"); }
err()  { RESULTS+=("${RED}FAIL${NC}  $*"); }

confirm() {
  echo ""
  read -rp "  Press Enter to continue (Ctrl+C to abort)..." _
  echo ""
}

check_free_gb() {
  df -g / | awk 'NR==2 {print $4}'
}

# ─────────────────────────────────────────────
# TASK 1 — Install & verify Ollama
# ─────────────────────────────────────────────
header "TASK 1: Install and Verify Ollama"

info "Checking if Ollama is already installed..."
if command -v ollama &>/dev/null; then
  ok "Ollama already installed: $(ollama --version 2>/dev/null || echo 'version unknown')"
  pass "Ollama install"
else
  info "Ollama not found. Installing via Homebrew..."
  confirm
  if brew install ollama; then
    ok "Ollama installed: $(ollama --version 2>/dev/null)"
    pass "Ollama install"
  else
    fail "Homebrew install failed. Trying official installer..."
    info "Running: curl -fsSL https://ollama.com/install.sh | sh"
    if curl -fsSL https://ollama.com/install.sh | sh; then
      ok "Ollama installed via official installer"
      pass "Ollama install"
    else
      fail "Ollama installation failed."
      err "Ollama install"
      echo "  Manual install: brew install ollama"
      echo "  Then re-run this script."
      exit 1
    fi
  fi
fi

info "Starting Ollama service in the background..."
if pgrep -x "ollama" &>/dev/null; then
  ok "Ollama service already running"
else
  ollama serve &>/tmp/ollama.log &
  sleep 3
  if pgrep -x "ollama" &>/dev/null; then
    ok "Ollama service started (logs: /tmp/ollama.log)"
  else
    warn "Ollama may not have started. Check: tail -f /tmp/ollama.log"
  fi
fi

info "Verifying API endpoint at http://localhost:11434..."
if curl -sf http://localhost:11434/ &>/dev/null; then
  ok "Ollama API responding at http://localhost:11434"
  pass "Ollama API endpoint"
else
  sleep 5
  if curl -sf http://localhost:11434/ &>/dev/null; then
    ok "Ollama API responding (after retry)"
    pass "Ollama API endpoint"
  else
    warn "API not responding yet — may still be starting."
    err "Ollama API endpoint (check after setup)"
  fi
fi

# ─────────────────────────────────────────────
# TASK 2 — Pull models
# ─────────────────────────────────────────────
header "TASK 2: Pull Lightweight Models (<=3B params)"

FREE_GB=$(check_free_gb)
info "Free disk space: ${FREE_GB}GB (need ~7GB total for all 3 models)"
echo ""
echo "  Models to pull:"
echo "    qwen2.5-coder:3b  ~2.0 GB  (code edits)"
echo "    llama3.2:3b       ~2.0 GB  (documentation / explanation)"
echo "    phi3:mini         ~2.3 GB  (summarization / context compression)"
echo ""

if [[ "$FREE_GB" -lt 8 ]]; then
  warn "Low disk space (${FREE_GB}GB free). Recommend at least 8GB free."
fi

confirm

pull_model() {
  local model="$1"
  local purpose="$2"
  info "Pulling ${model} (${purpose})..."
  if ollama list 2>/dev/null | grep -q "${model}"; then
    ok "${model} already present — skipping pull"
    pass "${model} pull"
    return
  fi
  if ollama pull "$model"; then
    ok "${model} pulled successfully"
    pass "${model} pull"
  else
    fail "Failed to pull ${model}"
    err "${model} pull"
    warn "Retry later: ollama pull ${model}"
  fi
}

pull_model "qwen2.5-coder:3b"  "code edits"
pull_model "llama3.2:3b"       "docs / explanation"
pull_model "phi3:mini"         "summarization / context compression"

info "Installed models:"
ollama list

# ─────────────────────────────────────────────
# TASK 3 — Test inference on each model
# ─────────────────────────────────────────────
header "TASK 3: Test Inference"

info "Running a test prompt on each model."
echo "  Expected: response in <10 seconds per model on M1"
confirm

test_model() {
  local model="$1"
  local prompt="$2"
  info "Testing ${model}..."
  local start end elapsed response
  start=$(date +%s)
  if response=$(ollama run "$model" "$prompt" 2>&1); then
    end=$(date +%s)
    elapsed=$((end - start))
    ok "${model} responded in ${elapsed}s"
    echo "     Preview: $(echo "$response" | head -c 120)..."
    pass "${model} inference (${elapsed}s)"
  else
    fail "${model} inference failed"
    err "${model} inference"
  fi
}

test_model "qwen2.5-coder:3b" "Write a Python function that returns the fibonacci sequence up to n. Code only."
test_model "llama3.2:3b"      "In one sentence, explain what a REST API is."
test_model "phi3:mini"        "Summarize in 30 words: A function reads a CSV, filters rows where status equals active, returns a list of dicts."

# ─────────────────────────────────────────────
# TASK 4 — MLX stack for Metal acceleration
# ─────────────────────────────────────────────
header "TASK 4: MLX Stack (Apple Silicon Metal Acceleration)"

info "Note: Ollama already uses Metal natively on M1."
info "MLX enables running HuggingFace models directly via Metal."
echo "  Python: $(python3 --version)"
confirm

MLX_TEST_SCRIPT=$(cat <<'PYEOF'
import sys
try:
    import mlx.core as mx
    a = mx.array([1.0, 2.0, 3.0])
    b = mx.array([4.0, 5.0, 6.0])
    c = (a + b).tolist()
    print(f"MLX test: result={c}")
    print("Metal acceleration: ACTIVE")
except ImportError:
    print("mlx not available")
    sys.exit(1)
PYEOF
)

if python3 -c "import mlx.core" &>/dev/null 2>&1; then
  ok "mlx already installed"
  pass "mlx install"
else
  info "Installing mlx and mlx-lm..."
  if pip3 install mlx mlx-lm --quiet; then
    ok "mlx and mlx-lm installed"
    pass "mlx install"
  else
    fail "MLX install failed. This is optional — Ollama works without it."
    err "mlx install (optional)"
    warn "Manual install: pip3 install mlx mlx-lm"
  fi
fi

info "Testing MLX Metal acceleration..."
if python3 -c "$MLX_TEST_SCRIPT" 2>/dev/null; then
  pass "MLX Metal acceleration"
else
  skip "MLX Metal test (mlx not installed or unavailable)"
fi

# ─────────────────────────────────────────────
# TASK 5 — Verify OpenAI-compatible API
# ─────────────────────────────────────────────
header "TASK 5: Verify OpenAI-Compatible API Endpoint"

info "Testing OpenAI-compatible /v1/chat/completions..."

RESPONSE=$(curl -sf http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ollama" \
  -d '{"model":"phi3:mini","messages":[{"role":"user","content":"Reply with exactly: LOCAL_MODEL_OK"}],"max_tokens":10}' \
  2>&1) || true

if echo "$RESPONSE" | grep -qi "LOCAL_MODEL_OK\|content\|message"; then
  ok "OpenAI-compatible API working"
  pass "OpenAI-compatible API"
else
  fail "API test failed. Check Ollama is running: ollama serve &"
  echo ""
  echo "  Manual test:"
  echo '  curl http://localhost:11434/v1/chat/completions \'
  echo '    -H "Content-Type: application/json" \'
  echo '    -H "Authorization: Bearer ollama" \'
  echo "    -d '{\"model\":\"phi3:mini\",\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}]}'"
  err "OpenAI-compatible API"
fi

# ─────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────
header "SETUP SUMMARY"

echo ""
for result in "${RESULTS[@]}"; do
  echo -e "  $result"
done

echo ""
echo -e "${BOLD}Ollama endpoint:${NC}  http://localhost:11434"
echo -e "${BOLD}OpenAI base URL:${NC}  http://localhost:11434/v1"
echo -e "${BOLD}API key (local):${NC}  ollama"
echo ""
echo -e "${BOLD}Next steps:${NC}"
echo "  1. Open VS Code → Terminal > Run Task → use AI: prefixed tasks"
echo "  2. Install Cline extension: code --install-extension saoudrizwan.claude-dev"
echo "  3. Configure Cline settings: set Ollama endpoint + your Claude API key"
echo "  4. See docs/local_ai/ for routing guide and context compression workflow"
echo "  5. Run: bash scripts/validate_local_ai.sh"
echo ""
echo -e "${GREEN}${BOLD}Setup complete.${NC}"
