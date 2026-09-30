"""成果图版本轨道：事件溯源 + 乐观锁 + 投影重放。

填图单元的草图不是独立存储的另一份数据，而是成果图版本轨道上的暂存草稿：
拖动单元边界后「提交确认」，事件流只追加一个新的确认版本（修订号 +1）。
刷新页面或重新进入工作区，读到的永远是事件流重放出的同一份确认版本。

填图详情、图幅名称目录、报告引用汇总清单三个入口全部从同一份事件投影
读取，入口之间不允许各存一份；报告引用钉住具体修订号，引用到哪版就
展示哪版的图幅名称、确认结论与地图标注。

单元身份 = 图幅编号 + 比例尺（unit_key）。同图幅编号、不同比例尺是两条
独立轨道，修订互不覆盖。没有修订号的旧图幅记录在启动时迁移为
initial_import 事件（修订号 1），既有成果按原审定版本原样保留。
"""
from __future__ import annotations

import copy
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

CANVAS_SIZE = 1000
MIN_EDGE = 20

# 迁移旧记录时顺序铺放的图幅标注位置（归一化画布 0~1000）
_MIGRATION_LAYOUT = [
    {"x": 60, "y": 60, "w": 300, "h": 220},
    {"x": 420, "y": 60, "w": 300, "h": 220},
    {"x": 60, "y": 360, "w": 300, "h": 220},
    {"x": 420, "y": 360, "w": 300, "h": 220},
    {"x": 60, "y": 660, "w": 300, "h": 220},
    {"x": 420, "y": 660, "w": 300, "h": 220},
]

# 成果图版本轨道关心、需要写进确认快照的业务字段
DETAIL_FIELDS = [
    "图幅编号", "图幅名称", "比例尺", "填图面积", "填图人员",
    "野外日期", "室内整理", "填图状态",
]


class VersioningError(Exception):
    """业务校验失败（HTTP 400）：调用参数本身不合法。"""


class RevisionConflict(VersioningError):
    """乐观锁冲突（HTTP 409）：提交基于的修订号已过期，并发修订只落一个版本。"""

    def __init__(self, message: str, *, current_revision: int, server_state: dict[str, Any]) -> None:
        super().__init__(message)
        self.current_revision = current_revision
        self.server_state = server_state


class StreamNotFound(VersioningError):
    """图幅版本轨道不存在（HTTP 404）。"""


def unit_key_of(sheet_no: str, scale: str) -> str:
    return f"{sheet_no.strip()}@{scale.strip()}"


def parse_unit_key(unit_key: str) -> tuple[str, str]:
    if "@" not in unit_key:
        raise VersioningError("单元标识格式应为「图幅编号@比例尺」")
    sheet_no, scale = unit_key.rsplit("@", 1)
    if not sheet_no or not scale:
        raise VersioningError("图幅编号与比例尺都不能为空")
    return sheet_no, scale


