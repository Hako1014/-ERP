"""Opt-in real-MySQL cross-module reconciliation test."""

import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.environ.get("ERP_MYSQL_E2E") != "1", reason="requires local MySQL")
def test_purchase_stock_order_shipment_finance_closure():
    result = subprocess.run(
        [sys.executable, "-m", "scripts.e2e_task070"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert "TASK-070 MySQL rollback verified" in result.stdout
