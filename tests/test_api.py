from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_list_regulations():
    response = client.get("/regulations")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_search_empty():
    response = client.post("/regulations/search", json={"query": ""})
    assert response.status_code == 200
    assert response.json() == []


def test_embedding_info():
    response = client.get("/embeddings/info")
    assert response.status_code == 200
    assert "models" in response.json()
