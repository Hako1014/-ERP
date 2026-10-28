"""平台订单/发货适配边界。具体平台协议未确定，默认仅使用本地 mock。"""

from typing import Any, Protocol

from app.models.platform import Platform, Shop


class PlatformAdapter(Protocol):
    def pull_orders(self, shop: Shop, *, cursor: str | None = None) -> list[dict[str, Any]]: ...

    def push_shipment(self, shop: Shop, payload: dict[str, Any]) -> dict[str, Any]: ...


class MockPlatformAdapter:
    def __init__(self, orders: list[dict[str, Any]] | None = None):
        self.orders = orders or []

    def pull_orders(self, shop: Shop, *, cursor: str | None = None) -> list[dict[str, Any]]:
        del shop, cursor
        return [dict(order) for order in self.orders]

    def push_shipment(self, shop: Shop, payload: dict[str, Any]) -> dict[str, Any]:
        del shop
        return {"success": True, "message": "mock，未回传真实平台", "order_no": payload["order_no"]}


def get_platform_adapter(platform: Platform) -> PlatformAdapter:
    """后续按 platform.code 注册淘宝、京东等真实适配器。"""
    del platform
    return MockPlatformAdapter()
