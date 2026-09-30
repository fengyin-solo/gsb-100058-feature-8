"""地质填图接口：成果图版本轨道 + 填图单元状态流转。

版本相关路径（catalog / annotations / workspace / export 等静态路径）必须
声明在 ``/{entry_id}`` 之前，否则会被动态路径抢先匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult,
    BoundaryRevisionPayload,
    ConfirmRevisionPayload,
    EntryPayload,
    PageResult,
)
from app.services.mapping import MappingService
from app.versioning import RevisionConflict, VersionError

router = APIRouter(prefix="/api/mapping", tags=["地质填图"])

service = MappingService()

LIST_FIELDS = ["图幅编号", "图幅名称", "比例尺", "填图面积", "填图人员", "野外日期", "室内整理", "填图状态"]
STATUSES = ["野外进行", "室内整理", "已验收", "已出版"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按图幅编号检索"),
    status: str | None = Query(default=None, description="野外进行、室内整理、已验收、已出版"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按图幅编号与状态过滤地质填图列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/catalog")
def sheet_catalog() -> dict[str, Any]:
    """图幅名称目录：版本号与审定结论的唯一投影，填图详情与报告引用都对它。"""
    entries = service.catalog()
    return {"module": "mapping", "view": "sheet_catalog", "total": len(entries), "items": entries}


@router.get("/annotations")
def map_annotations() -> dict[str, Any]:
    """地图标注：与目录同从事务投影重放，刷新后读到同一确认版本。"""
    items = service.annotations()
    return {"module": "mapping", "view": "map_annotations", "total": len(items), "items": items}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出地质填图清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "mapping", "total": total, "items": items}


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条填图单元（自动建立版本轨道）；比例尺不同、编号相同也允许并存。"""
    try:
        entry, missing = service.create_entry(payload.values)
    except VersionError as exc:
        return ActionResult(ok=False, message=str(exc))
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="填图单元已登记，成果图版本轨道已建立（v1）", entry=entry)


@router.get("/{entry_id}/workspace")
def get_workspace(entry_id: int) -> dict[str, Any]:
    """工作区读数：确认版本 + 待确认草稿全部来自事件流，刷新页面读到同一份。"""
    try:
        return service.workspace(entry_id)
    except VersionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{entry_id}/replay/{revision}")
def replay_revision(entry_id: int, revision: int) -> dict[str, Any]:
    """按修订号重放历史成果图：既有成果按原审定版本保留，可随时取回核对。"""
    try:
        return service.replay_revision(entry_id, revision)
    except VersionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{entry_id}/boundary", response_model=ActionResult)
def save_boundary(entry_id: int, payload: BoundaryRevisionPayload) -> ActionResult:
    """拖动单元边界后保存修订：乐观锁校验，并发修订只落一个版本。

    目录投影与地图标注在同一事务内刷新；事务失败二者一起回滚。
    """
    try:
        result = service.save_boundary(
            entry_id,
            geometry=payload.geometry,
            expected_revision=payload.expected_revision,
            note=payload.note or "",
            editor=payload.editor or "",
        )
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VersionError as exc:
        return ActionResult(ok=False, message=str(exc))
    workspace = service.workspace(entry_id)
    return ActionResult(
        ok=True,
        message=(
            f"边界修订 r{result['revision']} 已落入版本轨道（基于 v{result['base_revision']}），"
            "确认前不替换成果图；已同步刷新目录与地图标注"
        ),
        entry=workspace,
    )


@router.post("/{entry_id}/confirm", response_model=ActionResult)
def confirm_revision(entry_id: int, payload: ConfirmRevisionPayload) -> ActionResult:
    """确认成果图：审定结论同步到填图详情、图幅名称目录、报告引用汇总。"""
    try:
        workspace = service.confirm_revision(
            entry_id,
            revision=payload.revision,
            expected_revision=payload.expected_revision,
            conclusion=payload.conclusion,
            editor=payload.editor or "",
        )
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VersionError as exc:
        return ActionResult(ok=False, message=str(exc))
    return ActionResult(
        ok=True,
        message=f"成果图已确认为 v{payload.revision}，审定结论已同步至详情、目录与报告引用",
        entry=workspace,
    )


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条填图单元明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"填图单元 {entry_id} 不存在或已归档")
    return entry


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条填图单元执行开始野外、完成整理、申请验收；不允许的动作会被拦下并说明原因。"""
    action = str(payload.action or payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
