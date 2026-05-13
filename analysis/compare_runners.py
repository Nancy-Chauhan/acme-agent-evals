#!/usr/bin/env python3
"""Compare pi.dev harness runs against raw baseline runs."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = ROOT / "outputs" / "experiments"
SUMMARY_DIR = ROOT / "outputs" / "summaries"
FILENAME_RE = re.compile(
    r"(?P<prefix>pi-harness|raw-baseline)-(?:read|write-[^-]+)-(?P<model>.+)-(?P<runner>pi|raw)-run\d+\.csv$"
)


def main() -> int:
    args = _parse_args()
    paths = sorted(args.input_dir.glob("*.csv"))
    rows = _summarize(paths)
    if not rows:
        print(f"No matching pi-harness/raw-baseline experiment CSVs found in {args.input_dir}")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(args.output, rows)
    print(f"Wrote {args.output}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=EXPERIMENT_DIR)
    parser.add_argument(
        "--output",
        type=Path,
        default=SUMMARY_DIR / "runner_comparison.csv",
    )
    return parser.parse_args()


def _summarize(paths: list[Path]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    experiment_counts: dict[tuple[str, str], int] = {}
    for path in paths:
        match = FILENAME_RE.match(path.name)
        if not match:
            continue
        key = (match.group("model"), match.group("runner"))
        grouped.setdefault(key, []).extend(csv.DictReader(path.open(encoding="utf-8")))
        experiment_counts[key] = experiment_counts.get(key, 0) + 1

    rows: list[dict[str, Any]] = []
    for (model, runner), records in sorted(grouped.items()):
        outputs = [_parse_output(record.get("output", "")) for record in records]
        rows.append(
            {
                "model": model,
                "runner": runner,
                "experiments": experiment_counts[(model, runner)],
                "rows": len(records),
                "avg_correctness": _avg_score(records, "eval.correctness.score"),
                "avg_output_quality": _avg_score(records, "eval.output_quality.score"),
                "avg_efficiency": _avg_score(records, "eval.efficiency.score"),
                "avg_latency_score": _avg_score(records, "eval.latency.score"),
                "avg_tool_adherence": _avg_score(records, "eval.tool_adherence.score"),
                "avg_latency_seconds": _avg_output_value(outputs, "latency_seconds"),
                "avg_tool_calls": _avg_output_value(outputs, "tool_calls"),
                "error_rows": sum(1 for output in outputs if output.get("error")),
            }
        )
    return rows


def _parse_output(value: str) -> dict[str, Any]:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}


def _avg_score(records: list[dict[str, str]], column: str) -> float:
    values = [float(record[column]) for record in records if record.get(column) not in ("", None)]
    return round(mean(values), 3) if values else 0.0


def _avg_output_value(outputs: list[dict[str, Any]], key: str) -> float:
    values = [float(output.get(key) or 0) for output in outputs if key in output]
    return round(mean(values), 3) if values else 0.0


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    headers = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
