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


def no_net():
    """mock 全部境外网络函数抛异常：引导风/SST/VWS/SSTA 全离线，测试确定化。"""
    for fn in ("fetch_steering", "fetch_sst", "fetch_vws", "fetch_ssta"):
        setattr(predict, fn, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no net")))


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
    no_net()
    fc = predict.generate_self(st, None)
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
    # 强度联动：受 MPI 上界约束（默认 SST 28°C → MPI≈43 m/s，含 10% 裕度）
    for p in fc["points"]:
        assert 8 <= p["wind_ms"] <= 48
        assert predict.grade_of(p["wind_ms"]) == p["grade"]


def test_steering_turns_toward_wind():
    st = storm_of([(20, 130, 20), (21, 131, 22), (22, 132, 24),
                   (23, 133, 26), (24, 134, 28), (25, 135, 30)])
    predict.fetch_steering = lambda lat, lon: (270.0, 40.0)   # 东风引导：流向西（270°）
    predict.fetch_sst = lambda *a, **k: 28.0
    predict.fetch_ssta = lambda *a, **k: 28.0
    predict.fetch_vws = lambda *a, **k: 5.0
    fc = predict.generate_self(st, None)
    assert "steering" in fc["methods"]
    p0, pN = fc["points"][0], fc["points"][-1]
    # 对比无引导的纯持续性：被东风拉应明显偏西（终经度更小）
    no_net()
    fc0 = predict.generate_self(st, None)
    q0, qN = fc0["points"][0], fc0["points"][-1]
    assert (qN["lon"] - pN["lon"]) > 15                        # 东风把路径拉西 >15°经度


def test_analog_with_shapes():
    tmp = tempfile.mkdtemp()
    shp = os.path.join(tmp, "shapes.json")
    mk_shapes(shp, n_shapes=3)
    st = storm_of([(20, 130, 20), (21, 131, 22), (22, 132, 24),
                   (23, 133, 26), (24, 134, 28), (25, 135, 30)])
    no_net()
    fc = predict.generate_self(st, shp)
    assert "analog" in fc["methods"]


def test_analog_missing_shapes_ok():
    st = storm_of([(20, 130, 20), (21, 131, 22), (22, 132, 24),
                   (23, 133, 26), (24, 134, 28), (25, 135, 30)])
    no_net()
    fc = predict.generate_self(st, os.path.join(os.path.dirname(__file__), "nope.json"))
    assert fc is not None and "analog" not in fc["methods"]


def test_grades_consistent():
    assert predict.grade_of(10) == "TD"
    assert predict.grade_of(20) == "TS"
    assert predict.grade_of(30) == "STS"
    assert predict.grade_of(38) == "TY"
    assert predict.grade_of(45) == "STY"
    assert predict.grade_of(55) == "SuperTY"


def test_cone_structure():
    st = storm_of([(20, 130, 20), (21, 131, 22), (22, 132, 24),
                   (23, 133, 26), (24, 134, 28), (25, 135, 30)])
    no_net()
    cone = predict.generate_cone(st, None, n_members=60)
    assert cone is not None
    assert set(cone) >= {"p10", "p50", "p90", "n"}
    assert cone["n"] == 60
    for k in ("p10", "p50", "p90"):
        assert len(cone[k]) == 20
        for q in cone[k]:
            assert {"t", "lat", "lon"} <= set(q)


def test_cone_width_grows():
    st = storm_of([(20, 130, 20), (21, 131, 22), (22, 132, 24),
                   (23, 133, 26), (24, 134, 28), (25, 135, 30)])
    no_net()
    cone = predict.generate_cone(st, None, n_members=120, seed=7)
    # P10 与 P90 间距随提前量扩大（近端窄、远端宽）
    d1 = predict.hav([cone["p10"][0]["lat"], cone["p10"][0]["lon"]],
                     [cone["p90"][0]["lat"], cone["p90"][0]["lon"]])
    d2 = predict.hav([cone["p10"][-1]["lat"], cone["p10"][-1]["lon"]],
                     [cone["p90"][-1]["lat"], cone["p90"][-1]["lon"]])
    assert d2 > d1 * 3


def test_cone_reproducible():
    st = storm_of([(20, 130, 20), (21, 131, 22), (22, 132, 24),
                   (23, 133, 26), (24, 134, 28), (25, 135, 30)])
    no_net()
    a = predict.generate_cone(st, None, n_members=40, seed=11)
    b = predict.generate_cone(st, None, n_members=40, seed=11)
    assert a["p50"] == b["p50"]
    assert a["p90"] == b["p90"]


def test_cone_p50_near_deterministic():
    st = storm_of([(20, 130, 20), (21, 131, 22), (22, 132, 24),
                   (23, 133, 26), (24, 134, 28), (25, 135, 30)])
    no_net()
    fc = predict.generate_self(st, None)
    cone = predict.generate_cone(st, None, n_members=80, seed=3)
    det = [[q["lat"], q["lon"]] for q in fc["points"]]

    def mean_off(lin):
        return sum(predict.hav(a, b) for a, b in zip(det, lin)) / len(lin)

    off50 = mean_off([[q["lat"], q["lon"]] for q in cone["p50"]])
    off90 = mean_off([[q["lat"], q["lon"]] for q in cone["p90"]])
    assert off50 < off90                               # 中位成员贴近确定性，极端成员发散


def test_turn_smoothing():
    """转向平滑：即便引导风向强反向，逐 6h 方位角变化也受限 ≤8°。"""
    st = storm_of([(20, 130, 20), (21, 131, 22), (22, 132, 24),
                   (23, 133, 26), (24, 134, 28), (25, 135, 30)])
    # 流向 270°（西）强吹欲把东行的台风扯向西，考验转向限幅
    predict.fetch_steering = lambda lat, lon: (270.0, 60.0)
    predict.fetch_sst = lambda *a, **k: 28.0
    predict.fetch_ssta = lambda *a, **k: 28.0
    predict.fetch_vws = lambda *a, **k: 5.0
    fc = predict.generate_self(st, None)
    brs = []
    for a, b in zip(fc["points"][:-1], fc["points"][1:]):
        brs.append(predict.bearing([a["lat"], a["lon"]], [b["lat"], b["lon"]]))
    for x, y in zip(brs[:-1], brs[1:]):
        d = ((y - x + 540.0) % 360.0) - 180.0
        assert abs(d) <= 8.1


# ---------------------------------------------------------------- SST 强度耦合

def test_sst_mpi_formula():
    # DeMaria-Kaplan 1994：28°C≈43 m/s(STY)，30°C≈56(SuperTY)，26°C≈34(TC)
    m28 = predict.sst_mpi(28.0)
    m30 = predict.sst_mpi(30.0)
    m26 = predict.sst_mpi(26.0)
    assert 40 < m28 < 46
    assert m30 > m28 and m30 < 60
    assert m26 < m28 and m26 > 30


def test_intensity_grows_on_warm_sst():
    # 低温台风 20 m/s 进入 31°C 暖池 → 明显增强向 MPI 靠拢
    st = storm_of([(20, 130, 20)] * 6)
    no_net()
    predict.fetch_sst = lambda *a, **k: 31.0
    predict.fetch_ssta = lambda *a, **k: 31.0      # 距平 0，纯 SST 效应
    fc = predict.generate_self(st, None)
    w0 = fc["points"][0]["wind_ms"]
    wN = fc["points"][-1]["wind_ms"]
    assert wN > w0 + 10                          # 120h 内显著增强
    assert all(p["wind_ms"] <= 62 for p in fc["points"])   # MHz 上界约束


def test_intensity_decays_on_cold():
    # 强台风 强场遇到 24°C 冷水 → 衰减至 TD/TS 区间
    st = storm_of([(20, 130, 45), (21, 131, 45), (22, 132, 45),
                   (23, 133, 45), (24, 134, 45), (25, 135, 45)])
    no_net()
    predict.fetch_sst = lambda *a, **k: 24.0
    predict.fetch_ssta = lambda *a, **k: 24.0      # 距平 0，纯 SST 效应
    fc = predict.generate_self(st, None)
    w0 = fc["points"][0]["wind_ms"]
    wN = fc["points"][-1]["wind_ms"]
    assert wN < w0 - 5
    assert wN < 32                              # 冷水 MPI≈28m/s 承不起超强台风


def test_intensity_offline_still_finite():
    """SST 全失败（无网络）：仍在 28°C 默认值下可得到有限强度，不崩。"""
    st = storm_of([(20, 130, 20), (21, 131, 22), (22, 132, 24)])
    no_net()
    fc = predict.generate_self(st, None)
    assert fc is not None
    for p in fc["points"]:
        assert 8 <= p["wind_ms"] <= 50
        assert predict.grade_of(p["wind_ms"]) == p["grade"]


# ---------------------------------------------------------------- 切变 VWS

def test_vws_factor_clamp():
    assert predict.vws_factor(None) == 1.0
    assert predict.vws_factor(5.0) == 1.0          # 低切变无碍
    assert abs(predict.vws_factor(10.0) - 0.92) < 1e-9
    assert predict.vws_factor(30.0) == 0.35        # 高切变强抑制，封底


def test_intensity_suppressed_by_vws():
    """同 SST 下，强切变路径的风速上限明显低于弱切变。"""
    st = storm_of([(20, 130, 20), (21, 131, 22), (22, 132, 24)])
    no_net()
    predict.fetch_sst = lambda *a, **k: 30.0
    predict.fetch_ssta = lambda *a, **k: 30.0      # 距平 0，纯切变效应

    predict.fetch_vws = lambda *a, **k: 5.0
    lo = predict.generate_self(st, None)
    predict.fetch_vws = lambda *a, **k: 25.0
    hi = predict.generate_self(st, None)
    w_lo = lo["points"][-1]["wind_ms"]
    w_hi = hi["points"][-1]["wind_ms"]
    assert w_lo > w_hi + 10                        # 高切变抑制增强 ≥10 m/s


def test_analog_turn_series():
    """analog 携带转向序列：h 越大 blend 方向越接近序列尾（渐变转向），
    而非全程固定方向。"""
    tmp = tempfile.mkdtemp()
    shp = os.path.join(tmp, "shapes.json")
    # 构造东行→北转的历史段（后续转向）
    lat0, lon0 = 20.0, 125.0
    raw = []
    for j in range(20):
        if j < 8:
            lat, lon = lat0, lon0 + j * 1.5          # 东行
        else:
            lat, lon = lat0 + (j - 8) * 1.2, lon0 + 8 * 1.5   # 北转
        raw.append([round((lat - lat0) * 1e5), round((lon - lon0) * 1e5)])
    shapes = {"shapes": [{
        "id": "h1", "year": 1999, "path_km": 4000,
        "origin": [lat0 * 1e5, lon0 * 1e5], "pts": raw}]}
    with open(shp, "w", encoding="utf-8") as f:
        json.dump(shapes, f)
    st = storm_of([(20, 130, 20), (20.5, 131, 22), (21, 132, 24),
                   (21.5, 133, 26), (22, 134, 28), (22.5, 135, 30)])
    no_net()
    fc = predict.generate_self(st, shp)
    assert "analog" in fc["methods"]
    # 检查转向：末点应明显北偏（lat 增幅大）——analog 序列捕捉北转
    p0, pN = fc["points"][0], fc["points"][-1]
    dlat = pN["lat"] - p0["lat"]
    dlon = pN["lon"] - p0["lon"]
    assert dlat > 2.5                       # 纯东行则 dlat≈0，北转则显著


def test_offline_mode_no_network():
    """offline=True：不触发任何网络函数（steering/SST/VWS/SSTA），路径仅
    persistence+analog，用于回测。"""
    st = storm_of([(20, 130, 20), (21, 131, 22), (22, 132, 24),
                   (23, 133, 26), (24, 134, 28), (25, 135, 30)])
    for fn in ("fetch_steering", "fetch_sst", "fetch_vws", "fetch_ssta"):
        setattr(predict, fn, lambda *a, **k: (_ for _ in ()).throw(AssertionError("offline 不应请求网络")))
    fc = predict.generate_self(st, None, offline=True)
    assert fc is not None
    assert "steering" not in fc["methods"]
    assert fc["methods"] == ["persistence"]
    for p in fc["points"]:
        assert 8 <= p["wind_ms"] <= 48


# ---------------------------------------------------------------- 海温距平 SSTA

def test_ssta_factor_clamp():
    assert predict.ssta_factor(None) == 1.0
    assert abs(predict.ssta_factor(1.0) - 1.03) < 1e-9   # +1°C → +3%
    assert abs(predict.ssta_factor(-2.0) - 0.94) < 1e-9
    assert predict.ssta_factor(10.0) == 1.12            # 封顶 ±12%
    assert predict.ssta_factor(-10.0) == 0.88


def test_intensity_boosted_by_warm_anomaly():
    """同 SST 30°C，暖距平(+2°C)末点风速 > 冷距平(-2°C)。"""
    no_net()
    predict.fetch_sst = lambda *a, **k: 30.0
    predict.fetch_vws = lambda *a, **k: 5.0
    pts = [{"t": "2026-08-01T%02d:00:00+08:00" % (i % 24),
            "lat": 20 + i * 0.2, "lon": 130 + i * 0.2} for i in range(20)]
    predict.fetch_ssta = lambda *a, **k: 28.0      # 气候态 28 → 距平 +2°C（暖）
    warm = predict.evolve_intensity(pts, 20.0, sst_step=1)
    predict.fetch_ssta = lambda *a, **k: 32.0      # 气候态 32 → 距平 -2°C（冷）
    cold = predict.evolve_intensity(pts, 20.0, sst_step=1)
    w_warm = warm[-1]["wind_ms"]
    w_cold = cold[-1]["wind_ms"]
    assert w_warm > w_cold + 5


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