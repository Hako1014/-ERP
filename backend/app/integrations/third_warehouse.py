"""第三方仓储适配接口与本地 mock。"""

from typing import Any, Protocol


class ThirdWarehouseAdapter(Protocol):
    def sync_warehouse(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class MockThirdWarehouseAdapter:
    def sync_warehouse(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": True,
            "warehouse_code": payload["warehouse_code"],
            "sku_count": len(payload["stock"]),
            "message": "mock，未发送至真实第三方仓储",
        }


def get_third_warehouse_adapter() -> ThirdWarehouseAdapter:
    return MockThirdWarehouseAdapter()
