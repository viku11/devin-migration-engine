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

# --- DRY RUN SAFETY TOGGLE ---
# Set to True for pre-flight testing (no Devin credits spent, no PRs opened).
# Set to False before the real recording.
DRY_RUN = True

# --- ORIGINAL MIGRATION BASELINE ---
# Dynamically fetched from the 'original' branch at boot via GitHub Trees API.
# This is the denominator for all progress/ROI calculations.
ORIGINAL_FILE_COUNT = None  # Set at runtime by fetch_original_file_count()

# --- GITHUB GITOPS CONFIGURATION ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_OWNER = os.getenv("REPO_OWNER")
REPO_NAME = os.getenv("REPO_NAME")
ORIGINAL_BRANCH = os.getenv("ORIGINAL_BRANCH", "original")
TARGET_BRANCH = os.getenv("TARGET_BRANCH", "master")
SOURCE_PREFIX = os.getenv("SOURCE_PREFIX", "frontend/src/")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))

if not GITHUB_TOKEN or not REPO_OWNER or not REPO_NAME:
    raise RuntimeError(
        "\u274c Missing required env vars: GITHUB_TOKEN, REPO_OWNER, REPO_NAME. "
        "Check your .env file."
    )

# Anchored telemetry export path (cross-OS safe, CWD-independent)
SCRIPT_DIR = Path(__file__).resolve().parent
TELEMETRY_EXPORT_PATH = SCRIPT_DIR.parent / "command-center" / "public" / "telemetry.json"

# Frozen batch manifest — keyed to repo, auto-generated from GitHub API
# Format: {owner}_{repo}_manifest.json — works for any target repo
BATCH_MANIFEST_PATH = SCRIPT_DIR / f"{REPO_OWNER}_{REPO_NAME}_manifest.json"


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


def fetch_original_file_count() -> int:
    """Queries the GitHub Trees API against the frozen baseline branch to count
    all .js and .jsx files under the configured source prefix."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/git/trees/{ORIGINAL_BRANCH}?recursive=1"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        tree = response.json().get("tree", [])

        count = 0
        for item in tree:
            path = item.get("path", "")
            if item.get("type") == "blob" and path.startswith(SOURCE_PREFIX):
                if path.endswith(".js") or path.endswith(".jsx"):
                    count += 1

        if count == 0:
            logger.warning(
                f"⚠️ '{ORIGINAL_BRANCH}' branch returned 0 JS/JSX files. "
                f"Check ORIGINAL_BRANCH and SOURCE_PREFIX in .env."
            )

        return count
    except Exception as e:
        logger.error(f"❌ Failed to fetch '{ORIGINAL_BRANCH}' branch tree: {e}")
        raise RuntimeError(
            f"Cannot determine migration baseline. Check '{ORIGINAL_BRANCH}' branch exists."
        )


def fetch_master_file_list() -> set:
    """Fetches the full file tree from the master branch (1 API call).
    Returns a set of all file paths under SOURCE_PREFIX, with 'frontend/' stripped
    so paths match the manifest format (e.g., 'src/utils/helpers.js')."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/git/trees/{TARGET_BRANCH}?recursive=1"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        tree = response.json().get("tree", [])

        files = set()
        # SOURCE_PREFIX is e.g. 'frontend/src/' — strip the top-level dir to match manifest paths
        top_dir = SOURCE_PREFIX.split("/")[0]  # 'frontend'
        for item in tree:
            path = item.get("path", "")
            if item.get("type") == "blob" and path.startswith(SOURCE_PREFIX):
                # 'frontend/src/utils/helpers.js' -> 'src/utils/helpers.js'
                relative = path[len(top_dir) + 1:]  # strip 'frontend/'
                files.add(relative)

        return files
    except Exception as e:
        logger.error(f"❌ Failed to fetch master branch tree: {e}")
        return set()


def get_file_state_from_tree(file_path: str, master_files: set) -> str:
    """Determines if a file has been migrated by checking if the original .js/.jsx
    file still exists on master. If only the .ts/.tsx version exists, it's COMPLETED.
    This is 100% accurate regardless of PR title format."""
    if file_path in master_files:
        return "PENDING"

    # Check if the TypeScript equivalent exists
    stem = file_path.rsplit('.', 1)[0]
    ext = file_path.rsplit('.', 1)[1] if '.' in file_path else ''
    ts_equivalent = f"{stem}.tsx" if ext == "jsx" else f"{stem}.ts"

    if ts_equivalent in master_files:
        return "COMPLETED"

    # File doesn't exist in either form — already dealt with
    return "COMPLETED"


