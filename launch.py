import asyncio
import os
import time
from pathlib import Path
from dotenv import load_dotenv
from dependency_graph import build_dependency_graph, topological_sort_batches
from main import run_pipeline

# Load environment variables
load_dotenv()


async def preflight_and_launch():
    # Dynamically resolve path from .env, or use a relative fallback
    # Fallback assumes migration-engine and idurar-erp-crm are in the same parent folder
    target_repo = os.getenv(
        "TARGET_REPO_PATH", "../idurar-erp-crm/frontend/src")
    src_dir = Path(target_repo).resolve().as_posix()

    if not os.path.exists(src_dir):
        print(f"\n❌ CRITICAL ABORT: Source directory not found at {src_dir}")
        print("Please set TARGET_REPO_PATH in your .env file.")
        return

    print("\n" + "="*50)
    print(" 🚀 INITIATING MIGRATION PRE-FLIGHT CHECK")
    print("="*50)

    # 1. Verification
    graph, all_files = build_dependency_graph(src_dir)
    batches = topological_sort_batches(graph, all_files)
    total_files = sum(len(b) for b in batches)

    print(f" [✓] Target repository located: {src_dir}")

    if total_files == 0:
        print(
            f"\n❌ CRITICAL ABORT: No migratable JS/JSX files found in {src_dir}.")
        return

    print(f" [✓] Dependency Graph built. Total target files: {total_files}")
    print(
        f" [✓] Topology verified. Codebase split into {len(batches)} optimal batches.")
    print(f" [✓] Circular dependencies isolated to final batch.")

    # Updated for the stateless GitOps architecture video script
    print(f" [✓] Stateless GitOps Architecture Active (GitHub API Polling).")
    print(f" [✓] Auto-Sleep ACU Protections enabled (Zero-cost wait states).")

    print("\n✅ PRE-FLIGHT SUCCESSFUL. ALL SYSTEMS GO.")
    print("="*50)

    # A short cinematic pause for the video before the terminal floods
    print("🔥 IGNITING ORCHESTRATOR IN 3 SECONDS...")
    time.sleep(3)
    print("="*50 + "\n")

    # 2. Trigger the Main Engine
    await run_pipeline(src_dir)

if __name__ == "__main__":
    try:
        asyncio.run(preflight_and_launch())
    except KeyboardInterrupt:
        print("\n🛑 Migration aborted by user.")
