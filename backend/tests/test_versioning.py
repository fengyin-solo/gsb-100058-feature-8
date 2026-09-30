"""成果图版本轨道的端到端测试。

覆盖：旧记录迁移补版本号、边界修订事件重放、乐观锁并发只落一版、
事务失败目录与标注一起回滚、比例尺相同编号不互相覆盖、历史版本保留、
报告引用结论与成果图对得上。
"""
from __future__ import annotations

import pytest

from app.store import store
from app.versioning import (
    RevisionConflict,
    VersionError,
    VersionStore,
)


@pytest.fixture()
def versions() -> VersionStore:
    """每个用例使用独立的内存版本仓库，并从种子数据重新迁移。"""
    fresh = VersionStore()
    fresh.ensure_initialized()
    return fresh


def _track(versions: VersionStore, sheet_no: str, scale: str) -> dict:
    state = versions.state_by_identity(sheet_no, scale)
    assert state is not None
    return state


def test_legacy_rows_migrate_to_v1_with_reviewed_conclusion(versions: VersionStore) -> None:
    # 金山幅 1:50000 种子状态为“已验收” -> 补 v1，原审定结论保留
    state = _track(versions, "I49D001001", "1:50000")
    assert state["confirmed_revision"] == 1
    assert state["confirmed_conclusion"] == "原审定版本（已验收）"

    catalog = {item["track_id"]: item for item in versions.catalog_entries()}
    entry = catalog[state["track_id"]]
    assert entry["当前版本号"] == 1 and entry["版本状态"] == "已确认"

    # 明细表被回填了版本指针
    row = store.find("mapping", state["track_id"])
    assert row is not None
    assert row["修订号"] == 1 and row["版本号"] == 1


def test_same_sheet_no_different_scale_are_separate_tracks(versions: VersionStore) -> None:
    a = _track(versions, "I49D001001", "1:50000")
    b = _track(versions, "I49D001001", "1:25000")
    assert a["track_id"] != b["track_id"]
    assert a["confirmed_geometry"] != b["geometry"]
    # 重复（同编号+同比例尺）登记必须被拒绝
    with pytest.raises(VersionError):
        versions.create_track(sheet_no="I49D001001", sheet_name="金山幅", scale="1:50000")


def test_boundary_revision_stays_draft_until_confirm(versions: VersionStore) -> None:
    track = _track(versions, "I49D001001", "1:50000")
    new_geo = [[1, 1], [2, 1], [2, 2], [1, 2]]
    result = versions.save_boundary(
        track["track_id"], geometry=new_geo, expected_revision=1, note="接边"
    )
    assert result["revision"] == 2

    workspace = versions.workspace(track["track_id"])
    # 未确认前成果图仍是 v1，草稿 r2 挂在工作区
    assert workspace["confirmed_revision"] == 1
    assert workspace["confirmed_geometry"] != new_geo
    assert workspace["pending_revision"] is not None
    assert workspace["pending_revision"]["revision"] == 2


def test_optimistic_lock_concurrent_revisions_only_one_lands(versions: VersionStore) -> None:
    track = _track(versions, "I49D001001", "1:50000")
    versions.save_boundary(track["track_id"], geometry=[[1, 1], [2, 1], [2, 2]], expected_revision=1)
    # 另一个修订者仍拿着旧 head=1
    with pytest.raises(RevisionConflict) as excinfo:
        versions.save_boundary(
            track["track_id"], geometry=[[3, 3], [4, 3], [4, 4]], expected_revision=1
        )
    assert excinfo.value.current == 2
    # 重放最新版本后基于 r2 才能再落一版
    versions.save_boundary(track["track_id"], geometry=[[3, 3], [4, 3], [4, 4]], expected_revision=2)
    workspace = versions.workspace(track["track_id"])
    assert workspace["pending_revision"]["revision"] == 3
    # 被顶掉的 r2 草稿仍留在事件历史里（append-only）
    kinds = [(e["kind"], e["revision"]) for e in workspace["history"]]
    assert ("边界修订", 2) in kinds and ("边界修订", 3) in kinds


