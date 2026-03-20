# ShopDirect TypeScript Migration Engine

> Orchestrates parallel Devin API sessions to autonomously migrate a legacy React/JSX codebase to TypeScript — with dependency-aware batching, circuit breakers, and a real-time progress dashboard.

## The Problem It Solves

The IDURAR ERP frontend has **1,392 type errors across 80+ JSX files**. A TypeScript migration has been deferred for 18 months because:
- No single engineer wants to own it
- It never makes sprint planning (doesn't move a product metric)
- Doing it manually is 2+ weeks of tedious, error-prone work

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     main.py (Orchestrator)               │
│                                                          │
│  1. dependency_graph.py → topological sort               │
│     (leaf nodes first — sessions don't share context)    │
│                                                          │
│  2. state_store.py → SQLite idempotency                  │
│     (crash-safe, never re-dispatches completed files)    │
│                                                          │
│  3. worker_pool.py → asyncio bounded concurrency         │
│     MAX 3 parallel Devin sessions                        │
│     Circuit breaker: 3 attempts → DLQ                    │
│                                                          │
│  4. devin_client.py → Devin v3 API                       │
│     POST /v3/organizations/{id}/sessions                 │
│     Poll until terminal state                            │
│                                                          │
│  5. dashboard.py → Rich terminal live view               │
│     Real-time status of all files                        │
└─────────────────────────────────────────────────────────┘
          │                    │                │
    [Devin Session A]  [Devin Session B]  [Devin Session C]
    clone → rename    clone → rename    clone → rename
    tsc → fix → PR    tsc → fix → PR    tsc → fix → PR
```

## Why This Architecture

**Dependency-aware batching**: JSX files that import each other must be migrated in order. If `CartSummary` is migrated before `CartItem`, Devin hallucates the CartItem types. The topological sorter prevents this.

**Idempotency**: If the orchestrator crashes mid-run, restarting picks up exactly where it left off. No duplicate Devin sessions, no wasted ACUs.

**Circuit breaker + DLQ**: Devin occasionally gets stuck in unproductive loops. After 3 attempts, the file routes to a Dead Letter Queue with the blocker documented — the pipeline keeps moving, and a human reviews only the hard cases.

**Bounded concurrency**: `asyncio.Semaphore(3)` limits parallel sessions to protect ACU budget.

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
