#!/usr/bin/env python3
"""Build simple SVG charts from summary CSVs without GUI/font dependencies."""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    summary = ROOT / "outputs" / "summaries" / "model_summary.csv"
    evaluator_summary = ROOT / "outputs" / "summaries" / "evaluator_summary.csv"
    if not summary.exists():
        print("Run analysis/summarize.py first.")
        return 1

    model_rows = list(csv.DictReader(summary.open(encoding="utf-8")))
    if not model_rows:
        print("No summary rows found.")
        return 1

    out_dir = ROOT / "outputs" / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = [
        (
            out_dir / "latency_seconds.svg",
            _bar_chart(
                rows=model_rows,
                value_key="avg_latency_seconds",
                title="Average latency by model",
                y_label="Seconds",
                max_value=None,
            ),
        ),
        (
            out_dir / "tool_calls.svg",
            _bar_chart(
                rows=model_rows,
                value_key="avg_tool_calls",
                title="Average tool calls by model",
                y_label="Tool calls",
                max_value=None,
            ),
        ),
        (
            out_dir / "tool_adherence_proxy.svg",
            _bar_chart(
                rows=model_rows,
                value_key="tool_adherence_proxy",
                title="Tool adherence proxy by model",
                y_label="Proxy score",
                max_value=1.0,
            ),
        ),
    ]

    if evaluator_summary.exists():
        evaluator_rows = list(csv.DictReader(evaluator_summary.open(encoding="utf-8")))
        outputs.append(
            (
                out_dir / "correctness_score.svg",
                _bar_chart(
                    rows=evaluator_rows,
                    value_key="avg_correctness",
                    title="Average correctness score by model",
                    y_label="Score",
                    max_value=1.0,
                    label_fields=("model",),
                ),
            )
        )

    for path, svg in outputs:
        path.write_text(svg, encoding="utf-8")
        print(f"Wrote {path}")
    return 0


def _bar_chart(
    rows: list[dict[str, str]],
    value_key: str,
    title: str,
    y_label: str,
    max_value: float | None,
    label_fields: tuple[str, ...] = ("family", "version"),
) -> str:
    width = 920
    height = 480
    margin_left = 80
    margin_bottom = 95
    margin_top = 55
    plot_w = width - margin_left - 40
    plot_h = height - margin_top - margin_bottom
    bar_gap = 18
    bar_w = (plot_w - bar_gap * (len(rows) - 1)) / len(rows)
    values = [float(row[value_key]) for row in rows]
    chart_max = max_value or max(values) * 1.15 or 1.0

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="80" y="32" font-family="Arial, sans-serif" font-size="22" font-weight="700">{title}</text>',
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_h}" stroke="#222" stroke-width="1"/>',
        f'<line x1="{margin_left}" y1="{margin_top + plot_h}" x2="{margin_left + plot_w}" y2="{margin_top + plot_h}" stroke="#222" stroke-width="1"/>',
    ]

    for tick in _ticks(chart_max):
        y = margin_top + plot_h - (tick / chart_max) * plot_h
        parts.append(f'<line x1="{margin_left - 5}" y1="{y:.1f}" x2="{margin_left + plot_w}" y2="{y:.1f}" stroke="#ddd" stroke-width="1"/>')
        parts.append(f'<text x="35" y="{y + 5:.1f}" font-family="Arial, sans-serif" font-size="13">{tick:.2g}</text>')

    colors = ["#2878b5", "#62a8d2", "#7f7f7f", "#e07a5f", "#f2cc8f"]
    for i, row in enumerate(rows):
        score = float(row[value_key])
        x = margin_left + i * (bar_w + bar_gap)
        bar_h = (score / chart_max) * plot_h
        y = margin_top + plot_h - bar_h
        label = " ".join(row[field] for field in label_fields)
        color = colors[i % len(colors)]
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" fill="{color}"/>')
        parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 8:.1f}" text-anchor="middle" font-family="Arial, sans-serif" font-size="13">{score:.2f}</text>')
        parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{margin_top + plot_h + 28}" text-anchor="middle" font-family="Arial, sans-serif" font-size="13">{label}</text>')

    parts.append(f'<text x="18" y="250" transform="rotate(-90 18 250)" text-anchor="middle" font-family="Arial, sans-serif" font-size="14">{y_label}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _ticks(max_value: float) -> list[float]:
    if max_value <= 1.0:
        return [0, 0.25, 0.5, 0.75, 1.0]
    step = max(1.0, round(max_value / 4, 1))
    return [round(step * i, 1) for i in range(5)]


if __name__ == "__main__":
    raise SystemExit(main())