def validate_geometry(geometry: Any) -> dict[str, int]:
    """地图标注只认画布内的轴对齐矩形；非法标注让整笔事务回滚。"""
    if not isinstance(geometry, dict):
        raise VersioningError("地图标注必须是包含 x/y/w/h 的对象")
    try:
        rect = {name: int(round(float(geometry.get(name)))) for name in ("x", "y", "w", "h")}
    except (TypeError, ValueError):
        raise VersioningError("地图标注坐标必须是数字") from None
    x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
    if w < MIN_EDGE or h < MIN_EDGE:
        raise VersioningError(f"图幅标注宽高不能小于 {MIN_EDGE}")
    if x < 0 or y < 0 or x + w > CANVAS_SIZE or y + h > CANVAS_SIZE:
        raise VersioningError(f"图幅标注超出 {CANVAS_SIZE}×{CANVAS_SIZE} 成果图范围")
    return rect


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class VersionStore:
    """成果图版本轨道的唯一写入面与投影来源。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # unit_key -> 事件列表（只追加，修订的唯一事实来源）
        self._streams: dict[str, list[dict[str, Any]]] = {}
        # unit_key -> 暂存草稿（拖动后尚未提交确认的草图）
        self._drafts: dict[str, dict[str, Any]] = {}
        # 报告引用：钉住具体修订号，不单独存图幅名称/结论副本
        self._references: list[dict[str, Any]] = []
        # 投影：unit_key -> 当前确认版本快照（填图详情 / 目录 / 标注 共用）
        self._details: dict[str, dict[str, Any]] = {}
        # 外部 mapping 表：启动迁移后与投影保持同一份内容，供旧列表入口读取
        self._mapping_table: list[dict[str, Any]] | None = None
        self._next_seq = 1
        self._next_id = 1
        self._next_ref_id = 1

    # ------------------------------------------------------------------
    # 启动迁移：旧图幅记录没有修订号，补成 initial_import（修订号 1）
    # ------------------------------------------------------------------
    def bootstrap(self, mapping_rows: list[dict[str, Any]]) -> None:
        with self._lock:
            self._mapping_table = mapping_rows
            self._streams.clear()
            self._drafts.clear()
            self._references.clear()
            self._details.clear()
            self._next_seq = 1
            self._next_id = 1
            legacy = [dict(row) for row in mapping_rows]
            for position, row in enumerate(legacy):
                sheet_no = str(row.get("图幅编号") or "").strip()
                scale = str(row.get("比例尺") or "").strip()
                if not sheet_no or not scale:
                    continue
                key = unit_key_of(sheet_no, scale)
                # 理论上同 key 旧记录也只迁一次；同编号不同比例尺天然分流
                if key in self._streams:
                    continue
                row_id = int(row.get("id") or self._next_id)
                self._next_id = max(self._next_id, row_id + 1)
                geometry = dict(_MIGRATION_LAYOUT[position % len(_MIGRATION_LAYOUT)])
                status = str(row.get("status") or "")
                row["确认结论"] = _legacy_conclusion(status)
                row["geometry"] = geometry
                row["修订号"] = 1
                row["版本号"] = "R1"
                row["迁移自旧记录"] = True
                event = {
                    "seq": self._next_seq,
                    "ts": _now(),
                    "unit_key": key,
                    "type": "initial_import",
                    "revision": 1,
                    "actor": "系统迁移",
                    "note": "旧图幅记录迁移补版本号，既有成果按原审定版本保留",
                    "data": {"id": row_id, **row},
                }
                self._next_seq += 1
                self._streams[key] = [event]
            self._rebuild()

    # ------------------------------------------------------------------
    # 事件重放：清空投影，按 seq 顺序回放所有事件，重建全部派生视图
    # ------------------------------------------------------------------
    def _apply_event(self, state: dict[str, Any] | None, event: dict[str, Any]) -> dict[str, Any]:
        data = event["data"]
        if event["type"] == "initial_import":
            state = copy.deepcopy(data)
        elif state is None:
            raise VersioningError("事件流损坏：缺少初始导入事件")
        elif event["type"] == "revision_confirmed":
            # 一次确认同时落下边界与结论；未变化的字段沿用上一版
            for field, value in data.items():
                if field != "id":
                    state[field] = value
        elif event["type"] == "status_transition":
            state["status"] = data["status"]
            state["pending"] = data["pending"]
            state["abnormal"] = data["abnormal"]
        else:
            raise VersioningError(f"未知事件类型：{event['type']}")
        state["修订号"] = event["revision"]
        state["版本号"] = f"R{event['revision']}"
        state["最后事件"] = event["type"]
        state["更新时间"] = event["ts"]
        return state  # type: ignore[return-value]

    def _rebuild(self) -> None:
        """重放事件重建投影，并把同一份快照同步到 mapping 表与目录入口。"""
        details: dict[str, dict[str, Any]] = {}
        for key in self._streams:  # dict 保持首次出现顺序，目录顺序稳定
            state: dict[str, Any] | None = None
            for event in self._streams[key]:
                state = self._apply_event(state, event)
            assert state is not None
            details[key] = state
        self._details = details
        if self._mapping_table is not None:
            # 原地替换，保证外部拿到的列表对象与投影永远是同一份内容
            self._mapping_table.clear()
            for key in details:
                self._mapping_table.append(copy.deepcopy(details[key]))

    def replay_all(self) -> dict[str, Any]:
        """管理入口：丢弃投影并从事件流完整重放，核对三个入口仍一致。"""
        with self._lock:
            self._rebuild()
            return {
                "streams": len(self._streams),
                "events": sum(len(events) for events in self._streams.values()),
                "details": len(self._details),
                "catalog": len(self.build_catalog()),
                "references": len(self._references),
            }

    # ------------------------------------------------------------------
    # 事务：失败时事件、草稿、引用、目录与地图标注投影一起回滚
    # ------------------------------------------------------------------
    @contextmanager
    def _transaction(self) -> Iterator[None]:
        snapshot = (
            copy.deepcopy(self._streams),
            copy.deepcopy(self._drafts),
            copy.deepcopy(self._references),
            self._next_seq,
            self._next_id,
            self._next_ref_id,
        )
        try:
            yield
            self._rebuild()
        except BaseException:
            (
                self._streams,
                self._drafts,
                self._references,
                self._next_seq,
                self._next_id,
                self._next_ref_id,
            ) = (
                copy.deepcopy(snapshot[0]),
                copy.deepcopy(snapshot[1]),
                copy.deepcopy(snapshot[2]),
                snapshot[3],
                snapshot[4],
                snapshot[5],
            )
            # 目录与地图标注随事件一起回到提交前版本
            self._rebuild()
            raise

    def _append(self, key: str, event_type: str, data: dict[str, Any], *, revision: int,
                actor: str, note: str) -> dict[str, Any]:
        event = {
            "seq": self._next_seq,
            "ts": _now(),
            "unit_key": key,
            "type": event_type,
            "revision": revision,
            "actor": actor,
            "note": note,
            "data": data,
        }
        self._next_seq += 1
        self._streams[key].append(event)
        return event

    def _require_stream(self, key: str) -> list[dict[str, Any]]:
        try:
            return self._streams[key]
        except KeyError:
            raise StreamNotFound(f"成果图版本轨道 {key} 不存在") from None

    def _current(self, key: str) -> dict[str, Any]:
        self._require_stream(key)
        return self._details[key]

    # ------------------------------------------------------------------
    # 工作区 / 草稿 / 提交确认
    # ------------------------------------------------------------------
    def workspace(self) -> dict[str, Any]:
        """成果图版本轨道工作区：刷新或重进读到的都是已确认版本 + 草稿状态。"""
        with self._lock:
            units = []
            for key, detail in self._details.items():
                units.append({
                    "unit_key": key,
                    "id": detail.get("id"),
                    "图幅编号": detail.get("图幅编号"),
                    "图幅名称": detail.get("图幅名称"),
                    "比例尺": detail.get("比例尺"),
                    "status": detail.get("status"),
                    "修订号": detail.get("修订号"),
                    "版本号": detail.get("版本号"),
                    "确认结论": detail.get("确认结论") or "待确认",
                    "geometry": copy.deepcopy(detail.get("geometry")),
                    "draft": copy.deepcopy(self._drafts.get(key)),
                    "更新时间": detail.get("更新时间"),
                })
            return {"canvas": {"width": CANVAS_SIZE, "height": CANVAS_SIZE}, "units": units}

    def save_draft(self, key: str, geometry: dict[str, Any], *, base_revision: int | None,
                   note: str = "") -> dict[str, Any]:
        """暂存拖动后的草图边界；草稿不进事件流，不产生新版本。"""
        with self._lock:
            rect = validate_geometry(geometry)
            current = self._current(key)
            if base_revision is not None and int(base_revision) > int(current["修订号"]):
                raise VersioningError("草稿基准修订号不能高于当前确认版本")
            draft = {
                "geometry": rect,
                "base_revision": int(current["修订号"]),
                "saved_at": _now(),
                "note": note,
            }
            self._drafts[key] = draft
            return {"unit_key": key, "draft": copy.deepcopy(draft),
                    "当前修订号": current["修订号"]}

    def submit_revision(
        self,
        key: str,
        *,
        expected_revision: int,
        geometry: Any = None,
        conclusion: str | None = None,
        actor: str = "填图人员",
        note: str = "",
    ) -> dict[str, Any]:
        """提交确认：乐观锁校验通过才追加一个确认版本，并发修订只落一个版本。"""
        with self._lock:
            current = self._current(key)
            server_revision = int(current["修订号"])
            if int(expected_revision) != server_revision:
                raise RevisionConflict(
                    f"该图幅已被其他人修订到 R{server_revision}，您基于 R{expected_revision} "
                    f"的修改未保存，请刷新后基于最新版本重新修改",
                    current_revision=server_revision,
                    server_state={
                        "unit_key": key,
                        "修订号": server_revision,
                        "版本号": f"R{server_revision}",
                        "geometry": copy.deepcopy(current.get("geometry")),
                        "确认结论": current.get("确认结论"),
                    },
                )
            with self._transaction():
                draft = self._drafts.get(key)
                if geometry is None and draft is not None:
                    geometry = draft["geometry"]
                # 校验放进事务内：非法标注会让目录与地图标注随事件一起回滚
                rect = validate_geometry(geometry) if geometry is not None else None
                text = (conclusion or "").strip() or (draft or {}).get("note", "")
                data: dict[str, Any] = {}
                if rect is not None:
                    data["geometry"] = rect
                data["确认结论"] = text or current.get("确认结论") or "成果图边界已确认"
                self._append(
                    key,
                    "revision_confirmed",
                    data,
                    revision=server_revision + 1,
                    actor=actor,
                    note=note or "拖动单元边界后提交确认，成果图版本轨道落新版本",
                )
                self._drafts.pop(key, None)
            return copy.deepcopy(self._details[key])

    # ------------------------------------------------------------------
    # 状态流转：记录事件但不产生新成果图修订号，投影仍是唯一读取来源
    # ------------------------------------------------------------------
    def transition_status(self, key: str, *, status: str, pending: bool,
                          abnormal: bool, actor: str = "填图人员") -> dict[str, Any]:
        with self._lock:
            current = self._current(key)
            with self._transaction():
                self._append(
                    key,
                    "status_transition",
                    {"status": status, "pending": pending, "abnormal": abnormal},
                    revision=int(current["修订号"]),
                    actor=actor,
                    note=f"状态流转为「{status}」，确认版本不变",
                )
            return copy.deepcopy(self._details[key])

    # ------------------------------------------------------------------
    # 新图幅登记：同图幅编号 + 同比例尺已存在则拒绝，不同比例尺是新轨道
    # ------------------------------------------------------------------
    def register_unit(self, values: dict[str, Any]) -> dict[str, Any]:
        sheet_no = str(values.get("图幅编号") or "").strip()
        scale = str(values.get("比例尺") or "").strip()
        name = str(values.get("图幅名称") or "").strip()
        if not sheet_no or not scale or not name:
            raise VersioningError("图幅编号、图幅名称、比例尺为必填项")
        key = unit_key_of(sheet_no, scale)
        with self._lock:
            if key in self._streams:
                raise VersioningError(
                    f"图幅 {sheet_no}（{scale}）已存在成果图版本轨道，"
                    f"请在原轨道上修订；若比例尺不同请使用新比例尺登记"
                )
            with self._transaction():
                row_id = self._next_id
                self._next_id += 1
                position = (len(self._streams)) % len(_MIGRATION_LAYOUT)
                state = {
                    "id": row_id,
                    "图幅编号": sheet_no,
                    "图幅名称": name,
                    "比例尺": scale,
                    **{field: str(values.get(field) or "") for field in DETAIL_FIELDS
                       if field not in ("图幅编号", "图幅名称", "比例尺")},
                    "status": "野外进行",
                    "pending": True,
                    "abnormal": False,
                    "确认结论": "待确认",
                    "geometry": dict(_MIGRATION_LAYOUT[position]),
                }
                self._streams[key] = []
                self._append(
                    key,
                    "initial_import",
                    state,
                    revision=1,
                    actor=str(values.get("登记人") or "填图人员"),
                    note="新登记图幅，建立成果图版本轨道 R1",
                )
            return copy.deepcopy(self._details[key])

    # ------------------------------------------------------------------
    # 三个读取入口：填图详情 / 图幅名称目录 / 报告引用汇总，同一份投影
    # ------------------------------------------------------------------
    def list_details(self, *, keyword: str | None = None, status: str | None = None,
                     page: int = 1, size: int = 20) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            rows = list(self._details.values())
            if keyword:
                rows = [row for row in rows if keyword in str(row.get("图幅编号", ""))
                        or keyword in str(row.get("图幅名称", ""))]
            if status:
                rows = [row for row in rows if row.get("status") == status]
            total = len(rows)
            start = max(page - 1, 0) * size
            return [copy.deepcopy(row) for row in rows[start:start + size]], total

    def get_detail(self, entry_id: int) -> dict[str, Any] | None:
        with self._lock:
            for detail in self._details.values():
                if int(detail.get("id", 0)) == entry_id:
                    return copy.deepcopy(detail)
            return None

    def find_key_by_id(self, entry_id: int) -> str | None:
        with self._lock:
            for key, detail in self._details.items():
                if int(detail.get("id", 0)) == entry_id:
                    return key
            return None

    def build_catalog(self, *, keyword: str | None = None) -> list[dict[str, Any]]:
        """图幅名称目录：直接由确认版本投影生成，不另存副本。"""
        with self._lock:
            catalog = []
            for key, detail in self._details.items():
                catalog.append({
                    "unit_key": key,
                    "图幅编号": detail.get("图幅编号"),
                    "图幅名称": detail.get("图幅名称"),
                    "比例尺": detail.get("比例尺"),
                    "status": detail.get("status"),
                    "当前修订号": detail.get("修订号"),
                    "当前版本": detail.get("版本号"),
                    "确认结论": detail.get("确认结论") or "待确认",
                    "更新时间": detail.get("更新时间"),
                    "报告引用数": sum(1 for ref in self._references if ref["unit_key"] == key),
                })
            if keyword:
                catalog = [row for row in catalog if keyword in str(row["图幅编号"])
                           or keyword in str(row["图幅名称"])]
            catalog.sort(key=lambda row: (str(row["图幅编号"]), str(row["比例尺"])))
            return catalog

    def events(self, key: str) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._require_stream(key))

    # ------------------------------------------------------------------
    # 报告引用：登记时钉住修订号，汇总清单从对应版本快照取值
    # ------------------------------------------------------------------
    def _snapshot_at(self, key: str, revision: int) -> dict[str, Any]:
        state: dict[str, Any] | None = None
        for event in self._require_stream(key):
            if event["revision"] <= revision:
                state = self._apply_event(state, event)
        if state is None:
            raise VersioningError(f"图幅 {key} 不存在 R{revision} 版本")
        return state

    def register_reference(self, *, report_no: str, sheet_no: str, scale: str,
                           pinned_revision: int | None = None, note: str = "") -> dict[str, Any]:
        report_no = report_no.strip()
        if not report_no:
            raise VersioningError("报告编号不能为空")
        key = unit_key_of(sheet_no, scale)
        with self._lock:
            current = self._current(key)
            revision = int(pinned_revision) if pinned_revision is not None else int(current["修订号"])
            if revision < 1 or revision > int(current["修订号"]):
                raise VersioningError(
                    f"钉住修订号 R{revision} 不存在，当前最新为 R{current['修订号']}"
                )
            for ref in self._references:
                if ref["报告编号"] == report_no and ref["unit_key"] == key:
                    raise VersioningError(
                        f"报告 {report_no} 已引用图幅 {key}，同一引用不得各登记一份"
                    )
            ref = {
                "id": self._next_ref_id,
                "报告编号": report_no,
                "unit_key": key,
                "图幅编号": current["图幅编号"],
                "比例尺": current["比例尺"],
                "钉住修订号": revision,
                "note": note,
                "登记时间": _now(),
            }
            self._next_ref_id += 1
            self._references.append(ref)
            return copy.deepcopy(ref)

    def repin_reference(self, reference_id: int, *, pinned_revision: int | None = None) -> dict[str, Any]:
        """让既有报告引用跟进到指定（默认最新）确认版本；引用记录仍只有一条。"""
        with self._lock:
            ref = next((item for item in self._references if int(item["id"]) == reference_id), None)
            if ref is None:
                raise StreamNotFound(f"报告引用 {reference_id} 不存在")
            current = self._current(ref["unit_key"])
            revision = int(pinned_revision) if pinned_revision is not None else int(current["修订号"])
            if revision < 1 or revision > int(current["修订号"]):
                raise VersioningError(
                    f"钉住修订号 R{revision} 不存在，当前最新为 R{current['修订号']}"
                )
            ref["钉住修订号"] = revision
            return copy.deepcopy(ref)

    def reference_summary(self) -> list[dict[str, Any]]:
        """报告引用汇总清单：名称/结论/标注全部取自钉住版本快照，与成果图对得上。"""
        with self._lock:
            summary = []
            for ref in self._references:
                snapshot = self._snapshot_at(ref["unit_key"], ref["钉住修订号"])
                current = self._details[ref["unit_key"]]
                summary.append({
                    "id": ref["id"],
                    "报告编号": ref["报告编号"],
                    "unit_key": ref["unit_key"],
                    "图幅编号": snapshot["图幅编号"],
                    "图幅名称": snapshot["图幅名称"],
                    "比例尺": snapshot["比例尺"],
                    "引用版本": f"R{ref['钉住修订号']}",
                    "钉住修订号": ref["钉住修订号"],
                    "最新修订号": current["修订号"],
                    "引用是否最新": ref["钉住修订号"] == current["修订号"],
                    "确认结论": snapshot.get("确认结论") or "待确认",
                    "地图标注": copy.deepcopy(snapshot.get("geometry")),
                    "备注": ref.get("note", ""),
                    "登记时间": ref["登记时间"],
                })
            summary.sort(key=lambda row: (str(row["报告编号"]), str(row["unit_key"])))
            return summary


def _legacy_conclusion(status: str) -> str:
    if status in ("已验收", "已出版"):
        return "旧记录迁移：既有成果按原审定版本保留"
    return "待确认"


# 单例：mapping 服务与路由共用同一个版本轨道写入面
version_store = VersionStore()
