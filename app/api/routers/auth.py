from datetime import timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, EmailStr
from app.core.auth import (
    create_user, authenticate_user, create_access_token,
    create_session, delete_session, get_user_by_id, decode_token
)
from app.core.database import get_conn
from app.api.dependencies import get_current_user, require_admin

router = APIRouter(prefix="/api/auth", tags=["auth"])


def build_router():
    return router


class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    role: str


@router.post("/register", response_model=UserResponse)
def register(req: RegisterRequest):
    from app.core.auth import get_user_by_username

    existing = get_user_by_username(req.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    user_id = create_user(req.username, req.email, req.password)
    user = get_user_by_id(user_id)

    return UserResponse(
        id=user["id"],
        username=user["username"],
        email=user["email"],
        role=user["role"]
    )


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, request: Request):
    user = authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(
            status_code=401, detail="Invalid username or password")

    if not user.get("is_active", 1):
        raise HTTPException(status_code=403, detail="Account is disabled")

    token_data = {"sub": user["id"],
                  "username": user["username"], "role": user["role"]}
    access_token = create_access_token(token_data)

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    create_session(user["id"], access_token, ip_address, user_agent)

    return TokenResponse(
        access_token=access_token,
        user={
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "role": user["role"]
        }
    )


@router.post("/logout")
def logout(request: Request, current_user: dict = Depends(get_current_user)):
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        delete_session(token)
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserResponse)
def get_me(current_user: dict = Depends(get_current_user)):
    return UserResponse(
        id=current_user["id"],
        username=current_user["username"],
        email=current_user.get("email", ""),
        role=current_user["role"]
    )
