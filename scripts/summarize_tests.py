#!/usr/bin/env python3
"""Generate a markdown summary of the llauncher test suite.

Hybrid path + marker categorization, adapted from sibling repos
(``langgraph-agentic-scaffold/scripts/summarize_tests.py`` for the
walker shape, ``semantic-forge/scripts/summarize_tests.py`` for the
marker-extraction pattern).

Categorization here is two-axis:

* **Primary (path)**: ``tests/unit/`` -> ``unit``,
  ``tests/integration/`` -> ``integration``, anything else -> ``other``.
* **Secondary (markers)**: any of the four llauncher-meaningful
  markers attached to a test function or its enclosing ``Test*`` class
  surfaces in the marker table. Builtin pytest markers (``asyncio``,
  ``parametrize``, ``skip``, ``skipif``, parametrize/skip variants) are
  filtered out — they aren't categorization signals.

The script is stdlib-only and does not import the test code; it parses
each ``test_*.py`` with ``ast`` so it works on a fresh checkout without
pytest installed.

Run from anywhere:

.. code-block:: bash

    python scripts/summarize_tests.py
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

# Markers we treat as categorization signals. Anything else (pytest
# builtins like ``parametrize`` / ``asyncio`` / ``skip*``) is filtered.
MEANINGFUL_MARKERS: tuple[str, ...] = (
    "integration",
    "integration_real",
    "live",
    "real_model_health",
)

# Pytest builtins / pytest-asyncio markers; explicitly dropped so they
# don't show up in the per-test marker badges or in the marker table.
NOISE_MARKERS: frozenset[str] = frozenset(
    {"asyncio", "parametrize", "skip", "skipif", "xfail", "usefixtures"}
)

TEST_DIRECTORY = "tests"
OUTPUT_MARKDOWN_FILE = "docs/generated/TEST_SUITE_SUMMARY.md"


def _path_category(relative_path: str) -> str:
    """Return the primary (directory-based) category for a test file."""
    if "/unit/" in relative_path:
        return "unit"
    if "/integration/" in relative_path:
        return "integration"
    return "other"


def _decorator_marker_name(decorator: ast.expr) -> str | None:
    """Return the marker name if ``decorator`` is ``@pytest.mark.X``.

    Handles both bare ``@pytest.mark.X`` and the parametrize-style
    ``@pytest.mark.X(...)`` call form. Returns ``None`` for any other
    shape of decorator so unrelated decorators are silently ignored.
    """
    node = decorator.func if isinstance(decorator, ast.Call) else decorator
    if not isinstance(node, ast.Attribute):
        return None
    parent = node.value
    if not (isinstance(parent, ast.Attribute) and parent.attr == "mark"):
        return None
    grandparent = parent.value
    if not (isinstance(grandparent, ast.Name) and grandparent.id == "pytest"):
        return None
    return node.attr


def _collect_markers(decorators: list[ast.expr]) -> set[str]:
    """Extract pytest.mark.* names from a decorator list, minus noise."""
    found: set[str] = set()
    for dec in decorators:
        name = _decorator_marker_name(dec)
        if name is not None and name not in NOISE_MARKERS:
            found.add(name)
    return found


def _iter_test_functions(
    tree: ast.AST,
) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, set[str]]]:
    """Yield test functions paired with their effective marker set.

    "Effective" means decorators on the function plus decorators on
    the enclosing ``Test*`` class, if any — pytest applies class-level
    marks to every method.
    """
    results: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, set[str]]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            results.append((node, _collect_markers(node.decorator_list)))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            class_marks = _collect_markers(node.decorator_list)
            for inner in ast.iter_child_nodes(node):
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) and inner.name.startswith("test_"):
                    results.append((inner, class_marks | _collect_markers(inner.decorator_list)))
    return results


def summarize_tests(start_dir: str, output_file: str) -> None:
    """Walk ``start_dir``, write a markdown summary to ``output_file``."""
    project_root = Path(__file__).resolve().parent.parent
    search_path = project_root / start_dir
    output_path = project_root / output_file

    if not search_path.is_dir():
        raise SystemExit(f"Test directory not found: {search_path}")

    # path_counts: directory category -> {files, tests}
    path_counts: dict[str, dict[str, int]] = {
        "unit": {"files": 0, "tests": 0},
        "integration": {"files": 0, "tests": 0},
        "other": {"files": 0, "tests": 0},
    }
    # marker_counts: marker name -> number of test functions carrying it
    marker_counts: dict[str, int] = dict.fromkeys(MEANINGFUL_MARKERS, 0)

    # Per-file records for the detailed listing.
    file_records: list[dict] = []

    for root, _dirs, files in os.walk(search_path):
        for name in sorted(files):
            if not (name.startswith("test_") and name.endswith(".py")):
                continue
            file_path = Path(root) / name
            relative_path = file_path.relative_to(project_root).as_posix()
            category = _path_category(relative_path)

            try:
                source = file_path.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (OSError, SyntaxError) as exc:
                print(f"Warning: could not parse {relative_path}: {exc}")
                continue

            entries = _iter_test_functions(tree)
            if not entries:
                continue

            path_counts[category]["files"] += 1
            path_counts[category]["tests"] += len(entries)

            per_function: list[dict] = []
            for fn, markers in entries:
                for m in markers:
                    if m in marker_counts:
                        marker_counts[m] += 1
                per_function.append(
                    {
                        "name": fn.name,
                        "docstring": ast.get_docstring(fn),
                        "markers": sorted(markers),
                    }
                )

            file_records.append(
                {
                    "relative_path": relative_path,
                    "category": category,
                    "functions": per_function,
                }
            )

    total_files = sum(c["files"] for c in path_counts.values())
    total_tests = sum(c["tests"] for c in path_counts.values())

    lines: list[str] = [
        "# Test Suite Summary",
        "",
        "> **Auto-generated by `scripts/summarize_tests.py`** — do not hand-edit.",
        "> Regenerate after adding or renaming tests:",
        ">",
        "> ```bash",
        "> python scripts/summarize_tests.py",
        "> ```",
        ">",
        "> Source of truth for live pass/skip counts is `pytest`; this",
        "> document is an inventory of *which* tests exist, not which",
        "> ones currently pass. See `pytest.ini` for the runtime",
        "> configuration and `--cov-fail-under` floor.",
        "",
        "## Overview by directory",
        "",
        "| Category | Files | Tests |",
        "|----------|-------|-------|",
        f"| Unit | {path_counts['unit']['files']} | {path_counts['unit']['tests']} |",
        f"| Integration | {path_counts['integration']['files']} | {path_counts['integration']['tests']} |",
    ]
    if path_counts["other"]["files"] > 0:
        lines.append(f"| Other | {path_counts['other']['files']} | {path_counts['other']['tests']} |")
    lines.extend(
        [
            f"| **Total** | **{total_files}** | **{total_tests}** |",
            "",
            "## Tests carrying special markers",
            "",
            "Counts are at the *test function* level (class-level marks",
            "are propagated to every method). Markers are declared in",
            "`pytest.ini`; `integration_real` and `real_model_health` are",
            "ad-hoc markers used in the suite without declaration.",
            "",
            "| Marker | Tests |",
            "|--------|-------|",
        ]
    )
    for marker in MEANINGFUL_MARKERS:
        lines.append(f"| `@pytest.mark.{marker}` | {marker_counts[marker]} |")
    lines.append("")

    lines.append("## Detailed listing")
    lines.append("")
    for category in ("unit", "integration", "other"):
        in_cat = [r for r in file_records if r["category"] == category]
        if not in_cat:
            continue
        lines.append(f"### {category.capitalize()} (`tests/{category}/`)" if category != "other" else "### Other")
        lines.append("")
        for record in sorted(in_cat, key=lambda r: r["relative_path"]):
            lines.append(f"#### `{record['relative_path']}` ({len(record['functions'])} tests)")
            lines.append("")
            for fn in record["functions"]:
                badge = ""
                if fn["markers"]:
                    badge = " " + " ".join(f"`@{m}`" for m in fn["markers"])
                lines.append(f"- **`{fn['name']}`**{badge}")
                if fn["docstring"]:
                    first = fn["docstring"].strip().splitlines()[0].strip()
                    if first:
                        lines.append(f"  - *{first}*")
            lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {output_path} ({total_files} files, {total_tests} tests)")


if __name__ == "__main__":
    summarize_tests(TEST_DIRECTORY, OUTPUT_MARKDOWN_FILE)
