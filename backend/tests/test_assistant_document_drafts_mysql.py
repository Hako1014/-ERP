"""TASK-108/TASK-112 本机 MySQL 定向验收；测试业务行回滚，附件只写入临时目录。"""

import io
import os
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import func, select

from app.api import assistant as assistant_api
from app.api import assistant_document_drafts as draft_api
from app.api import assistant_extractions as extraction_api
from app.api.purchase_plan import router as purchase_plan_router
from app.core.config import get_settings
from app.core.errors import ErrorCode
from app.core.security import create_access_token
from app.integrations.assistant_provider import DeepSeekProvider, ProviderFailure
from app.models.basic import Unit
from app.models.assistant_document_draft import AssistantDocumentDraft
from app.models.assistant_extraction import AssistantExtraction
from app.models.org import Staff
from app.models.party import Supplier
from app.models.product import Product
from app.models.purchase import PurchaseOrder, PurchasePlan, PurchasePlanAttachment, PurchasePlanItem
from tests.test_assistant_attachments_mysql import data, save_body, upload
from tests.test_assistant_extractions_mysql import _extract, _image_bytes, env as extraction_env
from tests.test_assistant_conversations_mysql import create, ctx  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("ERP_TASK104_MYSQL") != "1",
    reason="仅显式启用本机 MySQL TASK-108 验收",
)


@pytest.fixture
def env(extraction_env):
    extraction_env.client.app.include_router(draft_api.router)
    extraction_env.client.app.include_router(purchase_plan_router)
    marker = extraction_env.marker
    with extraction_env.factory() as db:
        supplier = Supplier(
            company_id=extraction_env.ids.company,
            code=f"{marker}-SUP",
            name="Demo Supplier",
            is_active=True,
        )
        foreign_supplier = Supplier(
            company_id=extraction_env.ids.foreign_company,
            code=f"{marker}-FOREIGN-SUP",
            name="Foreign Supplier",
            is_active=True,
        )
        unit = Unit(company_id=extraction_env.ids.company, name="Piece", code="pcs")
        db.add_all([supplier, foreign_supplier, unit])
        db.flush()
        product = Product(
            company_id=extraction_env.ids.company,
            code=f"{marker}-PRODUCT",
            name="Widget",
            default_supplier_id=supplier.id,
            main_unit_id=unit.id,
            is_disabled=False,
        )
        foreign_product = Product(
            company_id=extraction_env.ids.foreign_company,
            code=f"{marker}-FOREIGN-PRODUCT",
            name="Foreign Widget",
            default_supplier_id=foreign_supplier.id,
            is_disabled=False,
        )
        db.add_all([product, foreign_product])
        db.commit()
        extraction_env.supplier_id = supplier.id
        extraction_env.product_id = product.id
        extraction_env.foreign_supplier_id = foreign_supplier.id
        extraction_env.foreign_product_id = foreign_product.id
    return extraction_env


