# Context Compression Workflow

Reduce Claude API token usage by pre-summarizing files with Ollama before sending to Claude.

**Typical savings: 60–80% token reduction** per file.
A 300-line Python file (~4,000 tokens) compresses to ~250 tokens (150–200 words).

---

## Workflow — Step by Step

```
1. Identify which files are relevant to your question
2. Run compress_context.sh on each file (uses phi3:mini)
3. Paste summaries into a condensed context block
4. Send ONLY the condensed block + your question to Claude
```

---

## Step 1 — Identify Relevant Files

Ask yourself:
- Which files does my question directly involve?
- Which files does that code import or depend on?
- Stop at 2-hop depth — don't over-collect

For this project, relevant files are usually:
- The agent file you're working on
- `context_assembly/assembler.py` (if context is involved)
- `policy/engine.py` (if policy routing is involved)
- The relevant config in `configs/`

---

## Step 2 — Compress Each File

**Option A — VS Code task (easiest)**
```
Terminal → Run Task → "AI: Compress Context (phi3)"
Enter file path when prompted
```

**Option B — Command line**
```bash
bash scripts/compress_context.sh agents/risk_intelligence/agent.py
```

**Option C — Direct Ollama call**
```bash
cat your_file.py | ollama run phi3:mini "$(cat <<'EOF'
Summarize this file for an AI assistant (Claude).
Include:
- Purpose: what this file does in one sentence
- Key functions: list each function name and its job (one line each)
- Dependencies: what it imports from other project files
- Potential issues: any obvious bugs, missing error handling, or design concerns
Limit your response to 150-200 words. Be specific, not generic.
EOF
)"
```

---

## Step 3 — Compile Condensed Context Block

After compressing each file, assemble like this:

```
=== CONDENSED CONTEXT ===

[agents/risk_intelligence/agent.py]
Purpose: Scores milestone impact when a dependency is blocked.
Key functions:
  - run(input): main entry, calls _score_impact and _build_output
  - _score_impact(state, graph): computes float 0-1 risk score using 3 factors
  - _build_output(score, evidence): constructs AgentOutput with uncertainty_notes
Dependencies: context_assembly.assembler, knowledge_graph.query_service, audit.logger
Potential issues: uncertainty_notes always returns empty list if score < 0.5 (bug per CLAUDE.md)

[configs/policies.yaml]
Purpose: Policy rules for project proj_123.
Key entries: escalate_issue requires approval_required; modify_schedule is deny.
Dependencies: none (pure config)
Potential issues: no version bump after last edit

=== END CONTEXT ===

My question: Why does the risk agent not escalate when a milestone has a 60% slip probability?
```

---

## Step 4 — Send to Claude

Paste the entire block above into Claude Code or Cline (Claude provider).
Claude receives structured context instead of raw files — less noise, lower cost.

---

## Dependency Map Prompt

To quickly map which files call which (before deciding what to compress):

```
ollama run phi3:mini "List the dependencies between these files.
Output a simple map: file_a → calls → file_b.
Files: [paste file names and first 20 lines of each]
Keep it to a flat list, no explanation."
```

---

## Compression Prompts Reference

**File summary (standard)**
```
Summarize this file for Claude.
Include: purpose, key functions, dependencies, potential bug areas.
Limit to 150-200 words.
```

**Function summary only**
```
List every function in this file.
For each: name, inputs, outputs, what it does (one sentence).
No code. No explanation. Just the list.
```

**Dependency map**
```
List what this file imports from other project files.
Output: import path → what it uses from it.
One line per dependency.
```

**Bug scan**
```
Scan this file for potential issues.
Look for: missing error handling, hardcoded values, empty required fields, policy bypasses.
List only real issues, not style suggestions.
Limit to 100 words.
```

---

## Token Savings Estimate

| File size | Raw tokens | Compressed | Savings |
|---|---|---|---|
| 50-line file | ~700 | ~200 | ~70% |
| 150-line file | ~2,000 | ~250 | ~87% |
| 300-line file | ~4,000 | ~250 | ~94% |
| 3 files combined | ~8,000 | ~750 | ~91% |

At $3/1M input tokens (Sonnet), compressing 3 files saves ~$0.02 per query.
At 100 queries/day, that's ~$2/day or ~$60/month.