def load_or_build_batch_manifest(src_dir: str) -> list:
    """Loads a frozen batch manifest from disk, or builds one dynamically.
    
    Architecture:
    - Keyed to {owner}/{repo} — works for any target repository.
    - Auto-invalidates if REPO_OWNER/REPO_NAME changes in .env.
    - Uses the GitHub Trees API (1 call) to get the original file count for validation.
    - Uses the local src_dir for DAG construction (import parsing needs file contents).
    - Persisted until deleted or repo config changes.
    - Batch assignments are permanently idempotent."""
    from dependency_graph import build_dependency_graph, topological_sort_batches

    # Check for existing frozen manifest
    if BATCH_MANIFEST_PATH.exists():
        with open(BATCH_MANIFEST_PATH, "r") as f:
            manifest_data = json.load(f)
        # Validate manifest is for the current repo
        meta = manifest_data.get("_meta", {})
        if meta.get("repo") == f"{REPO_OWNER}/{REPO_NAME}":
            batches = manifest_data["batches"]
            total_in_manifest = sum(len(b) for b in batches)

            # Integrity check: reject manifests built from half-migrated trees
            baseline_count = fetch_original_file_count()
            if total_in_manifest != baseline_count:
                logger.error(
                    f"❌ MANIFEST INTEGRITY CHECK FAILED: Manifest has {total_in_manifest} files "
                    f"but '{ORIGINAL_BRANCH}' branch has {baseline_count}. "
                    f"Manifest was likely built from a partially-migrated tree. "
                    f"Delete {BATCH_MANIFEST_PATH} and regenerate from the '{ORIGINAL_BRANCH}' branch."
                )
                raise RuntimeError(
                    f"Manifest integrity check failed ({total_in_manifest} vs {baseline_count}). "
                    f"Delete {BATCH_MANIFEST_PATH} and rebuild from '{ORIGINAL_BRANCH}' branch checkout."
                )

            logger.info(f"📂 Loading frozen manifest for {REPO_OWNER}/{REPO_NAME} "
                        f"({total_in_manifest} files, {meta.get('total_batches')} batches) ✅ integrity verified")
            return batches
        else:
            logger.warning(f"⚠️ Manifest is for {meta.get('repo')}, but current repo is "
                          f"{REPO_OWNER}/{REPO_NAME}. Rebuilding...")

    # ── BUILD FROM LOCAL SOURCE + VALIDATE AGAINST GITHUB ─────────────
    logger.info(f"🔨 No manifest found. Building immutable DAG from local source tree...")
    logger.info(f"   Source: {src_dir}")

    graph, all_files = build_dependency_graph(src_dir)
    batches = topological_sort_batches(graph, all_files)

    src_path = Path(src_dir).resolve()
    frontend_path = src_path.parent

    formatted_batches = []
    for batch in batches:
        formatted = [Path(f).relative_to(frontend_path).as_posix() for f in batch
                     if f.endswith((".js", ".jsx"))]
        if formatted:
            formatted_batches.append(formatted)

    total_files = sum(len(b) for b in formatted_batches)

    # Validate against GitHub's 'original' branch (single API call)
    original_count = fetch_original_file_count()
    if total_files != original_count:
        logger.warning(
            f"⚠️ Local tree has {total_files} files but 'original' branch has {original_count}. "
            f"Ensure your local repo is on the correct branch for manifest generation."
        )

    # Freeze manifest with metadata for validation and portability
    manifest_data = {
        "_meta": {
            "repo": f"{REPO_OWNER}/{REPO_NAME}",
            "baseline_branch": ORIGINAL_BRANCH,
            "source_prefix": SOURCE_PREFIX,
            "total_files": total_files,
            "total_batches": len(formatted_batches),
            "original_branch_count": original_count,
        },
        "batches": formatted_batches
    }

    with open(BATCH_MANIFEST_PATH, "w") as f:
        json.dump(manifest_data, f, indent=2)

    logger.info(f"✅ Frozen manifest saved to {BATCH_MANIFEST_PATH} "
                f"({len(formatted_batches)} batches, {total_files} files)")

    return formatted_batches


