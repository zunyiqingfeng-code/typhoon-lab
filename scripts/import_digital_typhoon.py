#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Digital Typhoon（日本 NII，agora.ex.nii.ac.jp）GeoJSON → 本项目 schema。

用途：
  1. 当下：把真实 JMA 轨迹灌进 latest.json 做真数据演示
  2. M4：历史台风库回填工具（DT 按 YYYYNN 提供 1951 至今每个台风的 GeoJSON）

口径说明（写进 meta.note，前端如实展示）：
  - wind 为 JMA 10 分钟平均风速（节），换算 m/s = kt * 0.514444
  - 强度分级按国标风速阈值由上换算，与 CMA 定强存在系统性偏差
  - 移向/移速由相邻点大圆几何推算，非官方报文
  - DT 收录有滞后，轨迹可能非全程；截止时间写入 note

用法：
  python3 scripts/import_digital_typhoon.py 输入.geojson [--name-zh 巴威] \\
      [--note "..."] [--out data/latest.json]
"""
import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fetch_typhoon import (SCHEMA_VERSION, TZ_BJ, make_point,  # noqa: E402
                           write_json)

KT2MS = 0.514444
R = 6371.0


def grade_from_ms(w):
    if w is None:
        return None
    return ("SuperTY" if w >= 51 else "STY" if w >= 41.5 else
            "TY" if w >= 32.7 else "STS" if w >= 24.5 else
            "TS" if w >= 17.2 else "TD")


def bearing_dist(a, b):
    """(lat,lon)A → B 的方位角(度)与距离(km)"""
    φ1, φ2 = math.radians(a[0]), math.radians(b[0])
    dλ = math.radians(b[1] - a[1])
    y = math.sin(dλ) * math.cos(φ2)
    x = math.cos(φ1) * math.sin(φ2) - math.sin(φ1) * math.cos(φ2) * math.cos(dλ)
    brg = (math.degrees(math.atan2(y, x)) + 360) % 360
    d = R * math.acos(max(-1, min(1,
        math.sin(φ1) * math.sin(φ2) +
        math.cos(φ1) * math.cos(φ2) * math.cos(dλ))))
    return brg, d


def convert(gj, name_zh, note):
    props = gj.get("properties", {})
    feats = gj.get("features", [])
    raw = []
    for f in feats:
        p = f.get("properties", {})
        c = f.get("geometry", {}).get("coordinates", [None, None])
        if p.get("time") is None or c[0] is None:
            continue
        raw.append((int(p["time"]), float(c[1]), float(c[0]),
                    p.get("wind"), p.get("pressure")))
    raw.sort()
    track = []
    for i, (ts, lat, lon, wkt, pres) in enumerate(raw):
        t = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(TZ_BJ)
        wms = round(wkt * KT2MS, 1) if wkt else None
        mv_dir = mv_kmh = None
        if i > 0:
            pts, plat, plon = raw[i - 1][0], raw[i - 1][1], raw[i - 1][2]
            dh = (ts - pts) / 3600
            if dh > 0:
                brg, dist = bearing_dist((plat, plon), (lat, lon))
                mv_dir, mv_kmh = round(brg), round(dist / dh)
        track.append(make_point(
            t.isoformat(), lat, lon,
            pressure_hpa=pres, wind_ms=wms, grade=grade_from_ms(wms),
            move_dir_deg=mv_dir, move_speed_kmh=mv_kmh))
    last = datetime.fromtimestamp(raw[-1][0], tz=timezone.utc) if raw else None
    storm = {
        "id": str(props.get("number") or props.get("id") or ""),
        "name_zh": name_zh or "",
        "name_en": (props.get("name") or "").upper(),
        "is_active": False,
        "basin": "WP",
        "track": track,
        "forecasts": [],
        "meta": {
            "source": "digital-typhoon-jma",
            "fetched_at": datetime.now(TZ_BJ).isoformat(),
            "note": note or (
                "JMA 10分钟平均风速换算；Digital Typhoon 收录至 %s，"
                "非全程轨迹" % (last.strftime("%m-%d %H:%M UTC") if last else "?")),
        },
    }
    return storm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson")
    ap.add_argument("--name-zh", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "latest.json"))
    a = ap.parse_args()
    gj = json.load(open(a.geojson, encoding="utf-8"))
    storm = convert(gj, a.name_zh, a.note)
    write_json(a.out, {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(TZ_BJ).isoformat(),
        "source": "digital-typhoon-jma",
        "fixture": False,
        "storms": [storm],
    })
    print("台风 %s (%s)：%d 个轨迹点" %
          (storm["name_en"], storm["id"], len(storm["track"])))


if __name__ == "__main__":
    main()
