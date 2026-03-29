# FREE STACK ALTERNATIVES

Zero-cost build and demo configuration for Autonomous PMO.
Replace the commercial stack with these options for development, prototyping, and client demos.
Production upgrade paths noted where relevant.

---

## Quick Summary

| Component | Original (Paid) | Free Alternative | Effort to Swap |
|---|---|---|---|
| LLM | Claude claude-sonnet-4-20250514 (paid after credits) | Ollama local / Groq free tier / Gemini Flash free tier | Low |
| Project data — Jira | Jira REST API (paid license) | GitHub Issues API | Low |
| Project data — Smartsheet | Smartsheet API (paid license) | Google Sheets API | Medium |
| Knowledge graph | Neo4j (paid cloud) | Kuzu (embedded, free) | Low |
| Auth + user management | Supabase (free tier limited) | Built-in FastAPI JWT | Low |
| Slack integration | Slack API (free tier limited) | Discord API (free) or skip for Phase 1 | Low |
| PostgreSQL + pgvector | Relational + vector DB | **SQLite + sqlite-vec** (embedded, file-based) | Low |

**Total infrastructure: Docker runs Redis only. SQLite and Kuzu are both file-based — no DB servers, no ports, no Docker containers for data.**

---

## Component by Component

---

### 1. LLM — Replace Paid Claude API

The Anthropic API has no permanent free tier. New users receive roughly $5 in free credits — enough for initial prototyping but not sustained development.

**Three free options ranked by recommendation:**

---

#### Option A — Ollama (Recommended for Development)

Run LLMs completely locally. No API key. No cost. No rate limits. Works offline.

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a capable model
ollama pull llama3.3        # Best reasoning, 70B — needs 48GB RAM
ollama pull llama3.1:8b     # Lighter — runs on 8GB RAM, good for testing
ollama pull deepseek-r1:8b  # Strong reasoning, lighter footprint
ollama pull mistral         # Fast, capable, 4GB RAM
```

OpenAI-compatible endpoint at `http://localhost:11434/v1` — works as a drop-in replacement with one config change.

**Tradeoff:** Output quality is lower than Claude Sonnet for complex reasoning tasks (Risk Intelligence, Program Director agents). Excellent for development and testing. Use for all local work, switch to a paid API only for demo polish.

**Best model for this project:** `llama3.1:8b` for development speed, `llama3.3` for demo quality if your machine can run it.

---

#### Option B — Groq Free Tier (Recommended for Demos)

Groq provides free API access to open-source models at extremely fast inference speeds. No credit card required for the free tier.

```bash
pip install groq
```

```python
# Drop-in replacement — change base_url and model only
from groq import Groq
client = Groq(api_key=os.environ["GROQ_API_KEY"])

response = client.chat.completions.create(
    model="llama-3.3-70b-versatile",  # Best available free model
    messages=[{"role": "user", "content": "..."}]
)
```

Free tier limits: 14,400 requests/day, 500,000 tokens/minute. More than sufficient for a demo.

**Get your key:** https://console.groq.com (no credit card required)

**Tradeoff:** Model selection is limited to open-source models. Llama 3.3 70B is strong but not Claude-level on complex multi-step reasoning. Good enough for demos and development.

---

#### Option C — Google Gemini API Free Tier

Google Gemini 2.5 Flash has a genuinely free tier: 15 requests/minute, 1 million tokens/day.

```bash
pip install google-generativeai
```

```python
import google.generativeai as genai
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
model = genai.GenerativeModel("gemini-2.5-flash")
```

**Get your key:** https://aistudio.google.com/apikey (no credit card required)

**Tradeoff:** Different SDK — requires slightly more code changes than Ollama or Groq. Strong performance on structured output tasks. Rate limits may constrain simulation testing.

---

#### Cost-Effective Production Path (When Free Credits Run Out)

If you need to move beyond free options and want to keep costs minimal:

| Model | Cost | Best for |
|---|---|---|
| Claude Haiku 4.5 | $0.25 / $1.25 per M tokens | Communication Agent, Execution Monitoring |
| DeepSeek V3.2 | $0.28 / $0.42 per M tokens | Planning Agent, Issue Management |
| Gemini 2.5 Flash | $0.30 / $2.50 per M tokens | Risk Intelligence, Knowledge Agent |
| Claude Sonnet 4.6 | $3.00 / $15.00 per M tokens | Program Director only |

Running all seven agents on a mixed model routing strategy costs approximately **$2–5/day** at demo scale (50–100 events/day). Not free — but very affordable.

---

### 2. Jira — Replace with GitHub Issues

