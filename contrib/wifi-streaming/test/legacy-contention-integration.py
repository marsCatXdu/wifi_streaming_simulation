#!/usr/bin/env python3
"""End-to-end checks for the fair legacy_mixed8 contention profile."""

import json
import pathlib
import shutil
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[3]
EXPECTED_2G = [
    "802.11n", "802.11ax", "802.11be", "802.11n",
    "802.11ax", "802.11be", "802.11n", "802.11ax",
]
EXPECTED_5G = [
    "802.11n", "802.11ac", "802.11ax", "802.11be",
    "802.11n", "802.11ac", "802.11ax", "802.11be",
]


def run(output: pathlib.Path, topology: str, run_number: int) -> tuple[dict, dict]:
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
            "--duration=0.3",
            "--fps=10",
            "--frameSize=1200",
            "--backgroundProfile=legacy_mixed8",
            "--backgroundRateMbps=1",
            "--randomOnMeanMs=50",
            "--randomOffMeanMs=50",
            "--backgroundStreamBase=4000",
            "--seed=19",
            f"--run={run_number}",
            f"--outputDir={output}",
        ],
        cwd=ROOT,
        check=True,
    )
    return (
        json.loads((output / "resolved_config.json").read_text()),
        json.loads((output / "summary.json").read_text()),
    )


def check_metadata(config: dict, summary: dict, association: str) -> None:
    background = config["background"]
    assert background["profile"] == "legacy_mixed8"
    assert background["traffic"] == "udp_random_onoff"
    assert background["direction"] == "uplink"
    assert background["stations_per_link"] == [8, 8]
    assert background["station_standards_per_link"] == [EXPECTED_2G, EXPECTED_5G]
    assert "802.11ac" not in background["station_standards_per_link"][0]
    assert set(background["station_standards_per_link"][0]) == {
        "802.11n", "802.11ax", "802.11be",
    }
    assert set(background["station_standards_per_link"][1]) == {
        "802.11n", "802.11ac", "802.11ax", "802.11be",
    }
    assert background["random_stream_base"] == 4000
    assert background["application_streams"] == list(range(4000, 4032, 2))
    assert background["random_on_mean_ms"] == 50
    assert background["random_off_mean_ms"] == 50
    assert background["association_mode"] == association
    assert config["wifi"]["standard"] == "802.11be"
    assert config["wifi"]["data_modes_per_link"] == ["EhtMcs5", "EhtMcs5"]
    assert all(value > 0 for value in summary["background_bytes_sent_per_link"])
    assert all(value > 0 for value in summary["background_bytes_received_per_link"])
    assert len(summary["background_bytes_sent_per_station"]) == 16


def main() -> None:
    temporary = pathlib.Path(tempfile.mkdtemp(prefix="wifi-streaming-legacy-mixed8-"))
    try:
        dual_config, dual_summary = run(temporary / "dual-a", "dual_interface", 3)
        _, repeated_summary = run(temporary / "dual-b", "dual_interface", 3)
        _, varied_summary = run(temporary / "dual-c", "dual_interface", 4)
        mlo_config, mlo_summary = run(temporary / "mlo", "mlo_str", 3)

        check_metadata(dual_config, dual_summary, "passive_scan")
        check_metadata(mlo_config, mlo_summary, "passive_scan_to_ap_mld_link")
        assert dual_config["wifi"]["channel_settings"] == mlo_config["wifi"]["channel_settings"]
        assert (
            dual_config["background"]["station_standards_per_link"]
            == mlo_config["background"]["station_standards_per_link"]
        )
        assert (
            dual_summary["background_bytes_sent_per_station"]
            == repeated_summary["background_bytes_sent_per_station"]
        )
        assert (
            dual_summary["background_bytes_sent_per_station"]
            != varied_summary["background_bytes_sent_per_station"]
        )

        print(
            "PASS legacy_mixed8: mixed standards, both-band traffic, "
            "repeatable streams, run variability, and matched dual/MLO contenders"
        )
    finally:
        shutil.rmtree(temporary)


if __name__ == "__main__":
    main()
