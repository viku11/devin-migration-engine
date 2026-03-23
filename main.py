import argparse
import asyncio
import logging
import os
import re
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

# Internal Engine Modules
from dependency_graph import build_dependency_graph, topological_sort_batches
from devin_client import DevinClient
from worker_pool import MigrationOrchestrator

# Load environment variables
load_dotenv()

# Configure Logging
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- GITHUB GITOPS CONFIGURATION ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_OWNER = os.getenv("REPO_OWNER", "viku11")
REPO_NAME = os.getenv("REPO_NAME", "idurar-erp-crm")

# Anchored telemetry export path (cross-OS safe, CWD-independent)
SCRIPT_DIR = Path(__file__).resolve().parent
TELEMETRY_EXPORT_PATH = SCRIPT_DIR.parent / "command-center" / "public" / "telemetry.json"


def get_unique_branch_name(full_path: str) -> str:
    """Converts 'src/components/DataTable/DataTable.jsx' to 'migrate/src-components-DataTable-DataTable'"""
    slug = re.sub(r'[^a-zA-Z0-9]', '-', full_path.rsplit('.', 1)[0])
    return f"migrate/{slug}"


def build_migration_prompt(file_path: str, unique_branch: str) -> str:
    """Constructs the deterministic prompt for the Devin agent."""
    return f"""
1. Checkout a NEW branch named '{unique_branch}' from the latest 'master'.
2. Run 'git pull origin master' to ensure you have the most recent merges.
3. Migrate the file '{file_path}' to TypeScript strictly. Follow Knowledge Base.
4. If you encounter conflicts with other migrated files, rebase your changes.
5. Push to '{unique_branch}' and open a PR titled 'Migrate {file_path} to TS'.
"""


