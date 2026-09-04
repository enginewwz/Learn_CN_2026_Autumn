#!/usr/bin/env python3
"""Measure website latency and relate it to a geographic speed-of-light bound.

Run this program *inside* a Mininet host namespace.  It writes every completed
measurement to CSV immediately, so an interrupted experiment can be resumed.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import pycurl
except ImportError:  # Give a more useful message than a traceback.
    pycurl = None  # type: ignore[assignment]


CSV_FIELDS = [
    "rank",
    "domain",
    "url",
    "ip",
    "country",
    "latitude",
    "longitude",
    "distance_km",
    "c_latency_ms",
    "ping_ms",
    "dns_ms",
    "tcp_transfer_ms",
    "total_time_ms",
    "http_code",
    "error",
]

PING_SUMMARY_RE = re.compile(
    r"=\s*([0-9.]+)/[0-9.]+/[0-9.]+(?:/[0-9.]+)?\s*ms"
)


@dataclass(frozen=True)
class Target:
    rank: int
    domain: str


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def load_targets(path: Path, limit: int) -> list[Target]:
    """Read either rank,domain CSV or a one-domain-per-line text file."""
    targets: list[Target] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for line_number, row in enumerate(csv.reader(stream), start=1):
            if not row:
                continue
            values = [part.strip() for part in row]
            if values[0].startswith("#"):
                continue
            if values[0].lower() in {"rank", "domain", "url"}:
                continue

            if len(values) >= 2 and values[0].isdigit():
                rank, raw_domain = int(values[0]), values[1]
            else:
                rank, raw_domain = line_number, values[0]

            domain = raw_domain.lower()
            domain = re.sub(r"^https?://", "", domain).split("/", 1)[0]
            domain = domain.split(":", 1)[0].strip(".")
            if not domain or "." not in domain or domain in seen:
                continue
            seen.add(domain)
            targets.append(Target(rank, domain))
            if len(targets) >= limit:
                break
    return targets


def ping_minimum_ms(ip: str, count: int, timeout_s: float) -> tuple[float | None, str]:
    if not shutil.which("ping"):
        return None, "ping command not found"
    # ip is obtained from libcurl, not user input.  Passing an argv list also
    # prevents shell interpretation.
    command = [
        "ping",
        "-n",
        "-c",
        str(count),
        "-W",
        str(max(1, math.ceil(timeout_s))),
        ip,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=count * timeout_s + 2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"ping: {exc}"
    match = PING_SUMMARY_RE.search(result.stdout)
    if match:
        return float(match.group(1)), ""
    return None, "ping unanswered"


def _curl_once(url: str, timeout_s: float, insecure: bool) -> dict[str, Any]:
    assert pycurl is not None
    curl = pycurl.Curl()
    try:
        curl.setopt(pycurl.URL, url)
        curl.setopt(pycurl.WRITEFUNCTION, lambda data: len(data))
        curl.setopt(pycurl.FOLLOWLOCATION, True)
        curl.setopt(pycurl.MAXREDIRS, 5)
        curl.setopt(pycurl.CONNECTTIMEOUT_MS, int(timeout_s * 1000))
        curl.setopt(pycurl.TIMEOUT_MS, int(timeout_s * 1000))
        curl.setopt(pycurl.NOSIGNAL, True)
        curl.setopt(pycurl.USERAGENT, "mininet-c-latency-study/1.0")
        curl.setopt(pycurl.ACCEPT_ENCODING, "")
        if insecure:
            curl.setopt(pycurl.SSL_VERIFYPEER, False)
            curl.setopt(pycurl.SSL_VERIFYHOST, 0)
        error = ""
        try:
            curl.perform()
        except pycurl.error as exc:
            error = f"curl {exc.args[0]}: {exc.args[1]}"

        dns_s = float(curl.getinfo(pycurl.NAMELOOKUP_TIME))
        first_byte_s = float(curl.getinfo(pycurl.STARTTRANSFER_TIME))
        total_s = float(curl.getinfo(pycurl.TOTAL_TIME))
        return {
            "url": str(curl.getinfo(pycurl.EFFECTIVE_URL) or url),
            "ip": str(curl.getinfo(pycurl.PRIMARY_IP) or ""),
            "dns_ms": dns_s * 1000 if dns_s > 0 else None,
            # TCP transfer is from receipt of the first response byte through
            # the final byte, matching the c-latency measurement literature.
            "tcp_transfer_ms": max(0.0, (total_s - first_byte_s) * 1000)
            if not error and first_byte_s > 0
            else None,
            "total_time_ms": total_s * 1000 if not error else None,
            "http_code": int(curl.getinfo(pycurl.RESPONSE_CODE)),
            "error": error,
        }
    finally:
        curl.close()


def measure_target(
    target: Target,
    timeout_s: float,
    ping_count: int,
    ping_timeout_s: float,
    insecure: bool,
) -> dict[str, Any]:
    result = _curl_once(f"https://{target.domain}/", timeout_s, insecure)
    if result["error"]:
        fallback = _curl_once(f"http://{target.domain}/", timeout_s, insecure)
        if not fallback["error"] or not result["ip"]:
            result = fallback
    ping_ms, ping_error = (None, "")
    if result["ip"]:
        ping_ms, ping_error = ping_minimum_ms(
            result["ip"], ping_count, ping_timeout_s
        )
    errors = [item for item in (result["error"], ping_error) if item]
    result.update(
        rank=target.rank,
        domain=target.domain,
        ping_ms=ping_ms,
        error="; ".join(errors),
    )
    return result


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius_km * math.asin(min(1.0, math.sqrt(a)))


def add_geography(
    row: dict[str, Any],
    reader: Any,
    source_lat: float,
    source_lon: float,
    propagation_km_s: float,
) -> None:
    row.update(
        country="",
        latitude=None,
        longitude=None,
        distance_km=None,
        c_latency_ms=None,
    )
    if not row.get("ip"):
        return
    try:
        record = reader.city(row["ip"])
        latitude = _as_float(record.location.latitude)
        longitude = _as_float(record.location.longitude)
        row["country"] = record.country.iso_code or ""
        row["latitude"], row["longitude"] = latitude, longitude
        if latitude is not None and longitude is not None:
            distance = haversine_km(source_lat, source_lon, latitude, longitude)
            row["distance_km"] = distance
            # The measured values are round-trip/request-response quantities.
            row["c_latency_ms"] = 2 * distance / propagation_km_s * 1000
    except Exception as exc:  # GeoIP exposes several database-specific errors.
        suffix = f"GeoIP: {exc}"
        row["error"] = "; ".join(filter(None, (row.get("error", ""), suffix)))


def completed_domains(path: Path) -> set[str]:
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with path.open(encoding="utf-8", newline="") as stream:
        return {row.get("domain", "") for row in csv.DictReader(stream)}


def clean_row(row: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for field in CSV_FIELDS:
        value = row.get(field, "")
        if isinstance(value, float):
            cleaned[field] = f"{value:.6f}"
        elif value is None:
            cleaned[field] = ""
        else:
            cleaned[field] = value
    return cleaned
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sites", type=Path, required=True, help="rank,domain CSV")
    parser.add_argument("--geoip-db", type=Path, required=True, help="GeoLite2-City.mmdb")
    parser.add_argument("--source-lat", type=float, required=True)
    parser.add_argument("--source-lon", type=float, required=True)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=12.0, help="HTTP seconds")
    parser.add_argument("--ping-count", type=int, default=3)
    parser.add_argument("--ping-timeout", type=float, default=2.0)
    parser.add_argument(
        "--propagation-speed",
        type=float,
        default=299_792.458,
        help="speed of light in vacuum, km/s (default: 299792.458)",
    )
    parser.add_argument("--output", type=Path, default=Path("results.csv"))
    parser.add_argument("--plot", type=Path, default=Path("latency_inflation_cdf.png"))
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--insecure", action="store_true", help="disable TLS verification")
    parser.add_argument("--no-resume", action="store_true", help="truncate output first")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if pycurl is None:
        raise SystemExit("缺少 pycurl；请运行: python -m pip install -r requirements.txt")
    if not args.sites.is_file():
        raise SystemExit(f"站点文件不存在: {args.sites}")
    if not args.geoip_db.is_file():
        raise SystemExit(f"GeoIP 数据库不存在: {args.geoip_db}")
    if not (-90 <= args.source_lat <= 90 and -180 <= args.source_lon <= 180):
        raise SystemExit("源经纬度超出范围")
    for name in ("limit", "workers", "ping_count"):
        if getattr(args, name) <= 0:
            raise SystemExit(f"--{name.replace('_', '-')} 必须大于 0")
    if args.timeout <= 0 or args.ping_timeout <= 0 or args.propagation_speed <= 0:
        raise SystemExit("超时和传播速度必须大于 0")


def run(args: argparse.Namespace) -> int:
    validate_args(args)
    try:
        import geoip2.database
    except ImportError:
        raise SystemExit("缺少 geoip2；请运行: python -m pip install -r requirements.txt")

    targets = load_targets(args.sites, args.limit)
    if not targets:
        raise SystemExit("站点文件中没有有效域名")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.no_resume:
        args.output.unlink(missing_ok=True)
    done = completed_domains(args.output)
    pending = [target for target in targets if target.domain not in done]
    print(f"目标 {len(targets)} 个，已完成 {len(done & {t.domain for t in targets})} 个，待测 {len(pending)} 个")

    write_lock = threading.Lock()
    file_exists = args.output.exists() and args.output.stat().st_size > 0
    mode = "a" if file_exists else "w"
    with geoip2.database.Reader(str(args.geoip_db)) as reader, args.output.open(
        mode, encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
            stream.flush()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    measure_target,
                    target,
                    args.timeout,
                    args.ping_count,
                    args.ping_timeout,
                    args.insecure,
                ): target
                for target in pending
            }
            completed = 0
            for future in as_completed(futures):
                target = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    row = {
                        "rank": target.rank,
                        "domain": target.domain,
                        "error": f"worker: {type(exc).__name__}: {exc}",
                    }
                add_geography(
                    row,
                    reader,
                    args.source_lat,
                    args.source_lon,
                    args.propagation_speed,
                )
                with write_lock:
                    writer.writerow(clean_row(row))
                    stream.flush()
                completed += 1
                if completed % 25 == 0 or completed == len(pending):
                    print(f"进度: {completed}/{len(pending)}", flush=True)

    if not args.no_plot:
        try:
            from plot_results import plot_results

            plot_results(args.output, args.plot)
            print(f"图已保存: {args.plot}")
        except ImportError as exc:
            print(f"绘图跳过（缺少依赖）: {exc}", file=sys.stderr)
            return 2
    print(f"数据已保存: {args.output}")
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
