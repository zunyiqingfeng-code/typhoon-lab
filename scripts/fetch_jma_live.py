#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取 JMA 当前在编台风 → data/realtime/jma_latest.json（与 latest.json 同 schema）。
浏览器直连 JMA 可能受网络/CORS 限制，此文件由 GitHub Actions 定时生成并 commit，
前端优先直连、次选本镜像、再回退本地档案。失败不破坏既有文件（退出码 0，方便 CI 忽略）。

增量归档：每天（UTC 日期）首次运行时把当次抓到的 specs 原文存到
data/jma_archive/<年>/<月>/<UTC日>-<TCid>.json，累积 JMA 预报史供未来复盘评测。
用法：python3 scripts/fetch_jma_live.py [--no-archive]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_typhoon  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "realtime")
OUT = os.path.join(OUT_DIR, "jma_latest.json")
ARCH_ROOT = os.path.join(ROOT, "data", "jma_archive")


def archive_specs(storms):
    """把本次抓取的 JMA 台风明细落一份当日快照（当天已存在则跳过，不重复）。"""
    utc = datetime.now(timezone.utc)
    day = utc.strftime("%Y-%m-%d")
    done, skipped = 0, 0
    for st in storms:
        tc = st.get("id", "TC")     # 形如 JMA-TC2615
        if tc.startswith("JMA-"):
            tc = tc[4:]
        fp = os.path.join(ARCH_ROOT, str(utc.year), "%02d" % utc.month,
                          "%s-%s.json" % (day, tc))
        if os.path.exists(fp):
            skipped += 1
            continue
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as fh:
            json.dump(st, fh, ensure_ascii=False, separators=(",", ":"))
        done += 1
    return done, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-archive", action="store_true", help="跳过当日增量归档")
    args = ap.parse_args()
    adapter = fetch_typhoon.JmaAdapter()
    year = datetime.now().year
    try:
        storms = adapter.fetch_year(year)
    except Exception as e:  # noqa: BLE001
        print("JMA 抓取失败（保留既有镜像）：%s" % e)
        return 0
    os.makedirs(OUT_DIR, exist_ok=True)
    payload = {
        "schema_version": "1.1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "source": "jma",
        "fixture": False,
        "storms": storms,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print("JMA 镜像写出：%d 个在编台风 → %s" % (len(storms), OUT))
    if not args.no_archive:
        done, skipped = archive_specs(storms)
        print("JMA 增量归档：新增 %d，当日已存在跳过 %d" % (done, skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
