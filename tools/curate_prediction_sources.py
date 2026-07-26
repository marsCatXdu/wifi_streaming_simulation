#!/usr/bin/env python3
"""Curate commit-consistent fixed-link pairs for prediction analysis."""

from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from prediction_dataset import (
    canonical_json,
    discover_source_runs,
    read_yaml,
    sha256_file,
    validate_analysis_config,
)
from prepare_prediction_stage_a_round2 import (
    Condition,
    condition_templates,
    semantic_condition,
)
from repair_prediction_obss import (
    SCENARIOS as OOD_SCENARIOS,
    scenario_templates,
    semantic_scenario,
)
from validate_outputs import validate_run

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"
ANALYSIS_CONFIG = ROOT / "experiments/configs/prediction_analysis.yaml"
OUTPUT_DIR = RESULTS_DIR / "prediction_source_manifest_provisional"
POLICIES = frozenset({"fixed_link_0", "fixed_link_1"})
STAGE_A_TARGET_PER_CONDITION = 10
STAGE_A_PREFIX = "prediction_stage_a"
OOD_PREFIX = "prediction_obss"

Classifier = Callable[[dict[str, Any]], tuple[str, tuple[str, ...]] | None]


@dataclass(frozen=True)
class Candidate:
    """One validated fixed-link run eligible for pairing."""

    cohort: str
    semantic_name: str
    semantic_sort_key: tuple[str, ...]
    seed: int
    run: int
    project_commit: str
    ns3_commit: str
    policy: str
    config_identity: str
    run_id: str
    run_dir: Path
    source_root: Path

    @property
    def group_key(self) -> tuple[object, ...]:
        """Return the policy-independent matching key."""
        return (
            self.cohort,
            self.semantic_sort_key,
            self.seed,
            self.run,
            self.project_commit,
            self.ns3_commit,
        )

    @property
    def sort_key(self) -> tuple[object, ...]:
        """Return deterministic run ordering."""
        return self.group_key + (self.policy, str(self.run_dir))


@dataclass(frozen=True)
class MatchedGroup:
    """Exactly one validated run for each fixed-link policy."""

    key: tuple[object, ...]
    cohort: str
    semantic_name: str
    semantic_sort_key: tuple[str, ...]
    runs: tuple[Candidate, Candidate]

    @property
    def sort_key(self) -> tuple[object, ...]:
        """Return deterministic group ordering."""
        return self.key


def result_roots(results_dir: Path, prefix: str) -> list[Path]:
    """Return the original result root and every direct repair root."""
    if not results_dir.is_dir():
        return []
    return sorted(
        (
            child
            for child in results_dir.iterdir()
            if child.is_dir()
            and (
                child.name == prefix
                or child.name.startswith(f"{prefix}_repair")
            )
        ),
        key=lambda path: path.name,
    )


def stage_a_classifier(
    expected: set[Condition],
) -> Classifier:
    """Build a classifier restricted to the frozen Stage-A conditions."""

    def classify(config: dict[str, Any]) -> tuple[str, str] | None:
        condition = semantic_condition(config)
        if condition not in expected:
            return None
        return condition.label, (
            condition.correlation_mode,
            "" if condition.rate_mbps_per_station is None
            else str(condition.rate_mbps_per_station),
        )

    return classify


def ood_classifier(identities: dict[str, str]) -> Classifier:
    """Build a classifier restricted to the frozen OOD scenarios."""

    def classify(config: dict[str, Any]) -> tuple[str, str] | None:
        scenario = semantic_scenario(config, identities)
        if scenario not in OOD_SCENARIOS:
            return None
        return scenario, (scenario,)

    return classify


def policy_independent_config(config: dict[str, Any]) -> str:
    """Canonicalize resolved configuration after removing selected policy."""
    normalized = copy.deepcopy(config)
    normalized.pop("run_id", None)
    normalized.pop("policy", None)
    return canonical_json(normalized)


