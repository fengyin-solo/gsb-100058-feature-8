"""地球物理接口：维护物探测线，覆盖开始施测、完成采集、提交处理等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.geophysics import GeophysicsService

router = APIRouter(prefix="/api/geophysics", tags=["地球物理"])

service = GeophysicsService()

LIST_FIELDS = ["测线编号", "勘探区", "物探方法", "测线长度", "点距", "施测日期", "数据质量", "测线状态"]
STATUSES = ["待施测", "施测中", "已采集", "数据处理", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按测线编号检索"),
    status: str | None = Query(default=None, description="待施测、施测中、已采集、数据处理、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按测线编号与状态过滤地球物理列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条物探测线明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"物探测线 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条物探测线，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="物探测线已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条物探测线执行开始施测、完成采集、提交处理；不允许的动作会被拦下并说明原因。"""
    action = str(payload.action or payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出地球物理清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "geophysics", "total": total, "items": items}
