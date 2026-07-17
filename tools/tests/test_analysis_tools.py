from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from run_experiments import cli_arguments, derive_run_id, expand_config
from plot_results import _approach_key, _approach_label, plot
from summarize_runs import group_key, summarize
from validate_outputs import ValidationError, validate_run


def write_csv(path: Path, header: list[str], row: list[object]) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(header)
        writer.writerow(row)


def make_run(path: Path, run_id: str, seed: int = 1) -> None:
    path.mkdir()
    (path / "resolved_config.json").write_text(json.dumps({
        "run_id": run_id, "seed": seed, "run": 1, "topology": "single_link",
        "policy": "fixed_link_0", "stream": {"source": "synthetic"},
    }))
    (path / "build_info.json").write_text(json.dumps({
        "ns3_version": "ns-3.48", "ns3_upstream_commit": "upstream",
        "project_git_commit": "project", "compiler": "compiler",
        "build_profile": "debug", "execution_timestamp_utc": "now", "host": "host",
    }))
    frame_header = [
        "run_id", "frame_id", "generation_time_us", "frame_size_bytes", "packet_count",
        "frame_type", "deadline_us", "policy", "primary_link", "duplicated",
        "decision_time_us", "predicted_delay_link_0", "predicted_delay_link_1",
        "union_first_packet_us", "union_completion_us", "union_latency_us",
        "copy_0_completion_us", "copy_1_completion_us", "unique_packets_received",
        "duplicate_packets_received", "deadline_miss", "incomplete", "completion_mode",
    ]
    write_csv(path / "frames.csv", frame_header, [
        run_id, 7, 100, 1200, 1, "I_FRAME", 1000, "fixed_link_0", 0, 0, 100,
        0, 0, 150, 200, 100, 200, "", 1, 0, 0, 0, "union_complete",
    ])
    write_csv(path / "policy_decisions.csv", [
        "run_id", "frame_id", "decision_time_us", "policy", "primary_link",
        "duplicated", "secondary_link", "reason", "primary_score", "secondary_score",
    ], [run_id, 7, 100, "fixed_link_0", 0, 0, "", "configured", 0, 0])
    write_csv(path / "link_intervals.csv", [
        "timestamp_us", "link_id", "application_bytes_sent",
        "application_bytes_received", "redundant_bytes", "probe_bytes",
        "successful_mpdus", "failed_mpdus", "retransmissions",
        "mean_mpdu_service_time_us", "p95_mpdu_service_time_us", "queue_bytes",
        "estimated_rate_mbps", "phy_idle_time_us", "phy_cca_busy_time_us",
        "phy_tx_time_us", "phy_rx_time_us",
    ], [1000, 0, 1200, 1200, 0, 0, 1, 0, 0, 10, 10, "", "", 800, 50, 100, 50])
    write_csv(path / "mac_summary.csv", [
        "link_id", "node_id", "device_id", "successful_mpdus", "failed_mpdus",
        "retransmissions", "retry_limit_drops", "mean_mpdu_service_time_us",
        "p95_mpdu_service_time_us",
    ], [0, 1, 1, 1, 0, 0, 0, 10, 10])
    (path / "summary.json").write_text(json.dumps({
        "frame_count": 1, "complete_frame_count": 1, "incomplete_frame_count": 0,
        "deadline_miss_count": 0, "complete_ratio": 1, "incomplete_ratio": 0,
        "deadline_miss_ratio": 0, "latency_p50_us": 100, "latency_p90_us": 100,
        "latency_p95_us": 100, "latency_p99_us": 100, "latency_p99_9_us": None,
        "application_goodput_mbps": 0.0096, "application_bytes_sent": 1200,
        "application_bytes_delivered": 1200, "redundant_bytes_sent": 0,
        "redundant_byte_ratio": 0, "duplicate_frame_count": 0,
        "duplicate_recovery_count": 0, "duplicate_recovery_rate": 0,
        "duplicate_no_benefit_count": 0, "duplicate_no_benefit_ratio": 0,
        "successful_mpdus": 1, "failed_mpdus": 0, "retransmissions": 0,
        "phy_tx_time_us": 100, "phy_rx_time_us": 50,
        "phy_cca_busy_time_us": 50, "background_bytes_sent": 0,
        "background_bytes_received": 0, "background_throughput_mbps": 0,
    }))
    (path / "stdout.log").write_text("ok\n")


