from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.schemas import AuditLogResponse
from app.crud import get_audit_logs, get_audit_log_count, get_audit_log_actions, get_audit_log_resource_types
from app.models import User
from app.security import require_admin

router = APIRouter(prefix="/api/audit", tags=["审计日志"])


@router.get("/logs", response_model=List[AuditLogResponse])
def list_audit_logs(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    username: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """查询审计日志列表（仅管理员可查）"""
    return get_audit_logs(db, skip=skip, limit=limit, username=username, action=action, resource_type=resource_type, search=search)


@router.get("/logs/count")
def count_audit_logs(
    username: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """获取审计日志总数"""
    return {"total": get_audit_log_count(db, username=username, action=action, resource_type=resource_type, search=search)}


@router.get("/filters/actions")
def list_audit_actions(
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """获取所有操作类型"""
    return get_audit_log_actions(db)


@router.get("/filters/resource-types")
def list_audit_resource_types(
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """获取所有资源类型"""
    return get_audit_log_resource_types(db)