#!/usr/bin/env python3
"""Code-complexity audit for AI-Law-Assistant.

Combines three signals into one refactor-priority report:

  1. Per-function cyclomatic complexity (mccabe, via `ruff --select C901`).
  2. Per-function structural metrics (length, nesting depth, parameter count).
  3. Per-module coupling (fan-in / fan-out from the internal import graph).

Usage:
    app/.venv/Scripts/python.exe scripts/audit_complexity.py
    app/.venv/Scripts/python.exe scripts/audit_complexity.py --json out.json
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["app", "bin", "scripts", "tests"]
EXCLUDE = {
    ".venv", "__pycache__", "node_modules", ".git", "third_party",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "storybook-static", "dist",
}

COMPOUND = (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.AsyncFor,
            ast.AsyncWith, ast.Match)
FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


# --------------------------------------------------------------------- helpers
def py_files() -> list[Path]:
    out = []
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if not any(part in EXCLUDE for part in p.parts):
                out.append(p)
    return sorted(out)


def sloc(text: str) -> int:
    n = 0
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            n += 1
    return n


def max_nesting(node: ast.AST, depth: int = 0) -> int:
    """Deepest chain of nested compound statements below `node`."""
    best = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue  # nested defs are separate units
        d = depth + 1 if isinstance(child, COMPOUND) else depth
        best = max(best, max_nesting(child, d))
    return best


def func_len(node) -> int:
    return (getattr(node, "end_lineno", node.lineno) or node.lineno) - node.lineno + 1


def has_docstring(node) -> bool:
    return ast.get_docstring(node) is not None


# ------------------------------------------------------- ruff mccabe (C901)
def ruff_complexity() -> dict[tuple[str, str], int]:
    """{(relpath, func_name): mccabe_complexity}"""
    ruff = ROOT / "app" / ".venv" / "Scripts" / "ruff.exe"
    if not ruff.exists():
        ruff = ROOT / "app" / ".venv" / "bin" / "ruff"
    if not ruff.exists():
        print("!! ruff not found; skipping mccabe complexity", file=sys.stderr)
        return {}
    tmp = Path(tempfile.gettempdir()) / "ruff_cx_audit.json"
    cmd = [
        str(ruff), "check", *SCAN_DIRS,
        "--select", "C901",
        "--config", "lint.mccabe.max-complexity=1",
        "--output-format", "json", "--output-file", str(tmp),
    ]
    subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if not tmp.exists():
        return {}
    raw = json.loads(tmp.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], int] = {}
    for item in raw:
        msg = item.get("message", "")
        m = re.search(r"is too complex \((\d+)", msg)
        if not m:
            continue
        rel = str(Path(item["filename"]).resolve().relative_to(ROOT))
        # message form: `name` is too complex (N > 1)
        fn = re.search(r"`([^`]+)`", msg)
        out[(rel, fn.group(1) if fn else "?")] = int(m.group(1))
    return out


# ------------------------------------------------------------- import graph
def module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def build_graph(files: list[Path]):
    mods = {module_name(p): p for p in files}
    fan_in: Counter[str] = Counter()
    fan_out: Counter[str] = Counter()
    edges = defaultdict(set)
    for p in files:
        src_mod = module_name(p)
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                targets = [node.module]
            for t in targets:
                # resolve against the longest known module prefix
                parts = t.split(".")
                for i in range(len(parts), 0, -1):
                    cand = ".".join(parts[:i])
                    if cand in mods and cand != src_mod:
                        fan_out[src_mod] += 1
                        fan_in[cand] += 1
                        edges[src_mod].add(cand)
                        break
    return fan_in, fan_out, edges, mods


# -------------------------------------------------------------------- analyse
def analyse() -> dict:
    files = py_files()
    cx = ruff_complexity()
    fan_in, fan_out, edges, mods = build_graph(files)

    funcs: list[dict] = []
    modules: list[dict] = []

    for p in files:
        rel = str(p.relative_to(ROOT))
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.count("\n") + 1
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            modules.append({"path": rel, "error": str(exc)})
            continue

        comments = sum(1 for ln in text.splitlines() if ln.strip().startswith("#"))
        mod_funcs = [
            n for n in ast.walk(tree) if isinstance(n, FUNC_NODES)
        ]
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]

        for fn in mod_funcs:
            name = fn.name
            fname = f"{getattr(fn.parent, 'name', '')}.{name}" if hasattr(fn, "parent") else name
            # mccabe key uses the bare or qualified name as ruff prints it
            comp = cx.get((rel, name), cx.get((rel, fname), 1))
            funcs.append({
                "module": rel,
                "name": name,
                "line": fn.lineno,
                "length": func_len(fn),
                "args": len(fn.args.args) + len(fn.args.kwonlyargs) + len(fn.args.posonlyargs),
                "nesting": max_nesting(fn),
                "complexity": comp,
                "docstring": has_docstring(fn),
            })

        god = [{"name": c.name, "line": c.lineno,
                "methods": sum(1 for x in c.body if isinstance(x, FUNC_NODES))}
               for c in classes]
        modules.append({
            "path": rel,
            "lines": lines,
            "sloc": sloc(text),
            "comments": comments,
            "functions": len(mod_funcs),
            "classes": len(classes),
            "max_func_len": max((func_len(f) for f in mod_funcs), default=0),
            "max_complexity": max(
                (cx.get((rel, f.name), 1) for f in mod_funcs), default=0),
            "fan_in": fan_in.get(module_name(p), 0),
            "fan_out": fan_out.get(module_name(p), 0),
            "god_classes": [g for g in god if g["methods"] >= 15],
        })

    # ---------------------------------------------------------- refactor score
    for m in modules:
        if "error" in m:
            m["score"] = 0
            continue
        m["score"] = round(
            m["lines"] / 400                      # sheer size
            + m["max_complexity"] / 6             # worst function inside
            + m["fan_out"] / 12                   # coupling outward
            + sum(g["methods"] for g in m["god_classes"]) / 30
            + m["max_func_len"] / 150, 2)

    return {
        "files_scanned": len(files),
        "total_lines": sum(m.get("lines", 0) for m in modules),
        "total_sloc": sum(m.get("sloc", 0) for m in modules),
        "total_functions": len(funcs),
        "modules": sorted(modules, key=lambda m: -m.get("score", 0)),
        "functions": funcs,
        "edges": {k: sorted(v) for k, v in edges.items()},
        "fan_in_top": fan_in.most_common(20),
    }


# --------------------------------------------------------------------- report
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    r = analyse()
    mods, funcs = r["modules"], r["functions"]

    def table(title: str, rows: list[list[str]], widths: list[int]) -> None:
        print(f"\n{title}")
        print("-" * (sum(widths) + 2 * len(widths)))
        for row in rows:
            print("  " + "  ".join(str(c).ljust(w) for c, w in zip(row, widths)))

    print("=" * 96)
    print("CODE COMPLEXITY AUDIT")
    print("=" * 96)
    print(f"files            : {r['files_scanned']}")
    print(f"total lines      : {r['total_lines']:,}   (sloc {r['total_sloc']:,})")
    print(f"functions        : {r['total_functions']:,}")

    over = lambda th: sum(1 for f in funcs if f["length"] > th)
    cxo = lambda th: sum(1 for f in funcs if f["complexity"] > th)
    print(f"functions > 50 lines   : {over(50)}")
    print(f"functions > 100 lines  : {over(100)}")
    print(f"functions > 200 lines  : {over(200)}")
    print(f"complexity > 10        : {cxo(10)}")
    print(f"complexity > 20        : {cxo(20)}")
    print(f"complexity > 40        : {cxo(40)}")
    print(f"nesting depth > 4      : {sum(1 for f in funcs if f['nesting'] > 4)}")
    print(f"args > 6               : {sum(1 for f in funcs if f['args'] > 6)}")

    # ---- oversized files
    big = [m for m in mods if m.get("lines", 0) >= 500]
    table(f"FILES >= 500 LINES  ({len(big)})",
          [["lines", "sloc", "funcs", "cls", "maxFn", "maxCx", "fanOut", "path"]] +
          [["%d" % m["lines"], "%d" % m["sloc"], "%d" % m["functions"], "%d" % m["classes"],
            "%d" % m["max_func_len"], "%d" % m["max_complexity"], "%d" % m["fan_out"], m["path"]]
           for m in sorted(big, key=lambda x: -x["lines"])],
          [7, 6, 6, 4, 6, 6, 7, 60])

    # ---- worst functions by complexity
    table("TOP 25 FUNCTIONS BY CYCLOMATIC COMPLEXITY",
          [["cx", "lines", "nest", "args", "module:name"]] +
          [["%d" % f["complexity"], "%d" % f["length"], "%d" % f["nesting"], "%d" % f["args"],
            f"{f['module']}:{f['name']}"] for f in
           sorted(funcs, key=lambda x: -x["complexity"])[:25]],
          [5, 6, 5, 5, 70])

    # ---- longest functions
    table("TOP 20 LONGEST FUNCTIONS",
          [["lines", "cx", "nest", "module:name"]] +
          [["%d" % f["length"], "%d" % f["complexity"], "%d" % f["nesting"],
            f"{f['module']}:{f['name']}"] for f in
           sorted(funcs, key=lambda x: -x["length"])[:20]],
          [6, 5, 5, 70])

    # ---- god classes
    gods = [(m["path"], g) for m in mods for g in m.get("god_classes", [])]
    print(f"\nGOD CLASSES (>=15 methods)  ({len(gods)})")
    print("-" * 96)
    for path, g in sorted(gods, key=lambda x: -x[1]["methods"]):
        print(f"  {g['methods']:3d} methods  {path}:{g['name']}")

    # ---- coupling hubs
    table("TOP 15 MOST-IMPORTED INTERNAL MODULES (refactor blast radius)",
          [["fanIn", "lines", "module"]] +
          [["%d" % c, "%d" % next((m["lines"] for m in mods
                                  if m["path"].replace("\\", "/").replace("/", ".")
                                  .removesuffix(".py") == name.replace(".", "/")
                                  or m["path"].replace("\\", "/").endswith(name.replace(".", "/") + ".py")), 0),
            name] for name, c in r["fan_in_top"][:15]],
          [6, 7, 70])

    # ---- refactor priority
    table("REFACTOR PRIORITY (composite score)",
          [["score", "lines", "maxCx", "maxFn", "fanOut", "path"]] +
          [["%.2f" % m["score"], "%d" % m["lines"], "%d" % m["max_complexity"],
            "%d" % m["max_func_len"], "%d" % m["fan_out"], m["path"]]
           for m in mods[:20] if "error" not in m],
          [6, 7, 6, 6, 7, 62])

    if args.json:
        Path(args.json).write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\njson -> {args.json}")


if __name__ == "__main__":
    main()
