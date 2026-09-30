"""版本轨道相关 HTTP 接口测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.versioning import version_store


@pytest.fixture()
def client() -> TestClient:
    version_store.reset()
    return TestClient(app, raise_server_exceptions=False)


def test_list_rows_carry_migrated_version(client: TestClient) -> None:
    items = client.get("/api/mapping").json()["items"]
    by_identity = {(row["图幅编号"], row["比例尺"]): row for row in items}
    assert by_identity[("I49D001001", "1:50000")]["版本号"] == 1
    assert "原审定" in by_identity[("I49D001001", "1:50000")]["审定结论"]
    assert by_identity[("I49D001001", "1:25000")]["版本号"] == 1


def test_catalog_and_export_routes_match_before_dynamic(client: TestClient) -> None:
    assert client.get("/api/mapping/catalog").status_code == 200
    assert client.get("/api/mapping/annotations").status_code == 200
    assert client.get("/api/mapping/export").json()["total"] >= 4
    assert client.get("/api/geological_report/citations/summary").status_code == 200
    assert client.get("/api/geological_report/export").status_code == 200


def test_boundary_then_confirm_flow(client: TestClient) -> None:
    track_id = client.get("/api/mapping/catalog").json()["items"][0]["track_id"]
    geo = [[12, 34], [56, 34], [56, 78], [12, 78]]

    saved = client.post(
        f"/api/mapping/{track_id}/boundary",
        json={"geometry": geo, "expected_revision": 1, "note": "修测", "editor": "甲"},
    )
    assert saved.status_code == 200 and saved.json()["ok"]

    # 并发修订 -> 409
    clash = client.post(
        f"/api/mapping/{track_id}/boundary",
        json={"geometry": [[1, 1], [2, 1], [2, 2]], "expected_revision": 1},
    )
    assert clash.status_code == 409

    confirmed = client.post(
        f"/api/mapping/{track_id}/confirm",
        json={"revision": 2, "expected_revision": 1, "conclusion": "合格", "editor": "审定组"},
    )
    assert confirmed.status_code == 200 and confirmed.json()["ok"]

    detail = client.get(f"/api/mapping/{track_id}").json()
    assert detail["版本号"] == 2 and detail["审定结论"] == "合格"
    annotations = {item["track_id"]: item for item in client.get("/api/mapping/annotations").json()["items"]}
    assert annotations[track_id]["geometry"] == geo


def test_create_same_identity_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/mapping",
        json={"values": {"图幅编号": "I49D001001", "图幅名称": "金山幅", "比例尺": "1:50000"}},
    )
    assert resp.json()["ok"] is False


def test_citation_register_and_summary(client: TestClient) -> None:
    resp = client.post(
        "/api/geological_report/citations",
        json={"report_no": "GEOL-0002", "sheet_no": "I49D001001", "scale": "1:50000"},
    )
    assert resp.status_code == 200 and resp.json()["ok"]
    summary = client.get("/api/geological_report/citations/summary").json()
    mine = [item for item in summary["items"] if item["报告编号"] == "GEOL-0002"]
    assert mine and mine[0]["引用版本号"] == 1
