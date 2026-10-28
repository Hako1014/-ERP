"""第三方仓储库存快照同步编排。"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.integrations.third_warehouse import get_third_warehouse_adapter
from app.models.org import Staff
from app.models.warehouse import Location, StockLocation
from app.services.warehouse_service import get_warehouse_or_404


def sync_warehouse(db: Session, *, warehouse_id: int, staff: Staff) -> dict:
    warehouse = get_warehouse_or_404(db, warehouse_id)
    if not warehouse.is_enabled or warehouse.warehouse_type != "FINISHED" or not warehouse.sync_stock:
        raise BizException(ErrorCode.CONFLICT, "仅启用且参与库存同步的成品仓可同步")
    rows = db.execute(
        select(StockLocation.product_sku_id, func.sum(StockLocation.qty))
        .join(Location, StockLocation.location_id == Location.id)
        .where(Location.warehouse_id == warehouse_id, Location.is_enabled.is_(True))
        .group_by(StockLocation.product_sku_id)
        .order_by(StockLocation.product_sku_id)
    ).all()
    payload = {
        "warehouse_code": warehouse.code,
        "warehouse_name": warehouse.name,
        "stock": [{"sku_id": sku_id, "qty": str(qty)} for sku_id, qty in rows],
    }
    try:
        response = get_third_warehouse_adapter().sync_warehouse(payload)
    except Exception as exc:
        raise BizException(ErrorCode.CONFLICT, "第三方仓储同步调用失败") from exc
    if not isinstance(response, dict) or response.get("success") is not True:
        raise BizException(ErrorCode.CONFLICT, "第三方仓储同步失败")
    write_audit(
        db, action="THIRD_SYNC", target_type="warehouse", target_id=str(warehouse.id),
        summary=f"第三方仓储同步：{warehouse.code}",
        detail={"sku_count": len(rows), "message": response.get("message")},
        actor_id=staff.id, actor_name=staff.name,
    )
    db.commit()
    return response
