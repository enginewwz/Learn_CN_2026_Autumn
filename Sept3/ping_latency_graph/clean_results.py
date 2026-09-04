#!/usr/bin/env python3
"""Create an auditable clean subset of website latency measurements."""

from __future__ import annotations

import argparse
import csv
import ipaddress
import math
from collections import Counter
from pathlib import Path


METRICS = [
    "c_latency_ms",
    "ping_ms",
    "dns_ms",
    "tcp_transfer_ms",
    "total_time_ms",
]


def finite_number(value: str | None) -> float | None:
    try:
        number = float(value or "")
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def structural_rejection(row: dict[str, str], keep_geo_anomalies: bool) -> str | None:
    values = {name: finite_number(row.get(name)) for name in METRICS}
    missing = [name for name, value in values.items() if value is None]
    if missing:
        return "missing:" + ",".join(missing)

    try:
        address = ipaddress.ip_address(row.get("ip", ""))
    except ValueError:
        return "invalid_ip"
    if not address.is_global:
        return "non_global_ip"

    try:
        status = int(row.get("http_code", ""))
    except ValueError:
        return "invalid_http_code"
    if not 200 <= status < 400:
        return "http_not_2xx_3xx"

    assert all(value is not None for value in values.values())
    numeric = {name: float(value) for name, value in values.items()}
    non_positive = [name for name, value in numeric.items() if value <= 0]
    if non_positive:
        return "non_positive:" + ",".join(non_positive)
    if numeric["total_time_ms"] < numeric["dns_ms"] + numeric["tcp_transfer_ms"]:
        return "inconsistent_phases"
    if not keep_geo_anomalies and numeric["ping_ms"] < numeric["c_latency_ms"]:
        return "ping_below_c_latency"
    return None


def percentile(values: list[float], percentage: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentage / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def clean_results(
    input_path: Path,
    output_path: Path,
    rejected_path: Path,
    trim_percentile: float | None,
    keep_geo_anomalies: bool,
    geoip_db: Path | None = None,
    max_accuracy_radius_km: float | None = None,
    require_distance_beyond_accuracy: bool = False,
) -> tuple[int, int, Counter[str], dict[str, float]]:
    with input_path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if not fieldnames:
        raise ValueError(f"CSV 缺少表头: {input_path}")
    missing_columns = [name for name in METRICS + ["ip", "http_code"] if name not in fieldnames]
    if missing_columns:
        raise ValueError("CSV 缺少字段: " + ", ".join(missing_columns))
    accuracy_field = "geo_accuracy_radius_km"
    if accuracy_field not in fieldnames:
        fieldnames.append(accuracy_field)
    if (
        max_accuracy_radius_km is not None or require_distance_beyond_accuracy
    ) and geoip_db is None:
        raise ValueError("使用地理精度过滤时必须提供 --geoip-db")

    accepted: list[dict[str, str]] = []
    rejected: list[tuple[dict[str, str], str]] = []
    reasons: Counter[str] = Counter()
    for row in rows:
        reason = structural_rejection(row, keep_geo_anomalies)
        if reason:
            rejected.append((row, reason))
            reasons[reason.split(":", 1)[0]] += 1
        else:
            accepted.append(row)

    if geoip_db is not None:
        try:
            import geoip2.database
        except ImportError as exc:
            raise RuntimeError("缺少 geoip2，无法检查定位精度") from exc
        geo_kept: list[dict[str, str]] = []
        with geoip2.database.Reader(str(geoip_db)) as database:
            for row in accepted:
                try:
                    radius = database.city(row["ip"]).location.accuracy_radius
                except Exception:
                    radius = None
                row[accuracy_field] = "" if radius is None else str(radius)
                if radius is None and max_accuracy_radius_km is not None:
                    rejected.append((row, "geo_accuracy_missing"))
                    reasons["geo_accuracy_missing"] += 1
                elif (
                    radius is not None
                    and max_accuracy_radius_km is not None
                    and radius > max_accuracy_radius_km
                ):
                    rejected.append((row, "geo_accuracy_too_coarse"))
                    reasons["geo_accuracy_too_coarse"] += 1
                elif require_distance_beyond_accuracy:
                    distance = finite_number(row.get("distance_km"))
                    if radius is None or distance is None:
                        rejected.append((row, "geo_distance_or_accuracy_missing"))
                        reasons["geo_distance_or_accuracy_missing"] += 1
                    elif distance <= radius:
                        rejected.append((row, "geo_distance_within_accuracy_radius"))
                        reasons["geo_distance_within_accuracy_radius"] += 1
                    else:
                        geo_kept.append(row)
                else:
                    geo_kept.append(row)
        accepted = geo_kept

    thresholds: dict[str, float] = {}
    if trim_percentile is not None and accepted:
        thresholds = {
            name: percentile([float(row[name]) for row in accepted], trim_percentile)
            for name in METRICS
        }
        kept: list[dict[str, str]] = []
        for row in accepted:
            high = [name for name in METRICS if float(row[name]) > thresholds[name]]
            if high:
                reason = f"above_p{trim_percentile:g}:" + ",".join(high)
                rejected.append((row, reason))
                reasons["statistical_outlier"] += 1
            else:
                kept.append(row)
        accepted = kept

    output_path.parent.mkdir(parents=True, exist_ok=True)
    rejected_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(accepted)
    with rejected_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames + ["cleaning_reason"])
        writer.writeheader()
        for row, reason in rejected:
            writer.writerow({**row, "cleaning_reason": reason})
    return len(rows), len(accepted), reasons, thresholds


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("results_clean.csv"))
    parser.add_argument("--rejected", type=Path, default=Path("results_rejected.csv"))
    parser.add_argument(
        "--trim-percentile",
        type=float,
        help="drop a row if any metric is above this percentile, e.g. 99",
    )
    parser.add_argument(
        "--keep-geo-anomalies",
        action="store_true",
        help="keep rows where measured Ping is below c-latency",
    )
    parser.add_argument("--geoip-db", type=Path, help="GeoLite2-City.mmdb")
    parser.add_argument(
        "--max-accuracy-radius-km",
        type=float,
        help="drop GeoIP records whose accuracy radius exceeds this value",
    )
    parser.add_argument(
        "--require-distance-beyond-accuracy",
        action="store_true",
        help="drop records whose source distance is within the GeoIP accuracy radius",
    )
    args = parser.parse_args()
    if args.trim_percentile is not None and not 0 < args.trim_percentile <= 100:
        parser.error("--trim-percentile 必须在 (0, 100] 内")
    if args.max_accuracy_radius_km is not None and args.max_accuracy_radius_km <= 0:
        parser.error("--max-accuracy-radius-km 必须大于 0")
    if args.geoip_db is not None and not args.geoip_db.is_file():
        parser.error(f"GeoIP 数据库不存在: {args.geoip_db}")

    total, kept, reasons, thresholds = clean_results(
        args.input,
        args.output,
        args.rejected,
        args.trim_percentile,
        args.keep_geo_anomalies,
        args.geoip_db,
        args.max_accuracy_radius_km,
        args.require_distance_beyond_accuracy,
    )
    print(f"原始记录: {total}")
    print(f"保留记录: {kept} ({kept / total * 100:.1f}%)" if total else "保留记录: 0")
    for reason, count in reasons.most_common():
        print(f"剔除 {reason}: {count}")
    if thresholds:
        print("异常值上限 (ms):")
        for name, value in thresholds.items():
            print(f"  {name}: {value:.3f}")
    print(f"清洗结果: {args.output}")
    print(f"剔除明细: {args.rejected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
