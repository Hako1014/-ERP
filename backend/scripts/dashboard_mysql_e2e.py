"""使用本地 MySQL 验证首页看板接口；所有测试夹具均在事务结束时回滚。"""

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import json
import sys
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.security import create_access_token
from app.db.session import engine
from app.main import app
from app.models.basic import Brand, Category
from app.models.order import Order, OrderItem
from app.models.org import Staff
from app.models.party import Customer
from app.models.product import Product
from app.models.rbac import Role, StaffRole


BUSINESS_ZONE = ZoneInfo("Asia/Shanghai")
EXCLUDED_STATUSES = ("CANCELLED", "CANCELED", "DELETED")


def utc_naive(day):
    return datetime.combine(day, time.min, BUSINESS_ZONE).astimezone(timezone.utc).replace(tzinfo=None)


def sales_totals(db: Session, start_day, end_day):
    start, end = utc_naive(start_day), utc_naive(end_day + timedelta(days=1))
    effective_time = func.coalesce(Order.pay_time, Order.order_time)
    count, amount = db.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.order_amount), 0)).where(
            Order.pay_status == "PAID",
            Order.status.not_in(EXCLUDED_STATUSES),
            effective_time >= start,
            effective_time < end,
        )
    ).one()
    return int(count), Decimal(amount)


def dimension_amount(db: Session, start_day, end_day):
    start, end = utc_naive(start_day), utc_naive(end_day + timedelta(days=1))
    effective_time = func.coalesce(Order.pay_time, Order.order_time)
    amount = db.scalar(
        select(func.coalesce(func.sum(func.coalesce(OrderItem.amount, OrderItem.qty * OrderItem.price)), 0))
        .select_from(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.pay_status == "PAID",
            Order.status.not_in(EXCLUDED_STATUSES),
            OrderItem.is_gift.is_(False),
            effective_time >= start,
            effective_time < end,
        )
    )
    return Decimal(amount)


def main():
    today = datetime.now(BUSINESS_ZONE).date()
    marker = uuid4().hex[:12]
    with engine.connect() as connection:
        transaction = connection.begin()
        db = Session(bind=connection, autoflush=False, expire_on_commit=False)
        original_override = app.dependency_overrides.get(get_db)

        def override_get_db():
            yield db

        try:
            admin = db.execute(
                select(Staff)
                .join(StaffRole, StaffRole.staff_id == Staff.id)
                .join(Role, Role.id == StaffRole.role_id)
                .where(Role.code == "ADMIN")
                .limit(1)
            ).scalar_one()
            products = db.scalars(select(Product).order_by(Product.id).limit(2)).all()
            assert len(products) == 2, "本地 MySQL 缺少可用于验证分类和品牌的商品"

            # 临时补齐空缺维度；商品关联只在当前事务中改变，不持久化。
            categories = [Category(name=f"E2E分类-{marker}-{index}") for index in (1, 2)]
            brand = Brand(name=f"E2E品牌-{marker}")
            customer = Customer(
                code=f"E2E-{marker}",
                name=f"E2E客户-{marker}",
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            db.add_all([*categories, brand, customer])
            db.flush()
            products[0].category_id = categories[0].id
            products[1].category_id = categories[1].id
            products[1].brand_id = brand.id
            db.flush()

            app.dependency_overrides[get_db] = override_get_db
            client = TestClient(app)
            headers = {"Authorization": f"Bearer {create_access_token(admin.id, admin.name, [])}"}
            unauthenticated = client.get("/dashboard/overview")
            assert unauthenticated.status_code == 401

            overviews = {}
            for period in ("day", "week", "month"):
                response = client.get(
                    "/dashboard/overview",
                    params={"trend_period": period},
                    headers=headers,
                )
                assert response.status_code == 200, response.text
                payload = response.json()
                assert payload["code"] == "0", payload
                data = payload["data"]
                overviews[period] = data
                assert data["as_of"] == today.isoformat()
                assert data["timezone"] == "Asia/Shanghai"
                assert data["sales_trend"]["period"] == period

                items = data["sales_trend"]["items"]
                start_day = datetime.fromisoformat(data["sales_trend"]["start_date"]).date()
                count, amount = sales_totals(db, start_day, today)
                assert sum(item["order_count"] for item in items) == count
                assert sum(Decimal(item["amount"]) for item in items) == amount

                metrics = {item["key"]: item for item in data["metrics"]}
                today_count, today_amount = sales_totals(db, today, today)
                assert metrics["today_order_count"]["value"] == today_count
                assert Decimal(metrics["today_order_amount"]["value"]) == today_amount
                assert metrics["customer_growth"]["value"] >= 1
                assert len(data["recent_orders"]) > 0
                assert len(data["hot_products"]) > 0

                month_start = today.replace(day=1)
                expected_dimension_amount = dimension_amount(db, month_start, today)
                for key in ("category_sales", "brand_sales"):
                    assert len(data[key]) >= 2, f"{key} 未展示补齐后的维度"
                    assert sum(Decimal(item["amount"]) for item in data[key]) == expected_dimension_amount
                print(f"{period}: 指标、趋势、分类、品牌、订单、热门商品与 MySQL 一致")

            invalid = client.get(
                "/dashboard/overview",
                params={"trend_period": "invalid"},
                headers=headers,
            )
            assert invalid.status_code == 422
            if "--payload" in sys.argv:
                print("E2E_PAYLOAD=" + json.dumps(overviews, ensure_ascii=False, default=str))
        finally:
            if original_override is None:
                app.dependency_overrides.pop(get_db, None)
            else:
                app.dependency_overrides[get_db] = original_override
            transaction.rollback()
            db.close()
    print("E2E 通过；临时补充数据已回滚")


if __name__ == "__main__":
    main()
