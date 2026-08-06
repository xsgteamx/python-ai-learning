from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
import hmac
import time

from app.database import get_db
from app.models import User
from app.schemas import AuthResponse, LoginRequest, UserResponse
from app.security import create_access_token, get_current_user, hash_password, verify_password
from app.services.audit import write_audit_log, ACTION_LOGIN, ACTION_UPDATE, RESOURCE_USER
from app.services.transport_crypto import decrypt_transport_secret, get_public_key_pem

router = APIRouter(prefix="/api/auth", tags=["登录认证"])

LOGIN_WINDOW_SECONDS = 60
LOGIN_MAX_FAILURES = 5
_login_failures: dict[str, list[float]] = {}


def _client_key(req: Request, username: str) -> str:
    ip = req.client.host if req.client else "unknown"
    return f"{ip}:{username}"


def _check_login_rate_limit(req: Request, username: str) -> None:
    key = _client_key(req, username)
    now = time.time()
    failures = [ts for ts in _login_failures.get(key, []) if now - ts < LOGIN_WINDOW_SECONDS]
    _login_failures[key] = failures
    if len(failures) >= LOGIN_MAX_FAILURES:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="登录失败次数过多，请稍后再试")


def _record_login_failure(req: Request, username: str) -> None:
    key = _client_key(req, username)
    _login_failures.setdefault(key, []).append(time.time())


def _clear_login_failures(req: Request, username: str) -> None:
    _login_failures.pop(_client_key(req, username), None)


@router.get("/public-key")
def get_login_public_key():
    """获取登录/重置密码使用的 RSA 公钥"""
    return {"algorithm": "RSA-OAEP-256", "public_key": get_public_key_pem()}


@router.post("/login", response_model=AuthResponse)
def login(request: LoginRequest, req: Request, db: Session = Depends(get_db)):
    """账号密码登录"""
    _check_login_rate_limit(req, request.username)
    try:
        password_digest = decrypt_transport_secret(request.password)
    except Exception:
        _record_login_failure(req, request.username)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="密码传输格式不安全或已失效，请刷新页面后重试")

    user = db.query(User).filter(User.username == request.username).first()
    if not user or not user.is_active or not verify_password(password_digest, user.password_hash):
        _record_login_failure(req, request.username)
        write_audit_log(
            db,
            username=request.username,
            action=ACTION_LOGIN,
            resource_type=RESOURCE_USER,
            resource_name=request.username,
            detail="登录失败：用户名或密码错误",
            ip_address=req.client.host if req.client else None,
            status="failure",
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    _clear_login_failures(req, request.username)
    write_audit_log(
        db,
        username=user.username,
        action=ACTION_LOGIN,
        resource_type=RESOURCE_USER,
        resource_name=user.username,
        detail="登录成功",
        ip_address=req.client.host if req.client else None,
        status="success",
    )
    return AuthResponse(token=create_access_token(user), user=UserResponse.model_validate(user))


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """获取当前登录用户"""
    return current_user


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/change-password")
def change_password(
    request: ChangePasswordRequest,
    req: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """自助修改密码：验证旧密码后设置新密码，并使旧 Token 立即失效"""
    try:
        old_digest = decrypt_transport_secret(request.old_password)
        new_digest = decrypt_transport_secret(request.new_password)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="密码传输格式不安全或已失效，请刷新页面后重试")

    if not new_digest or len(new_digest) < 32:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="新密码格式不正确，请刷新页面后重试")

    if not verify_password(old_digest, current_user.password_hash):
        write_audit_log(
            db, username=current_user.username, action=ACTION_UPDATE,
            resource_type=RESOURCE_USER, resource_name=current_user.username,
            detail="修改密码失败：旧密码错误",
            ip_address=req.client.host if req.client else None,
            status="failure",
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="旧密码错误")

    if hmac.compare_digest(old_digest, new_digest):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="新密码不能与旧密码相同")

    current_user.password_hash = hash_password(new_digest)
    # 递增 token_version：当前会话以外的所有 Token 立即失效
    current_user.token_version = int(current_user.token_version or 0) + 1
    db.commit()
    db.refresh(current_user)

    write_audit_log(
        db, username=current_user.username, action=ACTION_UPDATE,
        resource_type=RESOURCE_USER, resource_name=current_user.username,
        detail="修改密码成功",
        ip_address=req.client.host if req.client else None,
        status="success",
    )

    # 旧 Token 已失效，返回新 Token 让前端平滑切换
    return {"token": create_access_token(current_user), "detail": "密码修改成功"}
