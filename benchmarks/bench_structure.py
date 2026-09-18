"""Benchmark: code structure.

Structural claims are easy to assert and hard to check, so this measures them
from the AST of both codebases rather than from anyone's opinion.

Complexity here is McCabe-style: one plus the number of branch points inside a
function.  Nesting depth is the deepest chain of nested statement blocks.  Both
are computed identically for both codebases.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

from harness import ROOT

LEGACY_FILE = ROOT / "legacy" / "Optimized.py"
NEW_ROOT = ROOT / "src" / "android_optimiser"

_BRANCH_NODES = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
    ast.With, ast.AsyncWith, ast.Assert, ast.IfExp,
)


class _FunctionMetrics(ast.NodeVisitor):
    def __init__(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        self.node = node
        self.complexity = 1
        self.max_depth = 0

    def visit(self, node):  # noqa: D102
        if isinstance(node, _BRANCH_NODES):
            self.complexity += 1
        if isinstance(node, ast.BoolOp):
            self.complexity += max(0, len(node.values) - 1)
        if isinstance(node, ast.comprehension) and node.ifs:
            self.complexity += len(node.ifs)
        super().visit(node)

    def measure_depth(self) -> int:
        def walk(node, depth: int) -> int:
            deepest = depth
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try,
                                      ast.ExceptHandler, ast.AsyncFor, ast.AsyncWith)):
                    deepest = max(deepest, walk(child, depth + 1))
                else:
                    deepest = max(deepest, walk(child, depth))
            return deepest

        return walk(self.node, 0)


def _analyse_file(path: Path) -> dict:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines()

    functions: list[dict] = []
    classes = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            classes += 1
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            metrics = _FunctionMetrics(node)
            metrics.visit(node)
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            annotated = sum(
                1
                for arg in [*node.args.args, *node.args.kwonlyargs]
                if arg.annotation is not None
            )
            total_args = len(node.args.args) + len(node.args.kwonlyargs)
            functions.append(
                {
                    "name": node.name,
                    "line": node.lineno,
                    "length": length,
                    "complexity": metrics.complexity,
                    "max_depth": metrics.measure_depth(),
                    "has_docstring": ast.get_docstring(node) is not None,
                    # None for zero-argument functions: there is nothing to
                    # annotate, so counting them as "fully annotated" would
                    # flatter code that simply takes no parameters.
                    "annotation_ratio": round(annotated / total_args, 2)
                    if total_args
                    else None,
                }
            )

    code_lines = [
        line for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]

    return {
        "path": str(path.relative_to(ROOT)),
        "total_lines": len(lines),
        "code_lines": len(code_lines),
        "functions": functions,
        "classes": classes,
        "module_has_docstring": ast.get_docstring(tree) is not None,
    }


def _summarise(files: list[dict]) -> dict:
    all_functions = [f for file in files for f in file["functions"]]
    if not all_functions:
        return {}
    complexities = [f["complexity"] for f in all_functions]
    lengths = [f["length"] for f in all_functions]
    depths = [f["max_depth"] for f in all_functions]
    # Zero-argument functions have no parameters to annotate and are reported as
    # None; including them would either crash the mean or flatter code that
    # simply takes no arguments.
    annotated = [
        f["annotation_ratio"] for f in all_functions
        if f["annotation_ratio"] is not None
    ]

    return {
        "files": len(files),
        "total_lines": sum(f["total_lines"] for f in files),
        "code_lines": sum(f["code_lines"] for f in files),
        "functions": len(all_functions),
        "classes": sum(f["classes"] for f in files),
        "mean_function_length": round(sum(lengths) / len(lengths), 1),
        "max_function_length": max(lengths),
        "mean_complexity": round(sum(complexities) / len(complexities), 2),
        "max_complexity": max(complexities),
        "functions_over_complexity_10": sum(1 for c in complexities if c > 10),
        "max_nesting_depth": max(depths),
        "mean_nesting_depth": round(sum(depths) / len(depths), 2),
        "functions_with_docstring_pct": round(
            sum(1 for f in all_functions if f["has_docstring"]) / len(all_functions) * 100, 1
        ),
        "functions_with_parameters": len(annotated),
        "mean_annotation_ratio": round(sum(annotated) / len(annotated), 2)
        if annotated else None,
        "modules_with_docstring_pct": round(
            sum(1 for f in files if f["module_has_docstring"]) / len(files) * 100, 1
        ),
    }


def _longest_functions(files: list[dict], limit: int = 8) -> list[dict]:
    rows = [
        {"file": f["path"], **fn}
        for f in files
        for fn in f["functions"]
    ]
    rows.sort(key=lambda r: (-r["complexity"], -r["length"]))
    return rows[:limit]


def count_tests() -> dict:
    test_files = sorted((ROOT / "tests").glob("test_*.py"))
    total = 0
    for path in test_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        total += sum(
            1 for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        )
    return {"test_files": len(test_files), "test_functions": total}


def count_dependencies() -> dict:
    """Third-party imports in each codebase.

    The standard-library set comes from ``sys.stdlib_module_names`` rather than
    a hand-written list: a hand-written list silently misreports any stdlib
    module someone forgot to add (``shutil`` and ``__future__`` were both
    reported as third-party dependencies before this was fixed).
    """
    stdlib_ok = set(sys.stdlib_module_names)

    def imports(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    found.add(node.module.split(".")[0])
        return found

    legacy = imports(LEGACY_FILE)
    new: set[str] = set()
    for path in NEW_ROOT.rglob("*.py"):
        new |= imports(path)

    return {
        "legacy": {
            "imports": sorted(legacy),
            "third_party": sorted(legacy - stdlib_ok),
        },
        "new": {
            "imports": sorted(new),
            "third_party": sorted(new - stdlib_ok),
        },
    }


def run() -> dict:
    legacy_files = [_analyse_file(LEGACY_FILE)]
    new_files = [_analyse_file(p) for p in sorted(NEW_ROOT.rglob("*.py"))]

    legacy = _summarise(legacy_files)
    new = _summarise(new_files)

    return {
        "legacy": {**legacy, "test_functions": 0, "test_files": 0},
        "new": {**new, **count_tests()},
        "legacy_hotspots": _longest_functions(legacy_files),
        "new_hotspots": _longest_functions(new_files),
        "new_module_breakdown": [
            {"path": f["path"], "lines": f["total_lines"], "functions": len(f["functions"])}
            for f in new_files
        ],
        "dependencies": count_dependencies(),
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
