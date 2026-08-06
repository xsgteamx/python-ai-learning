from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime, timedelta
from sqlalchemy import func

from app.models import Asset, User, UserAssetPermission, ExternalService, AuditLog, GrafanaDashboard
from app.security import hash_password
from app.services.crypto import encrypt_password, decrypt_password, encrypt_secret, decrypt_secret, is_encrypted
from app.schemas import (
    AssetCreate, AssetUpdate,
    UserCreate, UserUpdate, UserAssetPermissionUpdate,
    ExternalServiceCreate, ExternalServiceUpdate,
    GrafanaDashboardCreate, GrafanaDashboardUpdate
)


# ============================================================
# 资产 CRUD
# ============================================================

def get_assets(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    env: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    allowed_asset_ids: Optional[set] = None,
) -> List[Asset]:
    """查询资产列表。

    allowed_asset_ids:
        - None：不限制（管理员路径）
        - 空集合：返回空（无任何资产权限）
        - 非空集合：仅返回这些 id 的资产
    """
    query = db.query(Asset)
    if env:
        query = query.filter(Asset.env == env)
    if role:
        query = query.filter(Asset.role == role)
    if status:
        query = query.filter(Asset.status == status)
    if search:
        query = query.filter(
            Asset.hostname.contains(search) | Asset.ip.contains(search)
        )
    if allowed_asset_ids is not None:
        if not allowed_asset_ids:
            return []
        query = query.filter(Asset.id.in_(allowed_asset_ids))
    return query.offset(skip).limit(limit).all()


def get_user_allowed_asset_ids(db: Session, user: User) -> Optional[set]:
    """返回用户可访问的资产 ID 集合。

    - admin 用户返回 None，表示不限制
    - 普通用户返回其有 connect/execute 权限的资产 id 集合（可能为空集合）
    """
    if user and user.role == "admin":
        return None
    if not user:
        return set()
    rows = db.query(UserAssetPermission.asset_id).filter(
        UserAssetPermission.user_id == user.id,
        (UserAssetPermission.can_connect == True) | (UserAssetPermission.can_execute == True),
    ).all()
    return {r[0] for r in rows}


def get_asset(db: Session, asset_id: int) -> Optional[Asset]:
    return db.query(Asset).filter(Asset.id == asset_id).first()


def get_asset_by_hostname(db: Session, hostname: str) -> Optional[Asset]:
    return db.query(Asset).filter(Asset.hostname == hostname).first()


def _encrypt_asset_passwords(data: dict) -> dict:
    """对资产数据中的敏感字段进行加密：密码 / 私钥 / 证书"""
    # 密码类字段
    for field in ("password", "jump_password", "sudo_password"):
        if data.get(field) and not is_encrypted(data[field]):
            data[field] = encrypt_password(data[field])
    # 私钥内容 / 证书内容（PEM 文本）：明文存储风险高，统一加密
    for field in ("ssh_key", "jump_ssh_key", "ssh_cert", "jump_ssh_cert"):
        if data.get(field) and not is_encrypted(data[field]):
            data[field] = encrypt_secret(data[field])
    return data


def create_asset(db: Session, asset_data: AssetCreate) -> Asset:
    data = _encrypt_asset_passwords(asset_data.model_dump())
    db_asset = Asset(**data)
    db.add(db_asset)
    db.commit()
    db.refresh(db_asset)
    return db_asset


def update_asset(db: Session, asset_id: int, asset_data: AssetUpdate) -> Optional[Asset]:
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        return None
    update_data = _encrypt_asset_passwords(asset_data.model_dump(exclude_unset=True))
    for key, value in update_data.items():
        setattr(db_asset, key, value)
    db.commit()
    db.refresh(db_asset)
    return db_asset


def delete_asset(db: Session, asset_id: int) -> bool:
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        return False
    db.delete(db_asset)
    db.commit()
    return True


def update_asset_status(db: Session, asset_id: int, status: str) -> Optional[Asset]:
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        return None
    db_asset.status = status
    db_asset.last_check = datetime.now()
    db.commit()
    db.refresh(db_asset)
    return db_asset


# ============================================================
# 用户与远程连接权限 CRUD
# ============================================================

def get_users(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    is_active: Optional[bool] = None,
    search: Optional[str] = None
) -> List[User]:
    query = db.query(User)
    if is_active is not None:
        query = query.filter(User.is_active == is_active)
    if search:
        query = query.filter(
            User.username.contains(search) |
            User.display_name.contains(search) |
            User.email.contains(search)
        )
    return query.order_by(User.id.asc()).offset(skip).limit(limit).all()


