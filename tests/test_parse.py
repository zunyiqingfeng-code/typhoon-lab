#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析层测试：python3 tests/test_parse.py
把 2026-07 实测与逆向文档确证的字段形态钉进断言。改 fetch_typhoon.py 前后都跑一遍。"""
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_typhoon as ft  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    assert cond, "FAIL: " + msg
    ok += 1
    print("  ok -", msg)


check(ft.SCHEMA_VERSION == "1.1", "schema 版本 1.1")

# ---- 基础归一化 ----
check(ft.norm_dir("西北西") == 292.5, "旧式方位 西北西 → 292.5°")
check(ft.norm_dir("东北偏北") == 22.5, "国标方位 东北偏北 → 22.5°")
check(ft.norm_time("2026-07-14T09:00:00.000+00:00") ==
      "2026-07-14T17:00:00+08:00", "ISO UTC → 北京时间（TyhoonActivity 实测形态）")
check(ft.norm_time("2026-07-07 08:00:00") ==
      "2026-07-07T08:00:00+08:00", "legacy 北京时间形态")
r = ft.norm_radius("280|180|220|180")
check(r == {"ne": 280, "se": 180, "nw": 220, "sw": 180},
      "四段风圈顺序 东北|东南|西北|西南")
check(ft.norm_radius("250") == {"ne": 250, "se": 250, "sw": 250, "nw": 250},
      "单值风圈四象限对称")
check(ft.norm_radius("") is None and ft.norm_radius("0") is None,
      "空串/0 视为无风圈")

# ---- zjwater 详情解析（离线：mock http_get 喂样本） ----
sample = open(os.path.join(os.path.dirname(__file__),
                           "sample_zjwater_info.json"), encoding="utf-8").read()
with mock.patch.object(ft, "http_get", return_value=sample):
    st = ft.ZjwaterAdapter()._fetch_detail("https://x", "209901")

check(st["id"] == "209901" and st["name_en"] == "SAMPLE",
      "顶层字段与英文名大写")
check(st["is_active"] is True, "isactive '1' → True")
check(len(st["track"]) == 2, "两个实况点")
p0, p1 = st["track"]
check(p0["move_dir_deg"] == 292.5 and p0["r7"]["ne"] == 250,
      "点0：旧式方位 + 单值风圈")
check(p1["t"] == "2099-07-03T08:00:00+08:00",
      "点1：ISO UTC 时间归一为北京时间")
check(p1["r7"] == {"ne": 280, "se": 180, "nw": 220, "sw": 180},
      "点1：四段风圈象限映射")
check([f["agency"] for f in st["forecasts"]] == ["CMA", "CWA"],
      "机构映射 中国→CMA / 中国台湾→CWA 并按机构排序")
cma = st["forecasts"][0]
check(cma["issued_at"] == "2099-07-03T14:00:00+08:00",
      "ybsj(ISO UTC) 作为预报发布时间")
check("wind_ms" not in cma["points"][1] and "pressure_hpa" not in cma["points"][1],
      "预报 speed/pressure '0' 视为缺测不入库")
check(st["land"][0]["address"] == "某省某县沿海" and
      st["land"][0]["t"] == "2099-07-05T05:20:00+08:00", "登陆记录解析")

# ---- 合并去重 ----
merged = ft.merge_storm(st, st)
check(len(merged["track"]) == 2 and len(merged["forecasts"]) == 2,
      "重复合并不翻倍")

# ---- schema 1.1：保留每机构全部发布时次（复盘评测的原料） ----
multi = json.dumps({
    "tfid": "209902", "name": "多报", "enname": "multi", "isactive": "1",
    "points": [
        {"time": "2099-08-01 08:00:00", "lng": "130.0", "lat": "15.0",
         "strong": "台风", "speed": "35", "pressure": "960",
         "forecast": [{"tm": "中国", "forecastpoints": [
             {"time": "2099-08-02 08:00:00", "lng": "128.0", "lat": "17.0",
              "strong": "台风", "speed": "33", "pressure": "965",
              "ybsj": "2099-08-01T00:00:00.000+00:00"}]}]},
        {"time": "2099-08-01 20:00:00", "lng": "129.0", "lat": "16.0",
         "strong": "台风", "speed": "38", "pressure": "955",
         "forecast": [{"tm": "中国", "forecastpoints": [
             {"time": "2099-08-02 20:00:00", "lng": "127.0", "lat": "18.5",
              "strong": "台风", "speed": "36", "pressure": "958",
              "ybsj": "2099-08-01T12:00:00.000+00:00"}]}]},
    ],
}, ensure_ascii=False)
with mock.patch.object(ft, "http_get", return_value=multi):
    st2 = ft.ZjwaterAdapter()._fetch_detail("https://x", "209902")

cma_issues = [f["issued_at"] for f in st2["forecasts"] if f["agency"] == "CMA"]
check(cma_issues == ["2099-08-01T08:00:00+08:00", "2099-08-01T20:00:00+08:00"],
      "schema1.1：同机构两次发布都保留，按发布时刻排序")

red = ft.latest_forecast_only(st2)
cma_red = [f for f in red["forecasts"] if f["agency"] == "CMA"]
check(len(cma_red) == 1 and cma_red[0]["issued_at"] == "2099-08-01T20:00:00+08:00",
      "latest_forecast_only 只留每机构最新一份（前端契约）")

m2 = ft.merge_storm(st2, st2)
check(len([f for f in m2["forecasts"] if f["agency"] == "CMA"]) == 2,
      "合并按 (机构,发布时刻) 去重：历史不翻倍也不丢")

print("\n全部通过：%d 项断言" % ok)
