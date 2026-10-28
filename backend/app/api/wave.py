from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import wave_service

router = APIRouter(tags=["订单管理"])

read_order = require_permission("module:order")
manage_wave = require_permission("order:ship")


class WaveStrategyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    logistics_id: int | None = None
    warehouse_id: int | None = None
    shop_ids: list[int] = Field(default_factory=list, max_length=500)
    wave_order_count: int = Field(default=50, ge=1, le=5000)
    load_no_type: str = Field(default="BY_EXPRESS", max_length=16)
    sort_type: str = Field(default="DEFAULT", max_length=16)
    is_enabled: bool = True
    description: str | None = Field(default=None, max_length=255)


class WaveStrategyUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    logistics_id: int | None = None
    warehouse_id: int | None = None
    shop_ids: list[int] | None = Field(default=None, max_length=500)
    wave_order_count: int | None = Field(default=None, ge=1, le=5000)
    load_no_type: str | None = Field(default=None, max_length=16)
    sort_type: str | None = Field(default=None, max_length=16)
    is_enabled: bool | None = None
    description: str | None = Field(default=None, max_length=255)


class RunWaveRequest(BaseModel):
    strategy_ids: list[int] = Field(..., min_length=1, max_length=20)
    order_ids: list[int] | None = Field(
        default=None, min_length=1, max_length=5000
    )


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


@router.get("/wave-strategies", response_model=Result[dict])
def list_wave_strategies(
    keyword: str | None = None,
    is_enabled: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    return Result.ok(
        wave_service.list_wave_strategies(
            db,
            keyword=keyword,
            is_enabled=is_enabled,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/wave-strategies", response_model=Result[dict])
def create_wave_strategy(
    req: WaveStrategyCreateRequest,
    staff: Staff = Depends(manage_wave),
    db: Session = Depends(get_db),
):
    strategy = wave_service.create_wave_strategy(
        db,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        wave_service.wave_strategy_dict(db, strategy),
        message="波次策略已创建",
    )


@router.get("/wave-strategies/{strategy_id}", response_model=Result[dict])
def get_wave_strategy(
    strategy_id: int,
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    strategy = wave_service.get_wave_strategy_or_404(db, strategy_id)
    return Result.ok(wave_service.wave_strategy_dict(db, strategy))


@router.put("/wave-strategies/{strategy_id}", response_model=Result[dict])
def update_wave_strategy(
    strategy_id: int,
    req: WaveStrategyUpdateRequest,
    staff: Staff = Depends(manage_wave),
    db: Session = Depends(get_db),
):
    strategy = wave_service.update_wave_strategy(
        db,
        strategy_id=strategy_id,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        wave_service.wave_strategy_dict(db, strategy),
        message="波次策略已更新",
    )


@router.delete("/wave-strategies/{strategy_id}", response_model=Result[dict])
def delete_wave_strategy(
    strategy_id: int,
    staff: Staff = Depends(manage_wave),
    db: Session = Depends(get_db),
):
    wave_service.delete_wave_strategy(
        db,
        strategy_id=strategy_id,
        staff=staff,
    )
    return Result.ok(message="波次策略已删除")


@router.get(
    "/wave-strategies/{strategy_id}/pending-count",
    response_model=Result[dict],
)
def get_wave_strategy_pending_count(
    strategy_id: int,
    order_ids: list[int] | None = Query(default=None),
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    return Result.ok(
        {
            "strategy_id": strategy_id,
            "pending_count": wave_service.pending_order_count(
                db,
                strategy_id=strategy_id,
                order_ids=order_ids,
            ),
        }
    )


@router.post("/waves/run", response_model=Result[dict])
def run_wave(
    req: RunWaveRequest,
    staff: Staff = Depends(manage_wave),
    db: Session = Depends(get_db),
):
    return Result.ok(
        wave_service.run_wave_strategies(
            db,
            strategy_ids=req.strategy_ids,
            order_ids=req.order_ids,
            staff=staff,
        ),
        message="波次运行完成",
    )


@router.get("/waves", response_model=Result[dict])
def list_waves(
    strategy_id: int | None = None,
    express_type: str | None = None,
    keyword: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    return Result.ok(
        wave_service.list_waves(
            db,
            strategy_id=strategy_id,
            express_type=express_type,
            keyword=keyword,
            start_time=start_time,
            end_time=end_time,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/waves/{wave_id}", response_model=Result[dict])
def get_wave(
    wave_id: int,
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    wave = wave_service.get_wave_or_404(db, wave_id)
    return Result.ok(wave_service.wave_dict(db, wave, with_orders=True))


@router.get(
    "/waves/{wave_id}/load-numbers",
    response_model=Result[list[dict]],
)
def list_wave_load_numbers(
    wave_id: int,
    staff: Staff = Depends(read_order),
    db: Session = Depends(get_db),
):
    return Result.ok(
        wave_service.list_wave_load_numbers(db, wave_id=wave_id)
    )


@router.delete("/waves/{wave_id}", response_model=Result[dict])
def delete_wave(
    wave_id: int,
    staff: Staff = Depends(manage_wave),
    db: Session = Depends(get_db),
):
    wave_service.delete_wave(db, wave_id=wave_id, staff=staff)
    return Result.ok(message="波次已删除")
