from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.schemas import (
    UserCreate, UserUpdate, UserResponse,
    UserAssetPermissionUpdate, UserAssetPermissionResponse, UserPermissionDetail
)
from app.crud import (
    get_users, get_user, get_user_by_username, create_user, update_user, delete_user,
    get_user_permissions, set_user_permissions, get_user_permission_details
)
from app.models import User
from app.security import require_admin
from app.services.audit import (
    write_audit_log, ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE,
    RESOURCE_USER, RESOURCE_PERMISSION,
)
from app.services.transport_crypto import decrypt_transport_secret

router = APIRouter(prefix="/api/users", tags=["用户管理"])


@router.get("/", response_model=List[UserResponse])
def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """查询用户列表"""
    return get_users(db, skip=skip, limit=limit, is_active=is_active, search=search)


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_new_user(
    user: UserCreate,
    req: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """创建用户"""
    existing = get_user_by_username(db, user.username)
    if existing:
        raise HTTPException(status_code=400, detail=f"用户名 '{user.username}' 已存在")
    try:
        user.password = decrypt_transport_secret(user.password)
    except Exception:
        raise HTTPException(status_code=400, detail="密码传输格式不安全或已失效，请刷新页面后重试")
    result = create_user(db, user)
    write_audit_log(
        db, username=current_admin.username, action=ACTION_CREATE, resource_type=RESOURCE_USER,
        resource_name=user.username, detail=f"创建用户: {user.username}",
        ip_address=req.client.host if req.client else None,
    )
    return result


@router.get("/categories/roles", response_model=List[dict])
def get_user_role_categories(current_admin: User = Depends(require_admin)):
    """获取用户角色分类"""
    return [
        {"value": "admin", "label": "管理员", "description": "拥有所有服务器连接和命令执行权限"},
        {"value": "operator", "label": "运维人员", "description": "按服务器分配连接和执行权限"},
        {"value": "viewer", "label": "只读用户", "description": "默认无远程权限，可按服务器单独授权"},
    ]


@router.get("/categories/remote-actions", response_model=List[dict])
def get_remote_action_categories(current_admin: User = Depends(require_admin)):
    """获取远程连接权限动作分类"""
    return [
        {"value": "connect", "label": "允许连接/远程操作", "description": "允许测试SSH连接、打开远程终端、执行命令和文件传输，高危命令由后台拦截"},
    ]


@router.get("/{user_id}", response_model=UserResponse)
def get_user_detail(
    user_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """获取用户详情"""
    db_user = get_user(db, user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return db_user


@router.put("/{user_id}", response_model=UserResponse)
def update_user_detail(
    user_id: int,
    user: UserUpdate,
    req: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """更新用户信息"""
    if user.password:
        try:
            user.password = decrypt_transport_secret(user.password)
        except Exception:
            raise HTTPException(status_code=400, detail="密码传输格式不安全或已失效，请刷新页面后重试")
    db_user = update_user(db, user_id, user)
    if not db_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    write_audit_log(
        db, username=current_admin.username, action=ACTION_UPDATE, resource_type=RESOURCE_USER,
        resource_name=db_user.username, detail=f"更新用户: {db_user.username}",
        ip_address=req.client.host if req.client else None,
    )
    return db_user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_detail(
    user_id: int,
    req: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """删除用户，并清理该用户的资产权限"""
    db_user = get_user(db, user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    username = db_user.username
    if not delete_user(db, user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    write_audit_log(
        db, username=current_admin.username, action=ACTION_DELETE, resource_type=RESOURCE_USER,
        resource_name=username, detail=f"删除用户: {username}",
        ip_address=req.client.host if req.client else None,
    )


@router.get("/{user_id}/permissions", response_model=List[UserAssetPermissionResponse])
def list_user_permissions(
    user_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """查询用户已分配的远程连接权限"""
    if not get_user(db, user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return get_user_permissions(db, user_id)


@router.get("/{user_id}/permission-details", response_model=List[UserPermissionDetail])
def list_user_permission_details(
    user_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """查询用户在所有服务器上的权限明细，便于前端勾选分配"""
    if not get_user(db, user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return get_user_permission_details(db, user_id)


@router.put("/{user_id}/permissions", response_model=List[UserAssetPermissionResponse])
def update_user_permissions(
    user_id: int,
    permissions: UserAssetPermissionUpdate,
    req: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """覆盖保存用户的远程连接权限"""
    db_user = get_user(db, user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    result = set_user_permissions(db, user_id, permissions)
    write_audit_log(
        db, username=current_admin.username, action=ACTION_UPDATE, resource_type=RESOURCE_PERMISSION,
        resource_name=db_user.username, detail=f"更新用户权限: {db_user.username}",
        ip_address=req.client.host if req.client else None,
    )
    return result
