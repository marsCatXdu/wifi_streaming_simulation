#!/usr/bin/env python3
"""Controlled end-to-end check that offered background load harms streaming."""

import csv
import json
import pathlib
import shutil
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[3]


def run(output: pathlib.Path, extra: list[str]) -> tuple[dict, float]:
    arguments = [
        "--topology=dual_interface",
        "--policy=fixed_link_0",
        "--duration=2",
        "--fps=60",
        "--frameSize=40000",
        "--deadlineUs=33333",
        f"--outputDir={output}",
        *extra,
    ]
    subprocess.run(
        [str(ROOT / "ns3"), "run", "streaming-experiment", "--", *arguments],
        cwd=ROOT,
        check=True,
    )
    summary = json.loads((output / "summary.json").read_text())
    with (output / "mac_summary.csv").open(newline="") as source:
        link0 = next(row for row in csv.DictReader(source) if row["link_id"] == "0")
    service_p95 = float(link0["p95_mpdu_service_time_us"])
    return summary, service_p95


def main() -> None:
    temporary = pathlib.Path(tempfile.mkdtemp(prefix="wifi-streaming-load-test-"))
    try:
        baseline, baseline_service = run(temporary / "baseline", [])
        loaded, loaded_service = run(
            temporary / "loaded",
            [
                "--backgroundTraffic=udp_constant",
                "--backgroundDirection=mixed",
                "--backgroundStations0=4",
                "--backgroundStations1=4",
                "--backgroundRateMbps=30",
            ],
        )
        latency_worse = (
            loaded["latency_p95_us"] is not None
            and baseline["latency_p95_us"] is not None
            and loaded["latency_p95_us"] > baseline["latency_p95_us"]
        )
        delivery_worse = (
            loaded["deadline_miss_ratio"] > baseline["deadline_miss_ratio"]
            or loaded["incomplete_ratio"] > baseline["incomplete_ratio"]
        )
        assert latency_worse or delivery_worse, (
            "added offered load did not worsen latency or delivery: "
            f"baseline={baseline}, loaded={loaded}"
        )
        assert loaded_service > baseline_service, (
            "added offered load did not increase streaming-link MPDU service time: "
            f"{baseline_service} -> {loaded_service}"
        )
        heterogeneous_output = temporary / "heterogeneous-uplink"
        heterogeneous, _ = run(
            heterogeneous_output,
            [
                "--wifiStandard=eht",
                "--backgroundTraffic=udp_constant",
                "--backgroundDirection=uplink",
                "--backgroundStations0=1",
                "--backgroundStations1=1",
                "--backgroundStandard0=he",
                "--backgroundStandard1=vht",
                "--backgroundRateMbps=5",
            ],
        )
        resolved = json.loads((heterogeneous_output / "resolved_config.json").read_text())
        assert resolved["background"]["standards_per_link"] == [
            "802.11ax",
            "802.11ac",
        ]
        assert heterogeneous["background_throughput_mbps"] > 9.5, (
            "both 5 Mbps heterogeneous uplinks were not delivered: "
            f"{heterogeneous['background_throughput_mbps']} Mbps"
        )
        print(
            "PASS background degradation: "
            f"latency_p95_us={baseline['latency_p95_us']}->{loaded['latency_p95_us']}, "
            f"service_p95_us={baseline_service}->{loaded_service}"
        )
        print(
            "PASS heterogeneous uplink: HE/2.4 GHz + VHT/5 GHz with EHT AP, "
            f"throughput_mbps={heterogeneous['background_throughput_mbps']}"
        )
    finally:
        shutil.rmtree(temporary)


if __name__ == "__main__":
    main()
