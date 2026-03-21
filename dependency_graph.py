import os
import re
from collections import defaultdict, deque


def find_all_jsx_files(src_dir: str) -> list[str]:
    src_dir = os.path.abspath(src_dir)
    jsx_files = []
    for root, _, files in os.walk(src_dir):
        for f in files:
            if f.endswith(".jsx"):
                jsx_files.append(os.path.normpath(os.path.join(root, f)))
    return jsx_files


def parse_local_imports(filepath: str, all_files_set: set, norm_files_map: dict, src_dir: str) -> list[str]:
    deps = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return deps

    # Capture anything in quotes starting with . or @/
    import_paths = re.findall(r"['\"]((?:@/|\.)[^'\"]+)['\"]", content)

    abs_src_dir = os.path.abspath(src_dir)
    base_dir = os.path.dirname(os.path.abspath(filepath))

    for imp in set(import_paths):
        if imp.startswith("@/"):
            resolved_path = os.path.join(abs_src_dir, imp[2:])
        else:
            resolved_path = os.path.join(base_dir, imp)

        # Try extensions to match actual files on disk
        candidates = [
            os.path.normpath(resolved_path),
            os.path.normpath(resolved_path + ".jsx"),
            os.path.normpath(os.path.join(resolved_path, "index.jsx")),
            os.path.normpath(resolved_path + ".js"),
            os.path.normpath(os.path.join(resolved_path, "index.js")),
        ]

        for cand in candidates:
            cand_norm = os.path.normcase(cand)
            if cand_norm in norm_files_map:
                deps.append(norm_files_map[cand_norm])
                break

    return deps


def build_dependency_graph(src_dir: str) -> tuple[dict, list[str]]:
    all_files = find_all_jsx_files(src_dir)
    norm_files_map = {os.path.normcase(f): f for f in all_files}
    graph = defaultdict(set)
    for filepath in all_files:
        deps = parse_local_imports(filepath, set(
            all_files), norm_files_map, src_dir)
        for dep in deps:
            if dep != filepath:
                graph[filepath].add(dep)
    return dict(graph), all_files


def topological_sort_batches(graph: dict, all_files: list[str]) -> list[list[str]]:
    in_degree = {f: 0 for f in all_files}
    reverse_graph = defaultdict(set)
    for node, deps in graph.items():
        in_degree[node] = len(deps)
        for dep in deps:
            reverse_graph[dep].add(node)
    queue = deque([f for f in all_files if in_degree.get(f, 0) == 0])
    batches = []
    processed = set()
    while queue:
        current_batch = list(queue)
        batches.append(current_batch)
        queue = deque()
        for node in current_batch:
            processed.add(node)
            for dependent in reverse_graph.get(node, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0 and dependent not in processed:
                    queue.append(dependent)
    unreached = [f for f in all_files if f not in processed]
    if unreached:
        batches.append(unreached)
    return batches


def print_dependency_report(src_dir: str):
    abs_src = os.path.abspath(src_dir)
    graph, all_files = build_dependency_graph(abs_src)
    batches = topological_sort_batches(graph, all_files)
    print(f"\n{'='*60}\n  IDURAR ERP — DYNAMIC DEPENDENCY SYNC\n{'='*60}")
    print(f"  Total JSX files: {len(all_files)} | Batches: {len(batches)}\n")
    for i, batch in enumerate(batches):
        label = "LEAF NODES" if i == 0 else f"Batch {i+1}"
        print(f"  [{label}] — {len(batch)} files")
        for f in batch:
            rel = os.path.relpath(f, abs_src)
            if "routes.jsx" in rel or "main.jsx" in rel:
                print(f"    >>> FOUND CRITICAL FILE: {rel} <<<")
    return batches


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "./frontend/src"
    print_dependency_report(src)
