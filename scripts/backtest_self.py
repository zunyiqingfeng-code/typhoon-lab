#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backtest_self.py — SELF 自研推演回测：与官方机构误差对比的公正性检验。

对 data/archive/<year>/<id>.json 中 2025-06 起（ERA5 环境场覆盖期）的台风，
在轨迹上滚动取「已发生的最近 6 点」作为实况，调用 scripts/predict.py 生成
120h SELF 预报，与实际后续轨迹比对位置误差（haversine km），按 lead 档
（24/48/72/120h）聚合；同时聚合同一场台风该时段官方机构（CMA/JMA/...）
相同 lead 的历史误差，输出对比。

局限（诚实标注）：
  - 环境场（steering/SST/VWS/SSTA）取当前 Open-Meteo 预报，对历史时刻不
    严格对应（ERA5 仅覆盖 2025-06 起，且 steering 无历史格点）——路径主要
    由 persistence+analog 驱动，环境场只微调，偏差在可接受量级。
  - 样本量小（2025 年 6 月后台风数有限），结论指向趋势而非精确排名。

用法：
  python3 scripts/backtest_self.py [--limit N] [--years 2025]
输出 data/self_benchmark.json + 控制台对比表。
"""
import datetime
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import predict  # noqa: E402

R_EARTH = 6371.0


def hav(a, b):
    """haversine km"""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(h))


def lead_bucket(h):
    for b in (24, 48, 72, 120):
        if abs(h - b) <= 6:
            return b
    return None


def trim_history(track, n=6, step_h=6):
    """取实况轨迹最后 ~n 个 6h 步进点作为 SELF 输入（模拟"已观测"部分）。

    archive 历史轨迹多为逐小时点，直接喂会给 persistence 引入单步噪声，
    先按 step_h 时间间隔抽稀到至多 n 点，末点必须是 track 末端（保持 issued 时刻）。"""
    if not track:
        return []
    end_t = track[-1]
    try:
        t0 = datetime.datetime.fromisoformat(end_t["t"])
    except Exception:
        return track[-n:]
    picked = [end_t]
    target = t0
    for p in reversed(track[:-1]):
        try:
            t = datetime.datetime.fromisoformat(p["t"])
        except Exception:
            continue
        if t0 - t >= datetime.timedelta(hours=step_h * (len(picked))):
            picked.append(p)
        if len(picked) >= n:
            break
    picked.reverse()
    return picked if len(picked) >= 4 else track[-n:]


def backtest_storm(storm, shapes_path, offline=True, history=False):
    """对单个台风滚动回测：返回 {lead: [err, ...]} 与 n。"""
    track = storm.get("track") or []
    track = [p for p in track if p.get("lat") is not None and p.get("lon") is not None]
    if len(track) < 8:
        return None
    errors = {}
    # 按 24h 时间间隔滚动：从第 6 点起，每 24h 取一次"已观测"轨迹生成 SELF
    last_roll = None
    for i in range(6, len(track) - 3):
        cur_t = track[i].get("t")
        if cur_t is None:
            continue
        try:
            cur_dt = datetime.datetime.fromisoformat(cur_t)
        except Exception:
            continue
        if last_roll is not None and (cur_dt - last_roll) < datetime.timedelta(hours=24):
            continue
        hist = trim_history(track[:i + 1])
        if len(hist) < 4:
            continue
        last_roll = cur_dt
        st = {"id": storm.get("id"), "name_en": storm.get("name_en"),
              "is_active": False, "track": hist}
        try:
            fc = predict.generate_self(st, shapes_path, offline=True,
                                       at_time=(hist[-1]["t"] if history else None),
                                       steer_history=history)
        except Exception:
            continue
        if not fc or not fc.get("points"):
            continue
        fc_map = {}
        for p in fc["points"]:
            try:
                t = datetime.datetime.fromisoformat(p["t"])
            except Exception:
                continue
            fc_map[t] = (p["lat"], p["lon"])
        # 遍历 SELF 预报点，每个点找 ≤3h 内实况，按 lead 归档（避免重复计同一预报点）
        base_t = datetime.datetime.fromisoformat(hist[-1]["t"])
        obs_track = [(datetime.datetime.fromisoformat(p["t"]), (p["lat"], p["lon"]))
                     for p in track[i + 1:]
                     if p.get("t") and p.get("lat") is not None and p.get("lon") is not None]
        for t, fp in fc_map.items():
            h = (t - base_t).total_seconds() / 3600
            b = lead_bucket(h)
            if b is None:
                continue
            near = min(obs_track, key=lambda x: abs((x[0] - t).total_seconds()))
            if abs((near[0] - t).total_seconds()) > 3 * 3600:
                continue
            err = hav(fp, near[1])
            errors.setdefault(b, []).append(err)
    return errors


def collect_official(storm, issues):
    """聚合同台风官方机构同 lead 的历史误差（直接复用 archive 里预报 vs 实况）。"""
    track = storm.get("track") or []
    track = [p for p in track if p.get("lat") is not None and p.get("lon") is not None]
    if len(track) < 3:
        return None
    tmap = {}
    for p in track:
        try:
            tmap[datetime.datetime.fromisoformat(p["t"])] = (p["lat"], p["lon"])
        except Exception:
            continue
    errors = {}
    for fc in storm.get("forecasts") or []:
        if fc.get("agency") == "SELF":
            continue
        for p in fc.get("points") or []:
            try:
                t = datetime.datetime.fromisoformat(p["t"])
            except Exception:
                continue
            obs = tmap.get(t)
            if obs is None:
                near = min(tmap, key=lambda x: abs((x - t).total_seconds()))
                if abs((near - t).total_seconds()) > 3 * 3600:
                    continue
                obs = tmap[near]
            try:
                issued = datetime.datetime.fromisoformat(fc["issued_at"])
            except Exception:
                continue
            h = (t - issued).total_seconds() / 3600
            b = lead_bucket(h)
            if b is None or h < 18:
                continue
            errors.setdefault(b, []).append(hav((p["lat"], p["lon"]), obs))
    return errors


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--years", default="2025")
    ap.add_argument("--shapes", default="data/shapes.json")
    ap.add_argument("--out", default="data/self_benchmark.json")
    ap.add_argument("--online", action="store_true",
                    help="启用网络环境场（steering/SST/切变，慢）")
    ap.add_argument("--history", action="store_true",
                    help="用 Open-Meteo past_days 历史 500hPa 引导风（近 30 天台风）")
    args = ap.parse_args()
    offline = not args.online
    history = args.history

    shapes_path = args.shapes if os.path.exists(args.shapes) else None
    storms = []
    years = [y for y in args.years.split(",") if y]
    for y in years:
        d = os.path.join("data", "archive", y)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                storms.append(json.load(f))
    if args.limit:
        storms = storms[:args.limit]

    agg = {"self": {}, "official": {}, "storms": len(storms), "used": 0}
    for st in storms:
        r = backtest_storm(st, shapes_path, offline=offline, history=history)
        o = collect_official(st, None)
        if not r and not o:
            continue
        agg["used"] += 1
        for b, vals in (r or {}).items():
            agg["self"].setdefault(b, []).extend(vals)
        for b, vals in (o or {}).items():
            agg["official"].setdefault(b, []).extend(vals)

    out = {"schema": "self-benchmark-v1", "generated_at": datetime.datetime.now().isoformat(),
           "note": "SELF 回测：2025-06 起环境场覆盖期；路径主由 persistence+analog 驱动",
           "aggregate": {}}
    print("SELF 回测 · 台风 %d（有效 %d）" % (agg["storms"], agg["used"]))
    print("%-8s %14s %8s %14s %8s" % ("lead", "SELF均值km", "SELF样本", "官方均值km", "官方样本"))
    for b in (24, 48, 72, 120):
        sv = agg["self"].get(b) or []
        ov = agg["official"].get(b) or []
        sm = sum(sv) / len(sv) if sv else None
        om = sum(ov) / len(ov) if ov else None
        out["aggregate"][str(b)] = {
            "self_mean_km": round(sm, 1) if sm else None,
            "self_n": len(sv),
            "official_mean_km": round(om, 1) if om else None,
            "official_n": len(ov),
        }
        print("%-8s %14s %8d %14s %8d" % (str(b) + "h",
              ("%.1f" % sm) if sm else "-", len(sv),
              ("%.1f" % om) if om else "-", len(ov)))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("写出", args.out)


if __name__ == "__main__":
    main()
