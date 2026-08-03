#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 web/ 页面打成零依赖单文件：内联 maplibre、陆地轮廓、数据。
用途：本地免服务器双击打开 / 发预览给别人 / 文章附件。
    python3 scripts/build_standalone.py                # index（内联 latest+jma镜像+land）
    python3 scripts/build_standalone.py --page archive # 档案页（内联 records；逐台风 JSON 需服务器）
页面端 loadJSON 优先读 window.__KEY__，命中即不再 fetch。
"""
import argparse
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 页面 → {window 键: (相对 ROOT 的 JSON 路径)}
INLINE_KEYS = {
    "index": {
        "__LAND__": "web/assets/land_wp.json",
        "__TYPHOON_DATA__": "data/latest.json",
        "__JMA_MIRROR__": "data/realtime/jma_latest.json",
    },
    "archive": {
        "__LAND__": "web/assets/land_wp.json",
        "__RECORDS__": "data/records.json",
    },
}
EXTRA_JS = ("physics.js", "tracklib.js", "splash.js")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", choices=list(INLINE_KEYS), default="index")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.out is None:
        a.out = os.path.join(ROOT, "台风态势_%s_standalone.html" % a.page)

    read = lambda *p: open(os.path.join(ROOT, *p), encoding="utf-8").read()  # noqa: E731
    html = read("web", a.page + ".html")
    css = read("web", "vendor", "maplibre-gl.css")
    js = read("web", "vendor", "maplibre-gl.js")

    html = html.replace(
        '<link rel="stylesheet" href="vendor/maplibre-gl.css">',
        "<style>\n" + css + "\n</style>")
    for extra in EXTRA_JS:
        if os.path.exists(os.path.join(ROOT, "web", extra)):
            html = html.replace('<script src="%s"></script>' % extra,
                                "<script>\n" + read("web", extra) + "\n</script>")

    inline = []
    for key, path in INLINE_KEYS[a.page].items():
        p = os.path.join(ROOT, path)
        if not os.path.exists(p):
            continue
        data = json.load(open(p, encoding="utf-8"))
        inline.append("window.%s=%s;" % (key,
            json.dumps(data, ensure_ascii=False, separators=(",", ":"))))
    if inline:
        block = ("<script>\n" + "".join(inline) + "\n</script>\n" +
                 '<script src="vendor/maplibre-gl.js"></script>')
        html = html.replace('<script src="vendor/maplibre-gl.js"></script>', block)
    html = html.replace(
        '<script src="vendor/maplibre-gl.js"></script>',
        "<script>\n" + js + "\n</script>")

    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("写出 %s（%.2f MB）" % (a.out, os.path.getsize(a.out) / 1048576))


if __name__ == "__main__":
    main()
