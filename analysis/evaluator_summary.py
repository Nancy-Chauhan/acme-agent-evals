#!/usr/bin/env python3
"""Summarize exported Arize evaluator CSVs by model."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from statistics import mean, stdev
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = ROOT / "outputs" / "experiments"
SUMMARY_DIR = ROOT / "outputs" / "summaries"
SCORE_COLUMNS = [
    "eval.correctness.score",
    "eval.output_quality.score",
    "eval.efficiency.score",
    "eval.latency.score",
    "eval.tool_adherence.score",
]
FILENAME_RE = re.compile(
    r"(?:pi-harness|raw-baseline)-(?:read|write-[^-]+)-(.+)-(pi|raw)-run\d+\.csv$"
)


def main() -> int:
    args = _parse_args()
    paths = sorted(args.input_dir.glob(f"{args.prefix}*.csv"))
    if not paths:
        print(f"No experiment CSVs found in {args.input_dir}")
        return 0

    rows = _summarize_by_model(paths)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(args.output, rows)
    print(f"Wrote {args.output}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=EXPERIMENT_DIR)
    parser.add_argument("--prefix", default="pi-harness-read-")
    parser.add_argument(
        "--output",
        type=Path,
        default=SUMMARY_DIR / "evaluator_summary.csv",
    )
    return parser.parse_args()


def _summarize_by_model(paths: list[Path]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    experiments: dict[str, int] = {}
    for path in paths:
        model_name = _model_from_filename(path.name)
        grouped.setdefault(model_name, []).extend(
            csv.DictReader(path.open(encoding="utf-8"))
        )
        experiments[model_name] = experiments.get(model_name, 0) + 1

    rows: list[dict[str, Any]] = []
    for model_name, records in sorted(grouped.items()):
        row: dict[str, Any] = {
            "model": model_name,
            "experiments": experiments[model_name],
            "runs": len(records),
            "errors": sum(1 for record in records if record.get("error")),
        }
        for column in SCORE_COLUMNS:
            key = column.removeprefix("eval.").removesuffix(".score")
            values = _score_values(records, column)
            row[f"avg_{key}"] = _mean(values)
            row[f"stdev_{key}"] = _stdev(values)
        rows.append(row)
    return rows


def _score_values(records: list[dict[str, str]], column: str) -> list[float]:
    return [
        float(record[column])
        for record in records
        if record.get(column) not in ("", None)
    ]


def _mean(values: list[float]) -> float:
    return round(mean(values), 3) if values else 0.0


def _stdev(values: list[float]) -> float:
    return round(stdev(values), 3) if len(values) > 1 else 0.0


def _model_from_filename(name: str) -> str:
    match = FILENAME_RE.match(name)
    return match.group(1) if match else name.removesuffix(".csv")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    headers = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