def collect_candidates(
    roots: Iterable[Path],
    cohort: str,
    classifier: Classifier,
    validator: Callable[[Path], dict[str, Any]] = validate_run,
) -> tuple[list[Candidate], dict[str, Any]]:
    """Discover direct completed runs, validate them, and collect identities."""
    candidates: list[Candidate] = []
    stats: dict[str, Any] = {
        "roots": [],
        "visible_run_directories": 0,
        "hidden_directories_ignored": 0,
        "valid_runs": 0,
        "invalid_runs": 0,
        "out_of_scope_runs": 0,
        "non_fixed_policy_runs": 0,
        "invalid_identity_runs": 0,
    }
    for root in sorted((path.resolve() for path in roots), key=str):
        root_stats = {
            "root": str(root),
            "visible_run_directories": 0,
            "hidden_directories_ignored": 0,
            "valid_runs": 0,
        }
        if not root.is_dir():
            root_stats["missing"] = True
            stats["roots"].append(root_stats)
            continue
        for run_dir in sorted(root.iterdir(), key=lambda path: path.name):
            if not run_dir.is_dir():
                continue
            if run_dir.name.startswith("."):
                stats["hidden_directories_ignored"] += 1
                root_stats["hidden_directories_ignored"] += 1
                continue
            stats["visible_run_directories"] += 1
            root_stats["visible_run_directories"] += 1
            try:
                validation = validator(run_dir)
                if validation.get("valid") is not True:
                    raise ValueError("validator did not report valid=true")
                config = json.loads(
                    (run_dir / "resolved_config.json").read_text(encoding="utf-8")
                )
                build = json.loads(
                    (run_dir / "build_info.json").read_text(encoding="utf-8")
                )
                if not isinstance(config, dict) or not isinstance(build, dict):
                    raise ValueError("run identity files must contain mappings")
            except (OSError, ValueError, TypeError, KeyError):
                stats["invalid_runs"] += 1
                continue
            stats["valid_runs"] += 1
            root_stats["valid_runs"] += 1
            semantic = classifier(config)
            if semantic is None:
                stats["out_of_scope_runs"] += 1
                continue
            policy = config.get("policy")
            if policy not in POLICIES:
                stats["non_fixed_policy_runs"] += 1
                continue
            seed = config.get("seed")
            run = config.get("run")
            run_id = config.get("run_id")
            project_commit = build.get("project_git_commit")
            ns3_commit = build.get("ns3_upstream_commit")
            if not (
                isinstance(seed, int)
                and not isinstance(seed, bool)
                and seed > 0
                and isinstance(run, int)
                and not isinstance(run, bool)
                and run > 0
                and isinstance(run_id, str)
                and run_id
                and isinstance(project_commit, str)
                and project_commit
                and isinstance(ns3_commit, str)
                and ns3_commit
            ):
                stats["invalid_identity_runs"] += 1
                continue
            semantic_name, semantic_sort_key = semantic
            candidates.append(
                Candidate(
                    cohort=cohort,
                    semantic_name=semantic_name,
                    semantic_sort_key=semantic_sort_key,
                    seed=seed,
                    run=run,
                    project_commit=project_commit,
                    ns3_commit=ns3_commit,
                    policy=policy,
                    config_identity=policy_independent_config(config),
                    run_id=run_id,
                    run_dir=run_dir.resolve(),
                    source_root=root,
                )
            )
        stats["roots"].append(root_stats)
    return sorted(candidates, key=lambda item: item.sort_key), stats


