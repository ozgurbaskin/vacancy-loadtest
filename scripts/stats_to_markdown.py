#!/usr/bin/env python3
"""Render a Locust ``*_stats.csv`` (from ``--csv``) as a Markdown table, for the README.

uv run python scripts/stats_to_markdown.py reports/run_stats.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path


def render(path: Path) -> str:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    lines = [
        "| Type | Name | Requests | Failures | Median (ms) | 95% (ms) | Max (ms) | Req/s |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['Type']} | {row['Name']} | {row['Request Count']} | {row['Failure Count']} "
            f"| {float(row['Median Response Time']):.0f} | {row['95%']} "
            f"| {float(row['Max Response Time']):.0f} | {float(row['Requests/s']):.2f} |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    print(render(Path(sys.argv[1] if len(sys.argv) > 1 else "reports/run_stats.csv")))
