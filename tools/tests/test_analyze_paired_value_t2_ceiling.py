from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from analyze_paired_value_t2_ceiling import (
    CeilingError,
    analyze_ceiling,
    plot_ceiling,
    render_markdown,
)


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    """Write homogeneous test rows."""
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_campaign(
    root: Path,
    name: str,
    actions_by_seed: dict[int, set[int]],
    failed_actions_by_seed: dict[int, set[int]] | None = None,
    primary_misses_by_seed: dict[int, set[int]] | None = None,
) -> Path:
    """Write a small two-run raw campaign accepted by the ceiling tool."""
    runs = root / name / "runs"
    runs.mkdir(parents=True)
    failed_actions_by_seed = failed_actions_by_seed or {}
    primary_misses_by_seed = primary_misses_by_seed or {
        1: {0, 1, 2, 5},
        2: {0, 5},
    }
    scores = {
        1: [0.9, 0.2, 0.1, 0.8, 0.7],
        2: [0.5, 0.4, 0.3, 0.2, 0.1],
    }
    threshold_passes = {1: {0, 1, 3, 4}, 2: {0}}
    aggregate_runs = []
    manifest_runs = []
    for seed in (1, 2):
        run_id = f"{name}-{seed}"
        run_dir = runs / run_id
        run_dir.mkdir()
        decisions: list[dict[str, object]] = []
        frames: list[dict[str, object]] = []
        actions = actions_by_seed.get(seed, set())
        failed_actions = failed_actions_by_seed.get(seed, set())
        primary_misses = primary_misses_by_seed[seed]
        for frame_id in range(6):
            evaluated = frame_id < 5
            action = frame_id in actions
            primary_miss = frame_id in primary_misses
            rescued = action and primary_miss and frame_id not in failed_actions
            final_miss = primary_miss and not rescued
            generation = 1000 + frame_id * 100
            primary_completion = generation + (60 if primary_miss else 40)
            union_latency = 20 if rescued else (60 if final_miss else 40)
            if action:
                status = "action"
            elif not evaluated:
                status = "history_warmup"
            elif frame_id in threshold_passes[seed]:
                status = "airtime_guard_rejected"
            else:
                status = "below_score_threshold"
            decisions.append(
                {
                    "frame_id": frame_id,
                    "primary_copy_id": 0,
                    "feature_evaluated": int(evaluated),
                    "secondary_launched": int(action),
                    "decision_status": status,
                    "value_per_cost_score_float32": (
                        scores[seed][frame_id] if evaluated else ""
                    ),
                    "passes_score_threshold": (
                        int(frame_id in threshold_passes[seed])
                        if evaluated
                        else ""
                    ),
                    "canonical_reserved_airtime_us": 4 if evaluated else "",
                }
            )
            frames.append(
                {
                    "frame_id": frame_id,
                    "generation_time_us": generation,
                    "deadline_us": 50,
                    "copy_0_completion_us": primary_completion,
                    "copy_1_completion_us": "",
                    "deadline_miss": int(final_miss),
                    "union_latency_us": union_latency,
                }
            )
        write_csv(run_dir / "paired_value_t2_decisions.csv", decisions)
        write_csv(run_dir / "frames.csv", frames)
        resolved = {
            "policy": "paired_value_duplication_t2",
            "pairedValueDuplicationT2": {
                "measurement_start_ns": 0,
                "measurement_stop_ns": 1_000_000,
                "budget_fraction": 0.01,
            },
        }
        (run_dir / "resolved_config.json").write_text(
            json.dumps(resolved), encoding="utf-8"
        )
        airtime = {
            "initial_bucket_capacity_us": 2,
            "tagged_secondary_tx_airtime_us": 5 * len(actions),
        }
        (run_dir / "secondary_airtime_summary.json").write_text(
            json.dumps(airtime), encoding="utf-8"
        )
        aggregate_runs.append(
            {
                "run_id": run_id,
                "seed": seed,
                "run": 1,
                "policy": "paired_value_duplication_t2",
            }
        )
        manifest_runs.append({"run_id": run_id, "seed": seed, "run": 1})
    aggregate = {"schema_version": 1, "runs": aggregate_runs}
    (runs / "aggregate.json").write_text(json.dumps(aggregate), encoding="utf-8")
    manifest = {
        "experiment": name,
        "project_commit": f"{name}-commit",
        "matrix_sha256": f"{name}-matrix",
        "runs": manifest_runs,
    }
    (runs / "experiment_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return runs / "aggregate.json"


class AnalyzePairedValueT2CeilingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_separates_per_run_score_and_primary_information_ceilings(self) -> None:
        reference = write_campaign(self.root, "reference", {1: {0, 3}, 2: {0}})
        support = write_campaign(
            self.root,
            "support",
            {1: {0, 1, 2}, 2: {0}},
            failed_actions_by_seed={1: {0}},
        )

        report = analyze_ceiling(
            {"V5": reference},
            "V5",
            {"support": support},
            str_misses=8,
            target_miss_rate=0.30,
        )

        self.assertEqual(report["population"]["all_generated_frames"], 12)
        self.assertEqual(report["population"]["primary_misses"], 6)
        self.assertEqual(report["population"]["eligible_primary_misses"], 4)
        self.assertEqual(
            report["population"]["fixed_outside_current_candidate_population_misses"],
            2,
        )
        resource = report["canonical_static_resource_contract"]
        self.assertEqual(resource["per_run_refill_budget_us"], 10)
        self.assertEqual(resource["per_run_finite_budget_us"], 12)
        self.assertEqual(resource["maximum_actions_at_refill_budget"], 2)
        self.assertEqual(resource["maximum_actions_at_finite_budget"], 3)

        score = report["scalar_score_frontiers"]["V5"]
        self.assertEqual(
            score["per_run_refill_budget_score_order"]["captured_primary_misses"],
            2,
        )
        pooled = score["pooled_all_threshold_passers_sensitivity"]
        self.assertTrue(pooled["campaign_wide_cost_within_pooled_refill"])
        self.assertEqual(pooled["runs_exceeding_refill_budget"], 1)
        self.assertEqual(pooled["captured_primary_misses"], 3)
        required_score = score["minimum_uniform_score_cap_for_target"]
        self.assertEqual(required_score["uniform_action_cap_per_run"], 4)
        self.assertEqual(
            required_score["maximum_per_run_canonical_reserved_airtime_us"], 16
        )

        oracle = report["perfect_primary_information_oracle"]
        self.assertEqual(
            oracle["per_run_refill_budget"]["captured_primary_misses"], 3
        )
        self.assertEqual(
            oracle["per_run_finite_budget"]["captured_primary_misses"], 4
        )
        self.assertEqual(report["target"]["maximum_integer_final_misses"], 3)

        outcome_support = report["secondary_outcome_support"]
        self.assertEqual(outcome_support["frames_without_an_action_outcome"], 0)
        self.assertEqual(
            outcome_support["frames_with_policy_dependent_rescue_outcome"], 1
        )
        self.assertFalse(outcome_support["exact_secondary_outcome_oracle_identified"])
        markdown = render_markdown(report)
        self.assertIn("unused credit across independent runs", markdown)
        self.assertIn("not an exact secondary-outcome or P99 oracle", markdown)
        plot = self.root / "ceiling.png"
        plot_ceiling(report, plot)
        self.assertGreater(plot.stat().st_size, 1000)

    def test_rejects_primary_label_changes_between_factual_campaigns(self) -> None:
        reference = write_campaign(self.root, "reference", {1: {0}, 2: {0}})
        changed = write_campaign(
            self.root,
            "changed",
            {1: {0}, 2: {0}},
            primary_misses_by_seed={1: {0, 1, 2, 5}, 2: {1, 5}},
        )

        with self.assertRaisesRegex(CeilingError, "primary miss label differs"):
            analyze_ceiling(
                {"V5": reference, "changed": changed},
                "V5",
                str_misses=8,
                target_miss_rate=0.30,
            )


if __name__ == "__main__":
    unittest.main()
