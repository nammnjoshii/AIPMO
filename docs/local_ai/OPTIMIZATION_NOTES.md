# System Optimization Notes — M1 MacBook Air, 8GB RAM

## Hard Constraints

| Constraint | Limit | Why |
|---|---|---|
| Max model size | 3B parameters | 8GB RAM, no swap recommended during inference |
| Active models at once | 1 | Each 3B model uses ~2-3GB RAM |
| Quantization | Q4_K_M preferred | ~40% smaller vs FP16, minimal quality loss |
| Concurrent apps | Minimize | Chrome tabs + VS Code + Ollama can hit memory pressure |

---

## Installed Model Disk Usage

| Model | Disk | RAM during inference | Use case |
|---|---|---|---|
| `qwen2.5-coder:3b` | ~2.0 GB | ~2.5 GB | Code edits |
| `llama3.2:3b` | ~2.0 GB | ~2.5 GB | Docs / explanation |
| `phi3:mini` | ~2.3 GB | ~2.8 GB | Summarization |
| **Total on disk** | **~6.3 GB** | — | — |

Model files location: `~/.ollama/models/`

Check disk usage:
```bash
du -sh ~/.ollama/models/
du -sh ~/.ollama/models/blobs/*
```

---

## Memory Pressure Monitoring

Watch Activity Monitor → Memory tab:
- **Green** pressure bar: OK to run inference
- **Yellow** pressure bar: close unnecessary apps first
- **Red** pressure bar: Ollama will likely be slow or crash

Quick CLI check (also available as VS Code task "AI: Check Memory Pressure"):
```bash
vm_stat | grep -E "Pages free|wired|active"
```

Rule of thumb: keep at least 3GB free before starting a local model.

---

## Ollama Configuration

Default Ollama behavior on M1:
- Uses Metal GPU automatically (no config needed)
- Runs on CPU if Metal unavailable
- Unloads model from RAM after 5 minutes of inactivity (default)

Change model unload timeout (reduce to save RAM faster):
```bash
# Add to your shell profile (~/.zshrc)
export OLLAMA_KEEP_ALIVE=2m
```

Restart after config change:
```bash
pkill ollama && ollama serve &
```

---

## One Model at a Time

Ollama will load multiple models if you switch quickly. Force a model unload:
```bash
ollama stop qwen2.5-coder:3b
```

Or restart Ollama entirely (fastest way to free all model RAM):
```bash
pkill ollama && sleep 2 && ollama serve &
```

---

## If Inference Slows Down

In order:
1. Check memory pressure in Activity Monitor
2. Close Chrome tabs (each tab = 100–300MB)
3. Quit other heavy apps (Slack, Figma, etc.)
4. Restart Ollama: `pkill ollama && ollama serve &`
5. If still slow: switch to `phi3:mini` (smallest/fastest)
6. If unusable: use Claude API for that session

---

## Disk Management

If disk gets tight, remove unused models:
```bash
ollama rm llama3.2:3b        # Remove a model
ollama list                   # Confirm what's left
```

Never delete `~/.ollama/models/manifests` without also deleting the corresponding blobs — use `ollama rm` instead.

---

## Model Locations

| Path | Contents |
|---|---|
| `~/.ollama/models/manifests/` | Model metadata and config |
| `~/.ollama/models/blobs/` | Actual model weights (large files) |
| `/tmp/ollama.log` | Server logs (resets on restart) |

---

## Quantization Notes

Ollama pulls Q4_K_M by default for most models — this is correct for 8GB RAM.
Do not pull `:latest` tags that resolve to larger sizes without checking first:
```bash
ollama show qwen2.5-coder:3b   # Shows quantization level before pulling
```

Prefer tags with explicit size suffixes: `:3b`, `:7b` (avoid `:7b-q8` — too large for 8GB).
