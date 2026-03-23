# 🤖 Devin Migration Engine

> A stateless, fault-tolerant GitOps pipeline that autonomously migrates legacy JavaScript/JSX codebases to TypeScript using parallel AI agents (Devin). It uses AST-based dependency graphing, topological batch ordering, and GitHub as the single source of truth.

---

## What It Does

Given any React/JavaScript repository, this engine:

1. **Parses** the entire source tree into an Abstract Syntax Tree (AST)
2. **Maps** every import/export dependency between files
3. **Sorts** files into strict topological batches using Kahn's Algorithm (Directed Acyclic Graph)
4. **Dispatches** parallel Devin AI agents — one per file — to migrate JS/JSX → TS/TSX
5. **Enforces** batch ordering: Batch N must fully complete before Batch N+1 starts
6. **Tracks** progress in real-time via GitHub's Trees API (zero local state)
7. **Exports** live telemetry to the Command Center dashboard

### Why Topological Ordering Matters

If `CartSummary.jsx` imports `CartItem.jsx`, migrating them in the wrong order forces the AI to **hallucinate** CartItem's TypeScript interfaces — breaking the production build. By sorting bottom-up (leaf nodes first), every dependency is already typed when the AI reaches files that import it. This guarantees **zero cascading type errors** and **zero merge conflicts**.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    main.py (Orchestrator)                      │
│                                                                │
│  1. dependency_graph.py → AST parsing + topological sort       │
│     Kahn's Algorithm: O(V+E) DAG → strict batch ordering      │
│                                                                │
│  2. Frozen Batch Manifest ({owner}_{repo}_manifest.json)       │
│     Immutable: file X is Batch N forever. Survives restarts.   │
│                                                                │
│  3. worker_pool.py → asyncio bounded concurrency (15 slots)    │
│     Semaphore-gated parallel dispatch + exponential backoff    │
│                                                                │
│  4. devin_client.py → Devin v3 Enterprise API                  │
│     POST /v3/organizations/{id}/sessions                       │
│     Async session creation, polling, and cleanup               │
│                                                                │
│  5. GitHub Trees API → Stateless state detection               │
│     1 API call per poll: fetch master tree, diff against       │
│     original branch to determine per-file completion           │
│                                                                │
│  6. Telemetry → JSON export to Command Center dashboard        │
│     Real-time KPIs, per-batch topology, file-level drill-down  │
└──────────────────────────────────────────────────────────────┘
          │                    │                │
    [Devin Agent A]    [Devin Agent B]    [Devin Agent C]
    branch → migrate   branch → migrate   branch → migrate
    tsc → verify → PR  tsc → verify → PR  tsc → verify → PR
```

---

## Project Structure

```
devin-migration-engine/
├── main.py                          # Core orchestration pipeline
├── dependency_graph.py              # AST parser + Kahn's topological sort
├── devin_client.py                  # Devin v3 API client (async)
├── worker_pool.py                   # Bounded concurrency pool (semaphore)
├── launch.py                        # Preflight validator (env, manifest, GitHub)
├── cleanup.py                       # Utility: kill stuck sessions, clean branches
├── emergency_kill.py                # Emergency: terminate all active Devin sessions
├── requirements.txt                 # Python dependencies
├── .env                             # Single source of truth for ALL config
└── {owner}_{repo}_manifest.json     # Frozen batch manifest (auto-generated)
```

### Key Files Explained

| File | Purpose |
|---|---|
| `main.py` | The main pipeline loop. Loads manifest, detects state from GitHub Trees API, dispatches Devin agents per batch, polls for completion, exports telemetry. |
| `dependency_graph.py` | Pure functions. Parses JS/JSX files for `import`/`require` statements, builds a directed graph, then runs Kahn's Algorithm to produce topologically-sorted batches. |
| `devin_client.py` | Async Devin API wrapper. Creates sessions, polls status, deletes sessions (ACU cleanup). Handles rate limiting (429). |
| `worker_pool.py` | `MigrationOrchestrator` class with `asyncio.Semaphore(15)` for bounded concurrency. Includes exponential backoff on rate limits. |
| `launch.py` | Preflight checks: validates `.env`, verifies GitHub token, checks manifest integrity, confirms original branch file count. |
| `cleanup.py` | Post-run utility to clean up orphaned Devin sessions and stale Git branches. |
| `emergency_kill.py` | Nuclear option: kills ALL active Devin sessions in the org. Use when agents are stuck burning ACUs. |

---

## Configuration

All configuration lives in a single `.env` file. The Command Center dashboard can also write to this file via its Config API.

```env
# GitHub
GITHUB_TOKEN=ghp_xxxxxxxxxxxx        # PAT with repo scope
GITHUB_REPO=owner/repo-name          # Auto-derived from REPO_OWNER + REPO_NAME
REPO_OWNER=owner                     # GitHub username or org
REPO_NAME=repo-name                  # Repository name

