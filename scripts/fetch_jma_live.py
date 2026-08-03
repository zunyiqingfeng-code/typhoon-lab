#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取 JMA 当前在编台风 → data/realtime/jma_latest.json（与 latest.json 同 schema）。
浏览器直连 JMA 可能受网络/CORS 限制，此文件由 GitHub Actions 定时生成并 commit，
前端优先直连、次选本镜像、再回退本地档案。失败不破坏既有文件（退出码 0，方便 CI 忽略）。
用法：python3 scripts/fetch_jma_live.py
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_typhoon  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "realtime")
OUT = os.path.join(OUT_DIR, "jma_latest.json")


def main():
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
