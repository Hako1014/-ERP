"""金蝶接口边界和 mock 行为；不调用外部系统。"""

from decimal import Decimal

import pytest

from app.core.errors import BizException, ErrorCode
from app.integrations.kingdee import MockKingdeeAdapter, get_kingdee_adapter


def test_mock_subjects_voucher_and_balance():
    adapter = get_kingdee_adapter()
    assert isinstance(adapter, MockKingdeeAdapter)
    subjects = adapter.list_subjects()
    assert any(item["code"] == "1001" for item in subjects)
    voucher = adapter.send_voucher({"voucher_no": "PZ-001"})
    assert voucher == {"success": True, "kingdee_no": "MOCK-PZ-001", "message": "mock，未发送至金蝶"}
    balance = adapter.get_balance({"kingdee_account_code": "KD-1", "local_balance": "123.45"})
    assert balance["success"] is True
    assert Decimal(balance["balance"]) == Decimal("123.45")


def test_adapter_can_be_replaced_without_real_network(monkeypatch):
    import app.integrations.kingdee as kingdee

    class BrokenAdapter(MockKingdeeAdapter):
        def list_subjects(self):
            raise BizException(ErrorCode.CONFLICT, "金蝶不可用")

    monkeypatch.setattr(kingdee, "get_kingdee_adapter", lambda: BrokenAdapter())
    with pytest.raises(BizException, match="金蝶不可用"):
        kingdee.get_kingdee_adapter().list_subjects()
