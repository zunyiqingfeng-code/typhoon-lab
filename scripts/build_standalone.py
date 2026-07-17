#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 web/index.html 打成零依赖单文件：内联 maplibre、陆地轮廓、台风数据。
用途：本地免服务器双击打开 / 发预览给别人 / 文章附件。
    python3 scripts/build_standalone.py [--data data/latest.json] [--out ...]
"""
import argparse
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data", "latest.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "台风态势_standalone.html"))
    a = ap.parse_args()

    read = lambda *p: open(os.path.join(ROOT, *p), encoding="utf-8").read()  # noqa: E731
    html = read("web", "index.html")
    css = read("web", "vendor", "maplibre-gl.css")
    js = read("web", "vendor", "maplibre-gl.js")
    land = json.loads(read("web", "assets", "land_wp.json"))
    data = json.load(open(a.data, encoding="utf-8"))

    html = html.replace(
        '<link rel="stylesheet" href="vendor/maplibre-gl.css">',
        "<style>\n" + css + "\n</style>")
    phys = read("web", "physics.js")
    html = html.replace('<script src="physics.js"></script>',
                        "<script>\n" + phys + "\n</script>")
    inline = ("<script>window.__LAND__=" +
              json.dumps(land, separators=(",", ":")) +
              ";window.__TYPHOON_DATA__=" +
              json.dumps(data, ensure_ascii=False, separators=(",", ":")) +
              ";</script>\n<script>\n" + js + "\n</script>")
    html = html.replace('<script src="vendor/maplibre-gl.js"></script>', inline)

    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("写出 %s（%.2f MB）" % (a.out, os.path.getsize(a.out) / 1048576))


if __name__ == "__main__":
    main()
