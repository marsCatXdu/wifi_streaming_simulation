#!/usr/bin/env python3
"""Verify that passive secondary-airtime metering does not change selection."""

from __future__ import annotations

import csv
import pathlib
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
BEHAVIOR_FILES = (
    "frames.csv",
    "policy_decisions.csv",
    "selective_duplication_decisions.csv",
)


def normalized_csv(path: pathlib.Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a CSV while removing the intentionally different run identity."""
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise AssertionError(f"{path}: missing header")
        rows = []
        for row in reader:
            row.pop("run_id", None)
            rows.append(row)
        return [name for name in reader.fieldnames if name != "run_id"], rows


def run(output: pathlib.Path, meter_enabled: bool) -> None:
    subprocess.run(
        [
            str(ROOT / "ns3"),
            "run",
            "streaming-experiment",
            "--no-build",
            "--",
            "--topology=dual_interface",
            "--policy=selective_duplication",
            "--duration=0.3",
            "--fps=10",
            "--frameSize=2400",
            "--gopLength=60",
            "--payloadSize=1200",
            "--deadlineUs=33333",
            "--emissionMode=burst",
            "--predictionTelemetryEnabled=1",
            "--predictionSampleOffsetsUs=0,1000,2000,4000",
            "--predictionHistoryWindowsUs=1000,5000,20000",
            "--predictionPollingIntervalUs=1000",
            "--predictionPollingReportDelayUs=1000",
            "--selectiveDuplicationThreshold=0",
            "--selectiveDuplicationFrameBudget=0.3",
            "--selectiveDuplicationBurstHorizonFrames=10",
            "--selectiveDuplicationDecisionOffsetsUs=0,1000,2000,4000",
            f"--secondaryAirtimeMeterEnabled={int(meter_enabled)}",
            "--seed=23",
            "--run=7",
            f"--runId=meter-{'on' if meter_enabled else 'off'}",
            f"--outputDir={output}",
        ],
        cwd=ROOT,
        check=True,
    )


def main() -> None:
    temporary = pathlib.Path(tempfile.mkdtemp(prefix="wifi-streaming-meter-regression-"))
    try:
        disabled = temporary / "disabled"
        enabled = temporary / "enabled"
        run(disabled, False)
        run(enabled, True)
        for name in BEHAVIOR_FILES:
            left = normalized_csv(disabled / name)
            right = normalized_csv(enabled / name)
            if left != right:
                raise AssertionError(f"passive meter changed {name}")
        required_meter_files = {
            "secondary_airtime_events.csv",
            "secondary_airtime_settlements.csv",
            "secondary_airtime_summary.json",
        }
        missing = [name for name in required_meter_files if not (enabled / name).is_file()]
        if missing:
            raise AssertionError(f"meter-enabled run lacks {', '.join(sorted(missing))}")
        leaked = [name for name in required_meter_files if (disabled / name).exists()]
        if leaked:
            raise AssertionError(f"meter-disabled run wrote {', '.join(sorted(leaked))}")
        print("PASS selective duplication is identical with passive meter off/on")
    finally:
        shutil.rmtree(temporary)


if __name__ == "__main__":
    main()
