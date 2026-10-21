"""Functional smoke test against a running server.

Focus: the routers that were refactored (contracts / tax_audit / admin) plus the
order-sensitive `pdf-pipeline/summary` vs `{document_id}/summary` pair.
"""
from __future__ import annotations

import uuid

import httpx

BASE = "http://127.0.0.1:8000"
# trust_env=False: Windows registry proxy settings would otherwise be applied to
# loopback requests and the server would disconnect.
c = httpx.Client(base_url=BASE, timeout=30.0, trust_env=False)

results: list[tuple[str, str, str]] = []


def rec(name: str, resp, note: str = "") -> None:
    results.append((name, str(resp.status_code), note or resp.text[:110]))


def login() -> str:
    user = f"smoke_{uuid.uuid4().hex[:8]}"
    pw = "SmokeTest123!"
    r = c.post("/api/auth/register", json={
        "username": user, "password": pw, "email": f"{user}@example.com"})
    if r.status_code not in (200, 201, 400):
        rec("register", r)
    r = c.post("/api/auth/login", json={"username": user, "password": pw})
    rec("login", r)
    if r.status_code != 200:
        return ""
    return r.json().get("access_token", "")


token = login()
H = {"Authorization": f"Bearer {token}"} if token else {}

# ---- public / basic -------------------------------------------------------
rec("GET /health", c.get("/health"))
rec("GET /openapi.json", c.get("/openapi.json"), "schema")
rec("GET /docs", c.get("/docs"))

# ---- auth enforcement -----------------------------------------------------
rec("GET /api/auth/me (no token)", c.get("/api/auth/me"))

# ---- refactored: contracts ------------------------------------------------
rec("GET /contracts/ui-config", c.get("/contracts/ui-config", headers=H))
rec("GET /contracts/pdf-pipeline/summary",
    c.get("/contracts/pdf-pipeline/summary", headers=H),
    "SHADOW TEST: must not be captured by {document_id}/summary")
rec("GET /contracts/nonexistent/summary",
    c.get("/contracts/nonexistent/summary", headers=H),
    "dynamic route should 404")
rec("GET /contracts/nonexistent/preview-manifest",
    c.get("/contracts/nonexistent/preview-manifest", headers=H))
rec("GET /contracts/nonexistent/preview",
    c.get("/contracts/nonexistent/preview", headers=H))
rec("GET /contracts/nonexistent/source",
    c.get("/contracts/nonexistent/source", headers=H))
rec("POST /contracts/nonexistent/report/export",
    c.post("/contracts/nonexistent/report/export", headers=H, json={}))
rec("GET /contracts/audit/x/progress", c.get("/contracts/audit/x/progress", headers=H))

# ---- refactored: tax_audit -----------------------------------------------
rec("GET /tax-audit/contracts/c1/clauses",
    c.get("/tax-audit/contracts/c1/clauses", headers=H))
rec("GET /tax-audit/contracts/c1/matches",
    c.get("/tax-audit/contracts/c1/matches", headers=H))
rec("GET /tax-audit/contracts/c1/trace",
    c.get("/tax-audit/contracts/c1/trace", headers=H))
rec("GET /tax-audit/issues/i1/trace",
    c.get("/tax-audit/issues/i1/trace", headers=H))
rec("GET /tax-audit/exports/e1", c.get("/tax-audit/exports/e1", headers=H))
rec("GET /tax-audit/cleanup/jobs", c.get("/tax-audit/cleanup/jobs", headers=H))
rec("GET /tax-audit/archive/records", c.get("/tax-audit/archive/records", headers=H))

# ---- refactored: admin (expect 403 for a normal user) --------------------
rec("GET /api/admin/prompts (non-admin)", c.get("/api/admin/prompts", headers=H),
    "expect 403")
rec("GET /api/admin/skills (non-admin)", c.get("/api/admin/skills", headers=H),
    "expect 403")

# ---- search / regulations ------------------------------------------------
rec("GET /api/regulations", c.get("/api/regulations", headers=H))

print(f"{'endpoint':<44}{'code':<6}note")
print("-" * 100)
for name, code, note in results:
    print(f"{name:<44}{code:<6}{note}")
