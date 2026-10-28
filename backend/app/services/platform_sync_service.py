"""平台抓单与发货回传编排；真实平台协议由适配器实现。"""

from sqlalchemy.orm import Session

from app.core.errors import BizException, ErrorCode
from app.integrations.platform import get_platform_adapter
from app.models.order import Order, OrderShipment
from app.models.org import Staff
from app.models.platform import Platform, Shop
from app.services import order_service, ship_service


def _shop_adapter(db: Session, shop_id: int):
    shop = db.get(Shop, shop_id)
    if shop is None or shop.is_locked:
        raise BizException(ErrorCode.NOT_FOUND, "店铺不存在或已锁定")
    platform = db.get(Platform, shop.platform_id)
    if platform is None:
        raise BizException(ErrorCode.CONFLICT, "店铺关联平台不存在")
    return shop, get_platform_adapter(platform)


def pull_shop_orders(db: Session, *, shop_id: int, staff: Staff, cursor: str | None = None) -> dict:
    shop, adapter = _shop_adapter(db, shop_id)
    try:
        orders = adapter.pull_orders(shop, cursor=cursor)
    except Exception as exc:
        raise BizException(ErrorCode.CONFLICT, "平台抓单调用失败") from exc
    if not isinstance(orders, list) or len(orders) > 500 or any(
        not isinstance(item, dict) or item.get("shop_id") != shop_id for item in orders
    ):
        raise BizException(ErrorCode.CONFLICT, "平台抓单数据格式或店铺归属错误")
    if not orders:
        return {"created": 0, "updated": 0, "skipped": []}
    return order_service.fetch_orders(db, orders=orders, staff=staff)


def push_shipment(db: Session, *, shipment_id: int, staff: Staff) -> dict:
    shipment = db.get(OrderShipment, shipment_id)
    if shipment is None:
        raise BizException(ErrorCode.NOT_FOUND, "包裹不存在")
    if shipment.status != ship_service.STATUS_SHIPPED:
        raise BizException(ErrorCode.CONFLICT, "包裹发货后才能回传平台")
    order = db.get(Order, shipment.order_id)
    if order is None:
        raise BizException(ErrorCode.CONFLICT, "包裹关联订单不存在")
    shop, adapter = _shop_adapter(db, order.shop_id)
    payload = {
        "order_no": order.order_no,
        "shipment_no": shipment.shipment_no,
        "tracking_no": shipment.tracking_no,
        "logistics_company": order.logistics_company,
    }
    try:
        result = adapter.push_shipment(shop, payload)
        if not isinstance(result, dict) or result.get("success") is not True:
            raise ValueError("平台拒绝回传")
    except Exception as exc:
        ship_service.record_platform_sync(
            db, shipment_id=shipment_id, status="FAILED", message="平台回传失败", staff=staff,
        )
        raise BizException(ErrorCode.CONFLICT, "平台回传失败") from exc
    ship_service.record_platform_sync(
        db, shipment_id=shipment_id, status="SYNCED", message=str(result.get("message") or "回传成功")[:255], staff=staff,
    )
    return result
