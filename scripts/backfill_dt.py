#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Digital Typhoon 历史库批量回填（在本地网络跑，沙盒不出网）。

按 YYYYNN 规则拉每个台风的 GeoJSON：
  https://agora.ex.nii.ac.jp/digital-typhoon/geojson/wnp/{YYYYNN}.en.json

用法：
  python3 scripts/backfill_dt.py --from 2020 --to 2026          # 下载
  python3 scripts/backfill_dt.py --from 2020 --to 2026 --convert # 并转入 schema 归档

礼貌抓取：1.5s 间隔；单年连续 4 个编号 404 视为该年抓完。
产物：data/archive/<年>/dt_<编号>.geojson（--convert 时另出 <编号>_jma.json）
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from scripts.import_digital_typhoon import convert  # noqa: E402
from fetch_typhoon import write_json  # noqa: E402

BASE = "https://agora.ex.nii.ac.jp/digital-typhoon/geojson/wnp/%s.en.json"
UA = "typhoon-lab-backfill/1.0 (educational; polite 1.5s interval)"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="y0", type=int, required=True)
    ap.add_argument("--to", dest="y1", type=int, required=True)
    ap.add_argument("--convert", action="store_true",
                    help="同时转成本项目 schema 归档")
    ap.add_argument("--interval", type=float, default=1.5)
    a = ap.parse_args()

    total = 0
    for year in range(a.y0, a.y1 + 1):
        misses = 0
        ydir = os.path.join(ROOT, "data", "archive", str(year))
        os.makedirs(ydir, exist_ok=True)
        for n in range(1, 61):
            tfid = "%d%02d" % (year, n)
            dst = os.path.join(ydir, "dt_%s.geojson" % tfid)
            if os.path.exists(dst):
                misses = 0
                continue
            try:
                txt = get(BASE % tfid)
                gj = json.loads(txt)
                if not gj.get("features"):
                    raise ValueError("空轨迹")
                open(dst, "w", encoding="utf-8").write(txt)
                total += 1
                misses = 0
                print("落盘 %s（%d 点）" % (tfid, len(gj["features"])))
                if a.convert:
                    storm = convert(gj, "", "")
                    write_json(os.path.join(ydir, "%s_jma.json" % tfid), storm)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    misses += 1
                    if misses >= 4:
                        print("%d 年在 %02d 号后连续缺失，收尾" % (year, n))
                        break
                else:
                    print("HTTP %s @ %s，跳过" % (e.code, tfid))
            except Exception as e:  # noqa: BLE001
                print("失败 %s：%s" % (tfid, e))
            time.sleep(a.interval)
    print("完成：新增 %d 个台风" % total)


if __name__ == "__main__":
    main()
