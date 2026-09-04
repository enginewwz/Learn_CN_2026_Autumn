#!/usr/bin/env python3
"""Plot empirical or smoothed CDF curves of latency inflation over c-latency."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path


SERIES = [
    ("ping_ms", "Ping RTT", "#1597e5"),
    ("dns_ms", "DNS lookup", "#111111"),
    ("tcp_transfer_ms", "TCP data transfer", "#9e9e9e"),
    ("total_time_ms", "Total HTTP time", "#f28e1c"),
]


def number(value: str | None) -> float | None:
    try:
        result = float(value or "")
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def ecdf_points(
    values: list[float], x_min: float = 1.0, x_max: float = 1000.0
) -> tuple[list[float], list[float]]:
    """Build ECDF step points, clipping only for display at the axis edges."""
    if not values:
        return [], []
    clipped = sorted(min(x_max, max(x_min, value)) for value in values)
    count = len(clipped)
    x_values = [x_min, *clipped, x_max]
    y_values = [0.0, *((index + 1) / count for index in range(count)), 1.0]
    return x_values, y_values


def smooth_cdf_points(
    values: list[float],
    x_min: float = 1.0,
    x_max: float = 1000.0,
    *,
    bandwidth: float | None = None,
    point_count: int = 600,
) -> tuple[list[float], list[float], float]:
    """Estimate a smooth CDF with SciPy Gaussian KDE in log10(x) space.

    ``bandwidth`` is the ``scipy.stats.gaussian_kde`` covariance factor.
    Non-positive samples are retained as probability mass below the visible
    logarithmic axis.
    """
    if not values:
        return [], [], 0.0
    if bandwidth is not None and bandwidth <= 0:
        raise ValueError("bandwidth 必须大于 0")
    try:
        from scipy.stats import gaussian_kde, norm
    except ImportError as exc:
        raise ImportError(
            "平滑模式需要 scipy: conda install -c conda-forge scipy"
        ) from exc

    log_values = sorted(math.log10(value) for value in values if value > 0)
    log_min = math.log10(x_min)
    log_max = math.log10(x_max)
    log_grid = [
        log_min + (log_max - log_min) * index / (point_count - 1)
        for index in range(point_count)
    ]
    non_positive_count = len(values) - len(log_values)
    sample_count = len(values)

    if not log_values:
        y_values = [1.0] * point_count
        bandwidth_used = bandwidth or 0.0
    elif len(log_values) == 1 or log_values[0] == log_values[-1]:
        # gaussian_kde needs multiple samples with non-zero variance.
        kernel_width = bandwidth or 0.08
        y_values = [
            (
                non_positive_count
                + len(log_values)
                * float(norm.cdf((grid_value - log_values[0]) / kernel_width))
            )
            / sample_count
            for grid_value in log_grid
        ]
        bandwidth_used = kernel_width
    else:
        kde = gaussian_kde(log_values, bw_method=bandwidth)
        y_values = [
            (
                non_positive_count
                + len(log_values) * kde.integrate_box_1d(-math.inf, grid_value)
            )
            / sample_count
            for grid_value in log_grid
        ]
        bandwidth_used = float(kde.factor)

    return [10**value for value in log_grid], y_values, bandwidth_used


def plot_results(
    csv_path: Path,
    output_path: Path,
    *,
    x_min: float = 1.0,
    x_max: float = 1000.0,
    smooth: bool = False,
    bandwidth: float | None = None,
) -> None:
    if x_min <= 0 or x_max <= x_min:
        raise ValueError("CDF 横轴必须满足 0 < x_min < x_max")
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ImportError("请安装 matplotlib: python -m pip install matplotlib") from exc

    with csv_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError(f"CSV 中没有数据: {csv_path}")

    fig, axis = plt.subplots(figsize=(10, 7), constrained_layout=True)
    curves_drawn = 0
    for column, label, color in SERIES:
        inflation: list[float] = []
        for row in rows:
            c_latency = number(row.get("c_latency_ms"))
            measured = number(row.get(column))
            if (
                c_latency is not None
                and measured is not None
                and c_latency > 0
                and measured >= 0
            ):
                inflation.append(measured / c_latency)
        if smooth:
            x_values, y_values, _ = smooth_cdf_points(
                inflation, x_min, x_max, bandwidth=bandwidth
            )
        else:
            x_values, y_values = ecdf_points(inflation, x_min, x_max)
        if x_values:
            median = statistics.median(inflation)
            plot_options = {
                "color": color,
                "linewidth": 2.5,
                "label": f"{label}: median {median:.1f}x (n={len(inflation)})",
            }
            if smooth:
                axis.plot(x_values, y_values, **plot_options)
            else:
                axis.step(x_values, y_values, where="post", **plot_options)
            curves_drawn += 1

    if not curves_drawn:
        axis.text(
            0.5,
            0.5,
            "No valid samples",
            ha="center",
            va="center",
            transform=axis.transAxes,
        )
    else:
        axis.legend(fontsize=9, loc="lower right")
    axis.set_xscale("log")
    axis.set_xlim(x_min, x_max)
    axis.set_ylim(0, 1.01)
    axis.set_xlabel("Inflation over c-latency (measured latency / c-latency)")
    axis.set_ylabel("CDF")
    title_prefix = "Smoothed CDF" if smooth else "CDF"
    axis.set_title(f"{title_prefix} of website latency inflation over c-latency")
    axis.grid(which="major", alpha=0.35, linestyle="--")
    axis.grid(which="minor", axis="x", alpha=0.15, linestyle=":")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output", type=Path, default=Path("latency_inflation_cdf.png"))
    parser.add_argument("--x-min", type=float, default=1.0)
    parser.add_argument("--x-max", type=float, default=1000.0)
    parser.add_argument(
        "--smooth",
        action="store_true",
        help="draw a Gaussian-kernel CDF in log10 space instead of an ECDF",
    )
    parser.add_argument(
        "--bandwidth",
        type=float,
        help="scipy gaussian_kde bandwidth factor (default: Scott's rule)",
    )
    args = parser.parse_args()
    plot_results(
        args.csv,
        args.output,
        x_min=args.x_min,
        x_max=args.x_max,
        smooth=args.smooth,
        bandwidth=args.bandwidth,
    )
    print(f"图已保存: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