def test_transaction_failure_rolls_back_catalog_and_annotations(
    versions: VersionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    track = _track(versions, "I49D001001", "1:50000")
    before_events = len(versions._events[next(iter(versions._events))])  # noqa: SLF001

    def boom(self: VersionStore, key: str) -> dict:  # noqa: ARG001
        raise RuntimeError("模拟地图标注写盘失败")

    monkeypatch.setattr(VersionStore, "_rebuild_locked", boom)
    with pytest.raises(RuntimeError):
        versions.save_boundary(
            track["track_id"], geometry=[[9, 9], [8, 8], [7, 7]], expected_revision=1
        )
    monkeypatch.undo()

    key = next(k for k, tid in versions._index.items() if tid == track["track_id"])  # noqa: SLF001
    # 事件未追加
    assert len(versions._events[key]) == before_events  # noqa: SLF001
    # 目录与地图标注仍指向 v1，旧几何未被污染
    annotation = versions.annotations()[track["track_id"] - 1]
    assert annotation["标注版本"] == 1
    assert versions.workspace(track["track_id"])["pending_revision"] is None


def test_confirm_syncs_detail_catalog_annotation_and_keeps_history(versions: VersionStore) -> None:
    track = _track(versions, "I49D001001", "1:50000")
    new_geo = [[120, 110], [300, 110], [300, 260], [120, 260]]
    versions.save_boundary(track["track_id"], geometry=new_geo, expected_revision=1)
    versions.confirm_revision(
        track["track_id"], revision=2, expected_revision=1, conclusion="合格", editor="审定组"
    )

    workspace = versions.workspace(track["track_id"])
    assert workspace["confirmed_revision"] == 2
    assert workspace["confirmed_conclusion"] == "合格"

    # 三个入口读到同一份结论
    row = store.find("mapping", track["track_id"])
    catalog = {item["track_id"]: item for item in versions.catalog_entries()}
    annotation = {item["track_id"]: item for item in versions.annotations()}
    assert row["审定结论"] == "合格" and row["版本号"] == 2
    assert catalog[track["track_id"]]["审定结论"] == "合格"
    assert annotation[track["track_id"]]["审定结论"] == "合格"
    assert annotation[track["track_id"]]["geometry"] == new_geo

    # 确认环节同样有乐观锁：再确认一个过期基版必须 409
    versions.save_boundary(track["track_id"], geometry=[[1, 1], [2, 1], [2, 2]], expected_revision=2)
    with pytest.raises(RevisionConflict):
        versions.confirm_revision(
            track["track_id"], revision=3, expected_revision=1, conclusion="合格"
        )

    # 既有成果 v1 按原审定版本保留，可重放
    v1 = versions.replay_revision(track["track_id"], 1)
    assert v1["confirmed"] and "原审定" in v1["conclusion"]


def test_confirm_failure_rolls_back_row_status(
    versions: VersionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 用未审定的银沟幅（种子状态“室内整理”）：确认“合格”会改状态，失败必须回滚
    track = _track(versions, "I49D001002", "1:50000")
    versions.save_boundary(
        track["track_id"], geometry=[[1, 1], [2, 1], [2, 2]], expected_revision=1
    )

    def boom(self: VersionStore, key: str) -> dict:  # noqa: ARG001
        raise RuntimeError("模拟目录写入失败")

    monkeypatch.setattr(VersionStore, "_rebuild_locked", boom)
    with pytest.raises(RuntimeError):
        versions.confirm_revision(
            track["track_id"], revision=2, expected_revision=0, conclusion="合格"
        )
    monkeypatch.undo()

    workspace = versions.workspace(track["track_id"])
    assert workspace["confirmed_revision"] == 0
    assert workspace["pending_revision"]["revision"] == 2
    row = store.find("mapping", track["track_id"])
    assert row["status"] == "室内整理" and row["填图状态"] == "室内整理"
    assert row["pending"] is True
    annotation = {item["track_id"]: item for item in versions.annotations()}[track["track_id"]]
    assert annotation["标注状态"] == "待确认草稿"


def test_invalid_geometry_does_not_mutate_state(versions: VersionStore) -> None:
    track = _track(versions, "I49D001001", "1:50000")
    with pytest.raises(VersionError):
        versions.save_boundary(track["track_id"], geometry=[[0, 0], [1, 1]], expected_revision=1)
    workspace = versions.workspace(track["track_id"])
    assert workspace["pending_revision"] is None
    assert workspace["confirmed_revision"] == 1


def test_report_citation_conclusion_derived_from_replay(versions: VersionStore) -> None:
    track = _track(versions, "I49D001001", "1:50000")
    # 种子迁移已为 GEOL-0001 生成指向 v1 的引用
    seeded = versions.citations(report_no="GEOL-0001")
    assert len(seeded) == 1 and seeded[0]["引用版本号"] == 1
    assert seeded[0]["引用结论"].startswith("原审定")

    # 边界修订尚未确认时，报告侧结论不变（引用仍对得上 v1 事实）
    versions.save_boundary(track["track_id"], geometry=[[1, 1], [2, 1], [2, 2]], expected_revision=1)
    assert versions.citations(report_no="GEOL-0001")[0]["引用是否对得上"] is True

    # 确认 v2 后，旧引用被标记为版本落后；引用结论仍是重放 v1 得到的原结论
    versions.confirm_revision(
        track["track_id"], revision=2, expected_revision=1, conclusion="合格"
    )
    stale = versions.citations(report_no="GEOL-0001")[0]
    assert stale["引用是否对得上"] is False
    assert stale["引用结论"].startswith("原审定")
    assert stale["最新确认版本号"] == 2 and stale["最新确认结论"] == "合格"

    # 新引用默认钉最新确认版 v2，结论与成果图一致
    fresh = versions.cite(report_no="GEOL-0003", sheet_no="I49D001001", scale="1:50000")
    assert fresh["引用版本号"] == 2 and fresh["引用结论"] == "合格"
    assert fresh["引用是否对得上"] is True


def test_replay_unknown_revision_raises(versions: VersionStore) -> None:
    track = _track(versions, "I49D001001", "1:50000")
    with pytest.raises(VersionError):
        versions.replay_revision(track["track_id"], 99)


def test_migration_is_idempotent(versions: VersionStore) -> None:
    events_before = {key: len(value) for key, value in versions._events.items()}  # noqa: SLF001
    versions.ensure_initialized()
    events_after = {key: len(value) for key, value in versions._events.items()}  # noqa: SLF001
    assert events_before == events_after
