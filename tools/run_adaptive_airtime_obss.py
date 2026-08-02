#!/usr/bin/env python3
"""Build, run, validate, and plot the adaptive-airtime OBSS matrix."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "experiments/configs/closed_loop_adaptive_airtime_obss.yaml"
OUTPUT = ROOT / "results/adaptive_airtime_obss_v1/runs"


def main() -> int:
    workers = max(1, os.cpu_count() or 1)
    subprocess.run(
        [str(ROOT / "ns3"), "build", "streaming-experiment"],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "tools/run_experiments.py",
            str(CONFIG),
            "--workers",
            str(workers),
            "--output-root",
            str(OUTPUT),
            "--no-build",
            "--resume",
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "tools/plot_adaptive_airtime_duplication.py",
            str(OUTPUT),
        ],
        cwd=ROOT,
        check=True,
    )
    print(f"ADAPTIVE_AIRTIME_OBSS_DONE {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