def match_candidates(
    candidates: Iterable[Candidate],
) -> tuple[list[MatchedGroup], dict[str, int]]:
    """Pair candidates and reject incomplete, inconsistent, or ambiguous groups."""
    buckets: dict[tuple[object, ...], list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        buckets[candidate.group_key].append(candidate)

    groups: list[MatchedGroup] = []
    rejections: Counter[str] = Counter()
    for key in sorted(buckets):
        bucket = buckets[key]
        by_policy: dict[str, list[Candidate]] = defaultdict(list)
        for candidate in bucket:
            by_policy[candidate.policy].append(candidate)
        if any(len(by_policy.get(policy, [])) > 1 for policy in POLICIES):
            rejections["ambiguous_duplicate_policy_groups"] += 1
            continue
        if set(by_policy) != POLICIES:
            rejections["unmatched_groups"] += 1
            continue
        runs = tuple(
            by_policy[policy][0] for policy in sorted(POLICIES)
        )
        if len({candidate.config_identity for candidate in runs}) != 1:
            rejections["policy_config_mismatch_groups"] += 1
            continue
        first = runs[0]
        groups.append(
            MatchedGroup(
                key=key,
                cohort=first.cohort,
                semantic_name=first.semantic_name,
                semantic_sort_key=first.semantic_sort_key,
                runs=runs,
            )
        )
    return sorted(groups, key=lambda item: item.sort_key), dict(sorted(rejections.items()))


def select_groups(
    stage_groups: Iterable[MatchedGroup],
    ood_groups: Iterable[MatchedGroup],
    stage_conditions: Iterable[Condition],
    stage_target: int = STAGE_A_TARGET_PER_CONDITION,
) -> tuple[list[MatchedGroup], dict[str, Any]]:
    """Select a fixed Stage-A quota and every currently matched OOD group."""
    stage_by_name: dict[str, list[MatchedGroup]] = defaultdict(list)
    for group in stage_groups:
        stage_by_name[group.semantic_name].append(group)

    expected_names = sorted(condition.label for condition in stage_conditions)
    selected_stage: list[MatchedGroup] = []
    available_stage: dict[str, int] = {}
    for name in expected_names:
        available = sorted(stage_by_name.get(name, []), key=lambda item: item.sort_key)
        available_stage[name] = len(available)
        if len(available) < stage_target:
            raise ValueError(
                f"Stage-A condition {name} has {len(available)} matched groups; "
                f"{stage_target} required"
            )
        selected_stage.extend(available[:stage_target])
    unexpected = sorted(set(stage_by_name) - set(expected_names))
    if unexpected:
        raise ValueError(f"unexpected Stage-A conditions: {', '.join(unexpected)}")

    selected_ood = sorted(ood_groups, key=lambda item: item.sort_key)
    ood_counts = Counter(group.semantic_name for group in selected_ood)
    selected = sorted(selected_stage + selected_ood, key=lambda item: item.sort_key)
    return selected, {
        "stage_a": {
            "condition_count": len(expected_names),
            "target_groups_per_condition": stage_target,
            "available_groups_by_condition": available_stage,
            "selected_groups_by_condition": {
                name: stage_target for name in expected_names
            },
            "selected_group_count": len(selected_stage),
            "selected_run_count": 2 * len(selected_stage),
        },
        "ood": {
            "selected_groups_by_scenario": {
                scenario: ood_counts.get(scenario, 0)
                for scenario in sorted(OOD_SCENARIOS)
            },
            "selected_group_count": len(selected_ood),
            "selected_run_count": 2 * len(selected_ood),
        },
        "total": {
            "selected_group_count": len(selected),
            "selected_run_count": 2 * len(selected),
        },
    }


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically write stable, pretty-printed JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, encoding="utf-8"
    ) as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")
        temporary = Path(output.name)
    os.replace(temporary, path)


def manifest_document(
    selected: Iterable[MatchedGroup], output_dir: Path
) -> dict[str, Any]:
    """Build a discover_source_runs-compatible manifest."""
    runs = []
    for group in sorted(selected, key=lambda item: item.sort_key):
        for candidate in group.runs:
            runs.append(
                {
                    "directory": os.path.relpath(candidate.run_dir, output_dir),
                    "run_id": candidate.run_id,
                    "status": "complete",
                }
            )
    return {
        "experiment": "prediction-provisional-curated-sources",
        "runs": runs,
        "schema_version": 1,
    }


def verify_manifest(
    output_dir: Path,
    selected: Iterable[MatchedGroup],
) -> None:
    """Verify discovery, exact source paths, and two-policy group membership."""
    expected_groups = list(selected)
    expected_by_path = {
        candidate.run_dir: candidate
        for group in expected_groups
        for candidate in group.runs
    }
    discovered = discover_source_runs([output_dir])
    observed_paths = {source.run_dir for source in discovered}
    if (
        observed_paths != set(expected_by_path)
        or len(discovered) != len(expected_by_path)
    ):
        raise ValueError("emitted manifest did not rediscover the exact selected runs")

    policies_by_group: dict[tuple[object, ...], set[str]] = defaultdict(set)
    counts_by_group: Counter[tuple[object, ...]] = Counter()
    for source in discovered:
        expected = expected_by_path[source.run_dir]
        config = json.loads(
            (source.run_dir / "resolved_config.json").read_text(encoding="utf-8")
        )
        build = json.loads(
            (source.run_dir / "build_info.json").read_text(encoding="utf-8")
        )
        observed_identity = (
            config.get("seed"),
            config.get("run"),
            build.get("project_git_commit"),
            build.get("ns3_upstream_commit"),
            config.get("policy"),
            policy_independent_config(config),
        )
        expected_identity = (
            expected.seed,
            expected.run,
            expected.project_commit,
            expected.ns3_commit,
            expected.policy,
            expected.config_identity,
        )
        if observed_identity != expected_identity:
            raise ValueError(f"source identity changed after curation: {source.run_dir}")
        policies_by_group[expected.group_key].add(expected.policy)
        counts_by_group[expected.group_key] += 1
    for group in expected_groups:
        if (
            counts_by_group[group.key] != 2
            or policies_by_group[group.key] != POLICIES
        ):
            raise ValueError(f"curated group is not an exact policy pair: {group.key}")


