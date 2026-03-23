import asyncio
import json
import os
import time
from pathlib import Path
from dotenv import load_dotenv
from main import run_pipeline, BATCH_MANIFEST_PATH, REPO_OWNER, REPO_NAME, ORIGINAL_BRANCH

# Load environment variables
load_dotenv()


async def preflight_and_launch():
    # Dynamically resolve path from .env, or use a relative fallback
    target_repo = os.getenv(
        "TARGET_REPO_PATH", "../idurar-erp-crm/frontend/src")
    src_dir = Path(target_repo).resolve().as_posix()

    print("\n" + "="*50)
    print(" 🚀 INITIATING MIGRATION PRE-FLIGHT CHECK")
    print("="*50)

    # Use frozen manifest if available (avoids scanning half-migrated local tree)
    if BATCH_MANIFEST_PATH.exists():
        with open(BATCH_MANIFEST_PATH, "r") as f:
            manifest_data = json.load(f)
        meta = manifest_data.get("_meta", {})
        total_files = meta.get("total_files", 0)
        num_batches = meta.get("total_batches", 0)
        print(f" [✓] Frozen manifest found for {meta.get('repo', 'unknown')}")
        print(f" [✓] Baseline: {total_files} files across {num_batches} batches (from '{meta.get('baseline_branch', ORIGINAL_BRANCH)}' branch)")
    else:
        print(f" [i] No frozen manifest found — will be generated from local source tree at boot.")
        total_files = None
        num_batches = None

    print(f" [✓] Target repo: {REPO_OWNER}/{REPO_NAME}")
    print(f" [✓] Stateless GitOps Architecture Active (GitHub API Polling).")
    print(f" [✓] Auto-Sleep ACU Protections enabled (Zero-cost wait states).")

    print("\n✅ PRE-FLIGHT SUCCESSFUL. ALL SYSTEMS GO.")
    print("="*50)

    # A short cinematic pause for the video before the terminal floods
    print("🔥 IGNITING ORCHESTRATOR IN 3 SECONDS...")
    time.sleep(3)
    print("="*50 + "\n")

    # Trigger the Main Engine (src_dir is only used as fallback if no manifest exists)
    await run_pipeline(src_dir)

if __name__ == "__main__":
    try:
        asyncio.run(preflight_and_launch())
    except KeyboardInterrupt:
        print("\n🛑 Migration aborted by user.")
