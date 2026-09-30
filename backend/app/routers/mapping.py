"""地质填图接口。

除原有的列表/明细/登记/状态流转外，提供成果图版本轨道工作区接口：
- POST /draft          暂存拖动后的草图（不产生版本）
- POST /revisions      提交确认（乐观锁，冲突 409）
- GET  /catalog        图幅名称目录（由确认版本投影生成）
- GET  /references     报告引用汇总清单（按钉住修订号回填）
- POST /references     登记报告引用（钉住具体修订号）
- GET  /events         查看一条版本轨道的事件流
- POST /replay         事件重放，重建全部投影
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.mapping import MappingService
from app.versioning import RevisionConflict, StreamNotFound, VersioningError

router = APIRouter(prefix="/api/mapping", tags=["地质填图"])

service = MappingService()

LIST_FIELDS = ["图幅编号", "图幅名称", "比例尺", "填图面积", "填图人员", "野外日期", "室内整理", "填图状态"]
STATUSES = ["野外进行", "室内整理", "已验收", "已出版"]


def _fail(exc: VersioningError) -> HTTPException:
    if isinstance(exc, RevisionConflict):
        return HTTPException(status_code=409, detail={
            "message": str(exc),
            "current_revision": exc.current_revision,
            "server_state": exc.server_state,
        })
    if isinstance(exc, StreamNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


# 固定路径必须声明在 /{entry_id} 之前，否则会被当成 entry_id 匹配。


@router.get("/workspace")
def workspace() -> dict[str, Any]:
    """成果图版本轨道工作区：刷新或重新进入时读到当前确认版本与草稿状态。"""
    return service.workspace()


@router.get("/catalog")
def catalog(keyword: str | None = Query(default=None, description="按图幅编号或名称检索")) -> dict[str, Any]:
    """图幅名称目录：与填图详情共用同一份确认版本投影，不另存副本。"""
    items = service.catalog(keyword=keyword)
    return {"items": items, "total": len(items)}


@router.get("/references")
def reference_summary() -> dict[str, Any]:
    """报告引用汇总清单：图幅名称、确认结论、地图标注全部取自钉住版本。"""
    items = service.reference_summary()
    return {"items": items, "total": len(items)}


@router.post("/references", response_model=ActionResult)
def register_reference(payload: EntryPayload) -> ActionResult:
    """登记报告引用：钉住具体修订号，成果图出新版本后引用仍指向被引用的版本。"""
    try:
        ref = service.register_reference(payload.values)
    except VersioningError as exc:
        raise _fail(exc) from exc
    return ActionResult(ok=True, message="报告引用已登记并钉住引用版本", entry=ref)


@router.post("/references/repin", response_model=ActionResult)
def repin_reference(payload: EntryPayload) -> ActionResult:
    """让既有引用跟进到指定（默认最新）确认版本；引用记录不新增，汇总清单仍是一份。"""
    try:
        ref = service.repin_reference(payload.values)
    except VersioningError as exc:
        raise _fail(exc) from exc
    return ActionResult(ok=True, message="报告引用已跟进到新的确认版本", entry=ref)


@router.post("/draft", response_model=ActionResult)
def save_draft(payload: EntryPayload) -> ActionResult:
    """暂存拖动后的草图边界；草稿不进事件流，刷新后可继续编辑。"""
    try:
        draft = service.save_draft(payload.values)
    except VersioningError as exc:
        raise _fail(exc) from exc
    return ActionResult(ok=True, message="草图已暂存，尚未生成确认版本", entry=draft)


@router.post("/revisions", response_model=ActionResult)
def submit_revision(payload: EntryPayload) -> ActionResult:
    """提交确认版本：乐观锁校验通过才落新版本；并发修订只落一个版本，冲突请刷新重试。"""
    try:
        entry = service.submit_revision(payload.values)
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={
            "message": str(exc),
            "current_revision": exc.current_revision,
            "server_state": exc.server_state,
        }) from exc
    except VersioningError as exc:
        raise _fail(exc) from exc
    return ActionResult(
        ok=True,
        message=f"成果图确认版本已更新为 R{entry.get('修订号')}，详情/目录/报告引用同步到同一版本",
        entry=entry,
    )


@router.post("/events")
def events(payload: EntryPayload) -> dict[str, Any]:
    """查看一条成果图版本轨道的完整事件流（迁移导入、每次确认、状态流转）。"""
    try:
        items = service.history(payload.values)
    except VersioningError as exc:
        raise _fail(exc) from exc
    return {"unit_key": service.payload_key(payload.values), "events": items, "total": len(items)}


@router.post("/replay")
def replay() -> ActionResult:
    """事件重放：清空投影后从事件流完整重建，核对目录与地图标注仍与详情一致。"""
    result = service.replay()
    return ActionResult(ok=True, message="事件重放完成，各入口投影已重建", entry=result)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出地质填图清单：返回当前过滤条件下的全量确认版本数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "mapping", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按图幅编号或图幅名称检索"),
    status: str | None = Query(default=None, description="野外进行、室内整理、已验收、已出版"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按图幅编号与状态过滤地质填图列表；读的是成果图确认版本投影。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条填图单元明细（确认版本）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"填图单元 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条填图单元，即建立一条新的成果图版本轨道（R1）。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"登记失败：{'、'.join(missing)}")
    return ActionResult(ok=True, message="填图单元已登记，成果图版本轨道 R1 已建立", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条填图单元执行开始野外、完成整理、申请验收；状态流转不改确认修订号。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
