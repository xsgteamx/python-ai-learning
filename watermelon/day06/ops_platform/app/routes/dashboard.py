from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Dict, Any

from app.database import get_db
from app.crud import get_asset_stats, get_service_stats, get_external_services
from app.models import User
from app.security import get_current_user

router = APIRouter(prefix="/api/dashboard", tags=["仪表盘"])


@router.get("/stats")
def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    asset_stats = get_asset_stats(db)
    service_stats = get_service_stats(db)

    return {
        "assets": asset_stats,
        "services": service_stats,
    }
