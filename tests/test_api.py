"""API 集成测试：最优解、字典序 tie-break、不可行范围、幂等与查询。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def _source(id, ca, mg, alk, avail, cost):
    return {"id": id, "calcium": ca, "magnesium": mg, "alkalinity": alk,
            "available": avail, "cost_per_liter": cost}


def _request(sources, **overrides):
    body = {
        "target_volume": 1000.0,
        "targets": {
            "calcium": {"min": 50.0, "max": 60.0},
            "magnesium": {"min": 0.0, "max": 100.0},
            "alkalinity": {"min": 0.0, "max": 1000.0},
        },
        "sources": sources,
    }
    body.update(overrides)
    return body


def test_optimal_basic(client):
    # 钙约束要求 x_a >= 500，a 更贵，故取 x_a = 500
    body = _request([
        _source("a", 70, 10, 100, 1000, 2.0),
        _source("b", 30, 20, 200, 1000, 1.0),
    ])
    resp = client.post("/plans", json=body)
    assert resp.status_code == 201
    plan = resp.json()
    usage = {u["source_id"]: u["volume"] for u in plan["usage"]}
    assert usage["a"] == pytest.approx(500.0)
    assert usage["b"] == pytest.approx(500.0)
    assert plan["total_cost"] == pytest.approx(1500.0)
    assert sum(usage.values()) == pytest.approx(1000.0)


def test_supply_limit_forces_expensive_source(client):
    # 便宜水源 b 只有 100 L，剩余必须用贵的 a
    body = _request([
        _source("a", 55, 10, 100, 2000, 2.0),
        _source("b", 55, 10, 100, 100, 1.0),
    ])
    resp = client.post("/plans", json=body)
    assert resp.status_code == 201
    usage = {u["source_id"]: u["volume"] for u in resp.json()["usage"]}
    assert usage["b"] == pytest.approx(100.0)
    assert usage["a"] == pytest.approx(900.0)


def test_tie_break_lexicographic_by_source_id(client):
    # 两个水源化学指标与成本完全相同，成本解不唯一；
    # 字典序最小 => 按 ID 排序后靠前水源用量取最小（0）
    body = _request([
        _source("b", 55, 10, 100, 1000, 1.0),
        _source("a", 55, 10, 100, 1000, 1.0),
    ])
    resp = client.post("/plans", json=body)
    assert resp.status_code == 201
    usage = resp.json()["usage"]
    assert [u["source_id"] for u in usage] == ["a", "b"]  # 按 ID 排序
    assert usage[0]["volume"] == pytest.approx(0.0)
    assert usage[1]["volume"] == pytest.approx(1000.0)


def test_tie_break_three_sources(client):
    # a、b 等成本且指标相同，c 更贵；字典序最小 => a=0, b=1000, c=0
    body = _request([
        _source("c", 55, 10, 100, 1000, 5.0),
        _source("b", 55, 10, 100, 1000, 1.0),
        _source("a", 55, 10, 100, 1000, 1.0),
    ])
    resp = client.post("/plans", json=body)
    assert resp.status_code == 201
    usage = {u["source_id"]: u["volume"] for u in resp.json()["usage"]}
    assert usage["a"] == pytest.approx(0.0)
    assert usage["b"] == pytest.approx(1000.0)
    assert usage["c"] == pytest.approx(0.0)


def test_infeasible_returns_achievable_ranges(client):
    # 所有水源钙浓度 >= 70，目标区间 [50, 60] 不可达
    body = _request([
        _source("a", 70, 10, 100, 1000, 1.0),
        _source("b", 90, 20, 200, 1000, 2.0),
    ])
    resp = client.post("/plans", json=body)
    assert resp.status_code == 422
    detail = resp.json()
    assert detail["status"] == "infeasible"
    assert detail["achievable"]["calcium"]["min"] == pytest.approx(70.0)
    assert detail["achievable"]["calcium"]["max"] == pytest.approx(90.0)
    assert detail["requested"]["calcium"]["max"] == 60.0


def test_infeasible_volume_shortage(client):
    body = _request(
        [_source("a", 55, 10, 100, 300, 1.0),
         _source("b", 55, 10, 100, 400, 1.0)],
        target_volume=1000.0,
    )
    resp = client.post("/plans", json=body)
    assert resp.status_code == 422
    detail = resp.json()
    assert detail["max_volume"] == pytest.approx(700.0)
    assert detail["evaluated_at_volume"] == pytest.approx(700.0)


def test_idempotency_replay_and_conflict(client):
    body = _request(
        [_source("a", 70, 10, 100, 1000, 2.0),
         _source("b", 30, 20, 200, 1000, 1.0)],
        idempotency_key="order-42",
    )
    first = client.post("/plans", json=body)
    assert first.status_code == 201
    replay = client.post("/plans", json=body)
    assert replay.status_code == 200
    assert replay.json()["plan_id"] == first.json()["plan_id"]

    changed = dict(body, target_volume=2000.0)
    conflict = client.post("/plans", json=changed)
    assert conflict.status_code == 409


def test_get_plan_by_id(client):
    body = _request([
        _source("a", 70, 10, 100, 1000, 2.0),
        _source("b", 30, 20, 200, 1000, 1.0),
    ])
    created = client.post("/plans", json=body).json()
    resp = client.get(f"/plans/{created['plan_id']}")
    assert resp.status_code == 200
    assert resp.json() == created

    missing = client.get("/plans/does-not-exist")
    assert missing.status_code == 404


def test_validation_errors(client):
    # 水源数量不足 / 超上限 / ID 重复 / 区间倒置
    one = _request([_source("a", 55, 10, 100, 1000, 1.0)])
    assert client.post("/plans", json=one).status_code == 422

    seven = _request([_source(f"s{i}", 55, 10, 100, 1000, 1.0)
                      for i in range(7)])
    assert client.post("/plans", json=seven).status_code == 422

    dup = _request([_source("a", 55, 10, 100, 1000, 1.0),
                    _source("a", 55, 10, 100, 1000, 1.0)])
    assert client.post("/plans", json=dup).status_code == 422

    bad_range = _request([_source("a", 55, 10, 100, 1000, 1.0),
                          _source("b", 55, 10, 100, 1000, 1.0)])
    bad_range["targets"]["calcium"] = {"min": 60.0, "max": 50.0}
    assert client.post("/plans", json=bad_range).status_code == 422
