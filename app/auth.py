from datetime import datetime, timedelta, timezone
import os
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import Column, DateTime, Integer, String, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base, get_db

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "transit-pulse-ec-secret-key-change-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 12

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

router = APIRouter(prefix="/auth", tags=["Authentication"])


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(120), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False)
    access_code_used = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class RegisterRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)
    role: str = Field(..., pattern="^(commuter|driver|admin)$")
    access_code: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=72)
    role: str = Field(..., pattern="^(commuter|driver|admin)$")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


def hash_password(password: str) -> str:
    pw_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pw_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    pw_bytes = plain_password.encode("utf-8")[:72]
    return bcrypt.checkpw(pw_bytes, hashed_password.encode("utf-8"))


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: Optional[str] = payload.get("sub")
        if not email:
            raise credentials_error
    except jwt.PyJWTError:
        raise credentials_error

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        raise credentials_error
    return user


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register_user(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    if payload.role == "driver" and payload.access_code != "EC-DRV-2026":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Fleet Driver Authorization Code.",
        )
    if payload.role == "admin" and payload.access_code != "BCM-OPS-2026":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Management Security Clearance Code.",
        )

    normalized_email = payload.email.lower().strip()
    existing = await db.execute(select(User).where(User.email == normalized_email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    new_user = User(
        full_name=payload.full_name.strip(),
        email=normalized_email,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        access_code_used=payload.access_code,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    token = create_access_token({"sub": new_user.email, "role": new_user.role, "uid": new_user.id})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": new_user.id,
            "full_name": new_user.full_name,
            "email": new_user.email,
            "role": new_user.role,
        },
    }


@router.post("/login", response_model=TokenResponse)
async def login_user(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    normalized_email = payload.email.lower().strip()
    result = await db.execute(select(User).where(User.email == normalized_email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if user.role != payload.role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account belongs to '{user.role}', not '{payload.role}'.",
        )

    token = create_access_token({"sub": user.email, "role": user.role, "uid": user.id})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "role": user.role,
        },
    }


@router.get("/me")
async def read_current_user(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "full_name": current_user.full_name,
        "email": current_user.email,
        "role": current_user.role,
    }