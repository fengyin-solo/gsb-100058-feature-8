"""矿产评价接口：维护矿化线索，覆盖安排踏勘、开始评价、提交结论等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.mineral import MineralService

router = APIRouter(prefix="/api/mineral", tags=["矿产评价"])

service = MineralService()

LIST_FIELDS = ["线索编号", "勘探区", "矿种", "矿化类型", "发现方式", "踏勘日期", "评价结论", "线索状态"]
STATUSES = ["待踏勘", "踏勘中", "评价中", "已评价"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按线索编号检索"),
    status: str | None = Query(default=None, description="待踏勘、踏勘中、评价中、已评价"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按线索编号与状态过滤矿产评价列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条矿化线索明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"矿化线索 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条矿化线索，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="矿化线索已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条矿化线索执行安排踏勘、开始评价、提交结论；不允许的动作会被拦下并说明原因。"""
    action = str(payload.action or payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出矿产评价清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "mineral", "total": total, "items": items}
