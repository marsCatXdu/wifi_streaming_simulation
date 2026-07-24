from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from run_experiments import (
    cli_arguments,
    derive_run_id,
    expand_config,
    load_yaml,
    write_experiment_description,
)
from benchmark_prediction_telemetry import _overhead_classification
from plot_results import _approach_key, _approach_label, plot
from summarize_prediction_pilots import _candidate_id, _load_key, _target_band
from summarize_runs import group_key, summarize
from validate_outputs import (
    PREDICTION_BASE_COLUMNS,
    PREDICTION_EVENT_COLUMNS,
    PREDICTION_ROLLING_PREFIXES,
    ValidationError,
    _rolling_column,
    validate_run,
)


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


def add_prediction_sample(path: Path, oracle_enabled: bool = False) -> None:
    config_path = path / "resolved_config.json"
    config = json.loads(config_path.read_text())
    config.update({
        "topology": "dual_interface",
        "policy": "fixed_link_0",
        "wifi": {
            "standard": "802.11be",
            "ul_ofdma_enabled": False,
            "max_amsdu_size_bytes": 0,
            "fragmentation_threshold_bytes": 65535,
        },
        "stream": {
            "source": "synthetic",
            "deadline_us": 1000,
            "payload_size_bytes": 1200,
        },
        "predictionTelemetry": {
            "enabled": True,
            "sample_offsets_us": [0],
            "history_windows_us": [1000],
            "event_log_enabled": False,
            "oracle_features_enabled": oracle_enabled,
            "telemetry_schema_version": 2,
            "event_schema_version": 2,
            "feature_support_mask_version": 2,
        },
    })
    config_path.write_text(json.dumps(config))
    ofdma_header = [
        "device_group", "trigger_frames", "basic_trigger_frames", "bsrp_trigger_frames",
        "ru_grants", "tb_ppdus_transmitted", "tb_bytes_transmitted",
        "tb_mpdus_received", "tb_bytes_received",
    ]
    with (path / "ofdma_summary.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(ofdma_header)
        for group in ("target", "same_bss_background", "obss"):
            writer.writerow([group, 0, 0, 0, 0, 0, 0, 0, 0])

    label = "1ms"
    columns = PREDICTION_BASE_COLUMNS | {
        _rolling_column(prefix, label) for prefix in PREDICTION_ROLLING_PREFIXES
    }
    values = {column: "" for column in columns}
    values.update({
        "telemetry_schema_version": 2,
        "run_id": config["run_id"],
        "frame_id": 7,
        "path_id": 0,
        "copy_id": 0,
        "sample_stage": "T0",
        "sample_offset_us": 0,
        "sample_time_ns": 100000,
        "latest_feature_event_time_ns": "",
        "latest_feature_event_sequence": 0,
        "generation_time_ns": 100000,
        "deadline_time_ns": 1100000,
        "frame_age_us": 0,
        "deadline_slack_us": 1000,
        "sender_mac_complete": 0,
        "actionable": 1,
        "frame_size_bytes": 1200,
        "frame_packet_count": 1,
        "frame_type": "I_FRAME",
        "packets_submitted": 0,
        "application_socket_packet_bytes_submitted": 0,
        "packets_remaining_to_submit": 1,
        "mpdu_tx_attempts_total": 0,
        "mpdu_positive_acks_total": 0,
        "mpdu_tx_attempt_failures_total": 0,
        "mpdu_retries_total": 0,
        "mpdu_terminal_drops_total": 0,
        "mpdu_retry_limit_drops_total": 0,
        "mpdu_lifetime_drops_total": 0,
        "mpdu_queue_drops_total": 0,
        "ppdu_tx_count_total": 0,
        "frequency_band": "5GHz",
        "center_frequency_mhz": 5180,
        "frame_packets_mac_enqueued": 0,
        "frame_packets_mac_dequeued": 0,
        "frame_packets_tx_succeeded": 0,
        "frame_mpdu_attempt_failures": 0,
        "frame_packets_terminally_dropped": 0,
        "frame_packets_currently_queued": 0,
        "frame_mac_service_bytes_currently_queued": 0,
        "mac_queue_packets": 0,
        "mac_queue_service_bytes": 0,
        "packets_ahead_of_frame": 0,
        "mac_service_bytes_ahead_of_frame": 0,
        "frame_packets_pending_primary": 1,
        "frame_mac_service_bytes_not_acknowledged": 1286,
        "frame_mac_service_bytes_pending_primary": 1286,
        "feature_support_mask": hex(sum(
            1 << bit for bit in (
                set(range(0, 17)) | set(range(18, 54)) |
                ({54, 56, 57, 58, 59} if oracle_enabled else set())
            )
        )),
        "mpdu_attempts_1ms": 0,
        "mpdu_positive_acks_1ms": 0,
        "mpdu_attempt_failures_1ms": 0,
        "mpdu_retries_1ms": 0,
        "acknowledged_mac_service_bytes_1ms": 0,
        "phy_tx_time_1ms_us": 0,
        "phy_rx_time_1ms_us": 0,
        "phy_busy_time_1ms_us": 0,
        "phy_idle_time_1ms_us": 0,
        "phy_other_time_1ms_us": 0,
        "history_coverage_1ms_us": 0,
    })
    if oracle_enabled:
        values.update({
            "current_cw": 15,
            "nav_remaining_us": 0,
            "current_phy_state": "IDLE",
            "channel_access_status": "NOT_REQUESTED",
            "medium_busy_now": 0,
        })
    header = sorted(columns)
    write_csv(path / "prediction_samples.csv", header, [values[column] for column in header])


class MatrixTests(unittest.TestCase):
    def test_prediction_load_pilot_matrix(self) -> None:
        loaded = load_yaml(ROOT / "experiments/configs/prediction_load_pilot.yaml")
        baseline = load_yaml(
            ROOT / "experiments/configs/prediction_load_pilot_baseline.yaml"
        )
        loaded_specs = expand_config(loaded)
        baseline_specs = expand_config(baseline)
        self.assertEqual(len(loaded_specs), 90)
        self.assertEqual(len(baseline_specs), 6)
        self.assertFalse(
            any("prediction" in spec["config"] for spec in loaded_specs + baseline_specs)
        )
        self.assertEqual(
            {
                spec["config"]["background"]["correlation_mode"]
                for spec in loaded_specs
            },
            {
                "independent",
                "common_bursts",
                "mixed_common_and_independent",
            },
        )

    def test_prediction_pilot_candidate_derivation(self) -> None:
        config = {
            "policy": "fixed_link_0",
            "background": {
                "profile": "legacy_mixed8",
                "rate_mbps_per_station": 4,
                "correlation": {
                    "mode": "mixed_common_and_independent",
                    "common_on_mean_ms": 100,
                    "common_off_mean_ms": 100,
                    "local_on_mean_ms": 100,
                    "local_off_mean_ms": 100,
                },
            },
        }
        key = _load_key(config)
        self.assertAlmostEqual(key[-1], 0.75)
        self.assertEqual(_candidate_id(key), "mixed-r4-d75.0-link0")
        self.assertEqual(_target_band(0.02), "low")
        self.assertEqual(_target_band(0.07), "medium")
        self.assertEqual(_target_band(0.20), "high")
        self.assertEqual(_target_band(0.04), "outside_target_bands")

    def test_prediction_overhead_policy_boundaries(self) -> None:
        self.assertEqual(_overhead_classification(25), "PASS")
        self.assertEqual(
            _overhead_classification(25.0001),
            "PASS_WITH_PERFORMANCE_WARNING",
        )
        self.assertEqual(
            _overhead_classification(35),
            "PASS_WITH_PERFORMANCE_WARNING",
        )
        self.assertEqual(_overhead_classification(35.0001), "REVIEW_REQUIRED")

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
        enabled = {"prediction": {"prediction_telemetry_enabled": True}}
        disabled = {"prediction": {"prediction_telemetry_enabled": False}}
        self.assertNotEqual(derive_run_id(enabled, 1, 2, "n", "p"),
                            derive_run_id(disabled, 1, 2, "n", "p"))
        historical_identity = {
            "config": disabled, "seed": 1, "run": 2,
            "ns3_commit": "n", "project_commit": "p",
        }
        historical = hashlib.sha256(
            json.dumps(historical_identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:20]
        self.assertEqual(derive_run_id(disabled, 1, 2, "n", "p"), historical)

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
        ofdma_arguments = cli_arguments({
            "wifi": {
                "ul_ofdma_enabled": True,
                "ul_ofdma_scope": "all_he_eht_aps",
                "ul_ofdma_access_interval_ms": 5,
                "ul_ofdma_bsrp_enabled": True,
                "ul_ofdma_max_stations": 4,
                "ul_ofdma_psdu_size": 1200,
            },
        }, Path("."))
        self.assertIn("--ulOfdmaEnabled=1", ofdma_arguments)
        self.assertIn("--ulOfdmaScope=all_he_eht_aps", ofdma_arguments)
        self.assertIn("--ulOfdmaAccessIntervalMs=5", ofdma_arguments)
        self.assertIn("--ulOfdmaBsrpEnabled=1", ofdma_arguments)
        self.assertIn("--ulOfdmaMaxStations=4", ofdma_arguments)
        self.assertIn("--ulOfdmaPsduSize=1200", ofdma_arguments)
        prediction_arguments = cli_arguments({
            "prediction": {
                "prediction_telemetry_enabled": True,
                "prediction_sample_offsets_us": [0, 1000, 2000, 4000],
                "prediction_history_windows_us": [1000, 5000, 20000],
                "prediction_event_log_enabled": False,
                "prediction_oracle_features_enabled": True,
            },
        }, Path("."))
        self.assertIn("--predictionTelemetryEnabled=1", prediction_arguments)
        self.assertIn("--predictionSampleOffsetsUs=0,1000,2000,4000",
                      prediction_arguments)
        self.assertIn("--predictionHistoryWindowsUs=1000,5000,20000",
                      prediction_arguments)
        self.assertIn("--predictionEventLogEnabled=0", prediction_arguments)
        self.assertIn("--predictionOracleFeaturesEnabled=1", prediction_arguments)
        with self.assertRaises(ValueError):
            cli_arguments({
                "prediction": {"prediction_sample_offsets_us": [0, "1000"]},
            }, Path("."))

    def test_ofdma_matrices_have_paired_five_way_runs(self) -> None:
        for name in ("obss_contention_ul_ofdma.yaml",
                     "combined_contention_ul_ofdma.yaml"):
            path = ROOT / "experiments" / "configs" / name
            expanded = expand_config(load_yaml(path))
            self.assertEqual(len(expanded), 100)
            self.assertEqual({
                item["config"]["wifi"]["ul_ofdma_enabled"] for item in expanded
            }, {False, True})
            self.assertEqual(len({
                (item["config"]["topology"], item["config"]["policy"],
                 item["config"]["wifi"]["mlo_sta_max_inflights"])
                for item in expanded
            }), 5)

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

    def test_ofdma_states_have_distinct_plot_labels(self) -> None:
        disabled = {
            "topology": "dual_interface", "policy": "fixed_link_0",
            "config": {"wifi": {
                "sta_max_inflights": 1, "ul_ofdma_enabled": False,
            }},
        }
        enabled = {
            "topology": "dual_interface", "policy": "fixed_link_0",
            "config": {"wifi": {
                "sta_max_inflights": 1, "ul_ofdma_enabled": True,
            }},
        }
        self.assertEqual(_approach_label(disabled),
                         "Single 2.4 GHz interface / UL OFDMA off")
        self.assertEqual(_approach_label(enabled),
                         "Single 2.4 GHz interface / UL OFDMA on")
        self.assertNotEqual(_approach_key(disabled), _approach_key(enabled))

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
    def test_result_description_lists_associations_and_standards(self) -> None:
        config_path = ROOT / "experiments" / "configs" / \
            "combined_contention_ul_ofdma.yaml"
        document = load_yaml(config_path)
        specs = expand_config(document)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            write_experiment_description(document, specs, output)
            description = (output / "DESCRIPTION.rst").read_text()
            self.assertIn("Single 2.4 GHz interface", description)
            self.assertIn("three 802.11n", description)
            self.assertIn("Four independent APs", description)
            self.assertIn("RrMultiUserScheduler", description)

    def test_prediction_description_lists_only_configured_approaches(self) -> None:
        config_path = ROOT / "experiments" / "configs" / \
            "prediction_telemetry_smoke.yaml"
        document = load_yaml(config_path)
        specs = expand_config(document)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            write_experiment_description(document, specs, output)
            description = (output / "DESCRIPTION.rst").read_text()
            self.assertIn("compares 2 target-sender approach", description)
            self.assertIn("Single 2.4 GHz interface", description)
            self.assertIn("Single 5 GHz interface", description)
            self.assertNotIn("Application full duplication", description)
            self.assertIn("Prediction telemetry", description)
            self.assertIn("receiver-independent causal", description)

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

    def test_prediction_validator_accepts_causal_t0(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "run"
            make_run(run, "prediction")
            add_prediction_sample(run)
            result = validate_run(run)
            self.assertEqual(result["prediction_sample_count"], 1)
            self.assertEqual(result["prediction_event_count"], 0)

    def test_prediction_validator_rejects_future_feature_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "run"
            make_run(run, "prediction")
            add_prediction_sample(run)
            sample_path = run / "prediction_samples.csv"
            with sample_path.open(newline="", encoding="utf-8") as source:
                reader = csv.DictReader(source)
                rows = list(reader)
                header = reader.fieldnames
            self.assertIsNotNone(header)
            rows[0]["latest_feature_event_time_ns"] = "100001"
            rows[0]["latest_feature_event_sequence"] = "1"
            with sample_path.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=header)
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaises(ValidationError):
                validate_run(run)

    def test_prediction_validator_enforces_passive_oracle_nulls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "run"
            make_run(run, "prediction")
            add_prediction_sample(run, oracle_enabled=True)
            self.assertEqual(validate_run(run)["prediction_sample_count"], 1)

            sample_path = run / "prediction_samples.csv"
            with sample_path.open(newline="", encoding="utf-8") as source:
                reader = csv.DictReader(source)
                rows = list(reader)
                header = reader.fieldnames
            self.assertIsNotNone(header)
            rows[0]["remaining_backoff_slots"] = "3"
            with sample_path.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=header)
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaises(ValidationError):
                validate_run(run)

    def test_prediction_validator_rejects_enqueue_before_submission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "run"
            make_run(run, "prediction")
            add_prediction_sample(run)
            config_path = run / "resolved_config.json"
            config = json.loads(config_path.read_text())
            config["predictionTelemetry"]["event_log_enabled"] = True
            config_path.write_text(json.dumps(config))

            header = sorted(PREDICTION_EVENT_COLUMNS)
            common = {
                field: "" for field in header
            }
            common.update({
                "event_schema_version": 2,
                "run_id": "prediction",
                "event_time_ns": 100000,
                "path_id": 0,
                "copy_id": 0,
                "frame_id": 7,
                "packet_index": 0,
                "mac_queue_packets": 0,
                "mac_queue_service_bytes": 0,
                "current_phy_state": "IDLE",
            })
            registered = {
                **common, "event_sequence": 1, "event_type": "FRAME_REGISTERED"
            }
            enqueued = {
                **common,
                "event_sequence": 2,
                "event_type": "MAC_ENQUEUE",
                "mac_service_bytes": 1286,
                "mac_queue_packets": 1,
                "mac_queue_service_bytes": 1286,
            }
            with (run / "prediction_events.csv").open(
                "w", newline="", encoding="utf-8"
            ) as output:
                writer = csv.DictWriter(output, fieldnames=header)
                writer.writeheader()
                writer.writerows([registered, enqueued])
            with self.assertRaises(ValidationError):
                validate_run(run)


if __name__ == "__main__":
    unittest.main()
