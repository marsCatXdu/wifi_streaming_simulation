#!/usr/bin/env python3
"""Deterministic native STR MLO delivery and per-link activity smoke test."""

import csv
import json
import pathlib
import shutil
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[3]


def main() -> None:
    temporary = pathlib.Path(tempfile.mkdtemp(prefix="wifi-streaming-mlo-str-"))
    try:
        for wmm_mode, stream_tos, stream_tid, access_category in (
            ("off", 0, 0, "AC_BE"),
            ("on", 160, 5, "AC_VI"),
            ("af41", 136, 4, "AC_VI"),
        ):
            output = temporary / wmm_mode
            subprocess.run(
                [
                    str(ROOT / "ns3"),
                    "run",
                    "streaming-experiment",
                    "--",
                    "--topology=mlo_str",
                    "--wifiStandard=eht",
                    "--duration=1",
                    "--fps=120",
                    "--frameSize=50000",
                    "--deadlineUs=100000",
                    f"--wmmMode={wmm_mode}",
                    f"--outputDir={output}",
                ],
                cwd=ROOT,
                check=True,
            )

            resolved = json.loads((output / "resolved_config.json").read_text())
            wifi = resolved["wifi"]
            assert resolved["topology"] == "mlo_str"
            assert wifi["channel_settings"] == [
                "{1, 20, BAND_2_4GHZ, 0}",
                "{36, 20, BAND_5GHZ, 0}",
            ]
            assert wifi["frequency_ranges"] == [
                "WIFI_SPECTRUM_2_4_GHZ",
                "WIFI_SPECTRUM_5_GHZ",
            ]
            assert wifi["wmm_mode"] == wmm_mode
            assert wifi["stream_ip_tos"] == stream_tos
            assert wifi["stream_tid"] == stream_tid
            assert wifi["access_category"] == access_category
            assert wifi["tid_to_link_mapping_ul"] == f"{stream_tid} 0,1"
            assert wifi["str_mode"] == "STR"
            assert wifi["static_association"] is True
            assert wifi["block_ack_enabled"] is True
            assert wifi["application_socket_count"] == 1
            assert wifi["application_duplication"] is False

            summary = json.loads((output / "summary.json").read_text())
            assert summary["complete_frame_count"] == 120
            assert summary["incomplete_frame_count"] == 0

            with (output / "mac_summary.csv").open(newline="") as source:
                mac_rows = list(csv.DictReader(source))
            assert [row["link_id"] for row in mac_rows] == ["0", "1"]
            assert len({row["device_id"] for row in mac_rows}) == 1
            assert all(int(row["successful_mpdus"]) > 0 for row in mac_rows)

            with (output / "link_intervals.csv").open(newline="") as source:
                link_rows = list(csv.DictReader(source))
            assert [row["link_id"] for row in link_rows] == ["0", "1"]
            assert all(int(row["phy_tx_time_us"]) > 0 for row in link_rows)

            print(
                f"PASS native STR MLO WMM {wmm_mode}: "
                f"delivered_frames={summary['complete_frame_count']}, "
                f"successful_mpdus={[row['successful_mpdus'] for row in mac_rows]}"
            )
    finally:
        shutil.rmtree(temporary)


if __name__ == "__main__":
    main()