def get_user(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()


def create_user(db: Session, user_data: UserCreate) -> User:
    data = user_data.model_dump()
    password = data.pop("password")
    db_user = User(**data, password_hash=hash_password(password))
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def update_user(db: Session, user_id: int, user_data: UserUpdate) -> Optional[User]:
    db_user = get_user(db, user_id)
    if not db_user:
        return None
    update_data = user_data.model_dump(exclude_unset=True)
    password = update_data.pop("password", None)
    if password:
        db_user.password_hash = hash_password(password)
        # 改密后递增 token_version，使已签发的旧 Token 立即失效
        db_user.token_version = int(db_user.token_version or 0) + 1
    for key, value in update_data.items():
        setattr(db_user, key, value)
    db.commit()
    db.refresh(db_user)
    return db_user


def delete_user(db: Session, user_id: int) -> bool:
    db_user = get_user(db, user_id)
    if not db_user:
        return False
    db.query(UserAssetPermission).filter(UserAssetPermission.user_id == user_id).delete()
    db.delete(db_user)
    db.commit()
    return True


def get_user_permissions(db: Session, user_id: int) -> List[UserAssetPermission]:
    return db.query(UserAssetPermission).filter(
        UserAssetPermission.user_id == user_id
    ).all()


def set_user_permissions(
    db: Session,
    user_id: int,
    permission_data: UserAssetPermissionUpdate
) -> List[UserAssetPermission]:
    db.query(UserAssetPermission).filter(UserAssetPermission.user_id == user_id).delete()
    saved_permissions = []
    for item in permission_data.permissions:
        # 只要勾选了任意一项就保存，不再静默跳过
        db_permission = UserAssetPermission(
            user_id=user_id,
            asset_id=item.asset_id,
            can_connect=item.can_connect,
            can_execute=item.can_execute,
            allow_sensitive_commands=item.allow_sensitive_commands,
            allow_high_risk_commands=item.allow_high_risk_commands,
            blocked_commands=item.blocked_commands,
            remark=item.remark
        )
        db.add(db_permission)
        saved_permissions.append(db_permission)
    db.commit()
    for item in saved_permissions:
        db.refresh(item)
    return saved_permissions


def get_user_permission_details(db: Session, user_id: int) -> List[dict]:
    db_user = get_user(db, user_id)
    is_admin = bool(db_user and db_user.role == "admin")
    permissions = {
        p.asset_id: p
        for p in get_user_permissions(db, user_id)
    }
    assets = get_assets(db, limit=500)
    return [
        {
            "asset_id": asset.id,
            "hostname": asset.hostname,
            "ip": asset.ip,
            "env": asset.env,
            "role": asset.role,
            "can_connect": True if is_admin else (permissions.get(asset.id).can_connect if asset.id in permissions else False),
            "can_execute": True if is_admin else (permissions.get(asset.id).can_execute if asset.id in permissions else False),
            "allow_sensitive_commands": True if is_admin else (permissions.get(asset.id).allow_sensitive_commands if asset.id in permissions else False),
            "allow_high_risk_commands": True if is_admin else (permissions.get(asset.id).allow_high_risk_commands if asset.id in permissions else False),
            "blocked_commands": permissions.get(asset.id).blocked_commands if asset.id in permissions else None,
            "remark": permissions.get(asset.id).remark if asset.id in permissions else None,
        }
        for asset in assets
    ]


def has_remote_permission(db: Session, user_id: int, asset_id: int, action: str) -> bool:
    db_user = get_user(db, user_id)
    if not db_user or not db_user.is_active:
        return False
    if db_user.role == "admin":
        return True
    db_permission = db.query(UserAssetPermission).filter(
        UserAssetPermission.user_id == user_id,
        UserAssetPermission.asset_id == asset_id
    ).first()
    if not db_permission:
        return False
    can_connect = bool(db_permission.can_connect)
    can_execute = bool(db_permission.can_execute)
    if action == "connect":
        return can_connect or can_execute
    if action == "execute":
        # 远程权限简化为一个开关：允许连接即允许进入终端、执行命令和文件传输。
        # can_execute 字段保留用于兼容旧数据和旧前端。
        return can_connect or can_execute
    return False


def get_remote_command_policy(db: Session, user_id: int, asset_id: int) -> dict:
    """获取远程命令安全策略"""
    db_user = get_user(db, user_id)
    if db_user and db_user.role == "admin":
        return {
            "allow_sensitive_commands": True,
            "allow_high_risk_commands": True,
            "blocked_commands": "",
        }

    db_permission = db.query(UserAssetPermission).filter(
        UserAssetPermission.user_id == user_id,
        UserAssetPermission.asset_id == asset_id
    ).first()
    return {
        "allow_sensitive_commands": bool(db_permission.allow_sensitive_commands) if db_permission else False,
        "allow_high_risk_commands": bool(db_permission.allow_high_risk_commands) if db_permission else False,
        "blocked_commands": db_permission.blocked_commands if db_permission else "",
    }


def ensure_permission_can_execute(db: Session, user_id: int):
    """修复旧数据：确保 can_connect=True 的记录 can_execute 也为 True"""
    db.query(UserAssetPermission).filter(
        UserAssetPermission.user_id == user_id,
        UserAssetPermission.can_connect == True,
        UserAssetPermission.can_execute == False
    ).update({UserAssetPermission.can_execute: True})
    db.commit()


# ============================================================
# 外部服务入口 CRUD
# ============================================================

def get_external_services(db: Session, active_only: bool = False) -> List[ExternalService]:
    query = db.query(ExternalService)
    if active_only:
        query = query.filter(ExternalService.is_active == True)
    return query.order_by(ExternalService.sort_order.asc(), ExternalService.id.asc()).all()


def get_external_service(db: Session, service_id: int) -> Optional[ExternalService]:
    return db.query(ExternalService).filter(ExternalService.id == service_id).first()


def create_external_service(db: Session, service_data: ExternalServiceCreate) -> ExternalService:
    db_service = ExternalService(**service_data.model_dump())
    db.add(db_service)
    db.commit()
    db.refresh(db_service)
    return db_service


def update_external_service(db: Session, service_id: int, service_data: ExternalServiceUpdate) -> Optional[ExternalService]:
    db_service = get_external_service(db, service_id)
    if not db_service:
        return None
    update_data = service_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_service, key, value)
    db.commit()
    db.refresh(db_service)
    return db_service


