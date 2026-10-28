"""TASK-057 order flow smoke test against a running local backend."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx


BASE_URL = "http://127.0.0.1:8000"
ACCOUNT = "guanliyuan"
PASSWORD = "123456"
REQUEST_TIMEOUT = 30.0


class ApiError(RuntimeError):
    pass


class ApiClient:
    def __init__(self) -> None:
        self.client = httpx.Client(
            base_url=BASE_URL,
            timeout=REQUEST_TIMEOUT,
            trust_env=False,
        )
        self.token: str | None = None

    def close(self) -> None:
        self.client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = self.client.request(
            method,
            path,
            headers=headers,
            json=json_body,
            params=params,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiError(
                f"{method} {path} returned non-JSON "
                f"HTTP {response.status_code}: {response.text[:200]}"
            ) from exc
        if response.status_code >= 400 or str(payload.get("code")) != "0":
            raise ApiError(
                f"{method} {path} failed with HTTP {response.status_code}: "
                f"{json.dumps(payload, ensure_ascii=False)}"
            )
        return payload.get("data")

    def login(self) -> dict[str, Any]:
        data = self.request(
            "POST",
            "/auth/login",
            json_body={"account": ACCOUNT, "password": PASSWORD},
        )
        self.token = data["token"]
        return data


def get_stock(client: ApiClient) -> Decimal:
    data = client.request(
        "GET",
        "/stock-summary",
        params={
            "warehouse_id": 1,
            "product_id": 1,
            "sku_id": 1,
            "page": 1,
            "page_size": 20,
        },
    )
    items = data.get("items") or []
    if not items:
        return Decimal("0")
    return Decimal(str(items[0]["total_qty"]))


def ensure_logistics(client: ApiClient) -> int:
    rows = client.request("GET", "/logistics", params={"is_enabled": True})
    if rows:
        return int(rows[0]["id"])
    created = client.request(
        "POST",
        "/logistics",
        json_body={
            "name": "TASK-057 E2E Logistics",
            "company": "SF Express",
            "is_default": True,
            "is_enabled": True,
        },
    )
    return int(created["id"])


def ensure_order_approval_flow(
    client: ApiClient,
    staff_id: int,
    staff_name: str,
) -> int:
    flows = client.request("GET", "/approval/flows")
    for flow in flows:
        if flow.get("doc_type") == "order" and flow.get("approvers"):
            return int(flow["id"])
    created = client.request(
        "POST",
        "/approval/flows",
        json_body={
            "name": "TASK-057 E2E Order Approval",
            "doc_type": "order",
            "approvers": [
                {
                    "step": 1,
                    "name": "Order Review",
                    "approver_ids": [staff_id],
                }
            ],
        },
    )
    if not created.get("approvers"):
        raise ApiError(
            f"approval flow created without approvers: "
            f"{json.dumps(created, ensure_ascii=False)}"
        )
    return int(created["id"])


def create_order(
    client: ApiClient,
    *,
    run_no: str,
    logistics_id: int,
) -> int:
    created = client.request(
        "POST",
        "/orders",
        json_body={
            "order_no": run_no,
            "shop_id": 1,
            "warehouse_id": 1,
            "logistics_id": logistics_id,
            "buyer_nick": "TASK057-E2E",
            "receiver_name": "E2E Receiver",
            "receiver_phone": "13800000000",
            "receiver_province": "Zhejiang",
            "receiver_city": "Hangzhou",
            "receiver_address": "TASK-057 E2E Address",
            "order_time": datetime.now().isoformat(timespec="seconds"),
            "pay_time": datetime.now().isoformat(timespec="seconds"),
            "pay_status": "PAID",
            "freight": "0.00",
            "paid_amount": "20.00",
            "items": [
                {
                    "product_id": 1,
                    "sku_id": 1,
                    "qty": "1.000",
                    "price": "20.00",
                    "is_gift": False,
                }
            ],
            "remark": "TASK-057 E2E",
        },
    )
    order_id = int(created["id"])
    if created["status"] != "NEW" or created["audit_status"] != "PENDING":
        raise ApiError(f"new order state is unexpected: {created}")
    return order_id


def approve_order(client: ApiClient, order_id: int) -> dict[str, Any]:
    result = client.request(
        "POST",
        f"/orders/{order_id}/approve",
        json_body={"comment": "TASK-057 E2E approval"},
    )
    if result["status"] != "PENDING" or result["audit_status"] != "APPROVED":
        raise ApiError(f"order approval state is unexpected: {result}")
    if not result.get("checks", {}).get("stock_checked"):
        raise ApiError(f"order stock check did not run: {result.get('checks')}")
    return result


def run_wave(
    client: ApiClient,
    *,
    order_id: int,
    logistics_id: int,
    run_no: str,
) -> int:
    strategy = client.request(
        "POST",
        "/wave-strategies",
        json_body={
            "name": f"{run_no}-STRATEGY",
            "logistics_id": logistics_id,
            "warehouse_id": 1,
            "shop_ids": [1],
            "wave_order_count": 10,
            "load_no_type": "BY_EXPRESS",
            "sort_type": "LOCATION",
            "is_enabled": True,
            "description": "TASK-057 E2E",
        },
    )
    result = client.request(
        "POST",
        "/waves/run",
        json_body={
            "strategy_ids": [int(strategy["id"])],
            "order_ids": [order_id],
        },
    )
    if result["wave_count"] != 1 or result["order_count"] != 1:
        raise ApiError(f"wave run result is unexpected: {result}")
    wave = result["results"][0]["waves"][0]
    return int(wave["id"])


def acquire_waybill(
    client: ApiClient,
    *,
    order_id: int,
    waybill_no: str,
) -> dict[str, Any]:
    result = client.request(
        "POST",
        "/print/waybills/acquire",
        json_body={
            "order_ids": [order_id],
            "print_type": "THERMAL",
            "waybill_nos": {str(order_id): waybill_no},
        },
    )
    if result["count"] != 1 or result["items"][0]["waybill_no"] != waybill_no:
        raise ApiError(f"waybill acquisition result is unexpected: {result}")
    return result


def create_print_batch(
    client: ApiClient,
    *,
    order_id: int,
    waybill_no: str,
) -> dict[str, Any]:
    result = client.request(
        "POST",
        "/print/batches",
        json_body={
            "order_ids": [order_id],
            "print_type": "THERMAL",
            "printer_name": "TASK-057 E2E Printer",
            "include_shipping_slip": True,
            "sort_type": "LOCATION",
            "waybill_nos": {str(order_id): waybill_no},
        },
    )
    if result["order_count"] != 1:
        raise ApiError(f"print batch result is unexpected: {result}")
    if result["pick_list"] is None or len(result["delivery_lists"]) != 1:
        raise ApiError(f"print batch documents are incomplete: {result}")
    return result


def download_print_pdf(
    client: ApiClient,
    *,
    batch_no: str,
) -> int:
    response = client.client.get(
        f"/print/batches/{batch_no}/pdf",
        params={"document_type": "ALL"},
        headers={"Authorization": f"Bearer {client.token}"},
    )
    if response.status_code != 200:
        raise ApiError(
            f"print PDF download failed with HTTP {response.status_code}: "
            f"{response.text[:200]}"
        )
    content = response.content
    if not content.startswith(b"%PDF") or len(content) < 1000:
        raise ApiError(
            f"print PDF content is invalid: {len(content)} bytes, "
            f"prefix={content[:8]!r}"
        )
    return len(content)


def scan_shipment(
    client: ApiClient,
    *,
    waybill_no: str,
) -> dict[str, Any]:
    result = client.request(
        "POST",
        "/shipments/scan",
        json_body={
            "scan_code": waybill_no,
            "qty": "1.000",
            "package_no": 1,
            "remark": "TASK-057 E2E scan shipment",
        },
    )
    if not result["completed"]:
        raise ApiError(f"scan shipment did not complete the order: {result}")
    if result["shipment"]["status"] != "SHIPPED":
        raise ApiError(f"shipment state is unexpected: {result['shipment']}")
    return result


def verify_order_and_stock(
    client: ApiClient,
    *,
    order_id: int,
    order_no: str,
    stock_before: Decimal,
) -> dict[str, Any]:
    order = client.request("GET", f"/orders/{order_id}")
    if order["status"] != "SHIPPED":
        raise ApiError(f"order final state is unexpected: {order}")
    stock_after = get_stock(client)
    expected = stock_before - Decimal("1.000")
    if stock_after != expected:
        raise ApiError(
            f"stock deduction mismatch: before={stock_before}, "
            f"after={stock_after}, expected={expected}"
        )
    flows = client.request(
        "GET",
        "/stock-flows",
        params={
            "order_no": order_no,
            "direction": "OUT",
            "page": 1,
            "page_size": 20,
        },
    )
    outbound = [
        item
        for item in flows["items"]
        if str(item.get("qty")) in {"1.000", "1"}
    ]
    if not outbound:
        raise ApiError(f"order outbound stock flow is missing: {flows}")
    return {
        "order_id": order_id,
        "order_no": order["order_no"],
        "order_status": order["status"],
        "audit_status": order["audit_status"],
        "tracking_no": order["tracking_no"],
        "stock_before": str(stock_before),
        "stock_after": str(stock_after),
        "stock_flow_id": outbound[0]["id"],
    }


def main() -> None:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    run_no = f"E2E-TASK057-{stamp}"
    waybill_no = f"SF{stamp}057"
    client = ApiClient()
    try:
        login = client.login()
        logistics_id = ensure_logistics(client)
        ensure_order_approval_flow(
            client,
            int(login["staff_id"]),
            str(login["name"]),
        )
        stock_before = get_stock(client)
        if stock_before < Decimal("1.000"):
            raise ApiError(
                f"insufficient stock for E2E: current={stock_before}"
            )

        order_id = create_order(
            client,
            run_no=run_no,
            logistics_id=logistics_id,
        )
        approve_order(client, order_id)
        wave_id = run_wave(
            client,
            order_id=order_id,
            logistics_id=logistics_id,
            run_no=run_no,
        )
        waybill = acquire_waybill(
            client,
            order_id=order_id,
            waybill_no=waybill_no,
        )
        batch = create_print_batch(
            client,
            order_id=order_id,
            waybill_no=waybill_no,
        )
        pdf_bytes = download_print_pdf(
            client,
            batch_no=str(batch["batch_no"]),
        )
        shipment = scan_shipment(client, waybill_no=waybill_no)
        verification = verify_order_and_stock(
            client,
            order_id=order_id,
            order_no=run_no,
            stock_before=stock_before,
        )

        summary = {
            "run_no": run_no,
            "logistics_id": logistics_id,
            "wave_id": wave_id,
            "waybill_no": waybill["items"][0]["waybill_no"],
            "print_batch_no": batch["batch_no"],
            "pdf_bytes": pdf_bytes,
            "shipment_id": shipment["shipment"]["id"],
            **verification,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
