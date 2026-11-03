#!/usr/bin/env python3
"""Audit that declared dependencies actually cover the source imports.

Run with the project virtualenv so installed metadata is visible:

    app/.venv/Scripts/python.exe scripts/audit_dependencies.py   # Windows
    app/.venv/bin/python scripts/audit_dependencies.py           # POSIX

Reports four classes of problem:
  A. imported but not declared in app/requirements.txt (installed locally, so it
     "works on this machine" but a fresh server may not have it)
  B. imported but neither declared nor installed
  C. declared but never imported (dead weight or de-facto transitive pin)
  D. installed version in the venv does not satisfy the declared specifier
     (the venv has drifted from the manifest)
"""
from __future__ import annotations

import ast
import sys
from collections import defaultdict
from importlib.metadata import distributions, packages_distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ = ROOT / "app" / "requirements.txt"

SCAN = {"runtime": ["app"], "ops": ["bin", "scripts"], "dev": ["tests"]}
EXCLUDE_PARTS = {
    ".venv", "__pycache__", "node_modules", ".git", "storybook-static",
    "dist", "third_party", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}


# this audit tool's own imports must not be counted as product dependencies
SELF = {"audit_dependencies.py"}


def iter_py(roots):
    for r in roots:
        base = ROOT / r
        if base.exists():
            for p in base.rglob("*.py"):
                if any(part in EXCLUDE_PARTS for part in p.parts):
                    continue
                if p.name in SELF:
                    continue
                yield p


def top_imports(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        print(f"  !! syntax error {path}: {exc}", file=sys.stderr)
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names.add(node.module.split(".")[0])
    return names


def canon(name: str) -> str:
    import re
    return re.sub(r"[-_.]+", "-", name).lower()


# ---------------------------------------------------------------- local names
local = set()
for entry in ROOT.iterdir():
    if entry.is_dir() and (entry / "__init__.py").exists():
        local.add(entry.name)
    elif entry.suffix == ".py":
        local.add(entry.stem)
for r in ("app", "bin", "scripts", "tests"):
    base = ROOT / r
    local.add(r)
    if base.exists():
        local.update(p.stem for p in base.glob("*.py"))

import_use: dict[str, set[tuple[str, str]]] = defaultdict(set)
for scope, roots in SCAN.items():
    for p in iter_py(roots):
        for name in top_imports(p):
            import_use[name].add((scope, str(p.relative_to(ROOT))))

stdlib = set(sys.stdlib_module_names)
third_party = {
    m: u for m, u in import_use.items()
    if m not in stdlib and m not in local and not m.startswith("_")
}

# ------------------------------------------------------------ installed state
pkg_to_dists = packages_distributions()
installed = {}
for d in distributions():
    name = (d.metadata or {}).get("Name")
    if name:
        installed[canon(name)] = d.version


def dists_for(mod: str) -> list[str]:
    return [canon(x) for x in pkg_to_dists.get(mod, [])]


# ------------------------------------------------------------- manifest parse
declared: dict[str, tuple[str, str]] = {}
if REQ.exists():
    import re
    for raw in REQ.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9._-]+)\s*(.*)$", line) if line else None
        if m:
            declared[canon(m.group(1))] = (m.group(1), m.group(2).strip())

# ------------------------------------------------------------------- analyse
A, B, C, D = [], [], [], []

for mod in sorted(third_party):
    dists = dists_for(mod)
    scopes = sorted({s for s, _ in third_party[mod]})
    files = sorted({f for _, f in third_party[mod]})
    if not dists:
        B.append((mod, scopes, files[:5]))
        continue
    undeclared = [d for d in dists if d not in declared]
    if undeclared:
        A.append((mod, [(d, installed.get(d)) for d in undeclared], scopes, len(files),
                  files[:3]))

for dcanon, (orig, spec) in sorted(declared.items()):
    used = any(
        dcanon in [canon(x) for x in dists] and mod in third_party
        for mod, dists in pkg_to_dists.items()
    )
    if not used:
        C.append((orig, spec, installed.get(dcanon)))

try:
    from packaging.specifiers import SpecifierSet
except ImportError:
    SpecifierSet = None  # type: ignore

for dcanon, (orig, spec) in sorted(declared.items()):
    iv = installed.get(dcanon)
    if iv is None:
        D.append((orig, spec, None, "NOT INSTALLED"))
    elif SpecifierSet is not None and spec:
        try:
            if not SpecifierSet(spec).contains(iv, prereleases=True):
                D.append((orig, spec, iv, "SPEC MISMATCH"))
        except Exception:
            pass

# --------------------------------------------------------------------- report
def section(title: str, rows: list) -> None:
    print(f"\n{'=' * 78}\n{title}  ({len(rows)})\n{'=' * 78}")
    if not rows:
        print("  none")
    for r in rows:
        print("  ", r)


print("=" * 78)
print("DEPENDENCY AUDIT")
print("=" * 78)
print(f"manifest         : {REQ}")
print(f"declared         : {len(declared)}")
print(f"installed dists  : {len(installed)}")
print(f"3rd-party imports: {len(third_party)}")

section("A. IMPORTED BUT NOT DECLARED (present locally only)", A)
section("B. IMPORTED, NOT DECLARED, NOT INSTALLED", B)
section("C. DECLARED BUT NEVER IMPORTED", C)
section("D. INSTALLED VERSION VIOLATES DECLARED SPECIFIER", D)

print("\nNote: A/B blocks deployment on a clean server; D means this venv has")
print("drifted from the manifest, so the server gets different versions.")