def curate(
    *,
    results_dir: Path = RESULTS_DIR,
    output_dir: Path = OUTPUT_DIR,
    analysis_path: Path = ANALYSIS_CONFIG,
    validator: Callable[[Path], dict[str, Any]] = validate_run,
) -> dict[str, Any]:
    """Curate source runs and atomically publish a manifest and report."""
    stage_conditions = set(
        condition_templates(
            ROOT / "experiments/configs/prediction_stage_a.yaml"
        )
    )
    if len(stage_conditions) != 16:
        raise ValueError(f"expected 16 Stage-A conditions, found {len(stage_conditions)}")
    _, ood_identities = scenario_templates(
        ROOT / "experiments/configs/prediction_obss.yaml"
    )
    analysis = read_yaml(analysis_path)
    validate_analysis_config(analysis)

    stage_roots = result_roots(results_dir, STAGE_A_PREFIX)
    ood_roots = result_roots(results_dir, OOD_PREFIX)
    stage_candidates, stage_discovery = collect_candidates(
        stage_roots,
        "stage_a",
        stage_a_classifier(stage_conditions),
        validator,
    )
    ood_candidates, ood_discovery = collect_candidates(
        ood_roots,
        "ood",
        ood_classifier(ood_identities),
        validator,
    )
    stage_groups, stage_rejections = match_candidates(stage_candidates)
    ood_groups, ood_rejections = match_candidates(ood_candidates)
    selected, summary = select_groups(
        stage_groups, ood_groups, stage_conditions
    )

    minimum = analysis["minimum_run_groups_per_required_ood_scenario"]
    ood_selected = summary["ood"]["selected_groups_by_scenario"]
    formal_by_scenario = {
        scenario: {
            "selected_run_groups": ood_selected.get(scenario, 0),
            "minimum_required_run_groups": minimum,
            "sufficient": ood_selected.get(scenario, 0) >= minimum,
        }
        for scenario in analysis["required_ood_scenarios"]
    }
    formal_ood_sufficient = all(
        value["sufficient"] for value in formal_by_scenario.values()
    )
    summary["ood"]["formal_sufficiency"] = {
        "status": "sufficient" if formal_ood_sufficient else "insufficient",
        "all_required_scenarios_sufficient": formal_ood_sufficient,
        "by_scenario": formal_by_scenario,
    }

    manifest = manifest_document(selected, output_dir.resolve())
    report = {
        "curation_schema_version": 1,
        "analysis_contract": {
            "path": os.path.relpath(analysis_path.resolve(), output_dir.resolve()),
            "sha256": sha256_file(analysis_path),
        },
        "selection_policy": {
            "fixed_link_policies": sorted(POLICIES),
            "match_fields": [
                "semantic_condition_or_scenario",
                "seed",
                "run",
                "project_git_commit",
                "ns3_upstream_commit",
            ],
            "pair_config_rule": "resolved configs identical after removing run_id and policy",
            "stage_a_order": "semantic condition, seed, run, commits",
            "stage_a_quota_per_condition": STAGE_A_TARGET_PER_CONDITION,
            "ood_rule": "all currently available unambiguous matched groups",
        },
        "discovery": {
            "stage_a": stage_discovery,
            "ood": ood_discovery,
        },
        "matching": {
            "stage_a": {
                "candidate_run_count": len(stage_candidates),
                "matched_group_count": len(stage_groups),
                "rejections": stage_rejections,
            },
            "ood": {
                "candidate_run_count": len(ood_candidates),
                "matched_group_count": len(ood_groups),
                "rejections": ood_rejections,
            },
        },
        "summary": summary,
        "verification": {
            "discover_source_runs": "PASS",
            "exactly_two_fixed_link_policies_per_group": "PASS",
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(output_dir / "experiment_manifest.json", manifest)
    verify_manifest(output_dir, selected)
    atomic_json(output_dir / "curation_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--analysis-config", type=Path, default=ANALYSIS_CONFIG)
    args = parser.parse_args()
    report = curate(
        results_dir=args.results_dir,
        output_dir=args.output_dir,
        analysis_path=args.analysis_config,
    )
    summary = report["summary"]
    ood = summary["ood"]
    print(
        "CURATION_COMPLETE "
        f"stage_a_groups={summary['stage_a']['selected_group_count']} "
        f"ood_groups={ood['selected_group_count']} "
        f"obss_only={ood['selected_groups_by_scenario']['obss_only']} "
        "obss_plus_legacy_mixed8="
        f"{ood['selected_groups_by_scenario']['obss_plus_legacy_mixed8']} "
        f"ood_formal_status={ood['formal_sufficiency']['status']}"
    )


if __name__ == "__main__":
    main()