Jira requires a paid Atlassian license. GitHub Issues is free for public and private repositories and provides a fully documented REST API.

**Why this works:** You're already using the GitHub API for velocity tracking. GitHub Issues covers tasks, status changes, labels (map to priority/severity), milestones, and dependency references via issue mentions.

```python
# integrations/github_issues/adapter.py
from github import Github  # pip install PyGithub — free

g = Github(os.environ["GITHUB_TOKEN"])
repo = g.get_repo("your-org/your-project")

# Fetch open issues (equivalent to Jira tasks)
issues = repo.get_issues(state="open", since=last_poll)

# Map to DeliveryEvent
def to_delivery_event(issue) -> DeliveryEvent:
    return DeliveryEvent(
        event_type="task.updated",
        event_id=f"gh_issue_{issue.number}",
        timestamp=issue.updated_at,
        project_id=repo.name,
        source="github_issues",
        payload={
            "task_id": str(issue.number),
            "title": issue.title,
            "status": "closed" if issue.state == "closed" else "open",
            "labels": [l.name for l in issue.labels],
            "assignee": issue.assignee.login if issue.assignee else None,
            "milestone": issue.milestone.title if issue.milestone else None,
        }
    )
```

**GitHub token:** Free. Go to github.com → Settings → Developer settings → Personal access tokens.
**API limits:** 5,000 requests/hour for authenticated requests — more than sufficient.

**Label mapping convention for this project:**

| GitHub Label | Maps To |
|---|---|
| `status: blocked` | task.updated → new_status: blocked |
| `status: in-progress` | task.updated → new_status: in_progress |
| `priority: high` | Risk signal input |
| `milestone: M3` | Milestone association |
| `dependency: #144` | Dependency link |

---

### 3. Smartsheet — Replace with Google Sheets API

Google Sheets is free with a Google account. The Sheets API is free up to 300 requests/minute.

```bash
pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

```python
# integrations/google_sheets/adapter.py
from googleapiclient.discovery import build
from google.oauth2 import service_account

# Use a service account — no user login required
creds = service_account.Credentials.from_service_account_file(
    "service_account.json",
    scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
)
service = build("sheets", "v4", credentials=creds)

def fetch_project_sheet(spreadsheet_id: str, range_name: str) -> List[Dict]:
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_name
    ).execute()
    return result.get("values", [])
```

**Setup:**
1. Create a project at console.cloud.google.com (free)
2. Enable Google Sheets API
3. Create a service account and download the JSON key
4. Share your project tracking spreadsheet with the service account email

**Tradeoff:** Google Sheets lacks native webhooks — you poll on a schedule instead of receiving push events. For Phase 1 this is fine. APScheduler polls every 5 minutes.

---

### 4. Knowledge Graph — Replace Neo4j with Kuzu

Neo4j Community Edition is free but requires Docker and significant memory. Kuzu is an embedded graph database — no server, no Docker container, no separate process. It runs inside your Python process like SQLite.

```bash
pip install kuzu
```

```python
# knowledge_graph/graph_store.py
import kuzu

db = kuzu.Database("./data/knowledge_graph")
conn = kuzu.Connection(db)

# Create schema
conn.execute("""
    CREATE NODE TABLE IF NOT EXISTS Project(
        id STRING PRIMARY KEY,
        name STRING,
        status STRING
    )
""")

conn.execute("""
    CREATE REL TABLE IF NOT EXISTS DEPENDS_ON(
        FROM Project TO Project,
        criticality STRING
    )
""")

# Query — Kuzu uses Cypher syntax (same as Neo4j)
result = conn.execute("""
    MATCH (p:Project)-[:DEPENDS_ON]->(dep:Project)
    WHERE dep.id = $system_id
    RETURN p.id, p.name
""", {"system_id": "system_finance_api"})
```

**Why Kuzu:** It uses Cypher query syntax — identical to Neo4j. The query_service.py code requires zero changes. Just swap the connection layer.

**Tradeoff:** Kuzu is single-process embedded — no multi-user graph server. Fine for Phase 1 development and demo. Upgrade to Neo4j or Amazon Neptune for Phase 2 multi-user deployment.

**If Kuzu feels too new:** NetworkX is the fallback.

```python
# Ultra-minimal fallback — in-memory graph, no persistence
import networkx as nx

G = nx.DiGraph()
G.add_node("project_erp", type="project", status="at_risk")
G.add_edge("project_erp", "system_finance_api", type="DEPENDS_ON", criticality="high")

