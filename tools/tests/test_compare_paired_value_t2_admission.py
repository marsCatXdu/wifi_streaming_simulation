from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from compare_paired_value_t2_admission import (
    ComparisonError,
    compare_campaigns,
    plot_comparison,
    render_markdown,
)


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    """Write homogeneous dictionaries as a CSV."""
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_campaign(
    root: Path,
    name: str,
    actions: set[int],
    final_misses: set[int],
    seed: int = 1,
) -> Path:
    """Write the minimal raw campaign accepted by the comparison tool."""
    runs = root / name / "runs"
    run_id = f"{name}-policy"
    run_dir = runs / run_id
    run_dir.mkdir(parents=True)
    decisions = []
    frames = []
    scores = [0.4, 0.3, 0.2, 0.1]
    primary_misses = {0, 1, 2}
    for frame_id in range(4):
        action = frame_id in actions
        decisions.append(
            {
                "frame_id": frame_id,
                "decision_status": "action" if action else "airtime_guard_rejected",
                "secondary_launched": int(action),
                "primary_copy_id": 0,
                "generation_time_ns": 1_000_000_000 + frame_id * 1_000_000,
                "value_per_cost_score_float32": scores[frame_id],
                "primary_bad12_probability": scores[frame_id] + 0.1,
                "passes_score_threshold": 1,
                "admission_tier": "strict" if action else "none",
            }
        )
        frames.append(
            {
                "frame_id": frame_id,
                "generation_time_us": 1000 + frame_id * 100,
                "deadline_us": 50,
                "copy_0_completion_us": (
                    1100 + frame_id * 100 if frame_id in primary_misses else 1040 + frame_id * 100
                ),
                "copy_1_completion_us": "",
                "deadline_miss": int(frame_id in final_misses),
            }
        )
    write_csv(run_dir / "paired_value_t2_decisions.csv", decisions)
    write_csv(run_dir / "frames.csv", frames)
    aggregate = {
        "schema_version": 1,
        "runs": [
            {
                "run_id": run_id,
                "seed": seed,
                "run": 1,
                "policy": "paired_value_duplication_t2",
            }
        ],
    }
    (runs / "aggregate.json").write_text(json.dumps(aggregate), encoding="utf-8")
    manifest = {
        "experiment": name,
        "matrix_sha256": f"{name}-matrix",
        "project_commit": f"{name}-commit",
        "runtime_contract_id": f"{name}-contract",
        "runtime_contract_sha256": f"{name}-contract-sha",
    }
    (runs / "experiment_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return runs / "aggregate.json"


class ComparePairedValueT2AdmissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_action_and_miss_transitions(self) -> None:
        baseline = write_campaign(self.root, "baseline", {0, 2}, {1})
        candidate = write_campaign(self.root, "candidate", {0, 1}, {2})

        report = compare_campaigns(baseline, candidate, "V3", "V4")

        self.assertEqual(report["paired_units"], 1)
        self.assertEqual(report["frame_rows"], 4)
        self.assertEqual(report["decision_invariants"]["score_different_rows"], 0)
        admission = report["admission"]
        self.assertEqual(admission["baseline_actions"], 2)
        self.assertEqual(admission["candidate_actions"], 2)
        self.assertEqual(admission["transitions"]["common_action"]["count"], 1)
        self.assertEqual(
            admission["transitions"]["baseline_only_action"]["count"], 1
        )
        self.assertEqual(
            admission["transitions"]["candidate_only_action"]["count"], 1
        )
        self.assertEqual(
            report["outcomes"]["final_miss_transitions"],
            {
                "miss -> on_time": 1,
                "on_time -> miss": 1,
                "on_time -> on_time": 2,
            },
        )
        self.assertIn("| Actions | 2 | 2 | +0 |", render_markdown(report))
        plot_path = self.root / "comparison.png"
        plot_comparison(report, plot_path)
        self.assertGreater(plot_path.stat().st_size, 1000)

    def test_rejects_mismatched_seed_run_units(self) -> None:
        baseline = write_campaign(self.root, "baseline", {0}, set(), seed=1)
        candidate = write_campaign(self.root, "candidate", {0}, set(), seed=2)

        with self.assertRaisesRegex(ComparisonError, "paired seed/run units differ"):
            compare_campaigns(baseline, candidate, "V3", "V4")


if __name__ == "__main__":
    unittest.main()
