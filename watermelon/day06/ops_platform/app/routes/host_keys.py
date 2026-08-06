"""主机密钥管理路由：查看/确认待确认的密钥变更，管理已知主机密钥。

当同一 IP 的服务器密钥发生变更（如重装系统、更换服务器）时，
_AuditHostKeyPolicy 会将新密钥记录为「待确认」而非直接放行，
管理员需在此处核对指纹后确认追加，避免手动编辑 known_hosts 文件，
同时保留对中间人攻击的防御。

所有敏感操作（确认/拒绝/删除）均写入数据库审计日志，便于追溯。
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models import User
from app.security import require_admin, get_current_user
from app.services.audit import (
    write_audit_log,
    ACTION_HOSTKEY_CONFIRM, ACTION_HOSTKEY_REJECT, ACTION_HOSTKEY_DELETE,
    RESOURCE_HOSTKEY,
)
from app.services.ssh_client import (
    list_pending_host_keys,
    confirm_pending_host_key,
    reject_pending_host_key,
    list_known_host_keys,
    delete_known_host_key,
)

router = APIRouter(prefix="/api/host-keys", tags=["主机密钥管理"])


class ConfirmRequest(BaseModel):
    """确认待确认项的请求体：要求输入新密钥指纹后 8 位字符做二次校验"""
    fingerprint_suffix: str


@router.get("/pending")
def list_pending(
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """获取待确认的主机密钥变更列表（仅管理员）"""
    return {"items": list_pending_host_keys()}


@router.get("/pending/count")
def count_pending(
    current_user: User = Depends(get_current_user)
):
    """获取待确认的密钥变更数量（用于前端角标提示，所有登录用户可见）"""
    return {"count": len(list_pending_host_keys())}


@router.post("/pending/{item_id}/confirm")
def confirm_pending(
    item_id: str,
    body: ConfirmRequest,
    req: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """确认一条待确认的密钥变更：将新密钥追加到 known_hosts（仅管理员）

    要求输入新密钥指纹的后 8 位字符做二次校验，防止误操作。
    """
    ok, msg, target = confirm_pending_host_key(item_id, fingerprint_suffix=body.fingerprint_suffix)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    # 写入审计日志
    write_audit_log(
        db, username=current_admin.username, action=ACTION_HOSTKEY_CONFIRM,
        resource_type=RESOURCE_HOSTKEY, resource_name=target.get("hostname"),
        detail=f"确认主机密钥变更: {target.get('hostname')} -> {target.get('fingerprint')}",
        ip_address=req.client.host if req.client else None, status="success",
    )
    return {"success": True, "message": msg}


@router.post("/pending/{item_id}/reject")
def reject_pending(
    item_id: str,
    req: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """拒绝一条待确认的密钥变更：从待确认列表删除，不写入 known_hosts（仅管理员）"""
    ok, msg, target = reject_pending_host_key(item_id)
    if not ok:
        raise HTTPException(status_code=404, detail=msg)
    write_audit_log(
        db, username=current_admin.username, action=ACTION_HOSTKEY_REJECT,
        resource_type=RESOURCE_HOSTKEY, resource_name=target.get("hostname"),
        detail=f"拒绝主机密钥变更: {target.get('hostname')} -> {target.get('fingerprint')}",
        ip_address=req.client.host if req.client else None, status="success",
    )
    return {"success": True, "message": msg}


@router.get("/known")
def list_known(
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """获取 known_hosts 中所有已知主机密钥（仅管理员）"""
    return {"items": list_known_host_keys()}


@router.delete("/known/{line_idx}")
def delete_known(
    line_idx: int,
    req: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """按行号删除 known_hosts 中的一条密钥记录（仅管理员）"""
    ok, msg, target = delete_known_host_key(line_idx)
    if not ok:
        raise HTTPException(status_code=404, detail=msg)
    write_audit_log(
        db, username=current_admin.username, action=ACTION_HOSTKEY_DELETE,
        resource_type=RESOURCE_HOSTKEY, resource_name=target.get("hostname"),
        detail=f"删除已知主机密钥: {msg}",
        ip_address=req.client.host if req.client else None, status="success",
    )
    return {"success": True, "message": msg}