# 2-hop neighborhood
neighborhood = nx.ego_graph(G, "project_erp", radius=2)
```

NetworkX loses data on restart — only use as a development stub, not for demo.

---

### 5. Auth — Replace Supabase with FastAPI JWT

Supabase has a free tier but it is limited (500MB database, 2 projects). For Phase 1 you do not need a managed auth service — a simple JWT implementation covers the demo use case.

```bash
pip install python-jose[cryptography] passlib[bcrypt] fastapi
```

```python
# security/auth.py
from jose import jwt
from datetime import datetime, timedelta

SECRET_KEY = os.environ["JWT_SECRET_KEY"]  # Generate with: openssl rand -hex 32
ALGORITHM = "HS256"

def create_token(user_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "exp": datetime.utcnow() + timedelta(hours=8)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
```

For the Streamlit demo UI, skip auth entirely in Phase 1. Add it in Phase 2 when you move to Next.js.

---

### 6. Slack — Replace with Discord or Skip

Slack's free tier limits message history to 90 days and has no webhook persistence. For a demo, Discord is fully free with a proper bot API and webhook support.

```bash
pip install discord.py
```

Or skip entirely for Phase 1. The core demo loop (Jira/GitHub → risk detection → decision brief) does not require Slack. Add it in Phase 2 as a notification channel.

---

## Updated Free Stack

```
autonomous-pmo/
├── LLM:              Ollama (local dev) / Groq (demo) / Gemini Flash (backup)
├── Project signals:  GitHub Issues API (free) + Google Sheets API (free)
├── State store:      PostgreSQL + pgvector (free, self-hosted via Docker)
├── Knowledge graph:  Kuzu (embedded, free) → Neo4j (Phase 2 upgrade)
├── Event bus:        Redis Streams (free, self-hosted via Docker)
├── Orchestration:    LangGraph (free, open source)
├── Auth:             FastAPI JWT (free, no external service)
├── Scheduling:       APScheduler (free, open source Python library)
├── Demo UI:          Streamlit (free, open source)
└── Notifications:    Discord API (free) or skip for Phase 1
```

**Total infrastructure cost: $0**
**Total API cost during development and demo: $0**

---

## Updated .env.example

```bash
# LLM — pick one, comment out the others

# Option A: Ollama (local — no key needed, just run ollama serve)
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b

# Option B: Groq (free tier — get key at console.groq.com)
# LLM_PROVIDER=groq
# GROQ_API_KEY=gsk_...
# GROQ_MODEL=llama-3.3-70b-versatile

# Option C: Gemini (free tier — get key at aistudio.google.com)
# LLM_PROVIDER=gemini
# GEMINI_API_KEY=AIza...
# GEMINI_MODEL=gemini-2.5-flash

# Option D: Anthropic (paid — use free credits first)
# LLM_PROVIDER=anthropic
# ANTHROPIC_API_KEY=sk-ant-...

# Infrastructure (all free, all embedded except Redis)
SQLITE_DB_PATH=./data/autonomous_pmo.db    # SQLite — file created automatically
REDIS_URL=redis://localhost:6379            # Only service that needs Docker
KUZU_DB_PATH=./data/knowledge_graph        # Kuzu — directory created automatically

# Project data sources (all free)
GITHUB_TOKEN=ghp_...           # github.com → Settings → Developer settings
GITHUB_ORG=your-org

# Google Sheets (free — requires service account setup)
GOOGLE_SERVICE_ACCOUNT_PATH=./secrets/service_account.json

# Auth
JWT_SECRET_KEY=                # Generate: openssl rand -hex 32

# Optional — skip for Phase 1
# DISCORD_BOT_TOKEN=
# SLACK_BOT_TOKEN=

# Config
LOG_LEVEL=INFO
ENVIRONMENT=development
TENANT_ID=default
```

---

## Updated models.yaml for Free LLMs

```yaml
# configs/models.yaml — free stack version

provider: ollama  # Change to groq or gemini for demo

ollama:
  base_url: http://localhost:11434
  default_model: llama3.1:8b

groq:
  default_model: llama-3.3-70b-versatile

gemini:
  default_model: gemini-2.5-flash

routing:
  communication_agent:
    model: llama3.1:8b      # Fast enough for structured narrative
  execution_monitoring_agent:
    model: llama3.1:8b      # Pattern detection — lightweight model fine
  issue_management_agent:
    model: llama3.1:8b      # Classification task — lightweight fine
  risk_intelligence_agent:
    model: llama3.3         # Most complex reasoning — use best available
  planning_agent:
    model: llama3.3         # Dependency reasoning — use best available
  program_director_agent:
    model: llama3.3         # Conflict resolution — use best available
  knowledge_agent:
    model: llama3.1:8b      # Retrieval + light reasoning — lightweight fine
```

---

## LLM Provider Abstraction Layer

Add this to avoid rewriting agent code when switching providers. One config change swaps the entire LLM backend.

```python
# llm/provider.py

import os
from typing import Dict, List

def get_client():
    provider = os.environ.get("LLM_PROVIDER", "ollama")

    if provider == "ollama":
        from openai import OpenAI
        return OpenAI(
            base_url=os.environ["OLLAMA_BASE_URL"] + "/v1",
            api_key="ollama"  # Ollama doesn't require a real key
        )

    elif provider == "groq":
        from groq import Groq
        return Groq(api_key=os.environ["GROQ_API_KEY"])

    elif provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        return genai

    elif provider == "anthropic":
        import anthropic
        return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

def get_model_for_agent(agent_name: str) -> str:
    import yaml
    with open("configs/models.yaml") as f:
        config = yaml.safe_load(f)
    provider = config["provider"]
    return config["routing"].get(agent_name, {}).get("model", config[provider]["default_model"])
```

Every agent calls `get_client()` and `get_model_for_agent(self.name)` — never hardcodes a provider or model name.

---

## What to Use at Each Build Stage

| Stage | LLM | Data Source | Graph |
|---|---|---|---|
| Development (daily coding) | Ollama local | GitHub Issues (your own repo) | Kuzu embedded |
| Integration testing | Ollama or Groq free tier | GitHub Issues | Kuzu embedded |
| Demo to potential clients | Groq free tier or Gemini Flash | GitHub Issues + Google Sheets | Kuzu embedded |
| Production (Phase 2+) | Claude Haiku / Sonnet mix | Jira + Smartsheet + GitHub | Neo4j or Neptune |

---

### 7. PostgreSQL — Replace with SQLite + sqlite-vec

SQLite is an embedded, file-based database — no server, no Docker container, no connection string beyond a file path. It stores the entire database in a single `.db` file.

```bash
pip install aiosqlite sqlite-vec sentence-transformers
```

**Canonical state, audit log, evaluation metrics, human review queue** all move to SQLite. SQLAlchemy supports SQLite with the same ORM models — change the connection string only.

```python
# state/canonical_state.py
DATABASE_URL = os.environ.get("SQLITE_DB_PATH", "./data/autonomous_pmo.db")
engine = create_async_engine(f"sqlite+aiosqlite:///{DATABASE_URL}")

# Enable WAL mode for concurrent reads
async with engine.begin() as conn:
    await conn.execute(text("PRAGMA journal_mode=WAL"))
```

**Vector similarity (replaces pgvector):** `sqlite-vec` adds a virtual table type to SQLite with cosine distance support. Embeddings generated locally by `sentence-transformers` — no API call.

```python
# context_assembly/case_matcher.py
import sqlite_vec
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("all-MiniLM-L6-v2")  # ~80MB, runs on CPU, free

# Create virtual table
conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS vec_cases USING vec0(embedding FLOAT32[384])")

# Query top-k similar cases
embedding = model.encode(event_text).tolist()
results = conn.execute("""
    SELECT case_id, vec_distance_cosine(embedding, ?) AS distance
    FROM vec_cases
    ORDER BY distance
    LIMIT ?
""", (embedding, top_k)).fetchall()
```

**Append-only audit log:** SQLite doesn't support DB-level role permissions. Enforce at the application layer — `AuditLogger` exposes only `log()` and `query()`, no update or delete methods.

**Database file location:** `./data/autonomous_pmo.db` (configured via `SQLITE_DB_PATH` env var). Created automatically on first run — no migration scripts needed.

**Production upgrade path:** For Phase 2+ multi-user or high-throughput deployments, migrate canonical state to PostgreSQL while keeping SQLite for audit logs and evaluation metrics (they're append-heavy and read rarely, ideal for SQLite long-term).

---

## One Thing to Know

The free LLM options (Ollama local, Groq, Gemini Flash) will produce lower quality outputs than Claude Sonnet on the two hardest agents — Risk Intelligence and Program Director. The structured output schema and few-shot examples in the prompt templates compensate significantly, but complex conflict resolution and multi-hop risk propagation reasoning will be noticeably weaker.

**The practical approach:** Build and test on Ollama. Run demos on Groq (fast, free, good enough). Use Claude Haiku for the first paying client if output quality is the deciding factor.

The architecture does not change at all between free and paid LLMs — only the `LLM_PROVIDER` env var and `configs/models.yaml`.
