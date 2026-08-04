# -*- coding: utf-8 -*-
"""test_health.py — Schema 一致性校验测试（health_check 强化项）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetch_typhoon as F  # noqa: E402


def mk_latest(storms, year=2026, fixture=False):
    return {"schema_version": F.SCHEMA_VERSION, "generated_at": "2026-08-04T10:00:00+08:00",
            "source": "zjwater", "fixture": fixture, "year": year, "storms": storms}


def mk_index(storms, year=2026):
    return {"year": year, "storms": [{"id": s["id"], "n_points": len(s.get("track", []))} for s in storms]}


def st(id_, track, fcs=None, active=True):
    return {"id": id_, "name_zh": "T", "name_en": "T", "is_active": active,
            "track": track, "forecasts": fcs or []}


def pt(t, lat, lon):
    return {"t": t, "lat": lat, "lon": lon, "wind_ms": 20, "pressure_hpa": 990, "grade": "TS"}


def fc(agency, issued, pts, ens=None):
    f = {"agency": agency, "issued_at": issued, "points": pts}
    if ens is not None:
        f["ensemble"] = ens
    return f


def good_storm():
    tr = [pt("2026-08-01T00:00:00+08:00", 20.0, 130.0),
          pt("2026-08-01T06:00:00+08:00", 20.5, 130.5),
          pt("2026-08-01T12:00:00+08:00", 21.0, 131.0)]
    return st("2026001", tr, [fc("CMA", "2026-08-01T00:00:00+08:00", tr)])


def test_clean_passes():
    s = good_storm()
    issues = F.health_check(mk_latest([s]), mk_index([s]))
    assert issues == []


def test_missing_track_point_field():
    s = good_storm()
    s["track"][1] = {"t": "2026-08-01T06:00:00+08:00", "lat": 20.5}  # 缺 lon
    issues = F.health_check(mk_latest([s]), mk_index([s]))
    assert any("缺 t/lat/lon" in i for i in issues)


def test_missing_forecast_point_field():
    s = good_storm()
    s["forecasts"][0]["points"][0] = {"t": "2026-08-01T00:00:00+08:00", "lat": 20.0}
    issues = F.health_check(mk_latest([s]), mk_index([s]))
    assert any("预报点缺字段" in i for i in issues)


def test_ensemble_bad_structure():
    s = good_storm()
    s["forecasts"][0]["ensemble"] = {"model": "ecmwf_ifs025", "members": []}  # 空 members
    issues = F.health_check(mk_latest([s]), mk_index([s]))
    assert any("系综 members 异常" in i for i in issues)


def test_ensemble_member_no_points():
    s = good_storm()
    s["forecasts"][0]["ensemble"] = {"model": "ecmwf_ifs025",
                                     "members": [{"member": "ctl", "points": []}]}
    issues = F.health_check(mk_latest([s]), mk_index([s]))
    assert any("系综成员" in i and "无点" in i for i in issues)


def test_active_not_in_index():
    s = good_storm()
    s["is_active"] = True
    # 索引里没有这个台风
    issues = F.health_check(mk_latest([s]), mk_index([]))
    assert any("不在当年索引中" in i for i in issues)


def test_schema_version_mismatch():
    s = good_storm()
    payload = mk_latest([s])
    payload["schema_version"] = "0.9"
    issues = F.health_check(payload, mk_index([s]))
    assert any("schema_version 异常" in i for i in issues)


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
