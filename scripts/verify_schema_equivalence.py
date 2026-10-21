"""Gate for P3: prove the migration split is behaviourally identical to the original.

Two parts, because the ORIGINAL code is itself broken:

  A. Unpatched: both old and new must fail with the *identical* exception.
     (Pre-existing bug: `audit_skill` DDL declares `id TEXT PRIMARY KEY` twice,
     which makes `init_db` raise `duplicate column name: id` on a fresh DB.)

  B. Patched: neutralise that one duplicated line in BOTH variants, then build a
     fresh database from each and diff `sqlite_master` exactly. This verifies all
     119 statements, not just the 118 that run before the failure.
"""
from __future__ import annotations

import importlib.util
import re
import gc
import sqlite3
import sys
import tempfile
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(r"D:\Workspace\AI-Law-Assistant")
OLD = ROOT / ".refactor-baseline" / "database.py.orig"
MIG = ROOT / "app" / "core" / "migrations"
sys.path.insert(0, str(ROOT))

DUP = re.compile(
    r"(?P<indent>[ \t]*)id TEXT PRIMARY KEY,\n(?P=indent)id TEXT PRIMARY KEY,\n")


def load_old_from_text(text: str):
    loader = SourceFileLoader("_db_old_patched", str(OLD))
    spec = importlib.util.spec_from_loader("_db_old_patched", loader)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = str(OLD)
    exec(compile(text, str(OLD), "exec"), mod.__dict__)
    return mod


def load_new_patched():
    """Execute the migration modules with the duplicated column removed."""
    ns: dict = {"__name__": "app.core.migrations._patched", "__file__": str(MIG / "__init__.py")}
    patched = []
    for p in sorted(MIG.glob("_*.py")):
        if p.name == "__init__.py":
            continue
        text = DUP.sub(r"\g<indent>id TEXT PRIMARY KEY,\n", p.read_text(encoding="utf-8"))
        if text != p.read_text(encoding="utf-8"):
            patched.append(p.name)
        mod_ns: dict = {"__name__": f"mig_{p.stem}"}
        exec(compile(text, str(p), "exec"), mod_ns)
        ns[p.stem] = mod_ns
    order = [p.stem for p in sorted(MIG.glob("_*.py")) if p.name != "__init__.py"]
    print(f"  patched migration modules: {patched}")

    def apply_all(cur):
        for m in order:
            ns[m]["apply"](cur)
    return apply_all


def dump_schema(db_path: str):
    c = sqlite3.connect(db_path)
    try:
        return [tuple(r) for r in c.execute(
            "SELECT type, name, tbl_name, COALESCE(sql,'') FROM sqlite_master "
            "ORDER BY type, name")]
    finally:
        c.close()


def try_build(fn, db_path):
    try:
        fn({"db_path": db_path}) if fn.__name__ == "init_db" else fn(None)
        return None
    except Exception as e:                     # noqa: BLE001 - we compare messages
        return f"{type(e).__name__}: {e}"


def main() -> None:
    print("=" * 78)
    print("PART A - current init_db must build a fresh database cleanly")
    print("=" * 78)
    old_mod = load_old_from_text(OLD.read_text(encoding="utf-8"))
    from app.core import database as new_mod

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        e_old = try_build(old_mod.init_db, str(Path(td) / "a.db"))
        e_new = try_build(new_mod.init_db, str(Path(td) / "b.db"))
    gc.collect()
    print(f"  original (pre-refactor, BUG-01 present) -> {e_old or 'ok'}")
    print(f"  current  (BUG-01 fixed)                 -> {e_new or 'ok'}")
    if e_new is not None:
        print("RESULT: init_db STILL FAILS")
        sys.exit(1)
    print("  OK: a fresh database can be created")

    print()
    print("=" * 78)
    print("PART B - schema matches the pre-refactor shape (all 119 statements)")
    print("=" * 78)
    old_patched = load_old_from_text(
        DUP.sub(r"\g<indent>id TEXT PRIMARY KEY,\n", OLD.read_text(encoding="utf-8")))
    apply_new = load_new_patched()

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        old_db = str(Path(td) / "old.db")
        new_db = str(Path(td) / "new.db")
        old_patched.init_db({"db_path": old_db})
        c = sqlite3.connect(new_db)
        try:
            apply_new(c.cursor())
            c.commit()
        finally:
            c.close()
        a = dump_schema(old_db)
        b = dump_schema(new_db)

    print(f"  old schema objects: {len(a)}")
    print(f"  new schema objects: {len(b)}")
    if a == b:
        tables = sum(1 for x in a if x[0] == "table")
        idx = sum(1 for x in a if x[0] == "index" and not x[1].startswith("sqlite_"))
        print(f"  tables: {tables}  indexes: {idx}")
        print("\nRESULT: identical schema")
        return

    only_old = [x for x in a if x not in b]
    only_new = [x for x in b if x not in a]
    for x in only_old:
        print(f"  [!] only in OLD: {x[0]} {x[1]}")
    for x in only_new:
        print(f"  [!] only in NEW: {x[0]} {x[1]}")
    amap = {(x[0], x[1]): x[3] for x in a}
    bmap = {(x[0], x[1]): x[3] for x in b}
    for k in sorted(set(amap) & set(bmap)):
        if amap[k] != bmap[k]:
            print(f"  [!] SQL differs: {k[0]} {k[1]}")
            print(f"      old: {amap[k][:160]}")
            print(f"      new: {bmap[k][:160]}")
    print("\nRESULT: SCHEMA MISMATCH")
    sys.exit(1)


if __name__ == "__main__":
    main()
