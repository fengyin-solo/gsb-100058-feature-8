"""地质填图业务规则：成果图版本轨道 + 状态流转。

填图详情列表与单条明细都从版本轨道的统一投影取版本号/审定结论，
图幅名称目录、地图标注、报告引用同样重放这一份事件流，任何入口都不另存。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.versioning import VersionError, version_store

MODULE = "mapping"
REQUIRED_FIELDS = ["图幅编号", "图幅名称", "比例尺"]
STATUS_ORDER = ["野外进行", "室内整理", "已验收", "已出版"]
ACTION_RULES = {"开始野外": "野外进行", "完成整理": "室内整理", "申请验收": "已验收"}
NEGATIVE_ACTIONS = []


class MappingService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        version_store.ensure_initialized()
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("图幅编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [dict(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        version_store.ensure_initialized()
        row = store.find(MODULE, entry_id)
        return None if row is None else dict(row)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        workspace = version_store.create_track(
            sheet_no=str(values["图幅编号"]),
            sheet_name=str(values["图幅名称"]),
            scale=str(values["比例尺"]),
            editor=str(values.get("填图人员") or ""),
        )
        return self.get_entry(int(workspace["track_id"])) or None, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于地质填图可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        try:
            version_store.record_status(entry_id, target)
        except VersionError as exc:
            return None, str(exc)
        return self.get_entry(entry_id), f"填图单元已{action}"

    # ---- 成果图版本轨道 ----

    def catalog(self) -> list[dict[str, Any]]:
        return version_store.catalog_entries()

    def annotations(self) -> list[dict[str, Any]]:
        return version_store.annotations()

    def workspace(self, track_id: int) -> dict[str, Any]:
        return version_store.workspace(track_id)

    def replay_revision(self, track_id: int, revision: int) -> dict[str, Any]:
        return version_store.replay_revision(track_id, revision)

    def save_boundary(
        self,
        track_id: int,
        *,
        geometry: list[list[float]],
        expected_revision: int,
        note: str = "",
        editor: str = "",
    ) -> dict[str, Any]:
        return version_store.save_boundary(
            track_id,
            geometry=geometry,
            expected_revision=expected_revision,
            note=note,
            editor=editor,
        )

    def confirm_revision(
        self,
        track_id: int,
        *,
        revision: int,
        expected_revision: int,
        conclusion: str,
        editor: str = "",
    ) -> dict[str, Any]:
        return version_store.confirm_revision(
            track_id,
            revision=revision,
            expected_revision=expected_revision,
            conclusion=conclusion,
            editor=editor,
        )
