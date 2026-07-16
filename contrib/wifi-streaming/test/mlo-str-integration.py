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
    output = temporary / "run"
    try:
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
        assert wifi["tid_to_link_mapping_ul"] == "0 0,1"
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
            "PASS native STR MLO: "
            f"delivered_frames={summary['complete_frame_count']}, "
            f"successful_mpdus={[row['successful_mpdus'] for row in mac_rows]}"
        )
    finally:
        shutil.rmtree(temporary)


if __name__ == "__main__":
    main()
