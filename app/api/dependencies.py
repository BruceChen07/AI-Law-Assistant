from typing import Any
from fastapi import Depends, HTTPException, Request
from app.core.auth import decode_token, get_user_by_id


def get_current_user(request: Request) -> dict:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_id = payload.get("sub")
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=403, detail="Admin permission required")
    return current_user


def get_app_llm(request: Request) -> Any:
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        raise HTTPException(status_code=503, detail="llm service unavailable")
    return llm


def get_app_embedder(request: Request) -> Any:
    embedder = getattr(request.app.state, "embedder", None)
    if embedder is None:
        raise HTTPException(
            status_code=503, detail="embedding service unavailable")
    return embedder


def get_app_reranker(request: Request) -> Any:
    reranker = getattr(request.app.state, "reranker", None)
    if reranker is None:
        raise HTTPException(
            status_code=503, detail="reranker service unavailable")
    return reranker


def get_app_translator(request: Request) -> Any:
    translator = getattr(request.app.state, "translator", None)
    if translator is None:
        raise HTTPException(
            status_code=503, detail="translation service unavailable")
    return translator
