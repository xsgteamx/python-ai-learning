from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.schemas import ExternalServiceCreate, ExternalServiceUpdate, ExternalServiceResponse
from app.crud import (
    get_external_services,
    get_external_service,
    create_external_service,
    update_external_service,
    delete_external_service,
)
from app.models import User
from app.security import get_current_user, require_admin
from app.services.audit import (
    write_audit_log, ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE,
    RESOURCE_SERVICE,
)

router = APIRouter(prefix="/api/services", tags=["服务入口"])


@router.get("/", response_model=List[ExternalServiceResponse])
def list_services(
    active_only: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取服务入口列表"""
    return get_external_services(db, active_only=active_only)


@router.post("/", response_model=ExternalServiceResponse, status_code=status.HTTP_201_CREATED)
def create_service(
    service: ExternalServiceCreate,
    req: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """添加服务入口（仅管理员）"""
    result = create_external_service(db, service)
    write_audit_log(
        db, username=current_admin.username, action=ACTION_CREATE, resource_type=RESOURCE_SERVICE,
        resource_name=service.name, detail=f"添加服务: {service.name}",
        ip_address=req.client.host if req.client else None,
    )
    return result


@router.put("/{service_id}", response_model=ExternalServiceResponse)
def update_service(
    service_id: int,
    service: ExternalServiceUpdate,
    req: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """编辑服务入口（仅管理员）"""
    db_service = update_external_service(db, service_id, service)
    if not db_service:
        raise HTTPException(status_code=404, detail="服务入口不存在")
    write_audit_log(
        db, username=current_admin.username, action=ACTION_UPDATE, resource_type=RESOURCE_SERVICE,
        resource_name=db_service.name, detail=f"编辑服务: {db_service.name}",
        ip_address=req.client.host if req.client else None,
    )
    return db_service


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(
    service_id: int,
    req: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin)
):
    """删除服务入口（仅管理员）"""
    db_service = get_external_service(db, service_id)
    if not db_service:
        raise HTTPException(status_code=404, detail="服务入口不存在")
    name = db_service.name
    if not delete_external_service(db, service_id):
        raise HTTPException(status_code=404, detail="服务入口不存在")
    write_audit_log(
        db, username=current_admin.username, action=ACTION_DELETE, resource_type=RESOURCE_SERVICE,
        resource_name=name, detail=f"删除服务: {name}",
        ip_address=req.client.host if req.client else None,
    )