# Branches
ORIGINAL_BRANCH=original             # Frozen source branch (never modified)
TARGET_BRANCH=master                 # Branch where PRs are merged

# Paths
SOURCE_PREFIX=frontend/src/           # Path prefix for source files in the repo tree
TARGET_REPO_PATH=C:/path/to/repo/frontend/src  # Local checkout (for DAG building only)

# Devin
DEVIN_API_KEY=cog_xxxxxxxxxxxx       # Devin API key
DEVIN_ORG_ID=org-xxxxxxxxxxxx        # Devin organization ID

# Tuning
POLL_INTERVAL=5                       # Seconds between GitHub state checks (default: 10)
DISPATCH_DELAY=2                      # Seconds between agent dispatches (default: 2)
```

---

## How to Run

### Prerequisites
- Python 3.10+
- A GitHub Personal Access Token (with `repo` scope)
- A Devin API key and Org ID
- The target repository cloned locally (for initial DAG building)

### Setup

```bash
cd devin-migration-engine
pip install -r requirements.txt
# Create and fill .env (see Configuration section above)
```

### Preflight Check

```bash
python launch.py
```

This validates:
- All required env vars are set
- GitHub token is valid
- Manifest file count matches the original branch
- Local source tree is accessible for DAG building

### Run the Engine

```bash
python main.py --src "C:/path/to/repo/frontend/src"
```

### Safety Controls

| Control | Description |
|---|---|
| `DRY_RUN = True` | Default. Simulates the entire pipeline without dispatching agents or spending credits. |
| `DRY_RUN = False` | Live mode. Dispatches real Devin sessions, opens real PRs. |
| `emergency_kill.py` | Terminates all active Devin sessions immediately. |

---

## How State Detection Works

The engine is **100% stateless**. It uses no database, no local state file, no cache. On every poll cycle:

1. Fetches the `master` branch file tree via GitHub Trees API (1 API call)
2. For each file in the manifest, checks if the `.tsx` version exists on master
3. If `.tsx` exists → `COMPLETED`. If only `.jsx` exists → `PENDING`
4. Calculates global progress, per-batch completion, and ROI metrics
5. Exports telemetry JSON to the Command Center dashboard

If the engine crashes and restarts, it simply asks GitHub "what's done?" and resumes. Zero data loss.

---

## Batch Manifest

The frozen manifest (`{owner}_{repo}_manifest.json`) is auto-generated on first run:

1. Parses the local source tree into an AST dependency graph
2. Runs Kahn's Algorithm to produce topologically-sorted batches
3. Validates file count against the `ORIGINAL_BRANCH` on GitHub
4. Freezes the manifest to disk — file X is Batch N **forever**

To regenerate: delete the manifest file and re-run the engine.

### Integrity Check

On every load, the manifest is validated:
- Repo name must match current `REPO_OWNER/REPO_NAME`
- File count must match the `ORIGINAL_BRANCH` tree count
- If either fails, the engine refuses to start (fail-fast)

---

## License

MIT

## Setup

```bash
# 1. Clone this repo
git clone https://github.com/viku11/migration-engine
cd migration-engine

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure credentials
cp .env.example .env
# Edit .env with your DEVIN_API_KEY, DEVIN_ORG_ID

# 4. Clone the target repo next to this one
git clone https://github.com/viku11/idurar-erp-crm ../idurar-erp-crm
```

## Usage

```bash
# Dry run — see the migration plan without dispatching sessions
python main.py --dry-run

# Run the full migration
python main.py

# Check current status
python main.py --status

# Run a specific batch only (e.g. resume after a crash)
python main.py --batch 0

# Point at a different src directory
python main.py --src /path/to/other/frontend/src
```

## What You See

```
╭─ ShopDirect × IDURAR — TypeScript Migration Engine ────────────────╮
│  Total Files: 84   ✅ Done: 12   🟡 Active: 3   🔴 DLQ: 1   ⚪ Queued: 68   [18% complete] │
╰────────────────────────────────────────────────────────────────────╯

 Batch │ File                        │ Status          │ Attempts │ PR / Session
───────┼─────────────────────────────┼─────────────────┼──────────┼──────────────
   1   │ CollapseBox.jsx             │ 🟢 PR Opened    │    1     │ github.com/...
   1   │ DynamicForm.jsx             │ 🟢 PR Opened    │    1     │ github.com/...
   1   │ useOnFetch.jsx              │ 🟡 Migrating... │    1     │ devin.ai/...
   2   │ InvoiceModule.jsx           │ ⚪ Queued       │    0     │ —
```

## Key Differentiator vs GitHub Copilot

Copilot requires an engineer to:
1. Open each file manually
2. Run `tsc`, read errors
3. Paste errors back into Copilot
4. Accept suggestions one by one

This engine treats Devin as **asynchronous compute**. You run `python main.py`, come back in the morning, and 80% of your migration is done with PRs ready for review.
