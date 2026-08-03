# -*- coding: utf-8 -*-
"""build_shapes.py — 扫全归档，把每条轨迹等弧长重采样成 32 点形状签名，
输出 data/shapes.json 供复盘台「相似路径检索」用（浏览器不必加载 110MB 归档）。

形状签名：起点对齐 + 总长归一化后的逐点位置（lat/lon ×1e5 整数，省体积）。
调用方只做平移/尺度不变比较，检索在浏览器端完成。
"""
import glob
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ARCH = os.path.join(ROOT, "data", "archive")
OUT = os.path.join(ROOT, "data", "shapes.json")
N_PTS = 32

D2R = 3.141592653589793 / 180.0


def haversine_km(a, b):
    f1, f2 = a[0] * D2R, b[0] * D2R
    df = (b[0] - a[0]) * D2R
    dl = (b[1] - a[1]) * D2R
    s = (sin(df / 2.0)) ** 2 + cos(f1) * cos(f2) * (sin(dl / 2.0)) ** 2
    return 6371.0 * 2.0 * asin(sqrt(s))


from math import asin, cos, sin, sqrt


def polyline_len(pts):
    return sum(haversine_km(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def resample(pts, n):
    """等弧长重采样：把折线均匀切 n 段，返回插值点 [lat,lon] 列表（n+1 点）。"""
    if len(pts) < 2:
        return None
    seg = [0.0]
    for i in range(len(pts) - 1):
        seg.append(seg[-1] + haversine_km(pts[i], pts[i + 1]))
    total = seg[-1]
    if total <= 0:
        return None
    out = [pts[0]]
    target_step = total / n
    j = 1
    for k in range(1, n):
        target = k * target_step
        while j < len(seg) - 1 and seg[j + 1] < target:
            j += 1
        t = (target - seg[j]) / (seg[j + 1] - seg[j] or 1.0)
        a, b = pts[j], pts[j + 1]
        out.append([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t])
    out.append(pts[-1])
    return out


def main():
    files = sorted(glob.glob(os.path.join(ARCH, "*", "*.json")))
    shapes = []
    for fp in files:
        try:
            with io.open(fp, encoding="utf-8") as f:
                s = json.load(f)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write("skip %s: %s\n" % (fp, e))
            continue
        tr = s.get("track") or []
        if len(tr) < 3:
            continue
        pts = [[p["lat"], p["lon"]] for p in tr if p.get("lat") is not None]
        if len(pts) < 3:
            continue
        rs = resample(pts, N_PTS)
        if not rs:
            continue
        lat0, lon0 = pts[0]
        path_km = polyline_len(pts)
        sig = []
        for p in rs:
            sig.append([round((p[0] - lat0) * 1e5), round((p[1] - lon0) * 1e5)])
        sid = s.get("id") or ""
        shapes.append({
            "id": sid,
            "year": s.get("year") or (int(sid[:4]) if len(sid) >= 4 and sid[:4].isdigit() else None),
            "name_zh": s.get("name_zh") or "", "name_en": s.get("name_en") or "",
            "start": (tr[0].get("t") if tr else None),
            "path_km": round(path_km),
            "origin": [round(lat0 * 1e5), round(lon0 * 1e5)],
            "pts": sig,
        })
    shapes.sort(key=lambda x: (x["year"], x["id"]))
    with io.open(OUT, "w", encoding="utf-8") as f:
        json.dump({"schema_version": "1.1", "generated_at": None,  # filled below
                   "n_shapes": len(shapes), "n_pts": N_PTS, "shapes": shapes},
                  f, ensure_ascii=False, separators=(",", ":"))
    # generated_at 单独填，避免每次构建 diff 整个文件
    data = json.load(io.open(OUT, encoding="utf-8"))
    import datetime
    data["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with io.open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("形状签名完成：%d 条台风 → %s（%d KB）" % (
        len(shapes), OUT, os.path.getsize(OUT) // 1024))


if __name__ == "__main__":
    main()
