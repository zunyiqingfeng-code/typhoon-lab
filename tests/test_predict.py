# -*- coding: utf-8 -*-
"""test_predict.py — SELF 推演引擎离线测试（不碰网络，mock 引导气流/形状库）。"""
import datetime
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import predict  # noqa: E402

TZ_BJ = datetime.timezone(datetime.timedelta(hours=8))


def track_of(points, t0=None):
    """构造统一 schema 轨迹。points: [(lat, lon, wind_ms), ...]，间隔 6h。"""
    if t0 is None:
        t0 = datetime.datetime(2026, 8, 1, 0, 0, tzinfo=TZ_BJ)
    out = []
    for i, (lat, lon, w) in enumerate(points):
        out.append({"t": (t0 + datetime.timedelta(hours=6 * i)).isoformat(),
                    "lat": lat, "lon": lon,
                    "wind_ms": w, "pressure_hpa": 1000 - i,
                    "grade": predict.grade_of(w)})
    return out


def storm_of(points, sid="202699", name="TEST"):
    return {"id": sid, "name_zh": name, "name_en": name, "is_active": True,
            "track": track_of(points)}


def mk_shapes(path, n_shapes=0):
    """生成 shapes.json：可选项——写入 n_shapes 条东行直线历史（起点 20N 130E 往东）。"""
    if n_shapes == 0:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"shapes": []}, f)
        return
    shapes = []
    for i in range(n_shapes):
        lat0 = 20 + i
        lon0 = 125 + i
        pts = [[lat0, lon0 + j * 2.0] for j in range(32)]
        shapes.append({
            "id": "1999%02d" % i, "year": 1999, "path_km": 4000,
            "origin": [lat0 * 1e5, lon0 * 1e5],
            "pts": [[(lat0 - lat0) * 1e5, (lon0 + j * 2.0 - lon0) * 1e5]
                    for j in range(32)],
        })
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"shapes": shapes}, f)


# ---------------------------------------------------------------- 用例

def test_insufficient_track_returns_none():
    st = storm_of([(20, 130, 20), (20.5, 130, 20)])
    assert predict.generate_self(st, None) is None


def test_persistence_only_no_network():
    st = storm_of([(20, 130, 20), (21, 131, 22), (22, 132, 24),
                   (23, 133, 26), (24, 134, 28), (25, 135, 30)])
    old = predict.fetch_steering
    predict.fetch_steering = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no net"))
    try:
        fc = predict.generate_self(st, None)
    finally:
        predict.fetch_steering = old
    assert fc is not None
    assert fc["agency"] == "SELF"
    assert fc["methods"] == ["persistence"]
    assert len(fc["points"]) == 20                # 120h / 6h
    # 走向：东北（北纬增加，东经增加）
    p0, pN = fc["points"][0], fc["points"][-1]
    assert pN["lat"] > p0["lat"] and pN["lon"] > p0["lon"]
    # 时间步进 6h
    t0 = datetime.datetime.fromisoformat(fc["points"][0]["t"])
    t1 = datetime.datetime.fromisoformat(fc["points"][1]["t"])
    assert (t1 - t0).total_seconds() == 6 * 3600
    # 强度联动：缓弱且不超过实况
    assert fc["points"][-1]["wind_ms"] < fc["points"][0]["wind_ms"]
    for p in fc["points"]:
        assert 0 < p["wind_ms"] <= 35
        assert p["grade"] in predict.GRADE_BY_WIND or predict.grade_of(p["wind_ms"]) == p["grade"]


def test_steering_turns_toward_wind():
    st = storm_of([(20, 130, 20), (21, 131, 22), (22, 132, 24),
                   (23, 133, 26), (24, 134, 28), (25, 135, 30)])
    predict.fetch_steering = lambda lat, lon: (270.0, 40.0)   # 东风引导：流向西（270°）
    fc = predict.generate_self(st, None)
    assert "steering" in fc["methods"]
    p0, pN = fc["points"][0], fc["points"][-1]
    # 对比无引导的纯持续性：被东风拉应明显偏西（终经度更小）
    predict.fetch_steering = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no net"))
    fc0 = predict.generate_self(st, None)
    q0, qN = fc0["points"][0], fc0["points"][-1]
    assert (qN["lon"] - pN["lon"]) > 15                        # 东风把路径拉西 >15°经度


def test_analog_with_shapes():
    tmp = tempfile.mkdtemp()
    shp = os.path.join(tmp, "shapes.json")
    mk_shapes(shp, n_shapes=3)
    st = storm_of([(20, 130, 20), (21, 131, 22), (22, 132, 24),
                   (23, 133, 26), (24, 134, 28), (25, 135, 30)])
    predict.fetch_steering = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no net"))
    fc = predict.generate_self(st, shp)
    assert "analog" in fc["methods"]


def test_analog_missing_shapes_ok():
    st = storm_of([(20, 130, 20), (21, 131, 22), (22, 132, 24),
                   (23, 133, 26), (24, 134, 28), (25, 135, 30)])
    predict.fetch_steering = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no net"))
    fc = predict.generate_self(st, os.path.join(os.path.dirname(__file__), "nope.json"))
    assert fc is not None and "analog" not in fc["methods"]


def test_grades_consistent():
    assert predict.grade_of(10) == "TD"
    assert predict.grade_of(20) == "TS"
    assert predict.grade_of(30) == "STS"
    assert predict.grade_of(38) == "TY"
    assert predict.grade_of(45) == "STY"
    assert predict.grade_of(55) == "SuperTY"


def test_turn_smoothing():
    """转向平滑：即便引导风向强反向，逐 6h 方位角变化也受限 ≤8°。"""
    st = storm_of([(20, 130, 20), (21, 131, 22), (22, 132, 24),
                   (23, 133, 26), (24, 134, 28), (25, 135, 30)])
    # 流向 270°（西）强吹欲把东行的台风扯向西，考验转向限幅
    predict.fetch_steering = lambda lat, lon: (270.0, 60.0)
    fc = predict.generate_self(st, None)
    brs = []
    for a, b in zip(fc["points"][:-1], fc["points"][1:]):
        brs.append(predict.bearing([a["lat"], a["lon"]], [b["lat"], b["lon"]]))
    for x, y in zip(brs[:-1], brs[1:]):
        d = ((y - x + 540.0) % 360.0) - 180.0
        assert abs(d) <= 8.1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    n = 0
    for f in fns:
        try:
            f()
            print("PASS %s" % f.__name__)
        except AssertionError as e:
            n += 1
            print("FAIL %s: %s" % (f.__name__, e))
    print("共 %d 个测试，失败 %d" % (len(fns), n))
    sys.exit(1 if n else 0)