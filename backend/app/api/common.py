from io import BytesIO

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.errors import BizException, ErrorCode
from app.api.deps import get_current_staff
from app.services.excel import TEMPLATE_REGISTRY, build_template

router = APIRouter(tags=["通用"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/common/excel-template/{name}")
def download_template(name: str, staff=Depends(get_current_staff)) -> StreamingResponse:
    spec = TEMPLATE_REGISTRY.get(name)
    if spec is None:
        raise BizException(ErrorCode.NOT_FOUND, f"模板不存在：{name}")
    data = build_template(spec)
    return StreamingResponse(
        BytesIO(data),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
    )
