import argparse
import asyncio
import logging
from pathlib import Path

from dependency_graph import build_dependency_graph, topological_sort_batches
from state_store import StateStore
from devin_client import DevinClient
from worker_pool import MigrationOrchestrator
from dashboard import print_batch_header, print_batch_complete, print_final_report

# Configure Enterprise-grade Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


def build_migration_prompt(file_path: str) -> str:
    """Generates a decoupled, repository-agnostic prompt payload."""
    return f"""
Please process the following target file according to the instructions in the project Knowledge Base:
Target File: {file_path}

CRITICAL INSTRUCTIONS: 
1. Confine all your work strictly to this target file to prevent merge conflicts.
2. Follow the project Knowledge Base rules exactly.
"""


def generate_branch_name(file_path: str) -> str:
    """Matches the branch name Devin generates automatically."""
    safe_path = file_path.replace('.jsx', '').replace(
        '/', '-').replace('\\', '-')
    return f"migrate/{safe_path}"


async def run_pipeline(src_dir: str):
    logger.info("Initializing dependency graph and state store...")
    store = StateStore()
    client = DevinClient()

    try:
        # 1. Build the DAG and batches
        graph, all_files = build_dependency_graph(src_dir)
        batches = topological_sort_batches(graph, all_files)

        # Format paths relative to the frontend directory
        src_path = Path(src_dir).resolve()
        frontend_path = src_path.parent

        formatted_batches = []
        for batch in batches:
            formatted_batch = [Path(f).relative_to(
                frontend_path).as_posix() for f in batch]
            formatted_batches.append(formatted_batch)

        store.initialize_files(formatted_batches)
        orchestrator = MigrationOrchestrator(store, client)

        # 2. Execute batches sequentially
        for i, batch in enumerate(formatted_batches):
            pending_files = store.get_pending_for_batch(i)
            if not pending_files:
                logger.info(f"Batch {i+1} already completed. Skipping.")
                continue

            print_batch_header(i, pending_files)

            tasks = []
            for file_path in pending_files:
                prompt = build_migration_prompt(file_path)
                branch_name = generate_branch_name(file_path)
                tasks.append(orchestrator.process_file(
                    file_path, branch_name, prompt))

            await asyncio.gather(*tasks)
            print_batch_complete(i, store.get_all_rows())

            # DAG MANUAL REVIEW GATE
            if i < len(formatted_batches) - 1:
                input(
                    f"\n[REVIEW GATE] Batch {i+1} complete. Please merge all PRs in GitHub, then press ENTER to start Batch {i+2}...")

        # 3. Final Output
        print_final_report(store)

    finally:
        # Guarantee sockets and DB connections close even on failure
        await client.close()
        store.close()


def main():
    parser = argparse.ArgumentParser(
        description="React to TypeScript Auto-Migration Engine")
    parser.add_argument("--src", required=True,
                        help="Path to the src directory (e.g., ..\\idurar-erp-crm\\frontend\\src)")
    args = parser.parse_args()

    try:
        asyncio.run(run_pipeline(args.src))
    except KeyboardInterrupt:
        logger.info(
            "[ABORT] Pipeline stopped gracefully by user. Database state preserved.")


if __name__ == "__main__":
    main()
