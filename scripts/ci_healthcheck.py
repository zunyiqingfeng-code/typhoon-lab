#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 健康检查：比对本次与上一次提交的 data/index.json。
台风数或轨迹点数骤降，多半是源站改版/解析失效——让 workflow 红灯，
GitHub 会自动邮件告警，无需第三方服务。跨运行趋势判断放这里，
纯形态自检在 fetch_typhoon.py 的 health_check 里。"""
import json
import subprocess
import sys


def load_current():
    with open("data/index.json", encoding="utf-8") as f:
        return json.load(f)


def load_prev():
    try:
        raw = subprocess.check_output(
            ["git", "show", "HEAD:data/index.json"],
            stderr=subprocess.DEVNULL)
        return json.loads(raw.decode("utf-8"))  # git blob 为 UTF-8，别用 locale
    except Exception:  # noqa: BLE001 首次运行或无历史，跳过对比
        return None


def npoints(idx):
    return sum(s.get("n_points", 0) for s in idx.get("storms", []))


def main():
    cur = load_current()
    ncur, pcur = len(cur.get("storms", [])), npoints(cur)
    print("本次：%d 个台风、%d 个轨迹点（year=%s）" %
          (ncur, pcur, cur.get("year")))
    issues = []
    prev = load_prev()
    if prev is not None:
        nprev, pprev = len(prev.get("storms", [])), npoints(prev)
        print("上次：%d 个台风、%d 个轨迹点" % (nprev, pprev))
        # 阈值放宽，避免单个台风瞬时解析失败误报；只抓系统性骤降
        if nprev >= 4 and ncur < nprev * 0.6:
            issues.append("台风数骤降 %d→%d（<60%%），疑源站改版或解析失效"
                          % (nprev, ncur))
        if pprev > 200 and pcur < pprev * 0.7:
            issues.append("轨迹点骤降 %d→%d（<70%%），归档为增量本不该缩水"
                          % (pprev, pcur))
    else:
        print("无上次快照可比对，跳过趋势判断")
    for m in issues:
        print("::error::健康检查 - " + m)
    if issues:
        sys.exit(1)
    print("健康检查通过")


if __name__ == "__main__":
    main()
