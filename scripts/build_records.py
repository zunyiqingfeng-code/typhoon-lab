#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""遍历 data/archive/ 全部台风，预计算路径长度与生命时长 → data/records.json。
浏览器加载不了 110MB 全归档，排行榜/年度对比需要逐台风指标，放管道侧一次算好。
用法：python3 scripts/build_records.py
"""
import json
import math
import os
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "data", "archive")
OUT = os.path.join(ROOT, "data", "records.json")


def haversine_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    s = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(s))


def main():
    storms = []
    years = sorted(d for d in os.listdir(ARCHIVE) if len(d) == 4 and d.isdigit())
    for y in years:
        for f in sorted(os.listdir(os.path.join(ARCHIVE, y))):
            if not f.endswith(".json"):
                continue
            try:
                with open(os.path.join(ARCHIVE, y, f), encoding="utf-8") as fh:
                    s = json.load(fh)
            except (OSError, ValueError):
                continue
            tr = s.get("track") or []
            if len(tr) < 2:
                continue
            path = sum(
                haversine_km([tr[i - 1]["lat"], tr[i - 1]["lon"]],
                             [tr[i]["lat"], tr[i]["lon"]])
                for i in range(1, len(tr)))
            try:
                start = datetime.fromisoformat(tr[0]["t"])
                end = datetime.fromisoformat(tr[-1]["t"])
                life_h = (end - start).total_seconds() / 3600
            except ValueError:
                life_h = None
            peak = max((p.get("wind_ms") or 0) for p in tr) or None
            pres = [p.get("pressure_hpa") for p in tr if p.get("pressure_hpa")]
            storms.append({
                "id": s.get("id"), "year": int(y),
                "name_zh": s.get("name_zh") or "", "name_en": s.get("name_en") or "",
                "start": tr[0]["t"], "end": tr[-1]["t"],
                "life_h": round(life_h, 1) if life_h is not None else None,
                "path_km": round(path), "peak_wind_ms": peak,
                "min_pressure_hpa": min(pres) if pres else None,
                "n_points": len(tr),
            })
    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().isoformat(),
        "n_storms": len(storms),
        "storms": storms,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print("记录完成：%d 个台风 → %s（%.0f KB）" %
          (len(storms), OUT, os.path.getsize(OUT) / 1024))


if __name__ == "__main__":
    main()
