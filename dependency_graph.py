import os
import re
from collections import defaultdict, deque


def find_all_files(src_dir: str) -> list[str]:
    """Scans the directory for all relevant frontend files."""
    src_dir = os.path.abspath(src_dir)
    all_files = []
    for root, _, files in os.walk(src_dir):
        for f in files:
            if f.endswith((".jsx", ".js", ".tsx", ".ts")):
                all_files.append(os.path.normpath(os.path.join(root, f)))
    return all_files


def parse_imports(filepath: str, norm_files_map: dict, src_dir: str) -> list[str]:
    """Statically analyzes imports to build the dependency map."""
    deps = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return deps

    # Matches both standard and absolute (@/) imports
    import_paths = re.findall(r"['\"]((?:@/|\.)[^'\"]+)['\"]", content)
    abs_src_dir = os.path.abspath(src_dir)
    base_dir = os.path.dirname(os.path.abspath(filepath))

    for imp in set(import_paths):
        clean_imp = re.sub(r'\.(jsx|js|tsx|ts)$', '', imp)
        res = os.path.join(abs_src_dir, clean_imp[2:]) if clean_imp.startswith(
            "@/") else os.path.join(base_dir, clean_imp)

        for ext in [".tsx", ".ts", ".jsx", ".js", "/index.tsx", "/index.ts", "/index.jsx", "/index.js"]:
            cand_norm = os.path.normcase(os.path.normpath(res + ext))
            if cand_norm in norm_files_map:
                deps.append(norm_files_map[cand_norm])
                break
    return deps


def build_dependency_graph(src_dir: str):
    """Builds the initial graph and maps all files on disk."""
    abs_src = os.path.abspath(src_dir)
    all_files = find_all_files(abs_src)
    norm_files_map = {os.path.normcase(f): f for f in all_files}

    full_graph = defaultdict(set)
    for f in all_files:
        for dep in parse_imports(f, norm_files_map, abs_src):
            if dep != f:
                full_graph[f].add(dep)

    return full_graph, all_files


def topological_sort_batches(graph, all_files):
    """
    Groups files into batches based on dependencies.
    Includes a recovery mechanism for circular dependencies.
    """
    in_degree = {f: 0 for f in all_files}
    rev_graph = defaultdict(set)
    for n, ds in graph.items():
        in_degree[n] = len(ds)
        for d in ds:
            rev_graph[d].add(n)

    # Kahn's Algorithm
    queue = deque([f for f in all_files if in_degree[f] == 0])
    all_batches = []
    processed_files = set()

    while queue:
        batch = sorted(list(queue))
        all_batches.append(batch)
        queue = deque()
        for n in batch:
            processed_files.add(n)
            for dep in rev_graph[n]:
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    queue.append(dep)

    # 1. Standard batch processing
    final_batches = []
    for batch in all_batches:
        js_only = [f for f in batch if f.endswith((".js", ".jsx"))]
        if js_only:
            final_batches.append(js_only)

    # 2. Recovery Batch: Catch any JS/JSX files stuck in a circular dependency
    # These files are technically un-sortable, so we process them last.
    leftovers = [f for f in all_files if f not in processed_files and f.endswith(
        (".js", ".jsx"))]
    if leftovers:
        final_batches.append(leftovers)

    return final_batches


if __name__ == "__main__":
    import sys
    # Default path tailored to your project structure
    src = sys.argv[1] if len(
        sys.argv) > 1 else "../idurar-erp-crm/frontend/src"

    g, files = build_dependency_graph(src)
    batches = topological_sort_batches(g, files)

    print(f"\n{'='*60}\n IDURAR MIGRATION ORCHESTRATOR — DEPENDENCY REPORT\n{'='*60}")
    total = 0
    for i, b in enumerate(batches):
        print(f"\n[BATCH {i+1}] - {len(b)} files")
        for f in b:
            print(f"  -> {os.path.relpath(f, os.path.abspath(src))}")
            total += 1
    print(f"\nTOTAL PENDING (Logic Corrected): {total}\n{'='*60}")
