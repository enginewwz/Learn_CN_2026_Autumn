#!/usr/bin/env python3
"""Download the first N entries from the current Tranco top-sites list."""

from __future__ import annotations

import argparse
import csv
import io
import urllib.request
import zipfile
from pathlib import Path


DEFAULT_URL = "https://tranco-list.eu/top-1m.csv.zip"


def download(url: str, output: Path, count: int) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "mininet-c-latency-study/1.0"})
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = response.read()
    if payload[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            member = archive.namelist()[0]
            text = archive.read(member).decode("utf-8")
    else:
        text = payload.decode("utf-8")

    selected: list[tuple[int, str]] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2 or not row[0].strip().isdigit():
            continue
        selected.append((int(row[0]), row[1].strip().lower()))
        if len(selected) >= count:
            break
    if len(selected) < count:
        raise RuntimeError(f"列表中只有 {len(selected)} 个有效条目，少于要求的 {count} 个")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("rank", "domain"))
        writer.writerows(selected)
    print(f"已写入 {len(selected)} 个站点: {output}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--output", type=Path, default=Path("sites.csv"))
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()
    if args.count <= 0:
        parser.error("--count 必须大于 0")
    download(args.url, args.output, args.count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
