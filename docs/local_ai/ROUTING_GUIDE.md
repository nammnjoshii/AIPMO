# Model Routing Guide — Manual Selection Reference

Use this table to decide which model to invoke per task.
**You always choose.** This guide just makes the decision fast.

---

## Quick Decision Table

| Task type | Model | Why |
|---|---|---|
| Edit code in a single file | `qwen2.5-coder:3b` (Ollama) | Fast, code-trained, no API cost |
| Fix a syntax / lint error | `qwen2.5-coder:3b` (Ollama) | Mechanical fix, no deep reasoning needed |
| Generate a docstring | `qwen2.5-coder:3b` (Ollama) | Repetitive structured output |
| Write boilerplate (class, test stub) | `qwen2.5-coder:3b` (Ollama) | Pattern completion, not reasoning |
| Format / refactor a function | `qwen2.5-coder:3b` (Ollama) | Contained scope |
| Explain what a function does | `llama3.2:3b` (Ollama) | Plain language, no code expertise needed |
| Write inline comments | `llama3.2:3b` (Ollama) | Documentation, not engineering |
| Draft a README section | `llama3.2:3b` (Ollama) | Prose generation |
| Summarize a file for context | `phi3:mini` (Ollama) | Fastest compression, smallest model |
| Summarize multiple files | `phi3:mini` (Ollama) | Run once per file, compile results |
| Map dependencies between files | `phi3:mini` (Ollama) | List output, not reasoning |
| Debug across 2+ files | Claude API | Needs cross-file reasoning |
| Architecture or design decision | Claude API | Needs broad context + judgment |
| Unfamiliar framework or language | Claude API | Needs knowledge depth |
| Root cause analysis of a complex bug | Claude API | Multi-step reasoning |
| Repository-wide refactor | Claude API | Too much context for local 3B model |
| Security review | Claude API | Precision critical |
| Writing agent logic (this project) | Claude API | Complex orchestration patterns |

---

## The One-File Rule

> If your task touches **one file**, use Ollama.
> If your task touches **two or more files**, use Claude.

This single heuristic covers ~80% of routing decisions.

---

## Model Profiles

### `qwen2.5-coder:3b` — Code tasks
- Best at: Python, TypeScript, Go, SQL, shell
- Strengths: completions, edits, test stubs, docstrings
- Weaknesses: multi-file context, architecture reasoning
- Invoke: `qwen2.5-coder:3b` in Cline model picker

### `llama3.2:3b` — Explanation and documentation
- Best at: plain-English explanations, comment writing, README prose
- Strengths: clarity, natural language
- Weaknesses: code generation (use qwen for that)
- Invoke: `llama3.2:3b` in Cline model picker

### `phi3:mini` — Summarization and context compression
- Best at: condensing a file into 150–200 words
- Strengths: speed, low memory footprint
- Weaknesses: not for code generation or reasoning
- Invoke: `phi3:mini` in Cline, or via `scripts/compress_context.sh`
- Also useful: run via VS Code task "AI: Compress Context (phi3)"

### Claude API — Complex reasoning
- Best at: multi-file debugging, architecture, unfamiliar domains
- When to use: any task where a 3B local model visibly struggles
- Model: `claude-sonnet-4-20250514`
- Cost note: use context compression first (see `CONTEXT_COMPRESSION.md`)

---

## When Local Models Struggle

Signs a task should escalate to Claude:
- Local model gives a wrong answer confidently
- Response ignores context you provided
- Task requires understanding relationships across files
- The answer needs to be right the first time (not trial and error)

When this happens: run `scripts/compress_context.sh` on relevant files, then send compressed context to Claude.