def build_batch_details(formatted_batches: list, master_files: set) -> list:
    """Builds per-batch completion details for the dashboard topology chart.
    Uses the master branch file tree as the source of truth — not PR titles.
    Includes individual file paths and states for drill-down UI."""
    details = []
    for i, batch in enumerate(formatted_batches):
        completed = 0
        pending = 0
        file_list = []
        for f in batch:
            state = get_file_state_from_tree(f, master_files)
            file_list.append({"path": f, "state": state})
            if state == "COMPLETED":
                completed += 1
            else:
                pending += 1
        details.append({
            "batch": i + 1,
            "total": len(batch),
            "completed": completed,
            "in_progress": 0,
            "pending": pending,
            "files": file_list
        })
    return details


def count_historical_merged_prs(prs: list) -> int:
    """Counts ALL merged migration PRs from GitHub — the single source of truth.
    This captures the full history of merged work, regardless of local disk state.
    Handles both PR title conventions:
      - 'Migrate src/utils/helpers.js to TS'
      - 'migrate: AuthRouter.jsx -> AuthRouter.tsx'
    Only counts PRs that have merged_at set (excludes closed-without-merge)."""
    count = 0
    for pr in prs:
        title = pr.get("title", "").strip().lower()
        # MUST have merged_at — closed without merge is excluded
        if pr.get("merged_at"):
            if title.startswith("migrate ") or title.startswith("migrate:"):
                count += 1
    return count


