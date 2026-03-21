import sqlite3
import requests
import os
import re
from dotenv import load_dotenv

load_dotenv()

GITHUB_REPO = os.getenv("GITHUB_REPO", "viku11/idurar-erp-crm")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

if not GITHUB_TOKEN:
    GITHUB_TOKEN = input("Enter GitHub token: ").strip()

gh_headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}


def sync_from_github():
    print("Connecting to migration_state.db...")
    conn = sqlite3.connect('migration_state.db')

    # 1. RESET STATE
    print("Wiping local state to PENDING...")
    conn.execute("UPDATE file_migrations SET status='PENDING', pr_url=NULL")

    # 2. FETCH ALL CLOSED PRS
    print(f"\nFetching closed PRs from {GITHUB_REPO}...")
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

    # Filter for merged PRs only
    merged_prs = [pr for pr in all_prs if pr.get("merged_at")]
    print(f"  Found {len(merged_prs)} merged PRs total.")

    # 3. IDENTIFY REVERTS
    # We track which PRs were reverted so we don't accidentally mark them COMPLETED.
    reverted_pr_numbers = set()
    for pr in merged_prs:
        title = pr.get("title", "").lower()
        # Look for the standard GitHub revert pattern: 'Revert "migrate: main.jsx -> main.tsx"'
        if "revert" in title:
            # Extract the PR number being reverted from the title or description
            match = re.search(
                r'#(\d+)', title) or re.search(r'#(\d+)', pr.get("body", "") or "")
            if match:
                reverted_num = int(match.group(1))
                reverted_pr_numbers.add(reverted_num)
                print(
                    f"  Detected Revert: PR #{pr.get('number')} reverts PR #{reverted_num}")

    # 4. STRICT MATCHING & REVERT VALIDATION
    unique_files_updated = set()
    rows = conn.execute("SELECT filepath FROM file_migrations").fetchall()

    for pr in merged_prs:
        pr_num = pr.get("number")

        # SKIP if this PR was itself a revert, or if this PR WAS reverted by a later one.
        if "revert" in pr.get("title", "").lower() or pr_num in reverted_pr_numbers:
            continue

        branch = pr.get("head", {}).get("ref", "")
        pr_url = pr.get("html_url", "")

        if not (branch.startswith("migrate/") or branch.startswith("devin/")):
            continue

        # Clean branch name (Handle -v2, -tsx, -fix suffixes)
        path_bits = branch.replace("migrate/", "").replace("devin/", "")
        path_bits = re.search(r'([a-zA-Z0-9\-/]+)',
                              path_bits).group(1)  # Basic path
        clean_name = re.sub(r'(-v\d+|-tsx|-fix|-migration)$',
                            '', path_bits).lower()
        clean_name = clean_name.replace("-", "/")

        best_match = None
        for (filepath,) in rows:
            fp_norm = filepath.replace("\\", "/").lower().replace(".jsx", "")

            # Match if the branch hint aligns with the actual file path
            if fp_norm.endswith(clean_name):
                parent_dir = os.path.basename(
                    os.path.dirname(filepath)).lower()
                if "/" in clean_name or parent_dir in branch.lower():
                    best_match = filepath
                    break

        if best_match:
            conn.execute(
                "UPDATE file_migrations SET status='COMPLETED', pr_url=? WHERE filepath=?",
                (pr_url, best_match)
            )
            unique_files_updated.add(best_match)

    # 5. FINAL SAFETY OVERRIDES
    # Ensure index collisions and specific audited files are correct
    overrides = [
        ("%settings%useDate.jsx", 'COMPLETED'),
        ("%settings%index.jsx", 'COMPLETED'),
        ("%AutoCompleteAsync%index.jsx", 'COMPLETED')
    ]
    for pattern, status in overrides:
        conn.execute(
            "UPDATE file_migrations SET status=? WHERE filepath LIKE ?", (status, pattern))

    conn.commit()

    # 6. SUMMARY
    print("\n" + "="*50)
    print("FINAL SYNC STATE (Revert-Aware):")
    stats = conn.execute(
        "SELECT status, COUNT(*) FROM file_migrations GROUP BY status").fetchall()
    for status, count in stats:
        print(f"  {status}: {count}")
    print("="*50)
    conn.close()


if __name__ == "__main__":
    sync_from_github()
