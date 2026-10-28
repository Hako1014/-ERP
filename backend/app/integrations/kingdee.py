"""金蝶对接接口与本地 mock；真实协议待客户确定后实现。"""

from typing import Any, Protocol


class KingdeeAdapter(Protocol):
    def list_subjects(self) -> list[dict[str, Any]]: ...

    def send_voucher(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def get_balance(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class MockKingdeeAdapter:
    def list_subjects(self) -> list[dict[str, Any]]:
        from app.services.account_subject_service import KINGDEE_SUBJECT_SEED

        return [dict(subject) for subject in KINGDEE_SUBJECT_SEED]

    def send_voucher(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"success": True, "kingdee_no": f"MOCK-{payload['voucher_no']}", "message": "mock，未发送至金蝶"}

    def get_balance(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": True,
            "account_code": payload.get("kingdee_account_code"),
            "balance": payload["local_balance"],
            "message": "使用本地 mock 同步，未调用真实金蝶接口",
        }


def get_kingdee_adapter() -> KingdeeAdapter:
    """未来可在此替换为已确认产品版本的真实适配器。"""
    return MockKingdeeAdapter()
