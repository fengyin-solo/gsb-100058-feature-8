"""地质报告业务规则：状态流转、字段校验与成果图引用汇总。

报告引用只钉成果图的（图幅、比例尺、版本号）指针，引用结论由填图版本轨道
重放得到，避免报告侧和成果图侧各存一套结论。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.versioning import VersionError, version_store

MODULE = "geological_report"
REQUIRED_FIELDS = ["报告编号", "勘探区", "报告类型"]
STATUS_ORDER = ["编制中", "待内审", "待外审", "已定稿", "已退回"]
ACTION_RULES = {"提交内审": "待内审", "提交外审": "待外审", "确认定稿": "已定稿"}
NEGATIVE_ACTIONS = []


class GeologicalReportService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("报告编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"勘探报告 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于地质报告可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"勘探报告已{action}"

    # ---- 成果图引用汇总清单 ----

    def citations(self, report_no: str | None = None) -> list[dict[str, Any]]:
        return version_store.citations(report_no=report_no)

    def cite(
        self,
        *,
        report_no: str,
        sheet_no: str,
        scale: str,
        pinned_revision: int | None = None,
        remark: str = "",
    ) -> dict[str, Any]:
        return version_store.cite(
            report_no=report_no,
            sheet_no=sheet_no,
            scale=scale,
            pinned_revision=pinned_revision,
            remark=remark,
        )
