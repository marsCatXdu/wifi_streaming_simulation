#!/usr/bin/env python3
"""End-to-end checks for mixed overlapping BSS contention."""

import csv
import json
import pathlib
import shutil
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[3]


def run(
    output: pathlib.Path, topology: str, run_number: int
) -> tuple[dict, dict, list[dict], list[dict]]:
    policy = "full_duplication" if topology == "dual_interface" else "fixed_link_0"
    subprocess.run(
        [
            str(ROOT / "ns3"),
            "run",
            "streaming-experiment",
            "--no-build",
            "--",
            f"--topology={topology}",
            f"--policy={policy}",
            "--duration=0.5",
            "--fps=10",
            "--frameSize=1200",
            "--propagationModel=log_distance_nakagami",
            "--obssProfile=mixed4x4",
            "--obssStationsPerBss=4",
            "--obssUlMinRateMbps=0.5",
            "--obssUlMaxRateMbps=3",
            "--obssDlMinRateMbps=2",
            "--obssDlMaxRateMbps=8",
            "--obssStationManager=minstrel_ht",
            "--obssOnMeanMs=20",
            "--obssOffMeanMs=20",
            "--seed=19",
            f"--run={run_number}",
            f"--outputDir={output}",
        ],
        cwd=ROOT,
        check=True,
    )
    config = json.loads((output / "resolved_config.json").read_text())
    summary = json.loads((output / "summary.json").read_text())
    with (output / "background_flows.csv").open(newline="") as source:
        flows = list(csv.DictReader(source))
    with (output / "background_rate_periods.csv").open(newline="") as source:
        periods = list(csv.DictReader(source))
    return config, summary, flows, periods


def period_signature(periods: list[dict]) -> list[tuple]:
    return [
        (
            row["bss_id"],
            row["sta_index"],
            row["direction"],
            row["period_index"],
            row["start_us"],
            row["end_us"],
            row["rate_mbps"],
        )
        for row in periods
    ]


def main() -> None:
    temporary = pathlib.Path(tempfile.mkdtemp(prefix="wifi-streaming-obss-"))
    try:
        dual_config, dual_summary, dual_flows, dual_periods = run(
            temporary / "dual-a", "dual_interface", 3
        )
        _, _, repeated_flows, repeated_periods = run(
            temporary / "dual-b", "dual_interface", 3
        )
        _, _, _, varied_periods = run(temporary / "dual-c", "dual_interface", 4)
        mlo_config, mlo_summary, mlo_flows, mlo_periods = run(
            temporary / "mlo", "mlo_str", 3
        )

        obss = dual_config["background"]["obss"]
        assert obss["profile"] == "mixed4x4"
        assert obss["stations_per_bss"] == 4
        assert obss["station_manager"] == "minstrel_ht"
        assert obss["use_latest_amendment_only"] is True
        assert len(obss["bsses"]) == 4
        assert [(item["standard"], item["link_id"]) for item in obss["bsses"]] == [
            ("802.11n", 0),
            ("802.11ax", 0),
            ("802.11ac", 1),
            ("802.11be", 1),
        ]
        assert len(dual_flows) == len(mlo_flows) == 32
        assert {row["direction"] for row in dual_flows} == {"uplink", "downlink"}
        assert len({int(row[key]) for row in dual_flows
                    for key in ("rate_stream", "on_stream", "off_stream")}) == 96
        assert all(int(row["bytes_sent"]) > 0 for row in dual_flows)
        assert all(int(row["bytes_received"]) > 0 for row in dual_flows)
        assert all(
            (0.5 <= float(row["rate_mbps"]) <= 3)
            if row["direction"] == "uplink"
            else (2 <= float(row["rate_mbps"]) <= 8)
            for row in dual_periods
        )
        assert all(value > 0 for value in dual_summary["background_bytes_received_per_link"])
        assert all(value > 0 for value in mlo_summary["background_bytes_received_per_link"])

        assert dual_config["background"]["obss"] == mlo_config["background"]["obss"]
        assert period_signature(dual_periods) == period_signature(mlo_periods)
        assert period_signature(dual_periods) == period_signature(repeated_periods)
        assert period_signature(dual_periods) != period_signature(varied_periods)
        assert [
            (row["rate_stream"], row["on_stream"], row["off_stream"])
            for row in dual_flows
        ] == [
            (row["rate_stream"], row["on_stream"], row["off_stream"])
            for row in repeated_flows
        ]
        print(
            "PASS mixed4x4 OBSS: four standards, bidirectional random-rate traffic, "
            "reproducible streams, and matched dual/MLO inputs"
        )
    finally:
        shutil.rmtree(temporary)


if __name__ == "__main__":
    main()