class MatrixTests(unittest.TestCase):
    def test_deterministic_cartesian_and_compatibility(self) -> None:
        document = {
            "base": {"stream": {"duration": 1}},
            "seeds": [2, 1], "runs": [3],
            "topologies": ["single_link", "dual_interface"],
            "policies": [{"name": "fixed_link_0", "topologies": ["single_link"]}],
            "sweep": {"stream.fps": [30, 60], "wifi.queue_max_packets": [10, 20]},
        }
        first = expand_config(document)
        self.assertEqual(first, expand_config(document))
        self.assertEqual(len(first), 8)
        self.assertTrue(all(item["config"]["topology"] == "single_link" for item in first))

    def test_run_id_is_stable_and_identity_sensitive(self) -> None:
        config = {"a": 1, "nested": {"b": 2}}
        first = derive_run_id(config, 1, 2, "n", "p")
        self.assertEqual(first, derive_run_id({"nested": {"b": 2}, "a": 1}, 1, 2, "n", "p"))
        self.assertNotEqual(first, derive_run_id(config, 2, 2, "n", "p"))

    def test_legacy_profile_cli_translation(self) -> None:
        arguments = cli_arguments({
            "topology": "mlo_str",
            "policy": "fixed_link_0",
            "background": {
                "background_profile": "legacy_mixed8",
                "background_stream_base": 4000,
                "random_on_mean_ms": 75,
                "random_off_mean_ms": 125,
            },
        }, Path("."))
        self.assertIn("--backgroundProfile=legacy_mixed8", arguments)
        self.assertIn("--backgroundStreamBase=4000", arguments)
        self.assertIn("--randomOnMeanMs=75", arguments)
        self.assertIn("--randomOffMeanMs=125", arguments)
        distance_arguments = cli_arguments({
            "propagation": {"station_distance_m": 10},
        }, Path("."))
        self.assertIn("--stationDistanceM=10", distance_arguments)
        stream_arguments = cli_arguments({
            "stream": {"gop_length": 60, "keyframe_size_multiplier": 4},
        }, Path("."))
        self.assertIn("--gopLength=60", stream_arguments)
        self.assertIn("--keyframeSizeMultiplier=4", stream_arguments)
        wifi_arguments = cli_arguments({
            "wifi": {"mlo_sta_max_inflights": 2},
        }, Path("."))
        self.assertIn("--mloStaMaxInflights=2", wifi_arguments)

    def test_mlo_inflight_variants_have_distinct_plot_labels(self) -> None:
        first = {
            "topology": "mlo_str", "policy": "fixed_link_0",
            "config": {"wifi": {"sta_max_inflights": 1}},
        }
        second = {
            "topology": "mlo_str", "policy": "fixed_link_0",
            "config": {"wifi": {"sta_max_inflights": 2}},
        }
        self.assertEqual(_approach_label(first), "MLO NMaxInflights=1")
        self.assertEqual(_approach_label(second), "MLO NMaxInflights=2")
        self.assertNotEqual(_approach_key(first), _approach_key(second))

    def test_fixed_interface_baselines_have_frequency_labels(self) -> None:
        link_24 = {
            "topology": "dual_interface", "policy": "fixed_link_0",
            "config": {"wifi": {"sta_max_inflights": 1}},
        }
        link_5 = {
            "topology": "dual_interface", "policy": "fixed_link_1",
            "config": {"wifi": {"sta_max_inflights": 1}},
        }
        self.assertEqual(_approach_label(link_24), "Single 2.4 GHz interface")
        self.assertEqual(_approach_label(link_5), "Single 5 GHz interface")

    def test_obss_cli_translation(self) -> None:
        arguments = cli_arguments({
            "propagation": {
                "propagation_model": "log_distance_nakagami",
                "path_loss_exponent": 3,
                "propagation_stream_base": 5000,
            },
            "obss": {
                "obss_profile": "mixed4x4",
                "obss_ul_min_rate_mbps": 0.5,
                "obss_ul_max_rate_mbps": 3,
                "obss_dl_min_rate_mbps": 2,
                "obss_dl_max_rate_mbps": 8,
                "obss_station_manager": "minstrel_ht",
                "obss_application_stream_base": 7000,
                "obss_wifi_stream_base": 8000,
            },
        }, Path("."))
        self.assertIn("--propagationModel=log_distance_nakagami", arguments)
        self.assertIn("--pathLossExponent=3", arguments)
        self.assertIn("--propagationStreamBase=5000", arguments)
        self.assertIn("--obssProfile=mixed4x4", arguments)
        self.assertIn("--obssUlMinRateMbps=0.5", arguments)
        self.assertIn("--obssUlMaxRateMbps=3", arguments)
        self.assertIn("--obssDlMinRateMbps=2", arguments)
        self.assertIn("--obssDlMaxRateMbps=8", arguments)
        self.assertIn("--obssStationManager=minstrel_ht", arguments)
        self.assertIn("--obssApplicationStreamBase=7000", arguments)
        self.assertIn("--obssWifiStreamBase=8000", arguments)

    def test_obss_grouping_ignores_resolved_positions(self) -> None:
        first = {
            "config": {
                "run_id": "a",
                "seed": 1,
                "background": {
                    "obss": {
                        "ul_min_rate_mbps": 0.5,
                        "bsses": [{"ap": [1, 2], "stas": [[3, 4]]}],
                    },
                },
            },
        }
        second = {
            "config": {
                "run_id": "b",
                "seed": 2,
                "background": {
                    "obss": {
                        "ul_min_rate_mbps": 0.5,
                        "bsses": [{"ap": [9, 8], "stas": [[7, 6]]}],
                    },
                },
            },
        }
        self.assertEqual(group_key(first), group_key(second))
        second["config"]["background"]["obss"]["ul_min_rate_mbps"] = 1
        self.assertNotEqual(group_key(first), group_key(second))


class OutputTests(unittest.TestCase):
    def test_validation_and_run_level_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_run(root / "one", "one", 1)
            make_run(root / "two", "two", 2)
            self.assertTrue(validate_run(root / "one")["valid"])
            result = summarize([root / "one", root / "two"])
            self.assertEqual(result["independent_sample_unit"], "run")
            group = result["groups"][0]
            self.assertEqual(group["metrics"]["deadline_miss_ratio"]["n"], 2)
            self.assertIsNone(group["metrics"]["duplicate_recovery_rate"]["mean"])
            self.assertIsNone(group["redundant_airtime_ratio"]["mean"])
            plots = root / "plots"
            plot(result, plots)
            self.assertTrue((plots / "latency_cdf.png").is_file())
            self.assertTrue((plots / "latency_pdf.png").is_file())
            self.assertTrue((plots / "miss_burst_distribution_by_group.png").is_file())

    def test_validator_rejects_count_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "run"
            make_run(run, "bad")
            summary = json.loads((run / "summary.json").read_text())
            summary["frame_count"] = 2
            (run / "summary.json").write_text(json.dumps(summary))
            with self.assertRaises(ValidationError):
                validate_run(run)


if __name__ == "__main__":
    unittest.main()
