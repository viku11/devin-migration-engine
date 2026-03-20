import asyncio
import time
from devin_client import (
    create_devin_session,
    check_github_for_pr,
    stop_devin_session,
    merge_github_pr
)

# Constants
MAX_CONCURRENT_SESSIONS = 7
PR_TIMEOUT = 600  # 10 Minutes


class MigrationOrchestrator:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_SESSIONS)

    async def process_file(self, file_path, branch_name, prompt):
        async with self.semaphore:
            # 1. Dispatch with Rate Limit Handling
            session_id = None
            while not session_id:
                session_id = await create_devin_session(file_path, prompt)
                if session_id == "RATE_LIMIT":
                    print(
                        f"   [429] Rate limited on {file_path}. Backing off 30s...")
                    await asyncio.sleep(30)
                    session_id = None
                elif not session_id:
                    print(
                        f"   [ERROR] Failed to start {file_path}. Retrying...")
                    await asyncio.sleep(5)

            print(f"[DISPATCH] Session {session_id} active for {file_path}.")

            # 2. Closed-Loop Monitoring
            start_time = time.time()
            success = False

            while time.time() - start_time < PR_TIMEOUT:
                pr_data = await check_github_for_pr(branch_name)

                if pr_data:
                    pr_num = pr_data['number']
                    print(f"[SUCCESS] PR #{pr_num} detected for {file_path}!")

                    # 3. Finalize: Merge & Stop
                    merged = await merge_github_pr(pr_num)
                    if merged:
                        print(f"   [MERGED] PR #{pr_num} merged successfully.")

                    await stop_devin_session(session_id)
                    success = True
                    break

                await asyncio.sleep(30)
                elapsed = int(time.time() - start_time)
                if elapsed % 60 == 0:
                    print(
                        f"   [POLLING] {file_path} - {elapsed}s/600s elapsed...")

            if not success:
                print(
                    f"[TIMEOUT] Abandoning {file_path} after 10 mins. Freeing slot.")
                await stop_devin_session(session_id)

            return success

# Entry point logic for main.py would call:
# orchestrator = MigrationOrchestrator()
# await asyncio.gather(*[orchestrator.process_file(f, b, p) for f, b, p in tasks])
