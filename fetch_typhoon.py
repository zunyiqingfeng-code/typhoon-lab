#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_typhoon.py — 台风数据抓取管道（M1）

设计原则：
  1. 纯标准库，零 pip 依赖，单文件可拷走
  2. 多源适配器：zjwater（浙江水利厅，主源）→ nmc（中央气象台，备源）
  3. 所有源产出统一 schema，前端只认 schema 不认源
  4. 每次抓取合并进本地归档（data/archive/），归档是自有资产，源挂了历史不丢
  5. fixture 模式生成结构完全一致的演示数据，source 字段明确标 "fixture"，
     前端据此挂红色演示角标 —— 假数据永远不许伪装成真数据

用法：
  python3 fetch_typhoon.py                    # auto：zjwater 优先，失败切 nmc
  python3 fetch_typhoon.py --source zjwater
  python3 fetch_typhoon.py --source fixture   # 离线开发用
  python3 fetch_typhoon.py --year 2025        # 抓指定年份（默认当年）

输出：
  data/latest.json          活跃台风（含近 N 天内停编的，避免刚停编页面就空）
  data/index.json           当年台风索引
  data/archive/<年>/<编号>.json   逐台风全量归档，增量合并

退出码：0 成功；2 所有真实源均失败（fixture 不算）
"""

import argparse
import glob
import gzip
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

SCHEMA_VERSION = "1.1"
TZ_BJ = timezone(timedelta(hours=8))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 每次成功请求后的礼貌间隔（秒）。正常抓取为 0；回填大批历史时调大，别打炸源站
_POLITE_DELAY = 0.0

# ---------------------------------------------------------------- 通用工具

def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def http_get(url, referer=None, timeout=12, retries=3):
    """带重试的 GET，返回解码后的文本。失败抛最后一次异常。"""
    headers = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    if referer:
        headers["Referer"] = referer
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                text = None
                for enc in ("utf-8", "gbk"):
                    try:
                        text = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                if text is None:
                    text = raw.decode("utf-8", errors="replace")
                if not text.strip():
                    raise ValueError("空响应体")   # 空 200 当可重试失败
                if _POLITE_DELAY:
                    time.sleep(_POLITE_DELAY)
                return text
        except Exception as e:  # noqa: BLE001 —— 网络层什么都可能抛
            last = e
            log("  重试 %d/%d：%s（%s）" % (i + 1, retries, url, e))
            time.sleep(1.5 * (i + 1))
    raise last


def to_num(v, cast=float):
    """脏数据容错转数值，失败返回 None。"""
    if v is None or v == "":
        return None
    try:
        return cast(v)
    except (TypeError, ValueError):
        try:
            return cast(str(v).strip())
        except (TypeError, ValueError):
            return None


# 中文方位 → 方位角（度）。含两套命名：国标"偏"式 与 接口实际返回的旧式
DIR16 = {
    "北": 0, "东北偏北": 22.5, "东北": 45, "东北偏东": 67.5,
    "东": 90, "东南偏东": 112.5, "东南": 135, "东南偏南": 157.5,
    "南": 180, "西南偏南": 202.5, "西南": 225, "西南偏西": 247.5,
    "西": 270, "西北偏西": 292.5, "西北": 315, "西北偏北": 337.5,
    # zjwater/nmc 实测返回旧式命名（如 "东北东"=ENE、"西北西"=WNW）
    "北北东": 22.5, "东北东": 67.5, "东南东": 112.5, "南南东": 157.5,
    "南南西": 202.5, "西南西": 247.5, "西北西": 292.5, "北北西": 337.5,
}

# 强度等级：中文 → 代码（国标 GB/T 19201）
GRADE = {
    "热带低压": "TD", "热带风暴": "TS", "强热带风暴": "STS",
    "台风": "TY", "强台风": "STY", "超强台风": "SuperTY",
}

# 预报机构中文 → 代码
AGENCY = {
    "中央气象台": "CMA", "中国": "CMA",
    "日本": "JMA", "美国": "JTWC",
    "中国香港": "HKO", "香港": "HKO",
    "中国台湾": "CWA", "台湾": "CWA",
    "韩国": "KMA",
}


def norm_dir(v):
    """移动方向：中文方位或数字均可 → 度数。"""
    n = to_num(v)
    if n is not None:
        return n % 360
    if isinstance(v, str):
        return DIR16.get(v.strip())
    return None


def norm_grade(v):
    if not v:
        return None
    v = str(v).strip()
    return GRADE.get(v, v)  # 未知等级保留原文，不吞


def norm_time(v):
    """时间容错解析 → ISO8601（统一 +08:00 北京时间）。
    实测两种形态并存：
      TyphoonInfo points/forecast:  '2026-07-07 08:00:00'（北京时间）
      TyhoonActivity / ybsj:        '2026-07-14T09:00:00.000+00:00'（UTC ISO）
    解析失败返回 None（该点丢弃并靠调用方计数告警）。"""
    if not v:
        return None
    s = str(v).strip()
    if "T" in s:  # ISO 带时区
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ_BJ)
            return dt.astimezone(TZ_BJ).isoformat()
        except ValueError:
            return None
    s = s.replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=TZ_BJ).isoformat()
        except ValueError:
            continue
    return None


def norm_pressure(v):
    """中心气压 → hPa，仅接受物理合理区间 [850,1050]，越界/缺测返回 None。
    实测个别机构把预报『风力等级』塞进气压字段（HKO 出现过 9–14），须挡掉。"""
    n = to_num(v)
    return n if n is not None and 850 <= n <= 1050 else None


def norm_radius(v):
    """风圈半径 → {ne,se,sw,nw}（km）或 None。
    实测形态：单值 '250'；四段 '280|180|220|180'。
    四段顺序为 东北|东南|西北|西南（对照官方 SPA 前端与在产项目
    typhoon-bavi-tracker/worker/normalize.ts 双重确证），
    即 ne=第1段, se=第2段, nw=第3段, sw=第4段。"""
    if v in (None, "", "0", 0):
        return None
    parts = re.split(r"[|,;/]", str(v).strip())
    nums = [to_num(p) for p in parts]
    nums = [n for n in nums if n is not None and n > 0]
    if not nums:
        return None
    if len(nums) >= 4:
        return {"ne": nums[0], "se": nums[1], "nw": nums[2], "sw": nums[3]}
    r = nums[0]
    return {"ne": r, "se": r, "sw": r, "nw": r}


# ---------------------------------------------------------------- 归一化 schema
# storm = {
#   "id": "202609", "name_zh": "巴威", "name_en": "BAVI",
#   "is_active": true, "basin": "WP",
#   "track": [ { "t": iso, "lat", "lon", "pressure_hpa", "wind_ms", "grade",
#                "move_dir_deg", "move_speed_kmh",
#                "r7": {ne,se,sw,nw} | null, "r10":..., "r12":... } ],
#   "forecasts": [ { "agency": "CMA", "issued_at": iso,
#                    "points": [ {t, lat, lon, pressure_hpa, wind_ms, grade} ] } ],
#   "meta": { "source": "zjwater", "fetched_at": iso }
# }


def make_point(t, lat, lon, **kw):
    p = {"t": t, "lat": lat, "lon": lon}
    p.update({k: v for k, v in kw.items() if v is not None})
    return p


# ---------------------------------------------------------------- 适配器：zjwater

ZJ_HOSTS = [
    "https://typhoon.slt.zj.gov.cn",   # 水利厅现域名
    "http://typhoon.zjwater.gov.cn",   # 旧域名，历史上长期可用
]


class ZjwaterAdapter:
    """浙江省水利厅台风路径系统。无鉴权 JSON。
    接口形态经 2026-07 实测（TyhoonActivity 直连返回 200）与在产开源项目
    逆向文档双重确证；字段仍可能随改版漂移，全部 .get 容错。"""

    name = "zjwater"

    def fetch_year(self, year):
        err = None
        for host in ZJ_HOSTS:
            try:
                # 活跃快照（注意站方拼写 bug：Tyhoon 少个 p）
                active_ids = set()
                try:
                    for it in json.loads(http_get(
                            "%s/Api/TyhoonActivity" % host, referer=host + "/")):
                        if it.get("tfid"):
                            active_ids.add(str(it["tfid"]))
                except Exception as e:  # noqa: BLE001
                    log("zjwater：TyhoonActivity 失败（%s），仅靠 isactive 字段" % e)

                lst = json.loads(http_get(
                    "%s/Api/TyphoonList/%d" % (host, year), referer=host + "/"))
                if not isinstance(lst, list):
                    raise ValueError("TyphoonList 返回非数组")
                log("zjwater：%s 列表 %d 条，活跃 %d 个" %
                    (host, len(lst), len(active_ids)))
                storms = []
                skipped = 0
                for item in lst:
                    tfid = str(item.get("tfid") or item.get("id") or "").strip()
                    # 常规 6 位 YYYYNN；个别未编号低压为 8 位
                    if not re.match(r"^\d{6}(\d{2})?$", tfid):
                        continue
                    try:
                        st = self._fetch_detail(host, tfid)
                    except Exception as de:  # noqa: BLE001 单台风详情故障不拖垮整年
                        skipped += 1
                        log("  跳过 %s（详情失败：%s）" % (tfid, de))
                        continue
                    if st:
                        if tfid in active_ids:
                            st["is_active"] = True
                        storms.append(st)
                if skipped:
                    log("zjwater：%d 年跳过 %d 个台风（详情故障）" % (year, skipped))
                # 列表非空却一个都没取到 → 该域名系统性故障，换下一个域名
                if lst and not storms:
                    raise RuntimeError("列表非空但所有详情失败，疑似域名故障")
                return storms
            except Exception as e:  # noqa: BLE001
                err = e
                log("zjwater：%s 失败（%s），换下一个域名" % (host, e))
        raise RuntimeError("zjwater 所有域名均失败：%s" % err)

    def _fetch_detail(self, host, tfid):
        d = json.loads(http_get("%s/Api/TyphoonInfo/%s" % (host, tfid),
                                referer=host + "/"))
        track, forecasts = [], []
        for rp in d.get("points") or []:
            t = norm_time(rp.get("time"))
            lat, lon = to_num(rp.get("lat")), to_num(rp.get("lng"))
            if t is None or lat is None or lon is None:
                continue
            track.append(make_point(
                t, lat, lon,
                pressure_hpa=norm_pressure(rp.get("pressure")),
                wind_ms=to_num(rp.get("speed")),
                grade=norm_grade(rp.get("strong")),
                move_dir_deg=norm_dir(rp.get("movedirection")),
                move_speed_kmh=to_num(rp.get("movespeed")),
                r7=norm_radius(rp.get("radius7")),
                r10=norm_radius(rp.get("radius10")),
                r12=norm_radius(rp.get("radius12")),
            ))
            for fc in rp.get("forecast") or []:
                agency = AGENCY.get(str(fc.get("tm", "")).strip(),
                                    str(fc.get("tm", "")).strip() or "UNKNOWN")
                pts, issued = [], t
                for fp in fc.get("forecastpoints") or []:
                    ft = norm_time(fp.get("time"))
                    fla, flo = to_num(fp.get("lat")), to_num(fp.get("lng"))
                    if ft is None or fla is None or flo is None:
                        continue
                    # 实测 speed/pressure 为 "0" 表示缺测；气压再过物理区间
                    w = to_num(fp.get("speed"))
                    pts.append(make_point(
                        ft, fla, flo,
                        pressure_hpa=norm_pressure(fp.get("pressure")),
                        wind_ms=w if w and w > 0 else None,
                        grade=norm_grade(fp.get("strong")),
                    ))
                    yb = norm_time(fp.get("ybsj"))  # 预报发布时间（ISO UTC）
                    if yb and yb > issued:
                        issued = yb
                if pts:
                    forecasts.append({"agency": agency, "issued_at": issued,
                                      "points": pts})
        if not track:
            return None
        # schema 1.1：保留每机构全部发布时次（复盘评测需要完整预报史）；
        # 仅去重完全相同的 (机构, 发布时刻)，同键取预报点更全的一份
        seen = {}
        for fc in forecasts:
            key = (fc["agency"], fc["issued_at"])
            if key not in seen or len(fc["points"]) > len(seen[key]["points"]):
                seen[key] = fc
        land = []
        for lp in d.get("land") or []:
            lt = norm_time(lp.get("landtime"))
            lla, llo = to_num(lp.get("lat")), to_num(lp.get("lng"))
            if lt and lla is not None and llo is not None:
                land.append({"t": lt, "lat": lla, "lon": llo,
                             "address": lp.get("landaddress") or "",
                             "grade": norm_grade(lp.get("strong"))})
        st = {
            "id": str(d.get("tfid") or tfid),
            "name_zh": d.get("name") or "",
            "name_en": (d.get("enname") or "").upper(),
            "is_active": str(d.get("isactive")) == "1",
            "basin": "WP",
            "track": sorted(track, key=lambda p: p["t"]),
            "forecasts": sorted(seen.values(),
                                 key=lambda f: (f["agency"], f["issued_at"])),
        }
        if land:
            st["land"] = land
        return st


# ---------------------------------------------------------------- 适配器：nmc（实验性）

NMC_GRADE = {"TD": "TD", "TS": "TS", "STS": "STS",
             "TY": "TY", "STY": "STY", "SuperTY": "SuperTY"}
NMC_AGENCY = {"BABJ": "CMA", "RJTD": "JMA", "PGTW": "JTWC",
              "VHHH": "HKO", "RKSL": "KMA"}


class NmcAdapter:
    """中央气象台台风网。JSONP 裸数组接口，无字段名。
    数组偏移对照在产项目 normalize.ts 确证：
      list_{year}.typhoonList 行：t[0]=内部dbid, t[3]=短编号 'YYNN'
      view_{dbid}.typhoon：ty[1]=英文名 ty[2]=中文名 ty[3]=编号
        ty[7]=='start' 表示活跃, ty[8]=轨迹点数组
      轨迹点 p：p[1]=时间'YYYYMMDDHHmm'(北京) p[3]=强度码 p[4]=经度 p[5]=纬度
        p[6]=气压 p[7]=风速m/s p[8]=方位码(NNW等) p[9]=移速
        p[10]=风圈 [['30KTS',ne,se,sw,nw],...] p[11]=预报 {机构码:[...]}
      预报点 q：q[0]=提前小时 q[1]=基准时间 q[2]=经度 q[3]=纬度
        q[4]=气压 q[5]=风速 q[7]=强度码"""

    name = "nmc"
    BASE = "https://typhoon.nmc.cn/weatherservice/typhoon/jsons"
    REFERER = "https://typhoon.nmc.cn/web.html"

    def _jsonp(self, url):
        txt = http_get(url, referer=self.REFERER)
        m = re.search(r"^[\w$]+\((.*)\)\s*;?\s*$", txt.strip(), re.S)
        if not m:
            raise ValueError("JSONP 解包失败：%s" % url)
        return json.loads(m.group(1))

    @staticmethod
    def _t(s):
        """'202607062300' → ISO +08:00"""
        s = str(s)
        if len(s) < 12:
            return None
        return norm_time("%s-%s-%s %s:%s" %
                         (s[0:4], s[4:6], s[6:8], s[8:10], s[10:12]))

    def fetch_year(self, year):
        data = self._jsonp("%s/list_%d" % (self.BASE, year))
        rows = data.get("typhoonList") or []
        log("nmc：list_%d 共 %d 条" % (year, len(rows)))
        storms = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 4:
                continue
            dbid, short = row[0], str(row[3])
            if not re.match(r"^\d{4}$", short):
                continue
            st = self._fetch_detail(dbid)
            if st:
                st["id"] = "%d%s" % (year // 100, short)  # '20'+'2609'
                storms.append(st)
        return storms

    def _fetch_detail(self, dbid):
        ty = self._jsonp("%s/view_%s" % (self.BASE, dbid)).get("typhoon")
        if not isinstance(ty, list) or len(ty) < 9:
            return None
        track = []
        fc_hist = []   # schema 1.1：[(观测时刻, 预报dict)]，保留全部带预报时次
        for p in ty[8] or []:
            if not isinstance(p, list) or len(p) < 8:
                continue
            t = self._t(p[1])
            lon, lat = to_num(p[4]), to_num(p[5])
            if t is None or lat is None or lon is None:
                continue
            radii = {}
            for r in (p[10] if len(p) > 10 and p[10] else []):
                # NMC 顺序即 ne,se,sw,nw
                if isinstance(r, list) and len(r) >= 5:
                    radii[str(r[0])] = {"ne": to_num(r[1]), "se": to_num(r[2]),
                                        "sw": to_num(r[3]), "nw": to_num(r[4])}
            track.append(make_point(
                t, lat, lon,
                pressure_hpa=norm_pressure(p[6]),
                wind_ms=to_num(p[7]),
                grade=NMC_GRADE.get(str(p[3]), str(p[3])),
                move_dir_deg=norm_dir(_NMC_DIR_ZH.get(str(p[8]), p[8]))
                    if len(p) > 8 else None,
                move_speed_kmh=to_num(p[9]) if len(p) > 9 else None,
                r7=radii.get("30KTS"), r10=radii.get("50KTS"),
                r12=radii.get("64KTS"),
            ))
            if len(p) > 11 and p[11]:
                fc_hist.append((t, p[11]))
        if not track:
            return None
        forecasts = []
        for base_t, fdict in fc_hist:
            for code, arr in fdict.items():
                pts = []
                for q in arr or []:
                    if not isinstance(q, list) or len(q) < 6:
                        continue
                    b = self._t(q[1])
                    if b is None:
                        continue
                    ft = (datetime.fromisoformat(b) +
                          timedelta(hours=to_num(q[0]) or 0)).isoformat()
                    flo, fla = to_num(q[2]), to_num(q[3])
                    if fla is None or flo is None:
                        continue
                    w = to_num(q[5])
                    pts.append(make_point(
                        ft, fla, flo,
                        pressure_hpa=norm_pressure(q[4]),
                        wind_ms=w if w and w > 0 else None,
                        grade=NMC_GRADE.get(str(q[7]), None)
                            if len(q) > 7 else None,
                    ))
                if pts:
                    forecasts.append({
                        "agency": NMC_AGENCY.get(str(code), str(code)),
                        "issued_at": base_t, "points": pts})
        seen = {}
        for fc in forecasts:
            key = (fc["agency"], fc["issued_at"])
            if key not in seen or len(fc["points"]) > len(seen[key]["points"]):
                seen[key] = fc
        return {"basin": "WP",
                "name_zh": ty[2] or "", "name_en": (ty[1] or "").upper(),
                "is_active": str(ty[7]) == "start",
                "track": sorted(track, key=lambda x: x["t"]),
                "forecasts": sorted(seen.values(),
                                    key=lambda f: (f["agency"], f["issued_at"]))}


# NMC 方位码 → 中文（复用 DIR16 求角度）
_NMC_DIR_ZH = {
    "N": "北", "NNE": "北北东", "NE": "东北", "ENE": "东北东",
    "E": "东", "ESE": "东南东", "SE": "东南", "SSE": "南南东",
    "S": "南", "SSW": "南南西", "SW": "西南", "WSW": "西南西",
    "W": "西", "WNW": "西北西", "NW": "西北", "NNW": "北北西",
}


# ---------------------------------------------------------------- 适配器：jma（第二意见源）

JMA_GRADE = {"TD": "TD", "TS": "TS", "STS": "STS", "TY": "TY"}


class JmaAdapter:
    """日本气象厅 bosai 台风 JSON。命名字段，较 nmc 数组偏移安全。
    结构经 2026-07-17 直连实测（targetTc.json + {TC}/specifications.json）确证并落盘
    tests/captured/jma_*。specifications 每段：advancedHours（0=分析/实况，>0=预报）、
    position.deg=[lat,lon]、pressure、category.en、validtime.UTC。

    实测当时仅一个 TD 在编，强台风才有的 maximumWind / 风圈字段未出现，
    故本适配器只取已确证字段（位置/气压/类别/时间），风速一律留空、无风圈；
    强台风的强度与风圈字段待有台风在编时补验（沙盒未验证项）。"""

    name = "jma"
    BASE = "https://www.jma.go.jp/bosai/typhoon/data"
    REFERER = "https://www.jma.go.jp/bosai/typhoon/"

    def _get(self, url):
        return json.loads(http_get(url, referer=self.REFERER))

    def fetch_year(self, year):
        lst = self._get("%s/targetTc.json" % self.BASE)
        if not isinstance(lst, list):
            raise ValueError("targetTc 非数组")
        log("jma：当前在编 %d 个" % len(lst))
        storms = []
        for it in lst:
            tc = it.get("tropicalCyclone")
            if not tc:
                continue
            try:
                st = self._fetch_detail(tc, it, year)
            except Exception as e:  # noqa: BLE001
                log("  跳过 %s（%s）" % (tc, e))
                continue
            if st:
                storms.append(st)
        if not storms:
            raise RuntimeError("jma 无可用在编台风")
        return storms

    def _fetch_detail(self, tc, meta, year):
        spec = self._get("%s/%s/specifications.json" % (self.BASE, tc))
        track, fpts, issued = [], [], None
        for part in spec:
            if part.get("part") == "title":
                issued = norm_time((part.get("issue") or {}).get("UTC"))
                continue
            pos = (part.get("position") or {}).get("deg")
            t = norm_time((part.get("validtime") or {}).get("UTC"))
            if not (isinstance(pos, list) and len(pos) >= 2) or t is None:
                continue
            lat, lon = to_num(pos[0]), to_num(pos[1])
            if lat is None or lon is None:
                continue
            grade = JMA_GRADE.get((part.get("category") or {}).get("en"),
                                  (part.get("category") or {}).get("en"))
            p = make_point(t, lat, lon,
                           pressure_hpa=norm_pressure(part.get("pressure")),
                           grade=grade)
            (track if (to_num(part.get("advancedHours")) or 0) == 0
             else fpts).append(p)
        if not track and not fpts:
            return None
        tn = str(meta.get("typhoonNumber") or "")
        sid = "%d%02d" % (year, int(tn)) if tn.isdigit() else "JMA-%s" % tc
        st = {"id": sid, "name_zh": "", "name_en": tc,
              "is_active": True, "basin": "WP",
              "track": sorted(track, key=lambda p: p["t"])}
        if fpts:
            st["forecasts"] = [{"agency": "JMA",
                                "issued_at": issued or track[0]["t"] if track else issued,
                                "points": sorted(fpts, key=lambda p: p["t"])}]
        else:
            st["forecasts"] = []
        return st


# ---------------------------------------------------------------- fixture

def build_fixture(now=None):
    """结构与真实数据完全一致的演示气旋。source='fixture'，前端挂红角标。"""
    now = now or datetime.now(TZ_BJ).replace(minute=0, second=0, microsecond=0)
    # (Δ小时, lat, lon, 风速m/s, 气压hPa, r7对称基数km)
    seq = [
        (-90, 13.6, 141.8, 15, 1002, 0), (-84, 13.9, 140.6, 18, 998, 150),
        (-78, 14.3, 139.3, 20, 995, 180), (-72, 14.8, 138.0, 23, 992, 200),
        (-66, 15.4, 136.6, 25, 988, 220), (-60, 16.1, 135.2, 28, 982, 240),
        (-54, 16.9, 133.8, 33, 975, 260), (-48, 17.8, 132.5, 38, 965, 280),
        (-42, 18.8, 131.3, 42, 958, 300), (-36, 19.9, 130.2, 45, 952, 310),
        (-30, 21.0, 129.2, 48, 948, 320), (-24, 22.1, 128.3, 52, 942, 330),
        (-18, 23.2, 127.5, 52, 940, 330), (-12, 24.2, 126.8, 50, 944, 320),
        (-6, 25.2, 126.2, 48, 948, 310), (0, 26.1, 125.7, 45, 952, 300),
    ]
    track = []
    rr = lambda d: {k: round(v) for k, v in d.items()}  # noqa: E731
    for dh, lat, lon, w, pr, r7 in seq:
        t = (now + timedelta(hours=dh)).isoformat()
        g = ("SuperTY" if w >= 51 else "STY" if w >= 41.5 else
             "TY" if w >= 32.7 else "STS" if w >= 24.5 else
             "TS" if w >= 17.2 else "TD")
        track.append(make_point(
            t, lat, lon, pressure_hpa=pr, wind_ms=w, grade=g,
            move_dir_deg=325, move_speed_kmh=22,
            r7=rr({"ne": r7 * 1.15, "se": r7, "sw": r7 * 0.8,
                   "nw": r7 * 0.95}) if r7 else None,
            r10=rr({"ne": r7 * 0.5, "se": r7 * 0.45, "sw": r7 * 0.35,
                    "nw": r7 * 0.4}) if r7 >= 240 else None,
            r12=rr({"ne": r7 * 0.28, "se": r7 * 0.24, "sw": r7 * 0.18,
                    "nw": r7 * 0.22}) if r7 >= 300 else None,
        ))
    def fc(agency, pts):
        return {"agency": agency, "issued_at": now.isoformat(),
                "points": [make_point((now + timedelta(hours=h)).isoformat(),
                                      la, lo, wind_ms=w, pressure_hpa=p,
                                      grade="STY" if w >= 41.5 else "TY")
                           for h, la, lo, w, p in pts]}
    forecasts = [
        fc("CMA", [(12, 27.0, 125.0, 45, 950), (24, 27.9, 124.2, 42, 955),
                   (36, 28.7, 123.2, 40, 960), (48, 29.4, 122.0, 38, 965),
                   (72, 30.6, 119.8, 30, 980)]),
        fc("JMA", [(12, 27.1, 125.3, 44, 952), (24, 28.2, 124.9, 42, 956),
                   (36, 29.3, 124.4, 40, 962), (48, 30.4, 123.8, 36, 970),
                   (72, 32.6, 123.5, 28, 985)]),
        fc("JTWC", [(12, 27.2, 125.6, 46, 948), (24, 28.5, 125.5, 44, 952),
                    (36, 29.9, 125.7, 42, 958), (48, 31.4, 126.4, 38, 966),
                    (72, 34.5, 129.0, 30, 982)]),
    ]
    return [{
        "id": "FX2600", "name_zh": "演示气旋", "name_en": "FIXTURE",
        "is_active": True, "basin": "WP",
        "track": track, "forecasts": forecasts,
    }]


# ---------------------------------------------------------------- 归档与输出

def merge_storm(old, new):
    """增量合并：轨迹按时间去重取新，预报按机构取最新 issued_at。"""
    if not old:
        return new
    by_t = {p["t"]: p for p in old.get("track", [])}
    for p in new.get("track", []):
        by_t[p["t"]] = p
    # schema 1.1：按 (机构, 发布时刻) 去重合并，保留全部历史预报；同键取更全的一份
    fc = {(f["agency"], f.get("issued_at", "")): f for f in old.get("forecasts", [])}
    for f in new.get("forecasts", []):
        key = (f["agency"], f.get("issued_at", ""))
        if key not in fc or len(f.get("points", [])) >= len(fc[key].get("points", [])):
            fc[key] = f
    merged = dict(old)
    merged.update({k: v for k, v in new.items()
                   if k not in ("track", "forecasts")})
    merged["track"] = sorted(by_t.values(), key=lambda p: p["t"])
    merged["forecasts"] = sorted(fc.values(),
                                 key=lambda f: (f["agency"], f.get("issued_at", "")))
    return merged


def latest_forecast_only(storm):
    """latest.json 只吐每机构最新一份预报，前端契约不变；完整预报史留在归档。"""
    best = {}
    for f in storm.get("forecasts", []):
        k = f["agency"]
        if k not in best or f.get("issued_at", "") > best[k].get("issued_at", ""):
            best[k] = f
    s = dict(storm)
    s["forecasts"] = sorted(best.values(), key=lambda f: f["agency"])
    return s


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)
    log("写出 %s（%.1f KB）" % (path, os.path.getsize(path) / 1024))


def health_check(latest_payload, index_payload):
    """结构自检：源站悄悄改版/返回残缺时在日志里报警，别静默通过。
    只做廉价的形态校验，不做跨运行的趋势判断（那属调度层）。"""
    issues = []
    if latest_payload.get("schema_version") != SCHEMA_VERSION:
        issues.append("latest schema_version 异常：%r" %
                      latest_payload.get("schema_version"))
    storms = latest_payload.get("storms")
    if not isinstance(storms, list):
        issues.append("latest.storms 非数组")
        storms = []
    for s in storms:
        if not s.get("track"):
            issues.append("台风 %s 无轨迹点" % s.get("id"))
        for f in s.get("forecasts", []):
            if not f.get("points"):
                issues.append("台风 %s 机构 %s 预报无点" %
                              (s.get("id"), f.get("agency")))
    n_idx = len(index_payload.get("storms", []))
    if not latest_payload.get("fixture") and n_idx == 0:
        issues.append("当年索引为空——源站列表可能异常，非淡季需排查")
    for m in issues:
        log("  [健康检查] " + m)
    log("健康检查：%d 项异常，当年索引 %d 个台风" % (len(issues), n_idx))
    return issues


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    d2r = math.pi / 180
    dlat = (lat2 - lat1) * d2r
    dlon = (lon2 - lon1) * d2r
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(lat1 * d2r) * math.cos(lat2 * d2r) * math.sin(dlon / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def _last_point(st):
    tr = st.get("track") or []
    return tr[-1] if tr else None


def match_extra_storm(extra, main_storms):
    """把补充源的台风匹配到主列表：先精确名字，再位置+时间近似。"""
    lp = _last_point(extra)
    for m in main_storms:
        if (extra.get("name_en") and m.get("name_en") and
                extra["name_en"].upper() == m["name_en"].upper()):
            return m
        ex_id = str(extra.get("id") or "")
        m_id = str(m.get("id") or "")
        if (ex_id and m_id and ex_id == m_id and
                ex_id.isdigit() and len(ex_id) == 6):
            return m
        if lp:
            mlast = _last_point(m)
            if mlast:
                try:
                    t1 = datetime.fromisoformat(lp["t"])
                    t2 = datetime.fromisoformat(mlast["t"])
                except ValueError:
                    continue
                if (abs((t1 - t2).total_seconds()) < 12 * 3600 and
                        _haversine_km(lp["lat"], lp["lon"],
                                      mlast["lat"], mlast["lon"]) < 400):
                    return m
    return None


def merge_extra_into(main, extra, source_name):
    """补充源并入主风暴：预报按 (agency, issued_at) 去重；实况轨迹点补缺。"""
    fc = {(f["agency"], f.get("issued_at", "")): f
          for f in main.get("forecasts", [])}
    for f in extra.get("forecasts", []):
        key = (f["agency"], f.get("issued_at", ""))
        if key not in fc or len(f.get("points", [])) >= len(fc[key].get("points", [])):
            fc[key] = f
    by_t = {p["t"]: p for p in main.get("track", [])}
    for p in extra.get("track", []):
        if p["t"] not in by_t:
            by_t[p["t"]] = p
    main["track"] = sorted(by_t.values(), key=lambda p: p["t"])
    main["forecasts"] = sorted(fc.values(),
                               key=lambda f: (f["agency"], f.get("issued_at", "")))
    if "sources" not in main:
        main["sources"] = []
    if source_name not in main["sources"]:
        main["sources"].append(source_name)


def run(source, year, out_dir, keep_days):
    now = datetime.now(TZ_BJ)
    fetched_at = now.isoformat()
    storms, used = None, None

    order = {"auto": ["zjwater", "nmc"], "zjwater": ["zjwater"],
             "nmc": ["nmc"], "jma": ["jma"], "fixture": ["fixture"],
             "multi": ["zjwater", "nmc"]}[source]
    adapters = {"zjwater": ZjwaterAdapter(), "nmc": NmcAdapter(),
                "jma": JmaAdapter()}

    for name in order:
        if name == "fixture":
            storms, used = build_fixture(), "fixture"
            break
        try:
            log("尝试数据源：%s" % name)
            storms, used = adapters[name].fetch_year(year), name
            break
        except Exception as e:  # noqa: BLE001
            log("数据源 %s 失败：%s" % (name, e))

    if storms is None:
        log("全部真实数据源失败。")
        return 2

    # multi 模式：抓补充源（KMA/HKO/PAGASA/CWA/JTWC-UCAR），并入主风暴
    if source == "multi":
        try:
            import fetch_sources as fsrc
        except ImportError:
            fsrc = None
            log("fetch_sources.py 缺失，跳过补充源")
        if fsrc:
            for src_name, extra_storms in fsrc.fetch_all_extra(year):
                if not extra_storms:
                    continue
                matched = 0
                for es in extra_storms:
                    if not es.get("is_active"):
                        continue
                    m = match_extra_storm(es, storms)
                    if m is not None:
                        merge_extra_into(m, es, src_name)
                        matched += 1
                log("补充源 %s：%d 个台风，匹配并入 %d 个" %
                    (src_name, len(extra_storms), matched))

    for s in storms:
        s["meta"] = {"source": used, "fetched_at": fetched_at}

    # 归档合并（fixture 不进归档）
    if used != "fixture":
        for i, s in enumerate(storms):
            apath = os.path.join(out_dir, "archive", str(year), s["id"] + ".json")
            old = None
            if os.path.exists(apath):
                with open(apath, encoding="utf-8") as f:
                    old = json.load(f)
            storms[i] = merge_storm(old, s)
            write_json(apath, storms[i])

    # latest：活跃 + 近 keep_days 天内仍有轨迹点的
    cutoff = (now - timedelta(days=keep_days)).isoformat()
    latest = [latest_forecast_only(s) for s in storms
              if s.get("is_active") or (s["track"] and s["track"][-1]["t"] >= cutoff)]
    latest_payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": fetched_at,
        "source": used,
        "fixture": used == "fixture",
        "storms": latest,
    }
    write_json(os.path.join(out_dir, "latest.json"), latest_payload)
    index_payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": fetched_at,
        "year": year,
        "storms": [{"id": s["id"], "name_zh": s["name_zh"],
                    "name_en": s["name_en"], "is_active": s.get("is_active", False),
                    "start": s["track"][0]["t"] if s["track"] else None,
                    "end": s["track"][-1]["t"] if s["track"] else None,
                    "n_points": len(s["track"])} for s in storms],
    }
    write_json(os.path.join(out_dir, "index.json"), index_payload)
    health_check(latest_payload, index_payload)
    log("完成：源=%s，台风 %d 个，latest 收录 %d 个" %
        (used, len(storms), len(latest)))
    return 0


def build_master_index(out_dir):
    """扫描 archive/ 生成跨年主索引，供前端历史/复盘选择器用。
    只取摘要字段——单台风归档可达 ~1MB，主索引须精简、可整体加载。"""
    root = os.path.join(out_dir, "archive")
    rows = []
    for path in sorted(glob.glob(os.path.join(root, "*", "*.json"))):
        year = os.path.basename(os.path.dirname(path))
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:  # noqa: BLE001
            log("主索引跳过 %s：%s" % (path, e))
            continue
        tr = d.get("track") or []
        if not tr:
            continue
        fcs = d.get("forecasts") or []
        winds = [p["wind_ms"] for p in tr if p.get("wind_ms") is not None]
        press = [p["pressure_hpa"] for p in tr if p.get("pressure_hpa") is not None]
        rows.append({
            "id": d.get("id"),
            "year": int(year) if year.isdigit() else None,
            "name_zh": d.get("name_zh", ""), "name_en": d.get("name_en", ""),
            "start": tr[0]["t"], "end": tr[-1]["t"], "n_points": len(tr),
            "n_forecasts": len(fcs),
            "agencies": sorted({f.get("agency") for f in fcs if f.get("agency")}),
            "peak_wind_ms": max(winds) if winds else None,
            "min_pressure_hpa": min(press) if press else None,
        })
    rows.sort(key=lambda r: ((r["year"] or 0), r["id"] or ""))
    years = sorted({r["year"] for r in rows if r["year"]})
    write_json(os.path.join(root, "index.json"), {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(TZ_BJ).isoformat(),
        "n_storms": len(rows), "years": years, "storms": rows,
    })
    log("主索引：%d 个台风，覆盖 %d 个年份" % (len(rows), len(years)))
    return len(rows)


def run_backfill(y0, y1, out_dir, delay=0.4):
    """回填 zjwater 历史到归档（含每机构全部预报史）。只写归档，不碰 latest.json。"""
    global _POLITE_DELAY
    _POLITE_DELAY = delay
    adapter = ZjwaterAdapter()
    now = datetime.now(TZ_BJ).isoformat()
    grand = 0
    for year in range(y0, y1 + 1):
        try:
            storms = adapter.fetch_year(year)
        except Exception as e:  # noqa: BLE001
            log("回填 %d 失败：%s" % (year, e))
            continue
        for s in storms:
            s["meta"] = {"source": "zjwater", "fetched_at": now}
            apath = os.path.join(out_dir, "archive", str(year), s["id"] + ".json")
            old = None
            if os.path.exists(apath):
                with open(apath, encoding="utf-8") as f:
                    old = json.load(f)
            write_json(apath, merge_storm(old, s))
        grand += len(storms)
        log("回填 %d 年完成：%d 个台风（累计 %d）" % (year, len(storms), grand))
    _POLITE_DELAY = 0.0
    log("回填结束：%d-%d 年，共 %d 个台风" % (y0, y1, grand))
    build_master_index(out_dir)
    return grand


def main():
    ap = argparse.ArgumentParser(description="台风数据抓取管道")
    ap.add_argument("--source", default="auto",
                    choices=["auto", "zjwater", "nmc", "jma", "fixture", "multi"])
    ap.add_argument("--year", type=int,
                    default=datetime.now(TZ_BJ).year)
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data"))
    ap.add_argument("--keep-days", type=int, default=7,
                    help="停编后仍保留在 latest 中的天数")
    ap.add_argument("--backfill", metavar="FROM-TO",
                    help="回填历史年份区间（如 2000-2026）：只写归档，带礼貌间隔")
    ap.add_argument("--reindex", action="store_true",
                    help="扫描归档重建跨年主索引 archive/index.json")
    a = ap.parse_args()
    if a.backfill:
        y0, y1 = (int(x) for x in a.backfill.split("-"))
        sys.exit(0 if run_backfill(y0, y1, a.out) else 2)
    if a.reindex:
        build_master_index(a.out)
        sys.exit(0)
    sys.exit(run(a.source, a.year, a.out, a.keep_days))


if __name__ == "__main__":
    main()
