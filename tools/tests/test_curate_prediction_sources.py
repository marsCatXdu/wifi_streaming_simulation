from __future__ import annotations

import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from curate_prediction_sources import (
    Candidate,
    MatchedGroup,
    atomic_json,
    manifest_document,
    match_candidates,
    policy_independent_config,
    result_roots,
    select_groups,
    verify_manifest,
)
from prepare_prediction_stage_a_round2 import Condition


def candidate(
    root: Path,
    name: str,
    policy: str,
    *,
    cohort: str = "stage_a",
    semantic_name: str = "unloaded",
    semantic_sort_key: tuple[str, ...] = ("unloaded", ""),
    seed: int = 1,
    project_commit: str = "project",
    ns3_commit: str = "upstream",
    config_identity: str = "{}",
) -> Candidate:
    run_dir = root / name
    run_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "run_id": name,
        "seed": seed,
        "run": 1,
        "policy": policy,
    }
    (run_dir / "resolved_config.json").write_text(
        json.dumps(config), encoding="utf-8"
    )
    (run_dir / "build_info.json").write_text(
        json.dumps(
            {
                "project_git_commit": project_commit,
                "ns3_upstream_commit": ns3_commit,
            }
        ),
        encoding="utf-8",
    )
    if config_identity == "{}":
        config_identity = policy_independent_config(config)
    return Candidate(
        cohort=cohort,
        semantic_name=semantic_name,
        semantic_sort_key=semantic_sort_key,
        seed=seed,
        run=1,
        project_commit=project_commit,
        ns3_commit=ns3_commit,
        policy=policy,
        config_identity=config_identity,
        run_id=name,
        run_dir=run_dir,
        source_root=root,
    )


def group(
    root: Path,
    name: str,
    *,
    cohort: str,
    semantic_name: str,
    semantic_sort_key: tuple[str, ...],
    seed: int,
) -> MatchedGroup:
    runs = tuple(
        candidate(
            root,
            f"{name}-{policy}",
            policy,
            cohort=cohort,
            semantic_name=semantic_name,
            semantic_sort_key=semantic_sort_key,
            seed=seed,
        )
        for policy in ("fixed_link_0", "fixed_link_1")
    )
    return MatchedGroup(
        key=runs[0].group_key,
        cohort=cohort,
        semantic_name=semantic_name,
        semantic_sort_key=semantic_sort_key,
        runs=runs,
    )


class CurationTests(unittest.TestCase):
    def test_pairing_requires_both_commits_and_rejects_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidates = [
                candidate(root, "good-0", "fixed_link_0", seed=1),
                candidate(root, "good-1", "fixed_link_1", seed=1),
                candidate(root, "unmatched", "fixed_link_0", seed=2),
                candidate(root, "ns3-a", "fixed_link_0", seed=3, ns3_commit="a"),
                candidate(root, "ns3-b", "fixed_link_1", seed=3, ns3_commit="b"),
                candidate(root, "duplicate-a", "fixed_link_0", seed=4),
                candidate(root, "duplicate-b", "fixed_link_0", seed=4),
                candidate(root, "duplicate-pair", "fixed_link_1", seed=4),
                candidate(root, "mismatch-0", "fixed_link_0", seed=5),
                candidate(
                    root,
                    "mismatch-1",
                    "fixed_link_1",
                    seed=5,
                    config_identity='{"changed":true}',
                ),
            ]
            matched, rejected = match_candidates(candidates)
            self.assertEqual([item.runs[0].seed for item in matched], [1])
            self.assertEqual(rejected["ambiguous_duplicate_policy_groups"], 1)
            self.assertEqual(rejected["policy_config_mismatch_groups"], 1)
            self.assertEqual(rejected["unmatched_groups"], 3)

    def test_selection_caps_stage_a_and_keeps_all_ood(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            conditions = {
                Condition("unloaded", None),
                Condition("independent", Decimal("1")),
            }
            stage = []
            for condition in conditions:
                sort_key = (
                    condition.correlation_mode,
                    "" if condition.rate_mbps_per_station is None
                    else str(condition.rate_mbps_per_station),
                )
                for seed in (3, 1, 2):
                    stage.append(
                        group(
                            root,
                            f"{condition.label}-{seed}",
                            cohort="stage_a",
                            semantic_name=condition.label,
                            semantic_sort_key=sort_key,
                            seed=seed,
                        )
                    )
            ood = [
                group(
                    root,
                    f"ood-{seed}",
                    cohort="ood",
                    semantic_name="obss_only",
                    semantic_sort_key=("obss_only",),
                    seed=seed,
                )
                for seed in (8, 7)
            ]
            selected, summary = select_groups(
                stage, ood, conditions, stage_target=2
            )
            selected_stage_seeds = sorted(
                item.runs[0].seed
                for item in selected
                if item.cohort == "stage_a"
            )
            self.assertEqual(selected_stage_seeds, [1, 1, 2, 2])
            self.assertEqual(summary["stage_a"]["selected_group_count"], 4)
            self.assertEqual(summary["ood"]["selected_group_count"], 2)

    def test_manifest_uses_relative_paths_and_is_rediscoverable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "raw"
            output = root / "curated"
            selected = [
                group(
                    source,
                    "pair",
                    cohort="ood",
                    semantic_name="obss_only",
                    semantic_sort_key=("obss_only",),
                    seed=11,
                )
            ]
            document = manifest_document(selected, output)
            self.assertTrue(
                all(not Path(item["directory"]).is_absolute()
                    for item in document["runs"])
            )
            atomic_json(output / "experiment_manifest.json", document)
            atomic_json(output / "experiment_manifest.json", document)
            verify_manifest(output, selected)

    def test_result_roots_include_only_original_and_repairs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results = Path(temporary)
            for name in (
                "prediction_stage_a",
                "prediction_stage_a_repair_v1",
                "prediction_stage_a_repair_v1_round2",
                "prediction_stage_a_smoke",
                "prediction_obss",
            ):
                (results / name).mkdir()
            self.assertEqual(
                [path.name for path in result_roots(results, "prediction_stage_a")],
                [
                    "prediction_stage_a",
                    "prediction_stage_a_repair_v1",
                    "prediction_stage_a_repair_v1_round2",
                ],
            )


if __name__ == "__main__":
    unittest.main()
