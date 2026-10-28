"""短信发送适配接口与本地 mock；不包含真实服务商协议。"""

from typing import Protocol


class SmsAdapter(Protocol):
    def send_reset_code(self, phone: str, code: str) -> None: ...


class MockSmsAdapter:
    def send_reset_code(self, phone: str, code: str) -> None:
        del phone, code


def get_sms_adapter() -> SmsAdapter:
    return MockSmsAdapter()
