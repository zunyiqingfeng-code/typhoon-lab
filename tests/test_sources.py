#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多源适配器测试：python3 tests/test_sources.py
把 2026-08-03 直连实测的源格式钉进断言。跑 fetch_sources.py / fetch_typhoon.py 改动前后都跑。"""
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_sources as fs  # noqa: E402
import fetch_typhoon as ft  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    assert cond, "FAIL: " + msg
    ok += 1
    print("  ok -", msg)


# ---- KMA：表格行解析（mock 页面） ----
KMA_HTML = """<div class="title"><strong>No.13 DOLPHIN</strong> KMA | Issued at(KST) : Tue, 4 Aug 2026, 10:00</div>
<table class="table-col"><tbody>
<tr><td>Tue, 4 Aug 2026, 00:00 Analysis</td><td>3</td><td>43</td><td>155</td><td>950</td><td>25.2</td><td>143.2</td><td>W</td><td>16</td><td><span>450</span><br/><span>[SW 350]</span></td><td><span>140</span><br/><span>[SW 110]</span></td><td>-</td></tr>
<tr><td>Tue, 4 Aug 2026, 12:00 Forecast</td><td>3</td><td>43</td><td>155</td><td>950</td><td>25.2</td><td>140.4</td><td>W</td><td>23</td><td><span>450</span><br/><span>[SW 350]</span></td><td><span>140</span><br/><span>[SW 110]</span></td><td>40</td></tr>
</tbody></table>"""
with mock.patch.object(fs, "http_get", return_value=KMA_HTML):
    kstorms = fs.KmaAdapter().fetch_active()

check(len(kstorms) == 1 and kstorms[0]["name_en"] == "DOLPHIN",
      "KMA：解析台风名与返回 1 个台风")
k = kstorms[0]
check(k["track"][0]["t"] == "2026-08-04T08:00:00+08:00" and
      k["track"][0]["lat"] == 25.2 and k["track"][0]["lon"] == 143.2,
      "KMA：UTC 时间转北京 +08:00，坐标解析")
check(k["track"][0]["wind_ms"] == 43 and k["track"][0]["pressure_hpa"] == 950,
      "KMA：风速/气压解析")
check(k["track"][0]["grade"] == "STS" and k["track"][0]["move_dir_deg"] == 270,
      "KMA：强度码 3→STS、W→270°")
check(k["track"][0]["r7"] == {"ne": 450, "se": 450, "sw": 350, "nw": 450},
      "KMA：风圈 '[SW 350]' 象限覆盖")
check(len(k["forecasts"][0]["points"]) == 1 and
      k["forecasts"][0]["points"][0]["t"] == "2026-08-04T20:00:00+08:00",
      "KMA：Forecast 行进预报点列表")

# ---- HKO：文本页解析（mock） ----
HKO_HTML = """<h1>Tropical Cyclone Position</h1>
Bulletin updated at 12:30 HKT 03/Aug/2026
Low Pressure Area at 11:00 HKT 03 August 2026 ( 17.1 N, 123.0 E, about 1090 km east-southeast of Hong Kong )
The tropical cyclone near Luzon has weakened into an area of low pressure.
Forecast Positions and Intensities
Date time Position Classification Maximum sustained wind near centre
Past Positions and Intensities
Date time Position Classification Maximum sustained wind near centre
11:00 HKT 03 August 2026 17.1 N 123.0 E Low Pressure Area 40 km/h
08:00 HKT 03 August 2026 16.5 N 123.2 E Tropical Depression 45 km/h
Notes:"""
with mock.patch.object(fs, "http_get", return_value=HKO_HTML):
    hs = fs.HkoAdapter().fetch_active()
check(len(hs) == 1, "HKO：返回 1 个 system")
check(hs[0]["track"][-1]["t"] == "2026-08-03T11:00:00+08:00" and
      hs[0]["track"][-1]["lat"] == 17.1 and hs[0]["track"][-1]["lon"] == 123.0,
      "HKO：Past Positions 解析时间/坐标")
check(abs(hs[0]["track"][-1]["wind_ms"] - 11.11) < 0.01,
      "HKO：km/h → m/s 换算")
check(hs[0]["track"][-1]["grade"] == "TD" and hs[0]["forecasts"] == [],
      "HKO：无 Forecast 块时预报为空")

# ---- PAGASA：公报 PDF 文本解析 ----
PAG_TXT = """
TROPICAL CYCLONE BULLETIN NR. 8
Tropical Depression LUIS
Issued at 8:00 AM, 03 August 2026
Location of Center (7:00 AM)
The center of Tropical Depression LUIS was estimated based
on all available data at 135 km East of Casiguran, Aurora
(16.4°N, 123.4°E).
Intensity
Maximum sustained winds of 55 km/h near the center,
gustiness of up to 70 km/h, and central pressure of 1002 hPa.
Present Movement
Northward slowly
TRACK AND INTENSITY FORECAST
12-Hour Forecast
5:00 PM
03 August 2026
16.8 123.0 140 km East of Echague, Isabela 55 TD NNW Slowly
24-Hour Forecast
5:00 AM
04 August 2026
17.0 122.4 In the vicinity of Palanan, Isabela 45 TD WNW Slowly
"""
p = fs.PagasaAdapter._parse(PAG_TXT, "x")
check(len(p) == 1 and p[0]["name_en"] == "LUIS", "PAGASA：台风名解析")
check(p[0]["track"][0]["t"] == "2026-08-03T07:00:00+08:00" and
      p[0]["track"][0]["lat"] == 16.4 and p[0]["track"][0]["lon"] == 123.4,
      "PAGASA：实况用观测时间（Location of Center 7:00 AM）")
check(abs(p[0]["track"][0]["wind_ms"] - 15.28) < 0.01,
      "PAGASA：55 km/h → 15.28 m/s")
check(p[0]["forecasts"][0]["points"][0]["t"] == "2026-08-03T17:00:00+08:00" and
      p[0]["forecasts"][0]["points"][0]["lat"] == 16.8,
      "PAGASA：12h 预报点（时间取自公报表格行）")

# ---- JTWC/UCAR b-deck 解析 ----
B_DECK = """WP, 12, 2026072700,   , BEST,   0, 128N, 1783E,  30, 1002, XX,
WP, 12, 2026080300,   , BEST,   0, 251N, 1432E,  30, 1002, XX,
WP, 12, 2026080400,   , BEST,   0, 251N, 1432E,  30, 1002, XX,
"""
u = fs.UcarBdeckAdapter._parse(B_DECK, "bwp122026.dat")
check(u is not None, "UCAR：解析出风暴")
check(u["id"] == "WP12" and u["track"][0]["t"] == "2026-07-27T08:00:00+08:00",
      "UCAR：编号 WP12、时间解析")
check(u["track"][0]["lat"] == 12.8 and u["track"][0]["lon"] == 178.3,
      "UCAR：lat '128N' → 12.8、lon '1783E' → 178.3（÷10）")
check(abs(u["track"][0]["wind_ms"] - 15.43) < 0.01,
      "UCAR：风速 kt × 0.5144 → m/s")

# ---- 匹配/合并 ----
main = [{"id": "202613", "name_en": "DOLPHIN", "track": [
    {"t": "2026-08-04T08:00:00+08:00", "lat": 25.2, "lon": 143.2}],
    "forecasts": [{"agency": "JMA", "issued_at": "T1", "points": [1]}]}]
extra = {"id": "202613", "name_en": "DOLPHIN", "track": [],
         "forecasts": [{"agency": "KMA", "issued_at": "T1", "points": [2]}]}
m = ft.match_extra_storm(extra, main)
check(m is main[0], "匹配：同名 DOLPHIN → 主风暴")
ft.merge_extra_into(main[0], extra, "kma")
check([f["agency"] for f in main[0]["forecasts"]] == ["JMA", "KMA"],
      "合并：KMA 预报并入")
check(main[0].get("sources") == ["kma"], "合并：记录 source 标记")

# ---- 不匹配：不同名字不同 id ----
other = {"id": "WP12", "name_en": "TWELVE", "track": [],
         "forecasts": []}
check(ft.match_extra_storm(other, main) is None,
      "不匹配：WP12/TWELVE 与 202613/DOLPHIN 无交集")

print("\n全部通过：%d 项断言" % ok)