"""
dependency_graph.py
-------------------
Parses JSX import statements to build a dependency graph,
then performs a topological sort to determine safe migration order.

Key insight: Devin sessions don't share context between each other.
If we migrate CartSummary.jsx before CartItem.jsx, Devin will
hallucinate the CartItem types. Leaf nodes (no local deps) must go first.
"""

import os
import re
from collections import defaultdict, deque


def find_all_jsx_files(src_dir: str) -> list[str]:
    """Recursively find all .jsx files under src_dir."""
    jsx_files = []
    for root, _, files in os.walk(src_dir):
        for f in files:
            if f.endswith(".jsx"):
                jsx_files.append(os.path.normpath(os.path.join(root, f)))
    return jsx_files


def parse_local_imports(filepath: str, all_files_set: set) -> list[str]:
    """
    Parse a JSX file and return a list of local .jsx dependencies
    that exist within our codebase.
    """
    deps = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return deps

    # Match: import X from './foo' or '../bar/baz'
    import_pattern = re.findall(
        r"""import\s+.*?from\s+['"](\.[^'"]+)['"]""", content
    )

    base_dir = os.path.dirname(filepath)

    for imp in import_pattern:
        # Try resolving with and without .jsx extension
        candidates = [
            os.path.normpath(os.path.join(base_dir, imp + ".jsx")),
            os.path.normpath(os.path.join(base_dir, imp, "index.jsx")),
            os.path.normpath(os.path.join(base_dir, imp)),
        ]
        for candidate in candidates:
            if candidate in all_files_set:
                deps.append(candidate)
                break

    return deps


def build_dependency_graph(src_dir: str) -> tuple[dict, list[str]]:
    """
    Returns:
        graph: dict mapping filepath -> set of files it DEPENDS ON
        all_files: flat list of all jsx files
    """
    all_files = find_all_jsx_files(src_dir)
    all_files_set = set(all_files)
    graph = defaultdict(set)

    for filepath in all_files:
        deps = parse_local_imports(filepath, all_files_set)
        for dep in deps:
            graph[filepath].add(dep)

    return dict(graph), all_files


def topological_sort_batches(graph: dict, all_files: list[str]) -> list[list[str]]:
    """
    Kahn's algorithm to produce batches of files that can be
    safely migrated in parallel.

    Batch 1 = leaf nodes (no local deps) — safest to migrate first.
    Batch N = files that depend on all previous batches being done.

    Returns list of batches, each batch is a list of filepaths.
    """
    # Build in-degree map: how many files does this file depend on?
    in_degree = {f: 0 for f in all_files}
    # reverse_graph: who depends on ME?
    reverse_graph = defaultdict(set)

    for node, deps in graph.items():
        for dep in deps:
            in_degree[node] = in_degree.get(node, 0)
        in_degree[node] = len(deps)
        for dep in deps:
            reverse_graph[dep].add(node)

    # Files with zero dependencies are our starting batch
    queue = deque([f for f in all_files if in_degree.get(f, 0) == 0])
    batches = []
    processed = set()

    while queue:
        current_batch = list(queue)
        batches.append(current_batch)
        queue = deque()

        for node in current_batch:
            processed.add(node)
            # Reduce in-degree for everything that depends on this node
            for dependent in reverse_graph.get(node, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0 and dependent not in processed:
                    queue.append(dependent)

    # Any files not reached (circular deps) go in a final catch-all batch
    unreached = [f for f in all_files if f not in processed]
    if unreached:
        batches.append(unreached)

    return batches


def print_dependency_report(src_dir: str):
    """Print a summary of the dependency graph and migration batches."""
    graph, all_files = build_dependency_graph(src_dir)
    batches = topological_sort_batches(graph, all_files)

    print(f"\n{'='*60}")
    print(f"  IDURAR ERP — Migration Dependency Analysis")
    print(f"{'='*60}")
    print(f"  Total JSX files found: {len(all_files)}")
    print(f"  Migration batches: {len(batches)}")
    print()

    for i, batch in enumerate(batches):
        label = "LEAF NODES (no deps — migrate first)" if i == 0 else f"Batch {i+1}"
        print(f"  [{label}] — {len(batch)} files")
        for f in batch[:5]:  # Show first 5 to keep output clean
            rel = os.path.relpath(f, src_dir)
            print(f"    • {rel}")
        if len(batch) > 5:
            print(f"    ... and {len(batch) - 5} more")
        print()

    return batches


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "./frontend/src"
    print_dependency_report(src)
