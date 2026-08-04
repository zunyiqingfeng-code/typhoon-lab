#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""predict.py — SELF 自研推演引擎（管道侧，纯标准库无 pip 依赖）

对一份台风实况轨迹（track），用三种方法融合外推 120h（6h 步进）：
  1. 持续性外推 persistence —— 最近实况位移矢量线性持续（短期主导）
  2. 引导气流 steering     —— Open-Meteo 500hPa 探针风矢量驱动（中期修正）
  3. 相似路径 analog       —— data/shapes.json 历史 32 点签名，检索窗口相似段，
                              取历史后续位移序列加权作为长期趋势（转向参考）
三源加权随提前量平滑过渡：短时重持续性、长时重相似、全程用 steering 压制无意义转向抖动。

输出统一 schema 的 SELF 预报（agency 固定 "SELF"），与其它机构预报同构，
写入 storm["forecasts"] 后自动进入 latest/归档/复盘评分，前端无需特判。

任意子方法失败自动降级，绝不抛异常打断抓取管道：
  - Open-Meteo 请求失败/超时 → 跳过 steering
  - shapes.json 缺失/为空     → 跳过 analog（仅持续性）
  - 实况轨迹不足 3 点         → 返回 None

用法（调试，非管道入口）：
  python3 scripts/predict.py            # 读 data/latest.json 活跃台风，打印 SELF 概要
