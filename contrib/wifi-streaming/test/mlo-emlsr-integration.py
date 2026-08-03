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
                "--seed=43",
                "--duration=2",
                "--fps=30",
                "--frameSize=12000",
                "--deadlineUs=33333",
                "--propagationModel=log_distance_nakagami",
                "--obssProfile=mixed4x4",
                f"--outputDir={output}",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
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
            "profile": "advanced_sta_ap_fixed_aux_v3",
            "manager": "ns3::AdvancedEmlsrManager",
            "ap_manager": "ns3::AdvancedApEmlsrManager",
            "link_ids": [0, 1],
            "main_phy_id": 1,
            "padding_delay_us": 128,
            "transition_delay_us": 128,
            "transition_timeout_us": 0,
            "medium_sync_duration_us": 5472,
            "msd_ofdm_ed_threshold_dbm": -72,
            "msd_max_n_txops": 1,
            "channel_switch_delay_us": 100,
            "switch_aux_phy": False,
            "aux_phy_tx_capable": False,
            "aux_phy_channel_width_mhz": 20,
            "aux_phy_max_modulation_class": "OFDM",
            "put_aux_phy_to_sleep": False,
            "in_device_interference": False,
            "use_notified_mac_header": True,
            "reset_cam_state": False,
            "allow_ul_txop_in_rx": False,
            "interrupt_switch": False,
            "use_aux_phy_cca": False,
            "switch_main_phy_back_delay_us": 5000,
            "keep_main_phy_after_dl_txop": False,
            "check_access_on_main_phy_link": True,
            "min_ac_to_skip_check_access": "AC_BK",
            "ap_use_notified_mac_header": True,
            "ap_early_switch_to_listening": False,
            "ap_wait_trans_delay_on_psdu_rx_error": True,
            "ap_update_cw_after_failed_icf": True,
            "ap_report_failed_icf": True,
            "cam_generate_backoff_without_tx": True,
            "cam_proactive_backoff": False,
            "cam_reset_backoff_threshold_us": 0,
            "cam_n_slots_left": 0,
            "cam_n_slots_left_min_delay_us": 25,
            "notify_mac_header_rx_end": True,
            "main_phy_frequency_ranges": [
                "WIFI_SPECTRUM_2_4_GHZ",
                "WIFI_SPECTRUM_5_GHZ",
            ],
        }

        runtime = json.loads((output / "mlo_runtime.json").read_text())
        assert runtime["mode"] == "EMLSR"
        assert runtime["profile"] == "advanced_sta_ap_fixed_aux_v3"
        assert runtime["emlsr_manager"] == "ns3::AdvancedEmlsrManager"
        assert runtime["ap_emlsr_manager"] == "ns3::AdvancedApEmlsrManager"
        assert runtime["emlsr_link_ids"] == [0, 1]
        assert runtime["ap_emlsr_enabled_per_link"] == [True, True]
        assert runtime["main_phy_id"] == 1
        assert runtime["initial_main_phy_link_id"] == 1
        assert runtime["initial_main_phy_band"] == "5 GHz"
        assert runtime["all_phy_settings_match_profile"] is True
        assert runtime["all_cam_settings_match_profile"] is True

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
        smoke_successful_mpdus = list(runtime["successful_mpdus_per_link"])
        smoke_phy_tx_time_us = list(runtime["phy_tx_time_us_per_link"])

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

        (output / "mlo_runtime.json").write_text(
            json.dumps(runtime), encoding="utf-8"
        )
        link_rows[0]["successful_mpdus"] = "0"
        link_rows[0]["phy_tx_time_us"] = "0"
        with (output / "link_intervals.csv").open(
            "w", newline="", encoding="utf-8"
        ) as destination:
            writer = csv.DictWriter(destination, fieldnames=link_rows[0].keys())
            writer.writeheader()
            writer.writerows(link_rows)
        runtime["successful_mpdus_per_link"][0] = 0
        runtime["phy_tx_time_us_per_link"][0] = 0
        (output / "mlo_runtime.json").write_text(
            json.dumps(runtime), encoding="utf-8"
        )
        with (output / "mac_summary.csv").open(newline="", encoding="utf-8") as source:
            mac_rows = list(csv.DictReader(source))
        mac_rows[0]["successful_mpdus"] = "0"
        with (output / "mac_summary.csv").open(
            "w", newline="", encoding="utf-8"
        ) as destination:
            writer = csv.DictWriter(destination, fieldnames=mac_rows[0].keys())
            writer.writeheader()
            writer.writerows(mac_rows)
        summary_path = output / "summary.json"
        summary = json.loads(summary_path.read_text())
        summary["successful_mpdus"] = sum(
            int(row["successful_mpdus"]) for row in link_rows
        )
        summary["phy_tx_time_us"] = sum(
            int(row["phy_tx_time_us"]) for row in link_rows
        )
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        assert validate_run(output)["valid"] is True
        print(
            "PASS practical EMLSR MLO: "
            f"successful_mpdus={smoke_successful_mpdus}, "
            f"phy_tx_time_us={smoke_phy_tx_time_us}"
        )
    finally:
        shutil.rmtree(temporary)


if __name__ == "__main__":
    main()