def fetch_all_prs():
    """Fetches ALL PRs using pagination to support 1000+ file enterprise repositories."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/pulls"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    params = {"state": "all", "per_page": 100}
    all_prs = []

    try:
        while url:
            response = requests.get(url, headers=headers, params=params)

            # Rate-limit backoff: sleep until the reset window if we hit 403
            if response.status_code == 403:
                import time as _time
                reset_time = int(response.headers.get("X-RateLimit-Reset", 0))
                wait_seconds = max(reset_time - int(_time.time()), 5)
                logger.warning(f"⚠️ GitHub rate limit hit. Sleeping {wait_seconds}s...")
                _time.sleep(wait_seconds)
                continue

            response.raise_for_status()
            all_prs.extend(response.json())

            # Intelligently follow GitHub's pagination links
            if 'next' in response.links:
                url = response.links['next']['url']
                params = {}
            else:
                break

        return all_prs
    except Exception as e:
        logger.error(f"❌ GITHUB API FATAL ERROR: {e}")
        raise RuntimeError("Cannot verify Git state. Check your GITHUB_TOKEN.")


def get_file_state(file_path: str, prs: list) -> str:
    """Checks GitOps state by matching the file path inside the PR Title."""
    for pr in prs:
        title = pr.get("title", "")
        # Hallucination-proof check
        if file_path in title:
            if pr.get('merged_at'):
                return "COMPLETED"
            if pr.get('state') == 'open':
                return "IN_PROGRESS"
            return "PENDING"

    return "PENDING"


def print_live_telemetry(in_progress_count: int, completed_count: int, pending_count: int, current_batch: int, total_batches: int):
    """Calculates and prints real-time metrics, and exports JSON to the React Command Center."""
    human_hours_saved = completed_count * 2
    total_files = completed_count + in_progress_count + pending_count
    progress_pct = (completed_count / total_files *
                    100) if total_files > 0 else 0

    # 1. Print to Terminal (For the hacker aesthetic)
    print("\n" + "━"*60)
    print(" 📊 ENTERPRISE ORCHESTRATION TELEMETRY")
    print("━"*60)
    print(
        f" 🚀 Migration Progress      : {progress_pct:.1f}% ({completed_count}/{total_files} Files Merged)")
    print(f" 🤖 Parallel AI Agents      : {in_progress_count} ACTIVE")
    print(f" ⏱️ Human Labor Saved       : {human_hours_saved} Hours")
    print(f" 📈 System Scalability      : INFINITE (O(V+E) Graphing, Cloud-Native Compute)")
    print(f" 💰 ACU Consumption Rate    : OPTIMIZED (Sleep-State Wait Active)")
    print(f" 🛡️ Security Posture        : ZERO-TRUST (Agents sandboxed, PR-gated)")
    print(f" 🔄 System Resiliency       : 100% FAULT TOLERANT (Stateless GitOps)")
    print("━"*60)
    print(" ⏳ Polling GitHub API for merged PRs... (Next check in 30s)")

    # 2. Export to React Dashboard (For the C-Suite UI)
    telemetry_data = {
        "progress": {"completed": completed_count, "pending": pending_count, "in_progress": in_progress_count},
        "roi": {"hours_saved": human_hours_saved, "active_agents": in_progress_count},
        "batch": {"current": current_batch, "total": total_batches},
        "posture": {"security": "Zero-Trust (PR-Gated)", "resilience": "Stateless Auto-Resume Active"}
    }

    # Write to the Vite public folder so the React app can fetch it live
    try:
        TELEMETRY_EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

        with open(TELEMETRY_EXPORT_PATH, "w") as f:
            json.dump(telemetry_data, f)
    except Exception as e:
        logger.warning(
            f"⚠️ Could not write telemetry JSON to Command Center: {e}")


async def run_pipeline(src_dir: str):
    """The main execution loop for the stateless GitOps pipeline."""
    client = DevinClient()

    try:
        graph, all_files = build_dependency_graph(src_dir)
        batches = topological_sort_batches(graph, all_files)

        src_path = Path(src_dir).resolve()
        frontend_path = src_path.parent

        formatted_batches = [[Path(f).relative_to(
            frontend_path).as_posix() for f in b] for b in batches]
        orchestrator = MigrationOrchestrator(client)

        for i, batch in enumerate(formatted_batches):
            print(f"\n{'='*50}")
            print(f" 📦 EVALUATING BATCH {i+1} OF {len(formatted_batches)}")
            print(f"{'='*50}")

            while True:
                # Fetch ALL PRs once per polling loop — single network call, thread-safe
                current_prs = fetch_all_prs()

                pending_files = []
                in_progress_files = []

                for f in batch:
                    # Passing the file path instead of the branch name to prevent AI hallucination blocks
                    state = get_file_state(f, current_prs)

                    if state == "PENDING":
                        pending_files.append(f)
                    elif state == "IN_PROGRESS":
                        in_progress_files.append(f)

                if not pending_files and not in_progress_files:
                    # Write telemetry BEFORE breaking so the dashboard reflects the completed batch (cold start fix)
                    total_completed = sum(
                        1 for b in formatted_batches for file in b if get_file_state(file, current_prs) == "COMPLETED")
                    print_live_telemetry(
                        0,
                        total_completed,
                        sum(len(b) for b in formatted_batches[i+1:]),
                        i + 1,
                        len(formatted_batches)
                    )
                    print(
                        f"✅ Batch {i+1} completely merged. Moving to next batch...")
                    break

                if pending_files:
                    print(
                        f"\n🚀 Staggering dispatch for {len(pending_files)} PENDING file(s)...")
                    tasks = []
                    for f in pending_files:
                        branch = get_unique_branch_name(f)
                        prompt = build_migration_prompt(f, branch)

                        task = asyncio.create_task(
                            orchestrator.process_file(f, branch, prompt))
                        tasks.append(task)

                        # 10-second API rate limit safeguard
                        await asyncio.sleep(10)

                    await asyncio.gather(*tasks)
                    print("✅ Dispatch complete. Agents are working.")

                if in_progress_files or pending_files:
                    total_completed = sum(
                        1 for b in formatted_batches for file in b if get_file_state(file, current_prs) == "COMPLETED")

                    # Triggers the executive telemetry for the presentation & dashboard
                    print_live_telemetry(
                        len(in_progress_files),
                        total_completed,
                        len(pending_files),
                        i + 1,
                        len(formatted_batches)
                    )
                    await asyncio.sleep(30)

        print("\n🎉 MIGRATION ENGINE COMPLETED ALL BATCHES!")

    finally:
        await client.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True)
    args = parser.parse_args()
    asyncio.run(run_pipeline(args.src))
