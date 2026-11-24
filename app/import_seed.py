"""Import current templates + markers from a JSON file.

Usage (direct SQLite, uses DB_PATH):

    python -m app.import_seed seed/current_templates.json [--mode replace|append]

Or over HTTP:

    python -m app.import_seed seed/current_templates.json \\
        --url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from .payload import extract_index_base, parse_template
from .service import import_templates
from .storage import Storage


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="导入现用磅单模板及标记")
    ap.add_argument("file", help="JSON 文件（模板数组或 {'templates': [...]}）")
    ap.add_argument("--mode", choices=["replace", "append"], default="replace")
    ap.add_argument("--force", action="store_true", help="忽略 12 套上限")
    ap.add_argument("--url", default=None, help="可选：通过 HTTP 导入到运行中的服务")
    args = ap.parse_args(argv)

    raw = json.loads(Path(args.file).read_text(encoding="utf-8"))

    if args.url:
        body = json.dumps(raw).encode("utf-8")
        sep = "&" if "?" in args.url else "?"
        url = args.url.rstrip("/") + f"/api/import?mode={args.mode}"
        if args.force:
            url += "&force=true"
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(resp.read().decode("utf-8"))
        return 0

    base = extract_index_base(raw if isinstance(raw, dict) else {})
    items = raw["templates"] if isinstance(raw, dict) and "templates" in raw else raw
    specs = [parse_template(x, base=base) for x in items]
    result = import_templates(Storage(), specs, mode=args.mode, force=args.force)
    print(
        f"imported {result['imported_count']} templates as version "
        f"{result['version']} (mode={result['mode']}), "
        f"warnings={len(result['warnings'])}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
