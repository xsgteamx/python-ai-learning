import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User


TOKEN_EXPIRE_SECONDS = 60 * 60 * 12
PASSWORD_ITERATIONS = 120_000


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    if not password_hash:
        return False
    try:
        algorithm, iterations, salt, expected = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            _b64decode(salt),
            int(iterations)
        )
        return hmac.compare_digest(_b64encode(digest), expected)
    except Exception:
        return False


def create_access_token(user: User) -> str:
    payload = {
        "user_id": user.id,
        "username": user.username,
        "role": user.role,
        "exp": int(time.time()) + TOKEN_EXPIRE_SECONDS,
        # token 版本号：改密后递增，使旧 Token 失效
        "v": int(user.token_version or 0),
    }
    payload_text = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(settings.SECRET_KEY.encode(), payload_text.encode(), hashlib.sha256).digest()
    return f"{payload_text}.{_b64encode(signature)}"


def parse_access_token(token: str) -> dict:
    try:
        payload_text, signature_text = token.split(".", 1)
        expected_signature = hmac.new(settings.SECRET_KEY.encode(), payload_text.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64encode(expected_signature), signature_text):
            raise ValueError("签名无效")
        payload = json.loads(_b64decode(payload_text))
        if int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("登录已过期")
        return payload
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效，请重新登录"
        )


def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    payload = parse_access_token(authorization.removeprefix("Bearer ").strip())
    user = db.query(User).filter(User.id == payload.get("user_id")).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用")
    # 校验 token_version：改密后旧 Token 立即失效
    if int(payload.get("v", 0)) != int(user.token_version or 0):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有管理员可以执行该操作")
    return current_user