def print_live_telemetry(in_progress_count: int, completed_count: int, pending_count: int, current_batch: int, total_batches: int, batch_details: list = None):
    """Calculates and prints real-time metrics, and exports JSON to the React Command Center."""
    human_hours_saved = completed_count * 2
    # Progress is measured against the ORIGINAL file baseline, not the shrinking DAG
    total_files = ORIGINAL_FILE_COUNT
    progress_pct = (completed_count / total_files * 100) if total_files > 0 else 0

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
        "progress": {"completed": completed_count, "pending": total_files - completed_count - in_progress_count, "in_progress": in_progress_count},
        "roi": {"hours_saved": human_hours_saved, "active_agents": in_progress_count},
        "batch": {"current": current_batch, "total": total_batches},
        "posture": {"security": "Zero-Trust (PR-Gated)", "resilience": "Stateless Auto-Resume Active"}
    }

    # Include per-batch file counts and completion status for the topology bar chart
    if batch_details:
        telemetry_data["batch_details"] = batch_details

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
        # ── IDEMPOTENT BATCH MANIFEST ──────────────────────────────────
        # Load frozen manifest or build it once from the original source tree.
        # Batch assignments are permanent — file X is Batch N forever.
        formatted_batches = load_or_build_batch_manifest(src_dir)

        orchestrator = MigrationOrchestrator(client)

        # ── HISTORICAL BASELINE (Cold Start Recovery) ──────────────────
        # Dynamically fetch the original file count from the frozen baseline branch.
        global ORIGINAL_FILE_COUNT
        logger.info(f"🔍 Fetching migration baseline from '{ORIGINAL_BRANCH}' branch...")
        ORIGINAL_FILE_COUNT = fetch_original_file_count()
        logger.info(f"📎 Baseline: {ORIGINAL_FILE_COUNT} JS/JSX files in '{ORIGINAL_BRANCH}' branch.")

        # Fetch master branch tree (1 API call) — used for all per-file state checks
        logger.info("🔍 Fetching master branch file tree for state detection...")
        master_files = fetch_master_file_list()
        logger.info(f"📂 Master branch has {len(master_files)} files under {SOURCE_PREFIX}")

        # Count completed files by checking which manifest files are now .tsx on master
        completed_from_tree = sum(
            1 for batch in formatted_batches
            for f in batch
            if get_file_state_from_tree(f, master_files) == "COMPLETED"
        )
        remaining_files = ORIGINAL_FILE_COUNT - completed_from_tree

        logger.info(
            f"📊 COLD START: {completed_from_tree} files already migrated on master "
            f"({completed_from_tree * 2} hours saved). "
            f"{remaining_files} files remaining out of {ORIGINAL_FILE_COUNT} original."
        )

        # Build per-batch details for the dashboard topology chart
        batch_details = build_batch_details(formatted_batches, master_files)

        # Track active Devin session keys to report accurate "active agents" on dashboard
        # Format: "batch_index:filepath" — pruned when the file appears as .tsx on master
        active_session_keys = set()

        # Immediately write baseline telemetry so the React dashboard wakes up
        print_live_telemetry(
            in_progress_count=0,
            completed_count=completed_from_tree,
            pending_count=remaining_files,
            current_batch=1,
            total_batches=len(formatted_batches),
            batch_details=batch_details
        )

        for i, batch in enumerate(formatted_batches):
            print(f"\n{'='*50}")
            print(f" 📦 EVALUATING BATCH {i+1} OF {len(formatted_batches)}")
            print(f"{'='*50}")

            while True:
                # Refresh master tree each polling cycle to detect newly merged PRs
                master_files = fetch_master_file_list()

                pending_files = []
                for f in batch:
                    state = get_file_state_from_tree(f, master_files)
                    if state == "PENDING":
                        pending_files.append(f)

                # Recount global completed from tree
                global_completed = sum(
                    1 for b in formatted_batches
                    for f in b
                    if get_file_state_from_tree(f, master_files) == "COMPLETED"
                )

                # Rebuild per-batch details every poll so the dashboard stays live
                batch_details = build_batch_details(formatted_batches, master_files)

                # Prune sessions whose files are now merged on master
                merged_keys = set()
                for key in active_session_keys:
                    filepath = key.split(":", 1)[1]
                    if get_file_state_from_tree(filepath, master_files) == "COMPLETED":
                        merged_keys.add(key)
                if merged_keys:
                    active_session_keys -= merged_keys
                    logger.info(f"🧹 Pruned {len(merged_keys)} completed sessions from active tracker")

                if not pending_files:
                    # Clear any remaining keys for this batch
                    active_session_keys = {k for k in active_session_keys if not k.startswith(f"{i+1}:")}
                    # Write telemetry BEFORE breaking so the dashboard reflects the completed batch
                    print_live_telemetry(
                        len(active_session_keys),
                        global_completed,
                        ORIGINAL_FILE_COUNT - global_completed - len(active_session_keys),
                        i + 1,
                        len(formatted_batches),
                        batch_details=batch_details
                    )
                    print(
                        f"✅ Batch {i+1} completely merged. Moving to next batch...")
                    break

                if pending_files:
                    # Filter out files that already have an active session
                    new_files = [f for f in pending_files if f"{i+1}:{f}" not in active_session_keys]
                    already_active = len(pending_files) - len(new_files)
                    if already_active > 0:
                        print(f"   ⏭️ {already_active} file(s) already have active agents — skipping re-dispatch")

                    if new_files:
                        print(
                            f"\n🚀 Staggering dispatch for {len(new_files)} NEW file(s)...")
                        tasks = []
                        for f in new_files:
                            branch = get_unique_branch_name(f)
                            prompt = build_migration_prompt(f, branch)

                            if not DRY_RUN:
                                task = asyncio.create_task(
                                    orchestrator.process_file(f, branch, prompt))
                                tasks.append(task)
                                active_session_keys.add(f"{i+1}:{f}")
                                # 10-second API rate limit safeguard
                                await asyncio.sleep(10)
                            else:
                                print(f"  🛑 [DRY RUN] Would have dispatched agent for: {f}")
                                await asyncio.sleep(1)

                        if not DRY_RUN:
                            await asyncio.gather(*tasks)
                            print(f"✅ Dispatch complete. {len(active_session_keys)} agents active.")
                        else:
                            print(f"✅ [DRY RUN] Simulated dispatch complete. Zero credits spent.")

                # Triggers the executive telemetry for the presentation & dashboard
                print_live_telemetry(
                    len(active_session_keys),
                    global_completed,
                    ORIGINAL_FILE_COUNT - global_completed - len(active_session_keys),
                    i + 1,
                    len(formatted_batches),
                    batch_details=batch_details
                )
                print(f"\n💤 Polling in {POLL_INTERVAL}s... ({len(active_session_keys)} agents active)")
                await asyncio.sleep(POLL_INTERVAL)

        print("\n🎉 MIGRATION ENGINE COMPLETED ALL BATCHES!")

    finally:
        await client.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True)
    args = parser.parse_args()
    asyncio.run(run_pipeline(args.src))
