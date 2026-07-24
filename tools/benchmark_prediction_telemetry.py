#!/usr/bin/env python3
"""Benchmark disabled, sampled, and event-logged prediction telemetry."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import resource
import shutil
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

from validate_outputs import validate_run

ROOT = Path(__file__).resolve().parents[1]
NS3_UPSTREAM_COMMIT = "d2add90b452d600cfb4859baed8e9ea633519447"
PROFILES = {
    "disabled": {
        "predictionTelemetryEnabled": "false",
        "predictionEventLogEnabled": "false",
    },
    "samples": {
        "predictionTelemetryEnabled": "true",
        "predictionEventLogEnabled": "false",
    },
    "samples_events": {
        "predictionTelemetryEnabled": "true",
        "predictionEventLogEnabled": "true",
    },
}


def _execute(command: list[str], log_path: str,
             result: multiprocessing.Queue[dict[str, Any]]) -> None:
    start = time.perf_counter()
    with Path(log_path).open("w", encoding="utf-8") as log:
        process = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                 check=False)
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    result.put({
        "returncode": process.returncode,
        "wall_time_s": time.perf_counter() - start,
        "peak_rss_kib": int(usage.ru_maxrss),
    })


def _row_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8") as source:
        return max(sum(1 for _ in source) - 1, 0)


def _run_profile(profile: str, repetition: int, output_root: Path,
                 project_commit: str, duration: float, seed: int) -> dict[str, Any]:
    run_id = f"prediction-benchmark-{profile}-{repetition}"
    run_dir = output_root / run_id
    if run_dir.exists():
        raise FileExistsError(f"benchmark run already exists: {run_dir}")
    temporary_log = output_root / f".{run_id}.stdout.tmp"
    arguments = {
        "outputDir": str(run_dir),
        "runId": run_id,
        "projectGitCommit": project_commit,
        "seed": str(seed),
        "run": "1",
        "topology": "dual_interface",
        "policy": "fixed_link_0",
        "duration": str(duration),
        "fps": "30",
        "frameSize": "12000",
        "gopLength": "60",
        "keyframeSizeMultiplier": "4",
        "payloadSize": "1200",
        "deadlineUs": "33333",
        "wifiStandard": "eht",
        "maxAmsduSize": "0",
        "fragmentationThreshold": "65535",
        "ulOfdmaEnabled": "false",
        "backgroundTraffic": "none",
        "predictionSampleOffsetsUs": "0,1000,2000,4000",
        "predictionHistoryWindowsUs": "1000,5000,20000",
        "predictionOracleFeaturesEnabled": "false",
        **PROFILES[profile],
    }
    command = [
        str(ROOT / "ns3"), "run", "streaming-experiment", "--no-build", "--",
        *(f"--{key}={value}" for key, value in arguments.items()),
    ]
    context = multiprocessing.get_context("fork")
    result_queue: multiprocessing.Queue[dict[str, Any]] = context.Queue()
    process = context.Process(
        target=_execute, args=(command, str(temporary_log), result_queue)
    )
    process.start()
    process.join()
    if process.exitcode != 0:
        raise RuntimeError(f"benchmark worker failed for {run_id}")
    measurement = result_queue.get()
    if run_dir.is_dir():
        shutil.move(str(temporary_log), run_dir / "stdout.log")
    if measurement["returncode"] != 0:
        raise RuntimeError(f"simulation failed for {run_id}; see {run_dir}/stdout.log")
    validate_run(run_dir, run_id, project_commit, NS3_UPSTREAM_COMMIT)
    output_bytes = sum(
        path.stat().st_size for path in run_dir.iterdir() if path.is_file()
    )
    return {
        "profile": profile,
        "repetition": repetition,
        "run_id": run_id,
        "wall_time_s": measurement["wall_time_s"],
        "peak_rss_kib": measurement["peak_rss_kib"],
        "output_bytes": output_bytes,
        "sample_rows": _row_count(run_dir / "prediction_samples.csv"),
        "event_rows": _row_count(run_dir / "prediction_events.csv"),
    }


def _median(rows: list[dict[str, Any]], field: str) -> float:
    return statistics.median(float(row[field]) for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--duration", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=29)
    args = parser.parse_args()
    if args.repetitions < 1 or args.duration <= 0 or args.seed < 1:
        parser.error("repetitions, duration, and seed must be positive")
    if args.output_root.exists() and any(args.output_root.iterdir()):
        parser.error("output root must be new or empty")
    args.output_root.mkdir(parents=True, exist_ok=True)

    subprocess.run([str(ROOT / "ns3"), "build", "streaming-experiment"],
                   cwd=ROOT, check=True)
    project_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    raw: list[dict[str, Any]] = []
    names = list(PROFILES)
    for repetition in range(args.repetitions):
        start = repetition % len(names)
        order = names[start:] + names[:start]
        for profile in order:
            measurement = _run_profile(
                profile, repetition, args.output_root, project_commit,
                args.duration, args.seed
            )
            raw.append(measurement)
            print(
                f"{profile} repetition={repetition} "
                f"wall={measurement['wall_time_s']:.6f}s"
            )

    summaries: dict[str, dict[str, Any]] = {}
    for profile in names:
        rows = [row for row in raw if row["profile"] == profile]
        summaries[profile] = {
            "median_wall_time_s": _median(rows, "wall_time_s"),
            "median_peak_rss_kib": _median(rows, "peak_rss_kib"),
            "median_output_bytes": _median(rows, "output_bytes"),
            "median_sample_rows": _median(rows, "sample_rows"),
            "median_event_rows": _median(rows, "event_rows"),
        }
    baseline = summaries["disabled"]["median_wall_time_s"]
    for profile, summary in summaries.items():
        summary["median_wall_time_overhead_ratio"] = (
            summary["median_wall_time_s"] / baseline - 1 if baseline else None
        )
    report = {
        "schema_version": 1,
        "method": "interleaved single-worker repetitions",
        "duration_s": args.duration,
        "seed": args.seed,
        "repetitions": args.repetitions,
        "project_commit": project_commit,
        "ns3_upstream_commit": NS3_UPSTREAM_COMMIT,
        "profiles": summaries,
        "measurements": raw,
    }
    report_path = args.output_root / "prediction_telemetry_benchmark.json"
    temporary = report_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, report_path)
    print(f"REPORT {report_path}")


if __name__ == "__main__":
    main()