def test_attachment_draft_create_retry_scope_and_business_copy(env):
    conversation = create(env, title=f"{env.marker}-TASK108")

    # Reuse the established image extraction path; its provider is deterministic and all DB
    # changes remain inside the MySQL fixture's outer rollback transaction.
    from tests.test_assistant_extractions_mysql import _image_bytes

    attachment = data(upload(env, conversation, f"{env.marker}-invoice.png", _image_bytes()))
    saved = env.client.post(
        "/assistant/attachments/messages",
        json=save_body(conversation, attachment),
    )
    assert saved.status_code == 200, saved.text
    extraction_response, _ = _extract(env, attachment)
    assert extraction_response.status_code == 200, extraction_response.text
    extraction = extraction_response.json()["data"]
    assert extraction["status"] == "COMPLETED"

    created = env.client.post("/assistant/document-drafts/from-extraction", json={
        "conversation_id": conversation["id"],
        "extraction_id": extraction["id"],
        "request_id": str(uuid4()),
    })
    assert created.status_code == 200, created.text
    draft = created.json()["data"]
    assert draft["source_attachment_id"] == attachment["id"]
    assert draft["source_text"]
    assert draft["ready"] is False

    # 草稿归本人和会话所有；同公司其他员工也不能通过会话 ID 读取。
    hidden = env.client.get(
        f"/assistant/document-drafts?conversation_id={conversation['id']}",
        headers={"Authorization": "Bearer " + create_access_token(env.ids.bob, "other", ["ADMIN"])},
    )
    assert hidden.status_code == 404

    foreign = env.client.put(f"/assistant/document-drafts/{draft['draft_id']}", json={
        "draft_version": draft["version"],
        "edits": {"supplier_id": env.foreign_supplier_id},
    })
    assert foreign.status_code == 400
    foreign_product = env.client.put(f"/assistant/document-drafts/{draft['draft_id']}", json={
        "draft_version": draft["version"],
        "edits": {"items": [{"product_id": env.foreign_product_id}]},
    })
    assert foreign_product.status_code == 400

    edited = env.client.put(f"/assistant/document-drafts/{draft['draft_id']}", json={
        "draft_version": draft["version"],
        "edits": {"plan_date": "2026-10-15T00:00:00"},
    })
    assert edited.status_code == 200, edited.text
    draft = edited.json()["data"]
    assert draft["ready"] is True, draft.get("missing")

    request_id = str(uuid4())
    form = {
        "supplier_id": env.supplier_id,
        "plan_date": "2026-10-15T00:00:00",
        "remark": f"{env.marker} TASK-108 图片识别测试计划",
        "items": [{"product_id": env.product_id, "sku_id": None, "planned_qty": "2.000", "purchase_price": None}],
    }
    confirm_body = {
        "draft_version": draft["version"],
        "request_id": request_id,
        "form": form,
        "attach_source": True,
    }
    confirmed = env.client.post(
        f"/assistant/document-drafts/{draft['draft_id']}/confirm", json=confirm_body,
    )
    assert confirmed.status_code == 200, confirmed.text
    plan = confirmed.json()["data"]
    assert plan["plan_no"] and plan["status"] == "IN_PROGRESS"
    assert plan["audit_status"] == "PENDING"
    assert len(plan["attachments"]) == 1

    replay = env.client.post(
        f"/assistant/document-drafts/{draft['draft_id']}/confirm", json=confirm_body,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["id"] == plan["id"]

    copied = plan["attachments"][0]
    content = env.client.get(
        f"/purchase-plans/{plan['id']}/attachments/{copied['id']}/content"
    )
    assert content.status_code == 200
    assert content.content
    assert content.headers["cache-control"] == "private, no-store"

    # 私人聊天删除后，显式复制出的业务附件仍随采购计划保留。
    current_chat = env.client.get(f"/assistant/conversations/{conversation['id']}").json()["data"]
    deleted = env.client.delete(
        f"/assistant/conversations/{conversation['id']}?version={current_chat['version']}&request_id={uuid4()}"
    )
    assert deleted.status_code == 200, deleted.text
    assert env.client.get(
        f"/purchase-plans/{plan['id']}/attachments/{copied['id']}/content"
    ).status_code == 200

    with env.factory() as db:
        plan_count = db.scalar(select(func.count(PurchasePlan.id)).where(
            PurchasePlan.company_id == env.ids.company,
            PurchasePlan.remark == form["remark"],
        ))
        attachment_count = db.scalar(select(func.count(PurchasePlanAttachment.id)).where(
            PurchasePlanAttachment.plan_id == plan["id"],
            PurchasePlanAttachment.company_id == env.ids.company,
        ))
        assert plan_count == 1
        assert attachment_count == 1


def _saved_image(env, title):
    conversation = create(env, title=title)
    attachment = data(upload(env, conversation, f"{env.marker}-input.png", _image_bytes()))
    saved = env.client.post(
        "/assistant/attachments/messages",
        json=save_body(conversation, attachment),
    )
    assert saved.status_code == 200, saved.text
    return conversation, attachment


def test_non_purchase_extraction_cannot_create_draft_or_business_document(env):
    env.fake.document_type = "other"
    conversation, attachment = _saved_image(env, f"{env.marker}-unsupported")
    extracted, _ = _extract(env, attachment)
    assert extracted.status_code == 200, extracted.text
    record = extracted.json()["data"]
    assert record["status"] == "COMPLETED"
    assert record["result"]["document_type_supported"] is False
    assert record["result"]["supported_for_draft"] is False
    assert record["result"]["created_business_document"] is False

    rejected = env.client.post("/assistant/document-drafts/from-extraction", json={
        "conversation_id": conversation["id"],
        "extraction_id": record["id"],
        "request_id": str(uuid4()),
    })
    assert rejected.status_code == 400
    with env.factory() as db:
        assert db.scalar(select(func.count(AssistantDocumentDraft.id)).where(
            AssistantDocumentDraft.company_id == env.ids.company,
        )) == 0
        assert db.scalar(select(func.count(PurchasePlan.id)).where(
            PurchasePlan.company_id == env.ids.company,
        )) == 0


def test_ambiguous_product_candidates_remain_unresolved_without_business_write(env):
    with env.factory() as db:
        product = db.get(Product, env.product_id)
        db.add(Product(
            company_id=env.ids.company,
            code=f"{env.marker}-DUPLICATE",
            name=product.name,
            main_unit_id=product.main_unit_id,
            is_disabled=False,
        ))
        db.commit()

    conversation, attachment = _saved_image(env, f"{env.marker}-ambiguous")
    extracted, _ = _extract(env, attachment)
    assert extracted.status_code == 200, extracted.text
    record = extracted.json()["data"]
    created = env.client.post("/assistant/document-drafts/from-extraction", json={
        "conversation_id": conversation["id"],
        "extraction_id": record["id"],
        "request_id": str(uuid4()),
    })
    assert created.status_code == 200, created.text
    draft = created.json()["data"]
    assert draft["ready"] is False
    assert draft["items"][0]["product"]["status"] == "ambiguous"
    assert len(draft["items"][0]["product"]["candidates"]) == 2
    with env.factory() as db:
        assert db.scalar(select(func.count(PurchasePlan.id)).where(
            PurchasePlan.company_id == env.ids.company,
        )) == 0


def test_provider_rate_limit_is_reported_without_creating_a_draft(env):
    class RateLimitedProvider:
        async def start_with_tools(self, messages, tools):
            raise ProviderFailure(
                ErrorCode.AI_RATE_LIMITED,
                "provider_rate_limited",
                ErrorCode.AI_RATE_LIMITED.default_message,
                retryable=True,
            )

    env.client.app.dependency_overrides[extraction_api.ASSISTANT_PROVIDER_DEPENDENCY] = (
        lambda: RateLimitedProvider()
    )
    conversation, attachment = _saved_image(env, f"{env.marker}-rate-limit")
    response, request_id = _extract(env, attachment)
    assert response.status_code == 429
    assert response.json()["code"] == ErrorCode.AI_RATE_LIMITED.value
    assert "rate_limited" not in response.text
    with env.factory() as db:
        extraction = db.get(AssistantExtraction, request_id)
        assert extraction.status == "FAILED"
        assert extraction.error_category == "provider_rate_limited"
        assert db.scalar(select(func.count(AssistantDocumentDraft.id)).where(
            AssistantDocumentDraft.company_id == env.ids.company,
        )) == 0
        assert db.scalar(select(func.count(PurchasePlan.id)).where(
            PurchasePlan.company_id == env.ids.company,
        )) == 0


def test_recognition_provider_failure_leaves_no_draft_or_business_document(env):
    class FailedProvider:
        async def start_with_tools(self, messages, tools):
            raise ProviderFailure(
                ErrorCode.AI_PROVIDER_ERROR,
                "provider_service_error",
                ErrorCode.AI_PROVIDER_ERROR.default_message,
                retryable=True,
            )

    env.client.app.dependency_overrides[extraction_api.ASSISTANT_PROVIDER_DEPENDENCY] = (
        lambda: FailedProvider()
    )
    _, attachment = _saved_image(env, f"{env.marker}-provider-failure")
    response, request_id = _extract(env, attachment)
    assert response.status_code == 502
    assert response.json()["code"] == ErrorCode.AI_PROVIDER_ERROR.value
    with env.factory() as db:
        extraction = db.get(AssistantExtraction, request_id)
        assert extraction.status == "FAILED"
        assert extraction.error_category == "provider_service_error"
        assert db.scalar(select(func.count(AssistantDocumentDraft.id)).where(
            AssistantDocumentDraft.company_id == env.ids.company,
        )) == 0
        assert db.scalar(select(func.count(PurchasePlan.id)).where(
            PurchasePlan.company_id == env.ids.company,
        )) == 0


@pytest.mark.skipif(
    os.environ.get("ERP_TASK112_DEEPSEEK_MYSQL") != "1",
    reason="显式启用真实 DeepSeek + 本机 MySQL 回滚验收",
)
def test_real_deepseek_image_to_purchase_plan_api_rollback(env, monkeypatch):
    """真实 Provider 经附件/识别/草稿/确认 API 创建单据；全部 MySQL 写入由 ctx 外层事务回滚。"""
    settings = get_settings()
    if not settings.assistant_api_key:
        pytest.skip("本机未配置 DeepSeek 密钥")
    if settings.assistant_model.casefold() != "deepseek-flash":
        pytest.skip("图片识别验收要求 deepseek-flash")
    settings = settings.model_copy(update={
        "assistant_enabled": True,
        "assistant_provider": "deepseek",
        "assistant_private_dir": str(env.settings.assistant_private_dir),
        "upload_dir": str(env.settings.upload_dir),
    })
    monkeypatch.setattr(assistant_api, "get_settings", lambda: settings)
    monkeypatch.setattr(extraction_api, "get_settings", lambda: settings)
    env.client.app.dependency_overrides[extraction_api.ASSISTANT_PROVIDER_DEPENDENCY] = (
        lambda: DeepSeekProvider(settings)
    )

    conversation = create(env, title=f"{env.marker}-TASK112-DeepSeek")
    text_session = data(env.client.post("/assistant/sessions"))
    text_draft_response = env.client.post("/assistant/document-drafts/parse", json={
        "conversation_id": conversation["id"],
        "request_id": str(uuid4()),
        "session_id": text_session["session_id"],
        "session_version": text_session["version"],
        "mode": "natural",
        "input_text": (
            "请录入一张采购计划：供应商 Demo Supplier，计划日期 2026-10-15；"
            "商品 Widget，单位 pcs，数量 2，备注 Synthetic TASK-112 text acceptance。"
        ),
    })
    assert text_draft_response.status_code == 200, text_draft_response.text
    text_draft = text_draft_response.json()["data"]
    assert text_draft["source_kind"] == "natural"
    assert text_draft["supplier"]["value"]["name"] == "Demo Supplier"
    assert text_draft["items"][0]["product"]["value"]["name"] == "Widget"
    assert text_draft["items"][0]["quantity"]["value"] == "2.000"
    assert text_draft["ready"] is True, text_draft["missing"]

    output = io.BytesIO()
    image = Image.new("RGB", (1200, 700), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    for y, line in (
        (60, "PURCHASE PLAN"),
        (170, "Supplier: Demo Supplier"),
        (250, "Date: 2026-10-15"),
        (360, "Product: Widget"),
        (430, "Product Code: TASK112-WIDGET"),
        (500, "Quantity: 2 pcs"),
        (600, "Remark: Synthetic TASK-112 acceptance only"),
    ):
        draw.text((70, y), line, fill="black", font=font)
    image.save(output, format="PNG")

    attachment = data(upload(env, conversation, f"{env.marker}-purchase-plan.png", output.getvalue()))
    saved = env.client.post(
        "/assistant/attachments/messages",
        json=save_body(conversation, attachment),
    )
    assert saved.status_code == 200, saved.text
    extraction_response, _ = _extract(env, attachment)
    assert extraction_response.status_code == 200, extraction_response.text
    extraction = extraction_response.json()["data"]
    assert extraction["status"] == "COMPLETED", extraction
    assert extraction["result"]["document_type"] == "purchase_plan"
    assert extraction["result"]["fields"]["supplier_name"]["value"] == "Demo Supplier"
    recognized_item = extraction["result"]["items"][0]["fields"]
    assert "widget" in recognized_item["product_name"]["value"].casefold()
    assert recognized_item["quantity"]["value"] == "2"

    created_draft = env.client.post("/assistant/document-drafts/from-extraction", json={
        "conversation_id": conversation["id"],
        "extraction_id": extraction["id"],
        "request_id": str(uuid4()),
    })
    assert created_draft.status_code == 200, created_draft.text
    draft = created_draft.json()["data"]
    edited = env.client.put(f"/assistant/document-drafts/{draft['draft_id']}", json={
        "draft_version": draft["version"],
        "edits": {
            "supplier_id": env.supplier_id,
            "plan_date": "2026-10-15T00:00:00",
            "items": [{"product_id": env.product_id, "planned_qty": "2.000"}],
        },
    })
    assert edited.status_code == 200, edited.text
    draft = edited.json()["data"]
    assert draft["ready"] is True, draft.get("missing")

    request_id = str(uuid4())
    form = {
        "supplier_id": env.supplier_id,
        "plan_date": "2026-10-15T00:00:00",
        "remark": f"{env.marker} TASK-112 DeepSeek rollback acceptance",
        "items": [{"product_id": env.product_id, "sku_id": None, "planned_qty": "2.000", "purchase_price": None}],
    }
    confirm_body = {"draft_version": draft["version"], "request_id": request_id, "form": form}
    confirmed = env.client.post(
        f"/assistant/document-drafts/{draft['draft_id']}/confirm", json=confirm_body,
    )
    assert confirmed.status_code == 200, confirmed.text
    plan = confirmed.json()["data"]
    assert plan["plan_no"] and plan["status"] == "IN_PROGRESS"
    replay = env.client.post(
        f"/assistant/document-drafts/{draft['draft_id']}/confirm", json=confirm_body,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["id"] == plan["id"]

    with env.factory() as db:
        assert db.scalar(select(func.count(PurchasePlan.id)).where(
            PurchasePlan.company_id == env.ids.company,
            PurchasePlan.remark == form["remark"],
        )) == 1
        stored_plan = db.get(PurchasePlan, plan["id"])
        assert stored_plan.status == "IN_PROGRESS" and stored_plan.audit_status == "PENDING"
        plan_items = db.scalars(select(PurchasePlanItem).where(PurchasePlanItem.plan_id == plan["id"])).all()
        assert len(plan_items) == 1 and plan_items[0].purchased_qty == 0
        assert db.scalar(select(func.count(PurchaseOrder.id)).where(PurchaseOrder.plan_id == plan["id"])) == 0