"""
import datetime
import json
import math
import os
import sys

D2R = math.pi / 180.0
R_EARTH = 6371.0
TZ_BJ = datetime.timezone(datetime.timedelta(hours=8))

LEAD_H = 120          # 外推总长（小时）
STEP_H = 6            # 每步（小时）
WIND_SLOW = 20.0      # 引导风速度下限 km/h（避免浮点抖动）

GRADE_BY_WIND = [      # CMA GB/T 19201 近中心最大风速 m/s 分级
    (51.0, "SuperTY"), (41.5, "STY"), (33.0, "TY"),
    (24.5, "STS"), (17.2, "TS"), (0.0, "TD"),
]


def grade_of(w):
    if not w:
        return "TD"
    for thr, g in GRADE_BY_WIND:
        if w >= thr:
            return g
    return "TD"


# ---------------------------------------------------------------- 几何

def hav(a, b):
    f1, f2 = a[0] * D2R, b[0] * D2R
    df = (b[0] - a[0]) * D2R
    dl = (b[1] - a[1]) * D2R
    s = math.sin(df / 2.0) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2.0) ** 2
    return 2.0 * R_EARTH * math.asin(math.sqrt(s))


def bearing(a, b):
    """a→b 初始方位角（度，0=北）。"""
    y = math.sin((b[1] - a[1]) * D2R) * math.cos(b[0] * D2R)
    x = (math.cos(a[0] * D2R) * math.sin(b[0] * D2R) -
         math.sin(a[0] * D2R) * math.cos(b[0] * D2R) * math.cos((b[1] - a[1]) * D2R))
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def dest(lat, lon, brg, km):
    d = km / R_EARTH
    lat1, lon1 = lat * D2R, lon * D2R
    brr = brg * D2R
    la2 = math.asin(math.sin(lat1) * math.cos(d) +
                    math.cos(lat1) * math.sin(d) * math.cos(brr))
    lo2 = lon1 + math.atan2(math.sin(brr) * math.sin(d) * math.cos(lat1),
                            math.cos(d) - math.sin(lat1) * math.sin(la2))
    return [math.degrees(la2), (math.degrees(lo2) + 540.0) % 360.0 - 180.0]


def _pt(p):
    return [p["lat"], p["lon"]]


# ---------------------------------------------------------------- 三方法

def _http_json(url, timeout=8):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "typhoon-lab/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


_STEER_CACHE = {}


def _steer_key(at_time, lat, lon):
    if not at_time:
        return None
    return (datetime.datetime.fromisoformat(at_time).strftime("%Y-%m-%dT%H"),
            round(lat, 1), round(lon, 1))


def _parse_dt(s):
    """容错解析时间字符串（ISO，带或不带时区 → 统一转 UTC aware）。"""
    try:
        dt = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def fetch_steering(lat, lon, radius_deg=6.0, at_time=None):
    """大尺度引导气流近似：台风中心 ±radius_deg 五点（中心/北/南/东/西）500hPa
    风矢量平均——台风自身环流半径可达数百 km，单点采样会被涡旋污染。
    经验关系：台风移动速度 ≈ 0.7 × 引导风速（beta 效应折减），并设物理上限。
    返回 (流向方位角, km/h)。失败抛异常由调用方降级。

    at_time 给出历史时刻（ISO），则用 past_days 窗口拉历史风场并取该时刻
    附近 ±6h 样本平均——供回测近 30 天台风时使用真实历史引导气流。
    历史请求按 (时刻, 位置) 缓存，避免回测重复拉取。"""
    ck = _steer_key(at_time, lat, lon)
    if ck and ck in _STEER_CACHE:
        return _STEER_CACHE[ck]
    pts = [(lat, lon), (lat + radius_deg, lon), (lat - radius_deg, lon),
           (lat, lon + radius_deg), (lat, lon - radius_deg)]
    sx = sy = ws = 0.0
    k = 0
    for plat, plon in pts:
        url = ("https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
               "&hourly=wind_speed_500hPa,wind_direction_500hPa"
               % (plat, plon))
        if at_time:
            url += "&past_days=30&forecast_days=1&wind_speed_unit=ms"
        else:
            url += "&forecast_days=2&wind_speed_unit=ms"
        d = _http_json(url, 8)
        hh = d.get("hourly") or {}
        spd = hh.get("wind_speed_500hPa") or []
        drc = hh.get("wind_direction_500hPa") or []
        tms = hh.get("time") or []
        if at_time and tms:
            target = _parse_dt(at_time)
            idxs = sorted(range(len(tms)),
                          key=lambda i: abs(_parse_dt(tms[i]) - target).total_seconds())
            ph = [(spd[i], drc[i]) for i in idxs[:12]
                  if spd[i] is not None and drc[i] is not None
                  and not math.isnan(spd[i]) and not math.isnan(drc[i])
                  and abs(_parse_dt(tms[i]) - target).total_seconds() <= 12 * 3600]
        else:
            # 取当前起 12h 窗口平均（12 个样本），平滑引导气流瞬时波动
            # ——单点 1-3h 采样会传导 GFS 短时扰动，导致逐 6h 预报路径抖动
            ph = [(s, r) for s, r in zip(spd, drc)
                  if s is not None and r is not None and not math.isnan(s) and not math.isnan(r)]
            ph = ph[:12]
        if not ph:
            continue
        m = len(ph)
        wx = sum(s * math.sin(r * D2R) for s, r in ph) / m
        wy = sum(s * math.cos(r * D2R) for s, r in ph) / m
        sx += wx
        sy += wy
        ws += math.hypot(wx, wy)
        k += 1
    if k == 0:
        raise ValueError("Open-Meteo 无有效风场样本")
    u = sx / k
    v = sy / k
    speed = math.hypot(u, v)
    if speed < 0.5:
        raise ValueError("引导风近似静稳")
    br = (math.degrees(math.atan2(u, v)) + 180.0 + 360.0) % 360.0
    res = (br, min(speed * 3.6 * 0.7, 55.0))      # km/h，0.7 折减，≤55
    if ck:
        _STEER_CACHE[ck] = res
    return res


def _persist_vector(track):
    """最近 ≤18h 实况位移 → (方位角, km/h)；样本不足返回 None。"""
    latlon = [p for p in track if p.get("lat") is not None and p.get("lon") is not None]
    if len(latlon) < 2:
        return None
    a, b = latlon[-2], latlon[-1]
    d = hav(_pt(a), _pt(b))
    try:
        dt = (datetime.datetime.fromisoformat(b["t"]) -
              datetime.datetime.fromisoformat(a["t"])).total_seconds() / 3600.0
    except (ValueError, KeyError):
        dt = float("inf")
    if dt <= 0 or not math.isfinite(dt):
        dt = STEP_H
    return bearing(_pt(a), _pt(b)), d / dt


def _resample(pts, n):
    """等弧长取 n 点（含首尾），pts:[[lat,lon]..]。"""
    if len(pts) < 2:
        return pts
    seg = [0.0]
    for i in range(len(pts) - 1):
        seg.append(seg[-1] + hav(pts[i], pts[i + 1]))
    total = seg[-1]
    if total <= 0:
        return None
    out = [pts[0]]
    step = total / (n - 1)
    j = 1
    for k in range(1, n - 1):
        tgt = k * step
        while j < len(seg) - 1 and seg[j + 1] < tgt:
            j += 1
        t = (tgt - seg[j]) / (seg[j + 1] - seg[j] or 1.0)
        a, b2 = pts[j], pts[j + 1]
        out.append([a[0] + (b2[0] - a[0]) * t, a[1] + (b2[1] - a[1]) * t])
    out.append(pts[-1])
    return out


def _window_sim(cur, win):
    n = min(len(cur), len(win))
    if n < 3:
        return float("inf")
    d = 0.0
    for i in range(n):
        d += math.hypot((cur[i][1] - cur[0][1]) * 111.32 - (win[i][1] - win[0][1]) * 111.32,
                        (cur[i][0] - cur[0][0]) * 110.57 - (win[i][0] - win[0][0]) * 110.57)
    return d / n


def analog_vector(track, shapes_path, top_n=3):
    """data/shapes.json 相似路径检索 → 后续运动参考 (方位角, km/h)。

    当前最近段与每个历史台风 32 点签名逐窗比较形状，取最相似 top 窗口的
    "后续段"位移加权平均作为长期趋势。返回 None 表示无可用资料。

    增强：返回值第二元素扩展为 (speed, series)，series 为相似段后续
    8 点的相对方向序列（供 _track 渐变转向用）；无 series 时退化为标量。"""
    latlon = [p for p in track if p.get("lat") is not None and p.get("lon") is not None]
    if len(latlon) < 6 or not shapes_path or not os.path.exists(shapes_path):
        return None
    try:
        with open(shapes_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    cur = _resample([_pt(p) for p in latlon[-9:]], 16)
    if not cur:
        return None
    bests = []
    for s in data.get("shapes") or []:
        raw = s.get("pts") or []
        if len(raw) < 20:
            continue
        lat0, lon0 = s.get("origin") or [0, 0]
        hist = [[lat0 + v[0] / 1e5, lon0 + v[1] / 1e5] for v in raw]
        for k in range(len(hist) - 15):
            w = hist[k:k + 16]
            if len(w) < 16:
                continue
            sim = _window_sim(cur, w)
            if (s.get("path_km") or 0) > 250:
                bests.append((sim, k, hist))
    if not bests:
        return None
    bests.sort(key=lambda x: x[0])
    vecs = []
    series = []
    for sim, k, hist in bests[:top_n]:
        tail = hist[k + 16:k + 19]
        if len(tail) < 2:
            continue
        spd = hav(tail[0], tail[-1]) / (2.0 * STEP_H)
        br = bearing(tail[0], tail[-1])
        wt = 1.0 / (sim + 1e-6)
        vecs.append((br, max(spd, 1e-6), wt))
        # 相似段后续 8 点方向序列（等时距 6h），用于渐变转向
        nxt = hist[k + 16:k + 25]
        if len(nxt) >= 3:
            seq = []
            for a, b2 in zip(nxt[:-1], nxt[1:]):
                seq.append(bearing(a, b2))
            if seq:
                series.append((wt, seq))
    if not vecs:
        return None
    tot = sum(v for _, _, v in vecs)
    sx = sum(math.cos(br * D2R) * wt for br, _, wt in vecs) / tot
    sy = sum(math.sin(br * D2R) * wt for br, _, wt in vecs) / tot
    spd = sum(sp * wt for _, sp, wt in vecs) / tot
    # 加权平均转向序列：按步融合各相似段的逐段方向
    if series:
        maxlen = max(len(s) for _, s in series)
        agg = []
        for i in range(maxlen):
            xs = ys = ts = 0.0
            for wt, s in series:
                if i < len(s):
                    xs += math.cos(s[i] * D2R) * wt
                    ys += math.sin(s[i] * D2R) * wt
                    ts += wt
            if ts > 0:
                agg.append((math.degrees(math.atan2(ys, xs)) + 360.0) % 360.0)
        return (math.degrees(math.atan2(sy, sx)) % 360.0, spd, agg)
    return (math.degrees(math.atan2(sy, sx)) % 360.0, spd)


# ---------------------------------------------------------------- 融合

def _weights(h):
    """提前量 h(h) 的三源权重。

    0~18h 持续性主导（短时惯性最强）；18h 起 analog 渐入（相似路径的
    转向信息应尽早生效，真实台风 24h 内即开始偏转）；>72h 趋稳
    steering+analog，persistence 完全退出（直线外推在长提前量必然发散）。"""
    if h <= 18:
        return {"persistence": 1.0, "steering": 0.0, "analog": 0.0}
    if h >= 72:
        return {"persistence": 0.0, "steering": 0.55, "analog": 0.45}
    # 18h→72h 线性过渡：persistence 1→0，analog 0→0.45，steering 0→0.55
    f = min(1.0, (h - 18) / 54.0)
    return {"persistence": 1.0 - f, "steering": 0.55 * f, "analog": 0.45 * f}


def _blend(methods, h):
    """h 时刻三源加权合成 (方位角, km/h)。

    analog 方法项可携带转向序列（第 3 元素为 list，逐 6h 步的方向），
    该方向随 h 推进而渐变，模拟真实台风连续转弯而非折线段。"""
    nom = _weights(h)
    wsum = sum(nom.get(name, 0.0) for name, _, _ in methods)
    if wsum <= 1e-9:                       # 名义权重全落在不可用源 → 均分
        wsum = len(methods)
        nom = {name: 1.0 for name, _, _ in methods}
    sx = sy = spd = tot = stot = 0.0
    for name, br, sp in methods:
        wt = nom.get(name, 0.0) / wsum
        if not wt:
            continue
        tot += wt
        if name == "analog" and isinstance(sp, (list, tuple)) and len(sp) >= 2 \
                and isinstance(sp[1], list) and len(sp[1]) >= 2:
            # 转向序列：sp=(spd, series)，取对应步方向（约每 6h 一步）
            series = sp[1]
            idx = max(0, min(len(series) - 1, int(h / STEP_H) - 1))
            br_a = series[idx]
            sx += math.cos(br_a * D2R) * wt
            sy += math.sin(br_a * D2R) * wt
            continue
        sx += math.cos(br * D2R) * wt
        sy += math.sin(br * D2R) * wt
        if name != "analog":              # analog 只定方向，速度无时间戳不可信
            spd += sp * wt
            stot += wt
    if tot <= 0:
        return None
    br = (math.degrees(math.atan2(sy, sx)) + 360.0) % 360.0
    return br, (spd / stot if stot > 0 else 0.0)


def _track(methods, start, t0, step=STEP_H, lead=LEAD_H,
           rng=None, perturb=False):
    """正推一条折线（每 step 一点）。start=(lat,lon)，t0=基准时刻(str)。

    perturb=True 时做集合扰动：每步方向加高斯扰动（幅随提前量渐扩）、
    速度乘对数正态扰动，产生一条"可能路径"成员。返回 [{t,lat,lon}]。"""
    import random as _random
    rng = rng or _random.Random(20260804)
    lat, lon = start
    prev_deg = None
    pts = []
    for k in range(1, int(lead / step) + 1):
        h = k * step
        r = _blend(methods, h)
        if r is None:
            continue
        br, spd = r
        if perturb:
            dbr = 2.5 + 0.35 * h                       # 方向扰动度数（高斯σ）
            br = (br + rng.gauss(0, dbr)) % 360.0
            spd = max(0.3, spd * (1.0 + rng.gauss(0, 0.12)))
        if prev_deg is not None:              # 平滑转向：夹在 ±8°/6h，防抖
            dd = ((br - prev_deg + 540.0) % 360.0) - 180.0
            br = prev_deg + max(-8.0, min(8.0, dd))
        prev_deg = br
        lat, lon = dest(lat, lon, br, spd * step)
        pts.append({"t": (t0 + datetime.timedelta(hours=h)).isoformat(),
                    "lat": round(lat, 3), "lon": round(lon, 3)})
    return pts


def generate_self(storm, shapes_path=None, step=STEP_H, lead=LEAD_H,
                  offline=False, at_time=None, steer_history=False):
    """引擎入口：storm 需含 track；shapes_path 指向 data/shapes.json（可缺）。

    offline=True 时禁用网络环境场（无 steering、SST 用默认 28°C）——
    用于纯形状回测/批量场景，路径退化为 persistence+analog。

    at_time / steer_history：历史回测用。steer_history=True 时即使 offline
    也拉 at_time 时刻的历史 500hPa 引导风（Open-Meteo past_days ~30 天回溯），
    路径含 steering；强度仍按 offline 处理（无历史强度场，用固定松弛）。

    返回 {"agency":"SELF","issued_at","points":[{t,lat,lon,wind_ms,grade}], "methods":[...]}
    轨迹样本不足返回 None。"""
    track = storm.get("track") or []
    latlon = [p for p in track if p.get("lat") is not None and p.get("lon") is not None]
    if len(latlon) < 3:
        return None
    pv = _persist_vector(track)
    sv, av = None, None
    methods = []
    if pv:
        methods.append(("persistence", pv[0], pv[1]))
    if not offline or steer_history:
        try:
            kw = {"at_time": at_time} if at_time else {}
            sv = fetch_steering(latlon[-1]["lat"], latlon[-1]["lon"], **kw)
            methods.append(("steering", sv[0], sv[1]))
        except Exception:
            pass
    if shapes_path:
        av = analog_vector(track, shapes_path)
        if av:
            if len(av) >= 3:
                methods.append(("analog", av[0], (av[1], av[2])))   # (spd, 转向序列)
            else:
                methods.append(("analog", av[0], av[1]))
    if not methods:
        return None

    last = latlon[-1]
    t_last = datetime.datetime.fromisoformat(last["t"])
    last_wind = last.get("wind_ms") or 0

    tr = _track(methods, (last["lat"], last["lon"]), t_last,
                step=step, lead=lead)
    points = evolve_intensity(tr, last_wind, use_net=not offline)
    return {
        "agency": "SELF",
        "issued_at": last["t"],
        "points": points,
        "methods": [m[0] for m in methods],
    }


def fetch_sst(lat, lon):
    """Open-Meteo 海表温度（℃）。失败抛异常由调用方降级。"""
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
           "&hourly=sea_surface_temperature&forecast_days=1" % (lat, lon))
    d = _http_json(url, 8)
    sst = (d.get("hourly") or {}).get("sea_surface_temperature") or []
    vals = [v for v in sst if v is not None and not math.isnan(v)]
    if not vals:
        raise ValueError("Open-Meteo 无 SST 样本")
    return float(vals[0])


def sst_mpi(sst_c):
    """DeMaria-Kaplan 1994 简式最大潜在强度 → m/s。

    Vmax = 28.2 + 55.8·exp(0.1813·(SST−28))，单位 kt，×0.5144 转 m/s。
    物理上界：28°C ~ 43 m/s(STY)、30°C ~ 56 m/s(SuperTY)，与观测量级一致。"""
    return (28.2 + 55.8 * math.exp(0.1813 * (sst_c - 28.0))) * 0.5144


def fetch_vws(lat, lon):
    """200-850hPa 垂直风切变（m/s）——台风强度最强抑制因子。
    用两层的风矢量差模长近似 VWS。失败抛异常由调用方降级。"""
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
           "&hourly=wind_speed_200hPa,wind_direction_200hPa,"
           "wind_speed_850hPa,wind_direction_850hPa"
           "&forecast_days=1&wind_speed_unit=ms" % (lat, lon))
    d = _http_json(url, 8)
    hh = d.get("hourly") or {}
    s2 = hh.get("wind_speed_200hPa") or []
    d2 = hh.get("wind_direction_200hPa") or []
    s8 = hh.get("wind_speed_850hPa") or []
    d8 = hh.get("wind_direction_850hPa") or []
    for a, b, c, e in zip(s2, d2, s8, d8):
        if any(v is None or math.isnan(v) for v in (a, b, c, e)):
            continue
        u = a * math.sin(b * D2R) - c * math.sin(e * D2R)
        v = a * math.cos(b * D2R) - c * math.cos(e * D2R)
        return math.hypot(u, v)
    raise ValueError("Open-Meteo 无有效切变样本")


def vws_factor(vws):
    """切变对 MPI 的乘性折扣：<8 m/s 无碍，10→~0.7，20+→~0.35。
    经验标定：垂直切变每增 1 m/s，MPI 约降 3-4%（Kaplan-DeMaria 类）。"""
    if vws is None:
        return 1.0
    return max(0.35, min(1.0, 1.0 - 0.04 * max(0.0, vws - 8.0)))


def fetch_ssta(lat, lon):
    """去年同期 ±7 天窗口 ERA5 海表温度均值（℃），作气候态参照。

    Open-Meteo archive-api 的 ERA5 仅覆盖约 2025 年中起（实测更早年份无
    该海域样本），故用去年同期窗口单年均值近似气候态（工程妥协，误差
    约 ±0.5℃ 量级）。海温距平 SSTA = 实时 SST − 本值。失败抛异常降级。"""
    now = datetime.datetime.now()
    last = now.year - 1
    lo = datetime.datetime(last, now.month, now.day) - datetime.timedelta(days=7)
    hi = datetime.datetime(last, now.month, now.day) + datetime.timedelta(days=7)
    url = ("https://archive-api.open-meteo.com/v1/era5?latitude=%.4f&longitude=%.4f"
           "&start_date=%s&end_date=%s&hourly=sea_surface_temperature"
           % (lat, lon, lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")))
    d = _http_json(url, 12)
    vals = [v for v in (d.get("hourly") or {}).get("sea_surface_temperature") or []
            if v is not None and not math.isnan(v)]
    if not vals:
        raise ValueError("ERA5 无气候态 SST 样本")
    return float(sum(vals) / len(vals))


def ssta_factor(ssta):
    """海温距平（℃）对 MPI 的乘性修正：暖异常增强、冷异常削弱。
    标定：每 +1℃ 距平 → MPI +3%，封顶 ±12%（±4℃ 量级），防离群海洋波。"""
    if ssta is None:
        return 1.0
    return 1.0 + max(-0.12, min(0.12, 0.03 * ssta))


def evolve_intensity(fc_points, last_wind, sst_step=4, use_net=True,
                     default_sst=28.0):
    """给预报路径点（[{t,lat,lon}]）附 wind_ms/grade：强度向该点 SST 对应 MPI
    指数松弛（近 6h 增量受限 ±9.5 m/s），并叠加垂直风切变折扣 + 海温距平修正。

    每 sst_step 个点采一次 SST/VWS/SSTA（省请求频次），失败各自独立降级
    （SST 失败回落默认 28°C，VWS/SSTA 失败置无修正因子），离线产出仍有限。
    返回带强度字段的新点列表。"""
    wind = last_wind
    sst = default_sst
    vws = None
    ssta = None
    res = []
    for k, q in enumerate(fc_points):
        if (k + 1) % sst_step == 0 and use_net:
            try:
                v = fetch_sst(q["lat"], q["lon"])
                if v and 8.0 < v < 40.0:
                    sst = v
            except Exception:
                pass
            try:
                vws = fetch_vws(q["lat"], q["lon"])
            except Exception:
                pass
            try:
                clim = fetch_ssta(q["lat"], q["lon"])
                if clim and 8.0 < clim < 40.0:
                    ssta = sst - clim
            except Exception:
                pass
        mpi = sst_mpi(sst) * vws_factor(vws) * ssta_factor(ssta)
        d = mpi - wind
        wind = max(8.0, min(78.0, wind + max(-9.5, min(9.5, 0.12 * d))))
        w = round(wind, 1)
        res.append({"t": q["t"], "lat": q["lat"], "lon": q["lon"],
                    "wind_ms": w, "grade": grade_of(w)})
    return res


def generate_cone(storm, shapes_path=None, n_members=60,
                  step=STEP_H, lead=LEAD_H, seed=20260804):
    """蒙特卡洛集合：n_members 条扰动路径，按整条路径相对 P50 的平均偏移
    排序，取 P10/P50/P90 三条折线（成员排序法，比逐坐标分位更抗畸形成员）。

    返回 {"p10":[{t,lat,lon}..], "p50":[...], "p90":[...], "n":n} 或 None。"""
    import random as _random
    track = storm.get("track") or []
    latlon = [p for p in track if p.get("lat") is not None and p.get("lon") is not None]
    if len(latlon) < 3:
        return None
    methods = []
    pv = _persist_vector(track)
    if pv:
        methods.append(("persistence", pv[0], pv[1]))
    try:
        sv = fetch_steering(latlon[-1]["lat"], latlon[-1]["lon"])
        methods.append(("steering", sv[0], sv[1]))
    except Exception:
        pass
    if shapes_path:
        av = analog_vector(track, shapes_path)
        if av:
            if len(av) >= 3:
                methods.append(("analog", av[0], (av[1], av[2])))
            else:
                methods.append(("analog", av[0], av[1]))
    if not methods:
        return None
    last = latlon[-1]
    t_last = datetime.datetime.fromisoformat(last["t"])
    start = (last["lat"], last["lon"])

    base = _track(methods, start, t_last, step=step, lead=lead)
    if not base:
        return None
    rng = _random.Random(seed)
    members = []
    for _ in range(n_members):
        m = _track(methods, start, t_last, step=step, lead=lead,
                   rng=rng, perturb=True)
        if not m:
            continue
        off = sum(hav([b["lat"], b["lon"]], [q["lat"], q["lon"]])
                  for b, q in zip(base, m)) / len(m)
        members.append((off, m))
    if not members:
        return None
    members.sort(key=lambda x: x[0])
    idx = {10: max(0, int(len(members) * 0.10) - 1),
           50: len(members) // 2,
           90: min(len(members) - 1, int(len(members) * 0.90) - 1)}
    out = {}
    for pct, i in idx.items():
        out["p%02d" % pct] = members[i][1]
    out["n"] = len(members)
    return out