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

print("\n全部通过：%d 项断言" % ok)
