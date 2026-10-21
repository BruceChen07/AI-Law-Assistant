#!/usr/bin/env python3
"""Snapshot the FastAPI route surface so a refactor can be proven behaviour-neutral.

Router splitting (closure -> module-level handlers) risks silently losing or
renaming a route, changing a response_model, or dropping a dependency. This tool
captures the full surface as JSON and diffs two snapshots.

Usage:
    # before the refactor
    app/.venv/Scripts/python.exe scripts/snapshot_api_surface.py --out before.json
    # ... refactor ...
    app/.venv/Scripts/python.exe scripts/snapshot_api_surface.py --out after.json
    app/.venv/Scripts/python.exe scripts/snapshot_api_surface.py --compare before.json after.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Factories to snapshot: (dotted module, callable name, needs_cfg)
FACTORIES = [
    ("app.api.routers.contracts", "build_router", True),
    ("app.api.routers.tax_audit", "build_router", True),
    ("app.api.routers.regulations", "build_router", True),
    ("app.api.routers.auth", "build_router", False),
    ("app.api.routers.health", "build_router", False),
    ("app.api.routers.embedding", "build_router", False),
    ("app.api.routers.admin", "router", False),
]

CFG_STUB = {
    "data_dir": str(ROOT / "data"),
    "files_dir": str(ROOT / "data" / "files"),
    "static_dir": str(ROOT / "web" / "dist"),
    "db_path": str(ROOT / "data" / "app.db"),
    "log_dir": str(ROOT / "logs"),
}


def norm_param(p) -> dict:
    """Describe a route parameter in a stable, comparable way."""
    d = {
        "name": getattr(p, "name", ""),
        "in": type(p).__name__,
    }
    # FastAPI request params carry where the value comes from
    field = getattr(p, "field_info", None)
    if field is not None:
        alias = getattr(field, "alias", None)
        if alias:
            d["alias"] = alias
    return d


def snapshot() -> dict:
    import importlib

    out = {}
    for module_path, attr, needs_cfg in FACTORIES:
        try:
            mod = importlib.import_module(module_path)
        except Exception as exc:
            out[module_path] = {"error": f"import failed: {exc}"}
            continue
        factory = getattr(mod, attr)
        try:
            # `attr` may be either a factory (build_router) or an already-built
            # APIRouter instance exported at module level (e.g. admin.router).
            if hasattr(factory, "routes") and not callable(getattr(factory, "__wrapped__", None)):
                from fastapi import APIRouter
                if isinstance(factory, APIRouter):
                    router = factory
                else:
                    router = factory(dict(CFG_STUB)) if needs_cfg else factory()
            else:
                router = factory(dict(CFG_STUB)) if needs_cfg else factory()
        except Exception as exc:
            out[module_path] = {"error": f"factory failed: {exc}"}
            continue

        routes = []
        for idx, r in enumerate(getattr(router, "routes", [])):
            routes.append({
                "order": idx,
                "path": getattr(r, "path", ""),
                "methods": sorted(getattr(r, "methods", []) or []),
                "name": getattr(r, "name", ""),
                "status_code": getattr(r, "status_code", None),
                "response_model": str(getattr(r, "response_model", None)),
                "params": [norm_param(p) for p in
                           getattr(getattr(r, "dependant", None), "dependencies", []) or []],
                "tags": sorted(getattr(r, "tags", []) or []),
            })
        # keep registration order: FastAPI matches routes in the order added
        out[module_path] = {"route_count": len(routes), "routes": routes}
    return out


_CLASS_RE = re.compile(r"<class '([^']+)'>")


def _rm_name(value) -> str:
    """Compare response models by class NAME, not by import path.

    Moving a Pydantic model into a `schemas` submodule changes `__module__` but
    not the API contract (OpenAPI component names come from `__name__`).
    """
    m = _CLASS_RE.search(str(value or ""))
    if m:
        return m.group(1).rsplit(".", 1)[-1]
    return str(value or "")


def compare(a: dict, b: dict, allow_added_deps: set[str] | None = None) -> int:
    problems = 0
    allow_added_deps = allow_added_deps or set()

    def key(r):
        return (r["path"], tuple(r["methods"]))

    for module in sorted(set(a) | set(b)):
        A, B = a.get(module, {}), b.get(module, {})
        if "error" in A or "error" in B:
            if A != B:
                print(f"[!] {module}: error state changed\n    before={A.get('error')}\n    after ={B.get('error')}")
                problems += 1
            continue
        ra = {key(r): r for r in A.get("routes", [])}
        rb = {key(r): r for r in B.get("routes", [])}
        missing = sorted(set(ra) - set(rb))
        added = sorted(set(rb) - set(ra))
        for k in missing:
            print(f"[!] {module}: ROUTE LOST  {k[1]} {k[0]}")
            problems += 1
        for k in added:
            print(f"[!] {module}: route added  {k[1]} {k[0]}")
            problems += 1
        for k in sorted(set(ra) & set(rb)):
            for field in ("name", "status_code"):
                if ra[k][field] != rb[k][field]:
                    print(f"[!] {module}: {k[0]} {field} changed: "
                          f"{ra[k][field]!r} -> {rb[k][field]!r}")
                    problems += 1
            if _rm_name(ra[k]["response_model"]) != _rm_name(rb[k]["response_model"]):
                print(f"[!] {module}: {k[0]} response_model changed: "
                      f"{ra[k]['response_model']!r} -> {rb[k]['response_model']!r}")
                problems += 1
            pa = [(p["name"], p["in"]) for p in ra[k]["params"]]
            pb = [(p["name"], p["in"]) for p in rb[k]["params"]]
            if pa != pb:
                # documented, intentional change: closure variable -> injected dep
                if allow_added_deps:
                    trimmed = [p for p in pb
                               if not (p not in pa and p[0] in allow_added_deps)]
                    if trimmed == pa:
                        continue
                print(f"[!] {module}: {k[0]} dependencies changed\n"
                      f"    before={pa}\n    after ={pb}")
                problems += 1
        if not (missing or added):
            print(f"[ok] {module}: {len(ra)} routes unchanged")

    # ---- shadowing check ----------------------------------------------
    # The invariant is NOT "registration order is byte-identical"; it is
    # "no route becomes unreachable". A route is shadowed when an EARLIER route
    # has a dynamic segment exactly where a later route has a literal, with the
    # same segment count and at least one shared HTTP method.
    def segs(path: str) -> list[str]:
        return [s for s in path.split("/") if s != ""]

    def shadow_pairs(routes: list[dict]) -> list[tuple[str, str]]:
        out = []
        for i, ri in enumerate(routes):
            si = segs(ri["path"])
            mi = set(ri["methods"])
            for rj in routes[i + 1:]:
                if not (mi & set(rj["methods"])):
                    continue
                sj = segs(rj["path"])
                if len(si) != len(sj):
                    continue
                dynamic_vs_literal = any(
                    x.startswith("{") and not y.startswith("{")
                    for x, y in zip(si, sj))
                if dynamic_vs_literal and all(
                        x.startswith("{") or x == y for x, y in zip(si, sj)):
                    out.append((ri["path"], rj["path"]))
        return out

    for module in sorted(set(a) & set(b)):
        A, B = a.get(module, {}), b.get(module, {})
        if "error" in A or "error" in B:
            continue
        seq_a = [(r["path"], tuple(r["methods"])) for r in A.get("routes", [])]
        seq_b = [(r["path"], tuple(r["methods"])) for r in B.get("routes", [])]
        if seq_a == seq_b:
            continue
        if set(seq_a) != set(seq_b):
            continue   # already reported as lost/added above
        shadows_before = shadow_pairs(A.get("routes", []))
        shadows_after = shadow_pairs(B.get("routes", []))
        new_shadows = [p for p in shadows_after if p not in shadows_before]
        print(f"[i] {module}: registration order changed")
        for i, (x, y) in enumerate(zip(seq_a, seq_b)):
            if x != y:
                print(f"    first diff at index {i}: {x[1]} {x[0]}  ->  {y[1]} {y[0]}")
                break
        if new_shadows:
            print(f"[!] {module}: {len(new_shadows)} NEW shadowed route pair(s):")
            for a_path, b_path in new_shadows:
                print(f"    {a_path}  shadows  {b_path}")
            problems += 1
        else:
            print(f"    ok: no route became unreachable (0 new shadowed pairs)")

    print()
    print("RESULT:", "identical route surface" if problems == 0
          else f"{problems} difference(s) - review before merging")
    return 0 if problems == 0 else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    ap.add_argument("--allow-added-dep", default="",
                    help="comma separated dependency names that may legitimately be "
                         "added (e.g. 'cfg' when a closure variable becomes Depends())")
    args = ap.parse_args()

    if args.compare:
        a = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
        b = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        allow = {s.strip() for s in args.allow_added_dep.split(",") if s.strip()}
        sys.exit(compare(a, b, allow))

    snap = snapshot()
    total = sum(v.get("route_count", 0) for v in snap.values())
    print(f"captured {total} routes from {len(FACTORIES)} routers")
    for k, v in snap.items():
        if "error" in v:
            print(f"  [!] {k}: {v['error']}")
        else:
            print(f"  {k}: {v['route_count']} routes")
    if args.out:
        Path(args.out).write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nwritten -> {args.out}")


if __name__ == "__main__":
    main()
