"""成果图版本轨道测试：迁移、乐观锁、事件重放、事务回滚、引用钉版。

直接运行：python -m unittest backend.tests.test_versioning -v
（在仓库根目录执行，需可导入 fastapi）
"""
from __future__ import annotations

import copy
import threading
import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.versioning import (
    RevisionConflict,
    VersionStore,
    unit_key_of,
    validate_geometry,
)


def legacy_rows() -> list[dict]:
    return [
        {"id": 1, "status": "已验收", "pending": False, "abnormal": False,
         "图幅编号": "MAPP-0001", "图幅名称": "青山幅", "比例尺": "1:50000"},
        {"id": 2, "status": "野外进行", "pending": True, "abnormal": False,
         "图幅编号": "MAPP-0001", "图幅名称": "青山幅", "比例尺": "1:25000"},
    ]


class VersionStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = legacy_rows()
        self.store = VersionStore()
        self.store.bootstrap(self.rows)

    def test_legacy_migration_adds_revision(self) -> None:
        """旧图幅记录没有修订号，迁移补上版本号 R1，既有成果按原审定版本保留。"""
        for row in self.rows:
            self.assertEqual(row["修订号"], 1)
            self.assertEqual(row["版本号"], "R1")
        self.assertEqual(self.rows[0]["确认结论"], "旧记录迁移：既有成果按原审定版本保留")
        details, total = self.store.list_details()
        self.assertEqual(total, 2)
        self.assertTrue(all(row["修订号"] == 1 for row in details))

    def test_same_sheet_different_scale_does_not_overwrite(self) -> None:
        """比例尺不同但图幅编号相同：两条独立轨道，修订互不覆盖。"""
        key50 = unit_key_of("MAPP-0001", "1:50000")
        key25 = unit_key_of("MAPP-0001", "1:25000")
        geo50 = {"x": 10, "y": 10, "w": 200, "h": 200}
        geo25_before = copy.deepcopy(self.store.get_detail(2)["geometry"])

        self.store.save_draft(key50, geo50, base_revision=1)
        updated = self.store.submit_revision(
            key50, expected_revision=1, geometry=geo50, conclusion="1:5万修订"
        )
        self.assertEqual(updated["修订号"], 2)
        # 同编号 1:2.5 万轨道仍是 R1，地图标注未被覆盖
        other = self.store.get_detail(2)
        self.assertEqual(other["修订号"], 1)
        self.assertEqual(other["geometry"], geo25_before)
        self.assertNotIn("1:5万修订", other["确认结论"])

    def test_refresh_reads_same_confirmed_version(self) -> None:
        """重放后读到的必须是同一份确认版本（事件流是唯一事实来源）。"""
        key = unit_key_of("MAPP-0001", "1:50000")
        geo = {"x": 50, "y": 60, "w": 300, "h": 250}
        self.store.save_draft(key, {"x": 800, "y": 700, "w": 100, "h": 100}, base_revision=1)
        self.store.submit_revision(key, expected_revision=1, geometry=geo, conclusion="终审版")
        # 草稿在确认后清除；重放投影与重放前一致
        ws = self.store.workspace()
        self.assertIsNone(next(u for u in ws["units"] if u["unit_key"] == key)["draft"])
        self.store.replay_all()
        detail = self.store.get_detail(1)
        self.assertEqual(detail["geometry"], geo)
        self.assertEqual(detail["确认结论"], "终审版")
        self.assertEqual(detail["修订号"], 2)
        catalog = {row["unit_key"]: row for row in self.store.build_catalog()}
        self.assertEqual(catalog[key]["确认结论"], "终审版")  # 目录与详情同一份结论

    def test_optimistic_lock_concurrent_single_winner(self) -> None:
        """并发修订：两个基于 R1 的提交只有一个落版本，输的一方收到 409 语义冲突。"""
        key = unit_key_of("MAPP-0001", "1:50000")
        winner_geo = {"x": 1, "y": 1, "w": 100, "h": 100}
        loser_geo = {"x": 2, "y": 2, "w": 100, "h": 100}

        self.store.submit_revision(key, expected_revision=1, geometry=winner_geo)
        with self.assertRaises(RevisionConflict) as ctx:
            self.store.submit_revision(key, expected_revision=1, geometry=loser_geo)
        self.assertEqual(ctx.exception.current_revision, 2)
        self.assertEqual(ctx.exception.server_state["geometry"], winner_geo)
        # 事件流里只有一次确认
        events = self.store.events(key)
        self.assertEqual([e["type"] for e in events].count("revision_confirmed"), 1)

    def test_concurrent_threads_only_one_revision(self) -> None:
        key = unit_key_of("MAPP-0001", "1:25000")
        results: list[object] = []

        def submit(x: int) -> None:
            try:
                self.store.submit_revision(
                    key, expected_revision=1,
                    geometry={"x": x, "y": x, "w": 80, "h": 80},
                )
                results.append("ok")
            except RevisionConflict:
                results.append("conflict")

        threads = [threading.Thread(target=submit, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(results.count("conflict"), 7)
        self.assertEqual(self.store.get_detail(2)["修订号"], 2)

    def test_transaction_rolls_back_events_catalog_and_annotation(self) -> None:
        """事务失败：事件不落、目录与地图标注一起回滚到提交前版本。"""
        key = unit_key_of("MAPP-0001", "1:50000")
        before_geo = copy.deepcopy(self.store.get_detail(1)["geometry"])
        before_catalog = copy.deepcopy(self.store.build_catalog())
        with self.assertRaises(Exception):
            self.store.submit_revision(
                key, expected_revision=1,
                geometry={"x": 980, "y": 0, "w": 100, "h": 100},  # 越界
            )
        self.assertEqual(self.store.get_detail(1)["geometry"], before_geo)
        self.assertEqual(self.store.build_catalog(), before_catalog)
        self.assertEqual([e["type"] for e in self.store.events(key)], ["initial_import"])

    def test_reference_pins_revision(self) -> None:
        """报告引用钉住修订号：成果图继续修订后，引用仍展示被钉住版本的名称/结论/标注。"""
        key = unit_key_of("MAPP-0001", "1:50000")
        r1_geo = copy.deepcopy(self.store.get_detail(1)["geometry"])
        self.store.register_reference(
            report_no="GEOL-0001", sheet_no="MAPP-0001", scale="1:50000", pinned_revision=1,
        )
        new_geo = {"x": 20, "y": 20, "w": 320, "h": 260}
        self.store.submit_revision(key, expected_revision=1, geometry=new_geo, conclusion="新版结论")
        summary = self.store.reference_summary()
        self.assertEqual(len(summary), 1)
        ref = summary[0]
        self.assertEqual(ref["引用版本"], "R1")
        self.assertEqual(ref["最新修订号"], 2)
        self.assertFalse(ref["引用是否最新"])
        self.assertEqual(ref["地图标注"], r1_geo)
        self.assertEqual(ref["确认结论"], "旧记录迁移：既有成果按原审定版本保留")
        # 重复引用拒绝，不各登记一份
        with self.assertRaises(Exception):
            self.store.register_reference(
                report_no="GEOL-0001", sheet_no="MAPP-0001", scale="1:50000",
            )

    def test_validate_geometry(self) -> None:
        self.assertEqual(validate_geometry({"x": 1.2, "y": 0, "w": "50", "h": 50}),
                         {"x": 1, "y": 0, "w": 50, "h": 50})
        for bad in [
            {"x": -1, "y": 0, "w": 50, "h": 50},
            {"x": 0, "y": 0, "w": 10, "h": 50},
            {"x": 0, "y": 0, "w": 50, "h": 1001},
            "not-a-dict",
        ]:
            with self.assertRaises(Exception):
                validate_geometry(bad)

    def test_register_duplicate_key_rejected(self) -> None:
        """同编号同比例尺重复登记拒绝；显式不同比例尺是新轨道。"""
        with self.assertRaises(Exception):
            self.store.register_unit({"图幅编号": "MAPP-0001", "图幅名称": "青山幅", "比例尺": "1:50000"})
        new = self.store.register_unit(
            {"图幅编号": "MAPP-0009", "图幅名称": "新幅", "比例尺": "1:10000"}
        )
        self.assertEqual(new["修订号"], 1)
        self.assertEqual(len(self.store.workspace()["units"]), 3)


class MappingApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_api_conflict_and_rollback_and_replay(self) -> None:
        ws = self.client.get("/api/mapping/workspace").json()
        key = "MAPP-0003@1:50000"
        unit = next(u for u in ws["units"] if u["unit_key"] == key)
        self.assertEqual(unit["修订号"], 1)

        def submit(rev: int, geo: dict) -> tuple:
            r = self.client.post("/api/mapping/revisions", json={"values": {
                "unit_key": key, "expected_revision": rev, "geometry": geo,
                "确认结论": f"基于R{rev}",
            }})
            return r.status_code, r.json()

        ok_status, ok_body = submit(1, {"x": 30, "y": 30, "w": 200, "h": 200})
        self.assertEqual(ok_status, 200)
        self.assertEqual(ok_body["entry"]["修订号"], 2)
        conflict_status, conflict_body = submit(1, {"x": 40, "y": 40, "w": 200, "h": 200})
        self.assertEqual(conflict_status, 409)
        self.assertEqual(conflict_body["detail"]["current_revision"], 2)

        bad = self.client.post("/api/mapping/revisions", json={"values": {
            "unit_key": "MAPP-0003@1:25000", "expected_revision": 1,
            "geometry": {"x": 900, "y": 900, "w": 200, "h": 200},  # 右下角越界
        }})
        self.assertEqual(bad.status_code, 400)

        replay = self.client.post("/api/mapping/replay")
        self.assertTrue(replay.json()["ok"])
        catalog = {row["unit_key"]: row
                   for row in self.client.get("/api/mapping/catalog").json()["items"]}
        self.assertEqual(catalog[key]["当前修订号"], 2)
        self.assertEqual(catalog[key]["确认结论"], "基于R1")
        self.assertEqual(catalog["MAPP-0003@1:25000"]["当前修订号"], 1)


if __name__ == "__main__":
    unittest.main()
