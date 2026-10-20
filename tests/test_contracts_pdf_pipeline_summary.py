from fastapi.testclient import TestClient
from uuid import uuid4

from app.main import app


client = TestClient(app)


def _login_headers():
    username = f"pdf_user_{uuid4().hex[:8]}"
    password = "adminpassword"
    login_resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    if login_resp.status_code != 200:
        client.post(
            "/api/auth/register",
            json={
                "username": username,
                "password": password,
                "email": f"{username}@example.com",
            },
        )
        login_resp = client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
    token = login_resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_get_pdf_pipeline_summary():
    resp = client.get("/contracts/pdf-pipeline/summary",
                      headers=_login_headers())
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["current_modules"]["version"] == "pdf_pipeline_v2"
    assert isinstance(payload["current_modules"]["modules"], list)
    assert "target_requirements" in payload
