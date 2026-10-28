"""物流取号/回收接口与 mock；不调用真实承运商。"""

from typing import Protocol

from app.models.order import Order
from app.models.platform import Logistics


class LogisticsProvider(Protocol):
    def acquire(self, order: Order, logistics: Logistics) -> str: ...

    def recycle(self, order: Order, logistics: Logistics, waybill_no: str) -> None: ...


class MockLogisticsProvider:
    def __init__(self, channel: str):
        self.channel = channel

    def acquire(self, order: Order, logistics: Logistics) -> str:
        del logistics
        return f"MOCK-{self.channel}-{order.id}"

    def recycle(self, order: Order, logistics: Logistics, waybill_no: str) -> None:
        del order, logistics, waybill_no


def get_logistics_provider(logistics: Logistics) -> LogisticsProvider:
    """天天/EMS 预留菜鸟，圆通预留天地华宇；均仅返回 mock 实现。"""
    name = logistics.name.upper()
    channel = "HUAYU" if "圆通" in name else "CAINIAO" if "天天" in name or "EMS" in name else "GENERIC"
    return MockLogisticsProvider(channel)
