"""地质填图业务规则。

填图单元的草图不单独存库，统一走「成果图版本轨道」（见 app.versioning）：
- 列表/详情读取的是事件流重放出的确认版本投影；
- 拖动边界先存草稿，提交确认才追加新版本（乐观锁）；
- 图幅名称目录、报告引用汇总清单与填图详情共用同一份投影。

状态流转仍走 run_action，落 status_transition 事件但不产生新成果图修订号。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.versioning import (
    RevisionConflict,
    VersioningError,
    unit_key_of,
    version_store,
)

MODULE = "mapping"
REQUIRED_FIELDS = ["图幅编号", "图幅名称", "比例尺"]
STATUS_ORDER = ["野外进行", "室内整理", "已验收", "已出版"]
ACTION_RULES = {"开始野外": "野外进行", "完成整理": "已验收", "申请验收": "已验收"}
NEGATIVE_ACTIONS = []

# 启动迁移：旧图幅记录没有修订号，bootstrap 会为每条记录补 initial_import(R1)。
# mapping 表对象交给版本仓库原地维护，保证旧入口读到的也是同一份确认版本。
version_store.bootstrap(store.rows(MODULE))


class MappingService:
    # -- 兼容旧入口：列表 / 详情都读确认版本投影 --------------------------------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        return version_store.list_details(keyword=keyword, status=status, page=page, size=size)

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return version_store.get_detail(entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        try:
            return version_store.register_unit(values), []
        except VersioningError as exc:
            return None, [str(exc)]

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        key = version_store.find_key_by_id(entry_id)
        if key is None:
            return None, f"填图单元 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于地质填图可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry = version_store.transition_status(
            key,
            status=target,
            pending=target != STATUS_ORDER[-1],
            abnormal=action in NEGATIVE_ACTIONS,
        )
        return entry, f"填图单元已{action}，成果图确认版本不变"

    # -- 成果图版本轨道工作区 ---------------------------------------------------
    def workspace(self) -> dict[str, Any]:
        return version_store.workspace()

    def save_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = self._payload_key(payload)
        return version_store.save_draft(
            key,
            payload.get("geometry") or {},
            base_revision=payload.get("base_revision"),
            note=str(payload.get("note") or ""),
        )

    def submit_revision(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = self._payload_key(payload)
        try:
            return version_store.submit_revision(
                key,
                expected_revision=int(payload.get("expected_revision")),
                geometry=payload.get("geometry"),
                conclusion=str(payload.get("确认结论") or payload.get("conclusion") or ""),
                actor=str(payload.get("actor") or "填图人员"),
                note=str(payload.get("note") or ""),
            )
        except RevisionConflict:
            raise
        except (TypeError, ValueError):
            raise VersioningError("expected_revision 必须是整数修订号") from None

    def catalog(self, keyword: str | None = None) -> list[dict[str, Any]]:
        return version_store.build_catalog(keyword=keyword)

    def history(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return version_store.events(self._payload_key(payload))

    def replay(self) -> dict[str, Any]:
        """事件重放：丢弃全部投影并从事件流重建，验证各入口仍指向同一版本。"""
        return version_store.replay_all()

    # -- 报告引用汇总清单 --------------------------------------------------------
    def reference_summary(self) -> list[dict[str, Any]]:
        return version_store.reference_summary()

    def register_reference(self, payload: dict[str, Any]) -> dict[str, Any]:
        return version_store.register_reference(
            report_no=str(payload.get("报告编号") or ""),
            sheet_no=str(payload.get("图幅编号") or ""),
            scale=str(payload.get("比例尺") or ""),
            pinned_revision=payload.get("钉住修订号"),
            note=str(payload.get("note") or ""),
        )

    def repin_reference(self, payload: dict[str, Any]) -> dict[str, Any]:
        return version_store.repin_reference(
            int(payload.get("id")),
            pinned_revision=payload.get("钉住修订号"),
        )

    @staticmethod
    def payload_key(payload: dict[str, Any]) -> str:
        return MappingService._payload_key(payload)

    @staticmethod
    def _payload_key(payload: dict[str, Any]) -> str:
        key = payload.get("unit_key")
        if key:
            return str(key)
        return unit_key_of(
            str(payload.get("图幅编号") or ""),
            str(payload.get("比例尺") or ""),
        )
