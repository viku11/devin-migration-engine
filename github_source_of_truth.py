import sqlite3
import requests
import os
import re
from dotenv import load_dotenv

load_dotenv()

# Configuration from .env
GITHUB_REPO = os.getenv("GITHUB_REPO", "viku11/idurar-erp-crm")
GITHUB_TOKEN = input("Enter GitHub token: ").strip()

gh_headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}


def sync_from_github():
    print("Connecting to migration_state.db...")
    conn = sqlite3.connect('migration_state.db')

    # 1. WIPE AND SANITIZE
    # Reset everything to PENDING and clear error_reason to fix dashboard pollution
    print("Wiping local state and cleaning error logs...")
    conn.execute("""
        UPDATE file_migrations 
        SET status='PENDING', attempts=0, error_reason=NULL
    """)

    # 2. FETCH REALITY FROM GITHUB
    print(f"\nFetching merged PRs from GitHub repo: {GITHUB_REPO}...")
    all_prs = []
    page = 1
    while True:
        resp = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/pulls",
            headers=gh_headers,
            params={"state": "closed", "per_page": 100, "page": page}
        )
        data = resp.json()
        if not data or not isinstance(data, list):
            break
        all_prs.extend(data)
        page += 1

    # Filter strictly for PRs that were actually merged
    merged_prs = [pr for pr in all_prs if pr.get("merged_at")]
    print(f"  Found {len(merged_prs)} merged PRs.")

    # 3. EXACT SUFFIX MATCHING
    # This prevents collisions between different index.jsx files
    marked = 0
    unique_files_updated = set()
    rows = conn.execute("SELECT filepath FROM file_migrations").fetchall()

    for pr in merged_prs:
        branch = pr.get("head", {}).get("ref", "")
        # Only process migration-related branches
        if not (branch.startswith("migrate/") or branch.startswith("devin/")):
            continue

        # Normalize branch path (e.g., migrate/components-Tag-index -> components/tag/index)
        path_part = branch.replace("migrate/", "").replace("devin/", "")
        # Remove leading ID if present
        path_part = re.sub(r"^\d+-", "", path_part)
        bp_norm = path_part.replace("-", "/").lower()
        if bp_norm.endswith(".jsx"):
            bp_norm = bp_norm[:-4]

        best_match = None
        best_score = -1

        for (filepath,) in rows:
            # Normalize file path (e.g., .../components/Tag/index.jsx -> components/tag/index)
            fp_norm = filepath.replace("\\", "/").lower()
            if fp_norm.endswith(".jsx"):
                fp_norm = fp_norm[:-4]

            # The branch path must perfectly match the END of the file path
            if fp_norm.endswith(bp_norm):
                score = len(bp_norm)
                if score > best_score:
                    best_score = score
                    best_match = filepath

        if best_match:
            conn.execute(
                "UPDATE file_migrations SET status='COMPLETED', error_reason=NULL WHERE filepath=?",
                (best_match,)
            )
            unique_files_updated.add(best_match)
            marked += 1

    print(f"  Processed {marked} PR matches.")
    print(
        f"  Successfully updated {len(unique_files_updated)} UNIQUE files based on GitHub reality.")

    # 4. ENFORCE STRICT OVERRIDES
    # main.jsx must stay PENDING for Batch 2
    conn.execute(
        "UPDATE file_migrations SET status='PENDING', attempts=0 WHERE filename='main.jsx'")
    print("  Enforced: main.jsx -> PENDING")

    # useDate.jsx in settings is a known completion
    conn.execute(
        "UPDATE file_migrations SET status='COMPLETED', error_reason=NULL WHERE filename='useDate.jsx' AND filepath LIKE '%settings%'")
    print("  Enforced: useDate.jsx -> COMPLETED")

    conn.commit()

    # 5. FINAL STATE SUMMARY
    print("\n==================================================")
    print("CLEANED FINAL STATE:")
    stats = conn.execute(
        "SELECT status, COUNT(*) FROM file_migrations GROUP BY status").fetchall()
    for status, count in stats:
        print(f"  {status}: {count}")
    print("==================================================")
    conn.close()


if __name__ == "__main__":
    sync_from_github()
