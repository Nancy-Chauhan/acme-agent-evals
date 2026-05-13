#!/usr/bin/env python3
"""Summarize local harness JSONL outputs."""

from __future__ import annotations

import json
import argparse
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "outputs" / "raw"
SUMMARY_DIR = ROOT / "outputs" / "summaries"


def main() -> int:
    args = _parse_args()
    records = _load_records(args.input)
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    if not records:
        print("No local result records found.")
        return 0

    rows = _summarize(records)
    out = args.output or SUMMARY_DIR / "model_summary.csv"
    _write_csv(out, rows)
    print(f"Wrote {out}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        nargs="+",
        help="Specific results JSONL to summarize. Defaults to all outputs/raw/results-*.jsonl.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="CSV output path. Defaults to outputs/summaries/model_summary.csv.",
    )
    return parser.parse_args()


def _load_records(input_path: list[Path] | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    paths = input_path if input_path else sorted(RAW_DIR.glob("results-*.jsonl"))
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            records.append(json.loads(line))
    return records


def _summarize(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault((record.get("family", ""), record.get("version", "")), []).append(record)

    rows: list[dict[str, Any]] = []
    for (family, version), items in sorted(grouped.items()):
        errored = [item for item in items if item.get("error")]
        rows.append(
            {
                "family": family,
                "version": version,
                "model": items[0].get("model", ""),
                "runs": len(items),
                "error_rate": len(errored) / max(len(items), 1),
                "avg_latency_seconds": _avg(items, "latency_seconds"),
                "avg_tool_calls": _avg(items, "tool_calls"),
                "avg_output_chars": mean(len(str(item.get("output", ""))) for item in items),
                "tool_adherence_proxy": _tool_adherence_proxy(items),
            }
        )
    return rows


def _avg(items: list[dict[str, Any]], key: str) -> float:
    values = [float(item.get(key) or 0) for item in items]
    return round(mean(values), 3) if values else 0.0


def _tool_adherence_proxy(items: list[dict[str, Any]]) -> float:
    scores = []
    for item in items:
        tools = [str(tool).lower() for tool in item.get("tool_names", [])]
        commands = [str(cmd).lower() for cmd in item.get("tool_commands", [])]
        if not tools:
            scores.append(0.0)
            continue
        score = 0.4
        if any("bash" in tool or "shell" in tool for tool in tools):
            score += 0.3
        if any("gh " in cmd or cmd.startswith("gh") or "git " in cmd for cmd in commands):
            score += 0.3
        scores.append(score)
    return round(mean(scores), 3) if scores else 0.0


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    headers = list(rows[0].keys())
    with path.open("w", encoding="utf-8") as f:
        f.write(",".join(headers) + "\n")
        for row in rows:
            f.write(",".join(_csv_cell(row[h]) for h in headers) + "\n")


def _csv_cell(value: Any) -> str:
    text = str(value)
    if "," in text or '"' in text:
        text = '"' + text.replace('"', '""') + '"'
    return text


if __name__ == "__main__":
    raise SystemExit(main())
