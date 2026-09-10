"""Guard the Python version floor.

`from __future__ import annotations` makes annotations lazy, so `list[str]` in a
signature is fine on any version.  Anything evaluated at import time is not: a
module-level alias like ``Term = tuple[int, int]`` raises
``TypeError: 'type' object is not subscriptable`` below Python 3.9, and no
syntax check catches it because the syntax is valid everywhere.
"""

from __future__ import annotations

import ast
import glob
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

#: builtins that only became subscriptable in 3.9 (PEP 585)
PEP585 = {"list", "dict", "tuple", "set", "frozenset", "type"}

#: standard-library APIs newer than the floor we claim to support
TOO_NEW = {
    "indent": "xml.etree.ElementTree.indent is 3.9+",
    "removeprefix": "str.removeprefix is 3.9+",
    "removesuffix": "str.removesuffix is 3.9+",
    "pairwise": "itertools.pairwise is 3.10+",
    "cache": "functools.cache is 3.9+",
}


def sources() -> list[str]:
    return sorted(glob.glob(os.path.join(ROOT, "sparabbs", "**", "*.py"), recursive=True))


class _RuntimeGenerics(ast.NodeVisitor):
    """Collect builtin generics outside annotation position."""

    def __init__(self) -> None:
        self.hits: list[tuple[int, str]] = []

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value:  # the annotation is lazy; the value is not
            self.visit(node.value)

    def visit_FunctionDef(self, node) -> None:
        for dec in node.decorator_list:
            self.visit(dec)
        defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d]
        for default in defaults:
            self.visit(default)
        for stmt in node.body:
            self.visit(stmt)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Subscript(self, node: ast.Subscript) -> None:
        value = node.value
        if isinstance(value, ast.Name) and value.id in PEP585:
            self.hits.append((node.lineno, ast.dump(node)[:60]))
        self.generic_visit(node)


class CompatTests(unittest.TestCase):
    def test_sources_are_found(self):
        self.assertGreater(len(sources()), 5)

    def test_no_builtin_generic_is_evaluated_at_runtime(self):
        offenders = []
        for path in sources():
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            scan = _RuntimeGenerics()
            scan.visit(tree)
            offenders += [
                f"{os.path.relpath(path, ROOT)}:{line}" for line, _ in scan.hits
            ]
        self.assertEqual(
            offenders,
            [],
            "use typing.List/Tuple/... for values evaluated at import time; "
            "builtin generics need Python 3.9",
        )

    def test_every_annotated_module_defers_annotations(self):
        missing = []
        for path in sources():
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            annotated = any(
                isinstance(n, ast.AnnAssign)
                or (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.returns)
                or (isinstance(n, ast.arg) and n.annotation)
                for n in ast.walk(tree)
            )
            deferred = any(
                isinstance(n, ast.ImportFrom)
                and n.module == "__future__"
                and any(a.name == "annotations" for a in n.names)
                for n in tree.body
            )
            if annotated and not deferred:
                missing.append(os.path.relpath(path, ROOT))
        self.assertEqual(missing, [], "annotations must stay lazy for old Pythons")

    def test_the_annotation_check_would_catch_a_regression(self):
        tree = ast.parse("def f(x: list[int]) -> None: pass\n")
        annotated = any(
            isinstance(n, ast.arg) and n.annotation for n in ast.walk(tree)
        )
        self.assertTrue(annotated, "the detector must see an annotated argument")

    def test_no_stdlib_api_newer_than_the_floor(self):
        offenders = []
        for path in sources():
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in TOO_NEW:
                    offenders.append(
                        f"{os.path.relpath(path, ROOT)}:{node.lineno}: "
                        f"{TOO_NEW[node.attr]}"
                    )
        self.assertEqual(offenders, [])

    def test_sources_parse_under_the_oldest_supported_grammar(self):
        for path in sources():
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            try:
                ast.parse(text, path, feature_version=(3, 7))
            except SyntaxError as exc:  # pragma: no cover - only on a regression
                self.fail(f"{path}:{exc.lineno}: {exc.msg}")


if __name__ == "__main__":
    unittest.main()
