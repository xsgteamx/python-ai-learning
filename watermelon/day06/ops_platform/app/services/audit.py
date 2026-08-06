"""
审计日志服务 - 记录关键操作审计轨迹
"""
import logging
from typing import Optional
from sqlalchemy.orm import Session

from app.models import AuditLog

logger = logging.getLogger(__name__)


def write_audit_log(
    db: Session,
    username: str,
    action: str,
    resource_type: str,
    resource_name: Optional[str] = None,
    detail: Optional[str] = None,
    ip_address: Optional[str] = None,
    status: str = "success",
) -> AuditLog:
    """写入一条审计日志"""
    try:
        # 字段长度保护，避免写入超长导致 DB 报错
        if username and len(username) > 50:
            username = username[:50]
        if action and len(action) > 50:
            action = action[:50]
        if resource_type and len(resource_type) > 50:
            resource_type = resource_type[:50]
        if resource_name and len(resource_name) > 200:
            resource_name = resource_name[:200]
        if ip_address and len(ip_address) > 50:
            ip_address = ip_address[:50]
        if status and len(status) > 20:
            status = status[:20]

        log_entry = AuditLog(
            username=username,
            action=action,
            resource_type=resource_type,
            resource_name=resource_name,
            detail=detail,
            ip_address=ip_address,
            status=status,
        )
        db.add(log_entry)
        db.commit()
        db.refresh(log_entry)
        return log_entry
    except Exception as e:
        logger.error(f"审计日志写入失败: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        # 不再 raise，审计日志写入失败不应该影响主业务操作


# 预定义的操作类型
ACTION_LOGIN = "login"
ACTION_LOGOUT = "logout"
ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"
ACTION_CONNECT = "connect"
ACTION_EXECUTE = "execute"
ACTION_UPLOAD = "upload"
ACTION_DOWNLOAD = "download"
ACTION_TEST = "test"
ACTION_TERMINAL = "terminal"
ACTION_BROWSE = "browse"
ACTION_HOSTKEY_CONFIRM = "hostkey_confirm"
ACTION_HOSTKEY_REJECT = "hostkey_reject"
ACTION_HOSTKEY_DELETE = "hostkey_delete"

# 预定义的资源类型
RESOURCE_ASSET = "asset"
RESOURCE_USER = "user"
RESOURCE_SERVICE = "service"
RESOURCE_PERMISSION = "permission"
RESOURCE_HOSTKEY = "hostkey"