"""成果图版本轨道：填图单元边界修订的单一事实源。

设计要点
--------
* 轨道身份是 ``(图幅编号, 比例尺)`` 复合键。比例尺不同、图幅编号相同的单元是
  两条独立轨道，互不覆盖；既有成果按原审定版本永久保留。
* 所有变更（登记、旧版迁移、边界修订、确认、状态流转）都追加为不可变事件。
  填图详情、图幅名称目录、地图标注、报告引用结论都是事件重放得到的投影，
  任何入口都不另存一份结论或几何。
* 边界修订走乐观锁：提交时必须携带当前已确认的修订号，并发修订只有一份能
  落下，后者收到 :class:`RevisionConflict`。
* 修订确认在一个事务里同时重写目录投影与地图标注投影，任一步失败整体回滚。
* 启动时把没有修订号的旧图幅记录迁移为 v1：原审定结论原样保留，未审定的
  记录迁移为待确认草稿。
"""
from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from app.seed import SEED_ROWS
from app.store import store

MAPPING_MODULE = "mapping"
REPORT_MODULE = "geological_report"

# 落到“已验收/已出版”的记录视为历史上已经审定过
REVIEWED_STATUSES = ("已验收", "已出版")

EVENT_CREATED = "track_created"
EVENT_BOUNDARY = "boundary_revised"
EVENT_CONFIRMED = "revision_confirmed"
EVENT_STATUS = "status_changed"

T = TypeVar("T")


class RevisionConflict(RuntimeError):
    """乐观锁冲突：客户端持有的修订号已不是最新。"""

    def __init__(self, message: str, *, current: int) -> None:
        super().__init__(message)
        self.current = current


