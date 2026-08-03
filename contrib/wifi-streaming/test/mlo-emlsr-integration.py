#!/usr/bin/env python3
"""Practical EMLSR profile activation and two-link activity smoke test."""

import csv
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))

from validate_outputs import ValidationError, validate_run


def main() -> None:
    temporary = pathlib.Path(tempfile.mkdtemp(prefix="wifi-streaming-mlo-emlsr-"))
    output = temporary / "run"
    try:
        completed = subprocess.run(
            [
                str(ROOT / "ns3"),
                "run",
                "streaming-experiment",
                "--",
                "--topology=mlo_emlsr",
                "--wifiStandard=eht",
                "--duration=1",
                "--fps=120",
                "--frameSize=50000",
                "--deadlineUs=100000",
                f"--outputDir={output}",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        (output / "stdout.log").write_text(completed.stdout, encoding="utf-8")

        resolved = json.loads((output / "resolved_config.json").read_text())
        wifi = resolved["wifi"]
        assert resolved["topology"] == "mlo_emlsr"
        assert wifi["multi_link_mode"] == "EMLSR"
        assert wifi["str_mode"] == "not_applicable"
        assert wifi["sta_max_inflights"] == 1
        assert wifi["application_socket_count"] == 1
        assert wifi["application_duplication"] is False
        assert wifi["emlsr"] == {
            "activated": True,
            "profile": "advanced_fixed_aux_v1",
            "manager": "ns3::AdvancedEmlsrManager",
            "link_ids": [0, 1],
            "main_phy_id": 1,
            "padding_delay_us": 128,
            "transition_delay_us": 128,
            "channel_switch_delay_us": 100,
            "switch_aux_phy": False,
            "aux_phy_tx_capable": False,
            "aux_phy_channel_width_mhz": 20,
            "put_aux_phy_to_sleep": False,
            "in_device_interference": False,
            "notify_mac_header_rx_end": True,
            "main_phy_frequency_ranges": [
                "WIFI_SPECTRUM_2_4_GHZ",
                "WIFI_SPECTRUM_5_GHZ",
            ],
        }

        runtime = json.loads((output / "mlo_runtime.json").read_text())
        assert runtime["mode"] == "EMLSR"
        assert runtime["profile"] == "advanced_fixed_aux_v1"
        assert runtime["emlsr_manager"] == "ns3::AdvancedEmlsrManager"
        assert runtime["emlsr_link_ids"] == [0, 1]
        assert runtime["ap_emlsr_enabled_per_link"] == [True, True]
        assert runtime["main_phy_id"] == 1
        assert runtime["initial_main_phy_link_id"] == 1
        assert runtime["initial_main_phy_band"] == "5 GHz"

        with (output / "link_intervals.csv").open(newline="") as source:
            link_rows = list(csv.DictReader(source))
        assert [int(row["link_id"]) for row in link_rows] == [0, 1]
        assert runtime["successful_mpdus_per_link"] == [
            int(row["successful_mpdus"]) for row in link_rows
        ]
        assert runtime["phy_tx_time_us_per_link"] == [
            int(row["phy_tx_time_us"]) for row in link_rows
        ]
        assert all(value > 0 for value in runtime["successful_mpdus_per_link"])
        assert all(value > 0 for value in runtime["phy_tx_time_us_per_link"])

        result = validate_run(output)
        assert result["valid"] is True
        tampered = dict(runtime)
        tampered["successful_mpdus_per_link"] = list(
            runtime["successful_mpdus_per_link"]
        )
        tampered["successful_mpdus_per_link"][0] += 1
        (output / "mlo_runtime.json").write_text(
            json.dumps(tampered), encoding="utf-8"
        )
        try:
            validate_run(output)
        except ValidationError as error:
            assert "differs from link_intervals.csv" in str(error)
        else:
            raise AssertionError("validator accepted tampered EMLSR link activity")
        print(
            "PASS practical EMLSR MLO: "
            f"successful_mpdus={runtime['successful_mpdus_per_link']}, "
            f"phy_tx_time_us={runtime['phy_tx_time_us_per_link']}"
        )
    finally:
        shutil.rmtree(temporary)


if __name__ == "__main__":
    main()