def delete_external_service(db: Session, service_id: int) -> bool:
    db_service = get_external_service(db, service_id)
    if not db_service:
        return False
    db.delete(db_service)
    db.commit()
    return True


# ============================================================
# Grafana监控看板 CRUD
# ============================================================

def get_grafana_dashboards(db: Session, active_only: bool = False) -> List[GrafanaDashboard]:
    query = db.query(GrafanaDashboard)
    if active_only:
        query = query.filter(GrafanaDashboard.is_active == True)
    return query.order_by(GrafanaDashboard.sort_order.asc(), GrafanaDashboard.id.asc()).all()


def get_grafana_dashboard(db: Session, dashboard_id: int) -> Optional[GrafanaDashboard]:
    return db.query(GrafanaDashboard).filter(GrafanaDashboard.id == dashboard_id).first()


def create_grafana_dashboard(db: Session, dashboard_data: GrafanaDashboardCreate) -> GrafanaDashboard:
    db_dashboard = GrafanaDashboard(**dashboard_data.model_dump())
    db.add(db_dashboard)
    db.commit()
    db.refresh(db_dashboard)
    return db_dashboard


def update_grafana_dashboard(db: Session, dashboard_id: int, dashboard_data: GrafanaDashboardUpdate) -> Optional[GrafanaDashboard]:
    db_dashboard = get_grafana_dashboard(db, dashboard_id)
    if not db_dashboard:
        return None
    update_data = dashboard_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_dashboard, key, value)
    db.commit()
    db.refresh(db_dashboard)
    return db_dashboard


def delete_grafana_dashboard(db: Session, dashboard_id: int) -> bool:
    db_dashboard = get_grafana_dashboard(db, dashboard_id)
    if not db_dashboard:
        return False
    db.delete(db_dashboard)
    db.commit()
    return True


# ============================================================
# 统计
# ============================================================

def get_asset_stats(db: Session) -> dict:
    total = db.query(Asset).count()
    online = db.query(Asset).filter(Asset.status == "online").count()
    offline = db.query(Asset).filter(Asset.status == "offline").count()
    unknown = db.query(Asset).filter(Asset.status == "unknown").count()
    
    env_stats = db.query(Asset.env, func.count(Asset.id)).group_by(Asset.env).all()
    role_stats = db.query(Asset.role, func.count(Asset.id)).group_by(Asset.role).all()
    
    return {
        "total": total,
        "online": online,
        "offline": offline,
        "unknown": unknown,
        "by_env": [{"env": env, "count": count} for env, count in env_stats],
        "by_role": [{"role": role, "count": count} for role, count in role_stats]
    }


def get_service_stats(db: Session) -> dict:
    total = db.query(ExternalService).count()
    active = db.query(ExternalService).filter(ExternalService.is_active == True).count()
    return {"total": total, "active": active}


# ============================================================
# 审计日志 CRUD
# ============================================================

def get_audit_logs(
    db: Session,
    skip: int = 0,
    limit: int = 50,
    username: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    search: Optional[str] = None,
) -> List[AuditLog]:
    query = db.query(AuditLog)
    if username:
        query = query.filter(AuditLog.username == username)
    if action:
        query = query.filter(AuditLog.action == action)
    if resource_type:
        query = query.filter(AuditLog.resource_type == resource_type)
    if search:
        query = query.filter(
            AuditLog.resource_name.contains(search) |
            AuditLog.detail.contains(search)
        )
    return query.order_by(AuditLog.id.desc()).offset(skip).limit(limit).all()


def get_audit_log_count(
    db: Session,
    username: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    search: Optional[str] = None,
) -> int:
    query = db.query(AuditLog)
    if username:
        query = query.filter(AuditLog.username == username)
    if action:
        query = query.filter(AuditLog.action == action)
    if resource_type:
        query = query.filter(AuditLog.resource_type == resource_type)
    if search:
        query = query.filter(
            AuditLog.resource_name.contains(search) |
            AuditLog.detail.contains(search)
        )
    return query.count()


def get_audit_log_actions(db: Session) -> List[str]:
    """获取所有操作类型"""
    return [row[0] for row in db.query(AuditLog.action).distinct().all()]


def get_audit_log_resource_types(db: Session) -> List[str]:
    """获取所有资源类型"""
    return [row[0] for row in db.query(AuditLog.resource_type).distinct().all()]