class VersionError(RuntimeError):
    """版本轨道上的非法操作（轨道不存在、几何非法、草稿不存在等）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def track_key(sheet_no: str, scale: str) -> str:
    return f"{sheet_no.strip()}@{scale.strip()}"


def default_geometry(track_id: int) -> list[list[float]]:
    """为旧记录/新登记单元生成确定性初始四边形。

    不同比例尺的同编号图幅几何刻意不同，避免视觉上互相覆盖。
    """
    base = 100.0 + (track_id % 5) * 24.0
    return [
        [round(base, 1), 100.0],
        [round(base + 160.0, 1), 100.0],
        [round(base + 160.0, 1), 220.0],
        [round(base, 1), 220.0],
    ]


def validate_geometry(geometry: Any) -> list[list[float]]:
    """单元边界是闭合多边形：至少 3 个顶点、每点为两个有限数值坐标。"""
    if not isinstance(geometry, list) or len(geometry) < 3:
        raise VersionError("单元边界至少需要 3 个顶点")
    cleaned: list[list[float]] = []
    for point in geometry:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise VersionError("每个边界顶点必须是 [x, y] 两个坐标")
        x, y = point
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise VersionError("边界顶点坐标必须是数值")
        if not (float(x) == x and float(y) == y):
            raise VersionError("边界顶点坐标不能是无穷或缺失值")
        cleaned.append([float(x), float(y)])
    return cleaned


def replay(events: list[dict[str, Any]], *, at_revision: int | None = None) -> dict[str, Any]:
    """从事件流重放出轨道当前（或指定修订号）状态。

    投影不持久化结论，任何入口需要结论/几何时都应走这里或其调用方，确保
    “成果图与报告引用对得上”。
    """
    state: dict[str, Any] = {
        "track_id": None,
        "sheet_no": "",
        "sheet_name": "",
        "scale": "",
        "head_revision": 0,
        "confirmed_revision": 0,
        "confirmed_geometry": None,
        "confirmed_conclusion": None,
        "confirmed_at": None,
        "confirmed_by": None,
        "geometry": None,
        "status": None,
        "pending": None,
        "events": [],
    }
    pending_rev: dict[str, Any] | None = None
    stop = False
    for event in events:
        revision = int(event["revision"])
        if at_revision is not None and revision > at_revision:
            stop = True
            break
        kind = event["kind"]
        if kind == EVENT_CREATED:
            state["track_id"] = event["track_id"]
            state["sheet_no"] = event["sheet_no"]
            state["sheet_name"] = event["sheet_name"]
            state["scale"] = event["scale"]
            state["geometry"] = copy.deepcopy(event["geometry"])
            state["head_revision"] = revision
            if event.get("confirmed"):
                state["confirmed_revision"] = revision
                state["confirmed_geometry"] = copy.deepcopy(event["geometry"])
                state["confirmed_conclusion"] = event.get("conclusion")
                state["confirmed_at"] = event.get("confirmed_at")
                state["confirmed_by"] = event.get("confirmed_by")
            state["status"] = event.get("status")
        elif kind == EVENT_BOUNDARY:
            state["head_revision"] = revision
            state["geometry"] = copy.deepcopy(event["geometry"])
            pending_rev = {
                "revision": revision,
                "geometry": copy.deepcopy(event["geometry"]),
                "note": event.get("note", ""),
                "editor": event.get("editor", ""),
                "created_at": event.get("created_at"),
                "base_revision": event.get("base_revision"),
                "superseded": False,
            }
        elif kind == EVENT_CONFIRMED:
            if pending_rev is None or pending_rev["revision"] != revision:
                # 极端情况下事件流异常：拒绝静默，交给调用方排查
                raise VersionError(f"修订 {revision} 缺少对应的边界修订事件")
            pending_rev["superseded"] = True
            state["confirmed_revision"] = revision
            state["confirmed_geometry"] = copy.deepcopy(pending_rev["geometry"])
            state["confirmed_conclusion"] = event.get("conclusion")
            state["confirmed_at"] = event.get("confirmed_at")
            state["confirmed_by"] = event.get("confirmed_by")
            state["status"] = event.get("status", state["status"])
            pending_rev = None
        elif kind == EVENT_STATUS:
            state["status"] = event.get("status", state["status"])
        state["events"].append(event)
    if stop and pending_rev is not None and at_revision is not None and pending_rev["revision"] > at_revision:
        # at_revision 截止在草稿落子之前时，草稿不投影出来
        pending_rev = None
    state["pending_revision"] = pending_rev
    return state


class VersionStore:
    """内存事件仓库 + 目录/标注/引用投影。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._index: dict[str, int] = {}
        self._catalog: dict[int, dict[str, Any]] = {}
        self._annotations: dict[int, dict[str, Any]] = {}
        self._citations: list[dict[str, Any]] = []
        self._seq = 0
        self._initialized = False

    # ---- 启动迁移 -------------------------------------------------------

    def ensure_initialized(self) -> None:
        """把旧图幅记录迁移进版本轨道（幂等，可重复调用）。"""
        with self._lock:
            if self._initialized:
                return
            for row in store.rows(MAPPING_MODULE):
                self._migrate_legacy_row(row)
            self._initialized = True
            self._seed_report_citations()

    def reset(self) -> None:
        """测试用：明细表还原为种子，清空全部版本数据后重新迁移。"""
        with self._lock:
            store.rows(MAPPING_MODULE)[:] = [
                copy.deepcopy(dict(row)) for row in SEED_ROWS[MAPPING_MODULE]
            ]
            self._events.clear()
            self._index.clear()
            self._catalog.clear()
            self._annotations.clear()
            self._citations.clear()
            self._seq = 0
            self._initialized = False
            self.ensure_initialized()

    def _migrate_legacy_row(self, row: dict[str, Any]) -> None:
        sheet_no = str(row.get("图幅编号") or "").strip()
        scale = str(row.get("比例尺") or "").strip()
        if not sheet_no or not scale:
            return
        key = track_key(sheet_no, scale)
        if key in self._index:
            return
        track_id = int(row["id"])
        geometry = default_geometry(track_id)
        reviewed = str(row.get("status") or "") in REVIEWED_STATUSES
        event: dict[str, Any] = {
            "seq": self._next_seq(),
            "track_id": track_id,
            "kind": EVENT_CREATED,
            "revision": 1,
            "sheet_no": sheet_no,
            "sheet_name": str(row.get("图幅名称") or ""),
            "scale": scale,
            "geometry": geometry,
            "status": row.get("status"),
            "source": "legacy",
            "created_at": _now(),
        }
        if reviewed:
            # 既有成果按原审定版本保留：旧记录补 v1，结论原文迁移
            event["confirmed"] = True
            event["conclusion"] = f"原审定版本（{row.get('status')}）"
            event["confirmed_at"] = str(row.get("野外日期") or _now())
            event["confirmed_by"] = str(row.get("填图人员") or "历史审定")
        self._events[key] = [event]
        self._index[key] = track_id
        self._rebuild_locked(key)

    def _seed_report_citations(self) -> None:
        """给报告 GEOL-0001 挂一条指向金山幅 1:50000 原审定版的引用。"""
        state = self.state_by_identity("I49D001001", "1:50000")
        if state is None or state["confirmed_revision"] == 0:
            return
        self._citations.append({
            "id": max((int(item["id"]) for item in self._citations), default=0) + 1,
            "report_no": "GEOL-0001",
            "track_id": state["track_id"],
            "sheet_no": state["sheet_no"],
            "sheet_name": state["sheet_name"],
            "scale": state["scale"],
            "pinned_revision": state["confirmed_revision"],
            "remark": "迁移生成：报告引用原审定成果图",
            "created_at": _now(),
        })

    # ---- 基础工具 -------------------------------------------------------

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _key_of(self, track_id: int) -> str | None:
        for key, tid in self._index.items():
            if tid == track_id:
                return key
        return None

    def _state_locked(self, key: str) -> dict[str, Any] | None:
        events = self._events.get(key)
        if not events:
            return None
        state = replay(events)
        state["key"] = key
        return state

    def _track_row(self, track_id: int) -> dict[str, Any] | None:
        return store.find(MAPPING_MODULE, track_id)

    # ---- 投影（目录 / 标注 / 详情 / 引用） ------------------------------

    def _rebuild_locked(self, key: str) -> dict[str, Any]:
        """从事件流重放并重建该轨道的目录与地图标注投影。"""
        state = self._state_locked(key)
        if state is None:  # pragma: no cover - 调用方保证 key 存在
            raise VersionError("版本轨道不存在")
        track_id = int(state["track_id"])
        confirmed = state["confirmed_revision"] > 0
        geometry = state["confirmed_geometry"] if confirmed else state["geometry"]
        annotation_state = "已确认" if confirmed else "待确认草稿"
        self._catalog[track_id] = {
            "track_id": track_id,
            "图幅编号": state["sheet_no"],
            "图幅名称": state["sheet_name"],
            "比例尺": state["scale"],
            "当前版本号": state["confirmed_revision"] or state["head_revision"],
            "修订号": state["confirmed_revision"] or state["head_revision"],
            "最新确认版本号": state["confirmed_revision"],
            "版本状态": annotation_state,
            "审定结论": state["confirmed_conclusion"] or "待确认",
            "确认时间": state["confirmed_at"],
            "确认人": state["confirmed_by"],
            "工作区状态": state["status"],
        }
        self._annotations[track_id] = {
            "track_id": track_id,
            "图幅编号": state["sheet_no"],
            "图幅名称": state["sheet_name"],
            "比例尺": state["scale"],
            "标注版本": state["confirmed_revision"] or state["head_revision"],
            "标注状态": annotation_state,
            "审定结论": state["confirmed_conclusion"] or "待确认",
            "geometry": copy.deepcopy(geometry),
        }
        # 回填图幅明细表上的版本指针（指针值仍来自上面的统一投影）
        row = self._track_row(track_id)
        if row is not None:
            row["版本号"] = self._catalog[track_id]["当前版本号"]
            row["修订号"] = self._catalog[track_id]["修订号"]
            row["确认版本号"] = state["confirmed_revision"]
            row["审定结论"] = state["confirmed_conclusion"] or "待确认"
        return state

    def _snapshot_locked(self) -> dict[str, Any]:
        return {
            "events": copy.deepcopy(self._events),
            "catalog": copy.deepcopy(self._catalog),
            "annotations": copy.deepcopy(self._annotations),
            "citations": copy.deepcopy(self._citations),
            "index": copy.deepcopy(self._index),
            "seq": self._seq,
            # 明细表整行备份：确认修订同时改状态/指针，回滚必须一起还原
            "rows": {
                int(row["id"]): copy.deepcopy(dict(row))
                for row in store.rows(MAPPING_MODULE)
            },
            "row_ids": [int(row["id"]) for row in store.rows(MAPPING_MODULE)],
        }

    def _restore_locked(self, snapshot: dict[str, Any]) -> None:
        self._events = copy.deepcopy(snapshot["events"])
        self._catalog = copy.deepcopy(snapshot["catalog"])
        self._annotations = copy.deepcopy(snapshot["annotations"])
        self._citations = copy.deepcopy(snapshot["citations"])
        self._index = copy.deepcopy(snapshot["index"])
        self._seq = snapshot["seq"]
        rows = store.rows(MAPPING_MODULE)
        # 删掉事务中新登记的行，再逐行还原（保持原有序列）
        rows[:] = [copy.deepcopy(row) for row in (snapshot["rows"][rid] for rid in snapshot["row_ids"])]

    def _transaction(self, key: str, mutate: Callable[[], T]) -> T:
        """在一个事务里追加事件并重放投影；异常时目录与地图标注一起回滚。"""
        with self._lock:
            snapshot = self._snapshot_locked()
            try:
                result = mutate()
                self._rebuild_locked(key)
                return result
            except Exception:
                self._restore_locked(snapshot)
                raise

    # ---- 查询入口（三个入口同读一份投影） ------------------------------

    def catalog_entries(self) -> list[dict[str, Any]]:
        """图幅名称目录：结论只此一份，详情/报告都引用它。"""
        self.ensure_initialized()
        with self._lock:
            return [
                copy.deepcopy(self._catalog[tid])
                for tid in sorted(self._catalog)
            ]

    def annotations(self) -> list[dict[str, Any]]:
        """地图标注投影：刷新页面后与目录、详情读到同一确认版本。"""
        self.ensure_initialized()
        with self._lock:
            return [
                copy.deepcopy(self._annotations[tid])
                for tid in sorted(self._annotations)
            ]

    def state_by_identity(self, sheet_no: str, scale: str) -> dict[str, Any] | None:
        self.ensure_initialized()
        with self._lock:
            return self._state_locked(track_key(sheet_no, scale))

    def get_track(self, track_id: int) -> dict[str, Any] | None:
        self.ensure_initialized()
        with self._lock:
            key = self._key_of(track_id)
            return self._state_locked(key) if key else None

    def workspace(self, track_id: int) -> dict[str, Any]:
        """工作区读取：已确认几何与待确认草稿都来自事件流，不存本地草图。"""
        state = self.get_track(track_id)
        if state is None:
            raise VersionError(f"填图单元 {track_id} 不存在或已归档")
        pending = state["pending_revision"]
        return {
            "track_id": track_id,
            "图幅编号": state["sheet_no"],
            "图幅名称": state["sheet_name"],
            "比例尺": state["scale"],
            "工作区状态": state["status"],
            "confirmed_revision": state["confirmed_revision"],
            "confirmed_conclusion": state["confirmed_conclusion"],
            "confirmed_at": state["confirmed_at"],
            "confirmed_by": state["confirmed_by"],
            "confirmed_geometry": copy.deepcopy(state["confirmed_geometry"]),
            "pending_revision": None if pending is None else {
                "revision": pending["revision"],
                "geometry": copy.deepcopy(pending["geometry"]),
                "note": pending["note"],
                "editor": pending["editor"],
                "created_at": pending["created_at"],
                "base_revision": pending["base_revision"],
            },
            "history": self._history_locked(state),
        }

    def _history_locked(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        history: list[dict[str, Any]] = []
        for event in state["events"]:
            kind = event["kind"]
            if kind == EVENT_CREATED:
                history.append({
                    "revision": event["revision"],
                    "kind": "登记建轨",
                    "geometry": copy.deepcopy(event["geometry"]),
                    "conclusion": event.get("conclusion"),
                    "editor": event.get("confirmed_by"),
                    "created_at": event.get("confirmed_at") or event.get("created_at"),
                    "state": "已确认" if event.get("confirmed") else "待确认",
                    "source": "旧版迁移" if event.get("source") == "legacy" else "登记",
                })
            elif kind == EVENT_BOUNDARY:
                history.append({
                    "revision": event["revision"],
                    "kind": "边界修订",
                    "geometry": copy.deepcopy(event["geometry"]),
                    "note": event.get("note", ""),
                    "editor": event.get("editor", ""),
                    "created_at": event.get("created_at"),
                    "base_revision": event.get("base_revision"),
                    "state": "待确认",
                })
            elif kind == EVENT_CONFIRMED:
                history.append({
                    "revision": event["revision"],
                    "kind": "确认成果图",
                    "conclusion": event.get("conclusion"),
                    "editor": event.get("confirmed_by"),
                    "created_at": event.get("confirmed_at"),
                    "state": "已确认",
                })
            elif kind == EVENT_STATUS:
                history.append({
                    "revision": event["revision"],
                    "kind": "状态流转",
                    "note": f"状态变更为「{event.get('status')}」",
                    "created_at": event.get("created_at"),
                    "state": "记录",
                })
        # 被后续边界修订顶掉的草稿与已确认后被新版替代的旧版，标注为历史
        confirmed_rev = state["confirmed_revision"]
        head_rev = state["head_revision"]
        pending_rev = None if state["pending_revision"] is None else state["pending_revision"]["revision"]
        for item in history:
            rev = item["revision"]
            if item["kind"] == "边界修订" and rev != pending_rev:
                item["state"] = "草稿（已被后续修订替代）"
            elif item["kind"] in ("登记建轨", "确认成果图") and item["state"] == "已确认" and rev < confirmed_rev:
                item["state"] = "历史确认版本（既有成果保留）"
            elif item["kind"] == "登记建轨" and item["state"] == "待确认" and rev < head_rev:
                item["state"] = "历史版本"
        return history

    def replay_revision(self, track_id: int, revision: int) -> dict[str, Any]:
        """重放指定修订号的成果图（历史版本可随时取回）。"""
        state = self.get_track(track_id)
        if state is None:
            raise VersionError(f"填图单元 {track_id} 不存在或已归档")
        key = self._key_of(track_id)
        assert key is not None
        with self._lock:
            at = replay(self._events[key], at_revision=revision)
        if at["head_revision"] < revision or at["head_revision"] == 0:
            raise VersionError(f"修订号 {revision} 不存在，可重放范围 1~{state['head_revision']}")
        confirmed = at["confirmed_revision"] == revision
        return {
            "track_id": track_id,
            "revision": revision,
            "geometry": copy.deepcopy(
                at["confirmed_geometry"] if confirmed else at["geometry"]
            ),
            "conclusion": at["confirmed_conclusion"] if confirmed else "待确认",
            "confirmed": confirmed,
        }

    # ---- 写入入口 -------------------------------------------------------

    def create_track(
        self,
        *,
        sheet_no: str,
        sheet_name: str,
        scale: str,
        editor: str = "",
    ) -> dict[str, Any]:
        """登记新填图单元并建立版本轨道。

        ``(图幅编号, 比例尺)`` 已存在时拒绝，避免比例尺不同的同编号图幅或
        重复登记互相覆盖。
        """
        self.ensure_initialized()
        sheet_no, sheet_name, scale = sheet_no.strip(), sheet_name.strip(), scale.strip()
        key = track_key(sheet_no, scale)
        with self._lock:
            if key in self._index:
                raise VersionError(f"图幅 {sheet_no}（{scale}）已存在版本轨道，不得覆盖既有成果")
            rows = store.rows(MAPPING_MODULE)
            track_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
            geometry = default_geometry(track_id)

            def mutate() -> None:
                row = {
                    "id": track_id,
                    "status": "野外进行",
                    "pending": True,
                    "abnormal": False,
                    "图幅编号": sheet_no,
                    "图幅名称": sheet_name,
                    "比例尺": scale,
                    "填图面积": "",
                    "填图人员": editor,
                    "野外日期": _now()[:10],
                    "室内整理": "未开始",
                    "填图状态": "野外进行",
                }
                rows.append(row)
                self._events[key] = [{
                    "seq": self._next_seq(),
                    "track_id": track_id,
                    "kind": EVENT_CREATED,
                    "revision": 1,
                    "sheet_no": sheet_no,
                    "sheet_name": sheet_name,
                    "scale": scale,
                    "geometry": geometry,
                    "status": "野外进行",
                    "source": "register",
                    "created_at": _now(),
                }]
                self._index[key] = track_id

            self._transaction(key, mutate)
            return self.workspace(track_id)

    def save_boundary(
        self,
        track_id: int,
        *,
        geometry: list[list[float]],
        expected_revision: int,
        note: str = "",
        editor: str = "",
    ) -> dict[str, Any]:
        """拖动单元边界后落一版草稿（乐观锁）。

        ``expected_revision`` 必须等于轨道最新修订号（确认版或在途草稿）：
        两个并发修订只有一个能落下，另一个收到
        :class:`RevisionConflict` 并需重放后重做。目录与地图标注在同一事务
        内刷新，失败一起回滚。
        """
        geometry = validate_geometry(geometry)
        self.ensure_initialized()
        with self._lock:
            key = self._key_of(track_id)
            if key is None:
                raise VersionError(f"填图单元 {track_id} 不存在或已归档")
            state_pre = self._state_locked(key)
            assert state_pre is not None
            head = state_pre["head_revision"]
            confirmed = state_pre["confirmed_revision"]
            if int(expected_revision) != head:
                raise RevisionConflict(
                    f"成果图轨道已有更新的修订 r{head}"
                    + (f"（确认版本 v{confirmed}）" if confirmed else "（尚未确认）")
                    + "，请重放最新版本后再提交修订",
                    current=head,
                )

            def mutate() -> dict[str, Any]:
                events = self._events[key]
                revision = max(int(event["revision"]) for event in events) + 1
                # 旧的未确认草稿被新草稿顶掉，但事件不删除，仍可审计
                events.append({
                    "seq": self._next_seq(),
                    "track_id": track_id,
                    "kind": EVENT_BOUNDARY,
                    "revision": revision,
                    "geometry": copy.deepcopy(geometry),
                    "note": note,
                    "editor": editor,
                    "created_at": _now(),
                    "base_revision": confirmed,
                })
                return {"revision": revision, "base_revision": confirmed}

            return self._transaction(key, mutate)

    def confirm_revision(
        self,
        track_id: int,
        *,
        revision: int,
        conclusion: str,
        expected_revision: int,
        editor: str = "",
    ) -> dict[str, Any]:
        """确认草稿为成果图版本：结论同步进详情、目录、标注（同一份投影）。"""
        conclusion = conclusion.strip()
        if not conclusion:
            raise VersionError("请填写审定结论后再确认成果图")
        self.ensure_initialized()
        with self._lock:
            key = self._key_of(track_id)
            if key is None:
                raise VersionError(f"填图单元 {track_id} 不存在或已归档")
            state_pre = self._state_locked(key)
            assert state_pre is not None
            base = state_pre["confirmed_revision"]
            if int(expected_revision) != base:
                raise RevisionConflict(
                    f"成果图已有更新的确认版本 v{base}，请刷新工作区后重试",
                    current=base,
                )
            pending = state_pre["pending_revision"]
            if pending is None or pending["revision"] != int(revision):
                raise VersionError(
                    f"修订号 {revision} 不是待确认草稿，请在工作区核对版本"
                )
            new_status = "已验收" if conclusion == "合格" else state_pre["status"]

            def mutate() -> None:
                self._events[key].append({
                    "seq": self._next_seq(),
                    "track_id": track_id,
                    "kind": EVENT_CONFIRMED,
                    "revision": int(revision),
                    "conclusion": conclusion,
                    "confirmed_by": editor or "审定人",
                    "confirmed_at": _now(),
                    "status": new_status,
                })
                row = self._track_row(track_id)
                if row is not None and conclusion == "合格":
                    row["status"] = "已验收"
                    row["填图状态"] = "已验收"
                    row["pending"] = False
                    row["abnormal"] = False

            self._transaction(key, mutate)
            return self.workspace(track_id)

    def record_status(self, track_id: int, status: str) -> dict[str, Any]:
        """状态流转也进事件流（不改修订号），保证重放结果一致。"""
        self.ensure_initialized()
        with self._lock:
            key = self._key_of(track_id)
            if key is None:
                raise VersionError(f"填图单元 {track_id} 不存在或已归档")
            state_pre = self._state_locked(key)
            assert state_pre is not None

            def mutate() -> None:
                events = self._events[key]
                self._events[key].append({
                    "seq": self._next_seq(),
                    "track_id": track_id,
                    "kind": EVENT_STATUS,
                    "revision": state_pre["head_revision"],
                    "status": status,
                    "created_at": _now(),
                })
                row = self._track_row(track_id)
                if row is not None:
                    row["status"] = status
                    row["填图状态"] = status
                    row["pending"] = status not in REVIEWED_STATUSES

            self._transaction(key, mutate)
            return self.workspace(track_id)

    # ---- 报告引用汇总 ---------------------------------------------------

    def cite(
        self,
        *,
        report_no: str,
        sheet_no: str,
        scale: str,
        pinned_revision: int | None = None,
        remark: str = "",
    ) -> dict[str, Any]:
        """报告登记一条成果图引用：只存指针（轨道+修订号），结论由重放得到。"""
        self.ensure_initialized()
        report_no = report_no.strip()
        if not report_no:
            raise VersionError("请填写报告编号")
        state = self.state_by_identity(sheet_no, scale)
        if state is None:
            raise VersionError(f"图幅 {sheet_no}（{scale}）不存在，无法登记报告引用")
        pin = int(pinned_revision) if pinned_revision is not None else state["confirmed_revision"]
        if pin <= 0:
            raise VersionError("该图幅尚无可引用的确认成果图版本")
        with self._lock:
            if pin > state["head_revision"]:
                raise VersionError(
                    f"修订号 {pin} 不存在，可引用范围 1~{state['head_revision']}"
                )
            citation_id = max((int(item["id"]) for item in self._citations), default=0) + 1
            record = {
                "id": citation_id,
                "report_no": report_no,
                "track_id": state["track_id"],
                "sheet_no": state["sheet_no"],
                "sheet_name": state["sheet_name"],
                "scale": state["scale"],
                "pinned_revision": pin,
                "remark": remark,
                "created_at": _now(),
            }
            self._citations.append(record)
            return self._citation_view_locked(record)

    def _citation_view_locked(self, record: dict[str, Any]) -> dict[str, Any]:
        """引用清单的一行：引用结论是重放被钉版本算出来的，不允许各存一份。"""
        key = self._key_of(int(record["track_id"]))
        assert key is not None
        pinned = replay(self._events[key], at_revision=int(record["pinned_revision"]))
        latest = self._state_locked(key)
        assert latest is not None
        pinned_conclusion = pinned["confirmed_conclusion"] or "待确认"
        latest_conclusion = latest["confirmed_conclusion"] or "待确认"
        return {
            "id": record["id"],
            "报告编号": record["report_no"],
            "图幅编号": record["sheet_no"],
            "图幅名称": record["sheet_name"],
            "比例尺": record["scale"],
            "引用版本号": record["pinned_revision"],
            "引用结论": pinned_conclusion,
            "最新确认版本号": latest["confirmed_revision"],
            "最新确认结论": latest_conclusion,
            "引用是否对得上": pinned_conclusion == latest_conclusion
            and record["pinned_revision"] == latest["confirmed_revision"],
            "备注": record.get("remark", ""),
            "登记时间": record.get("created_at"),
        }

    def citations(self, *, report_no: str | None = None) -> list[dict[str, Any]]:
        """报告引用汇总清单。"""
        self.ensure_initialized()
        with self._lock:
            records = [
                item for item in self._citations
                if report_no is None or item["report_no"] == report_no
            ]
            return [self._citation_view_locked(item) for item in records]


version_store = VersionStore()
