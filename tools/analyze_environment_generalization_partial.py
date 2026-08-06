#!/usr/bin/env python3
"""Analyze a balanced valid subset of a failed qualification campaign."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import random
import shutil
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Sequence

import analyze_environment_generalization_qualification as formal
import plot_environment_generalization_qualification as qualification_plots
import run_experiments


ANALYSIS_ID = "environment-generalization-qualification-partial-balanced-v1"
WARNING = (
    "EXPLORATORY PARTIAL RESULT: the preregistered 576-run campaign did not "
    "close. This balanced complete-scenario panel cannot establish formal "
    "qualification or promotion."
)


class PartialAnalysisError(RuntimeError):
    """Raised when partial evidence is inconsistent or cannot be balanced."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PartialAnalysisError(message)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=formal.ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise PartialAnalysisError(f"cannot resolve analyzer Git commit: {error}") from error


def _completed_frame_count(run_dir: Path) -> int:
    frames_path = run_dir / "frames.csv"
    try:
        with frames_path.open(newline="", encoding="utf-8") as source:
            rows = csv.DictReader(source)
            _require(
                rows.fieldnames is not None and "incomplete" in rows.fieldnames,
                f"{frames_path}: incomplete column is absent",
            )
            return sum(
                not qualification_plots._flag(row["incomplete"], "incomplete")
                for row in rows
            )
    except (OSError, csv.Error) as error:
        raise PartialAnalysisError(f"cannot read {frames_path}: {error}") from error


def _manifest_and_jobs(
    campaign_input: Path,
    contract: dict[str, Any],
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    tuple[str, ...],
    dict[str, tuple[str, ...]],
    dict[str, Any],
]:
    run_root, manifest_path = formal._manifest_root(campaign_input)
    manifest = formal._read_json(manifest_path)
    _require(manifest.get("schema_version") == formal.MANIFEST_SCHEMA_VERSION,
             "campaign manifest schema differs")
    _require(manifest.get("experiment") == formal.EXPECTED_EXPERIMENT,
             "campaign experiment identity differs")
    _require(manifest.get("project_commit") == _git_head(),
             "partial analyzer checkout differs from campaign commit")

    document = run_experiments.load_yaml(formal.CONFIG_PATH)
    matrix_sha256 = run_experiments.matrix_sha256(document)
    _require(manifest.get("matrix_sha256") == matrix_sha256,
             "campaign matrix hash differs")
    runtime = run_experiments.validate_runtime_contract(document)
    _require(runtime is not None, "qualification runtime closure is absent")
    _require(
        manifest.get("runtime_contract_id") == runtime["runtime_contract_id"]
        and manifest.get("runtime_contract_sha256") == runtime["runtime_contract_sha256"]
        and _canonical_json(manifest.get("source_artifacts"))
        == _canonical_json(runtime["source_artifacts"]),
        "campaign runtime closure differs",
    )

    specs = run_experiments.expand_config(document)
    arm_map = formal._arm_map(contract)
    expected: dict[str, dict[str, Any]] = {}
    expected_by_scenario: dict[str, set[str]] = {}
    for spec in specs:
        run_id = run_experiments.derive_run_id(
            spec["config"],
            spec["seed"],
            spec["run"],
            manifest["ns3_upstream_commit"],
            manifest["project_commit"],
            runtime,
            spec["scenario"],
        )
        identity = (spec["config"]["topology"], spec["config"]["policy"])
        _require(run_id not in expected and identity in arm_map,
                 "frozen expansion has a duplicate run or unknown arm")
        enriched = {**spec, "run_id": run_id, "arm_id": arm_map[identity]}
        expected[run_id] = enriched
        expected_by_scenario.setdefault(spec["scenario"]["scenario_id"], set()).add(run_id)
    _require(len(expected) == 576, "frozen expansion does not contain 576 runs")

    runs = manifest.get("runs")
    _require(isinstance(runs, list) and 0 < len(runs) < 576,
             "partial manifest must contain between 1 and 575 runs")
    manifest_by_id: dict[str, dict[str, Any]] = {}
    for row in runs:
        _require(isinstance(row, dict), "manifest run row is not an object")
        run_id = row.get("run_id")
        _require(isinstance(run_id, str) and run_id not in manifest_by_id,
                 "manifest has an invalid or duplicate run ID")
        _require(run_id in expected, f"manifest has an unexpected run ID: {run_id}")
        spec = expected[run_id]
        _require(
            row.get("status") == "complete"
            and row.get("directory") == run_id
            and row.get("seed") == spec["seed"]
            and row.get("run") == spec["run"]
            and _canonical_json(row.get("config")) == _canonical_json(spec["config"])
            and _canonical_json(row.get("scenario"))
            == _canonical_json(spec["scenario"]),
            f"manifest identity differs for {run_id}",
        )
        _require((run_root / run_id).is_dir(), f"canonical run directory is absent: {run_id}")
        manifest_by_id[run_id] = row

    families, frozen_scenarios = formal._family_scenario_order(specs)
    completed_counts = {
        run_id: _completed_frame_count(run_root / run_id) for run_id in manifest_by_id
    }
    p99_eligible_ids = {
        run_id
        for run_id, count in completed_counts.items()
        if count >= formal.MINIMUM_COMPLETED_FRAMES
    }
    p99_ineligible_runs = [
        {"run_id": run_id, "completed_frame_count": completed_counts[run_id]}
        for run_id in sorted(set(manifest_by_id) - p99_eligible_ids)
    ]

    complete_by_family: dict[str, list[str]] = {}
    incomplete_scenarios: list[dict[str, Any]] = []
    for family in families:
        complete_by_family[family] = []
        for scenario_id in frozen_scenarios[family]:
            expected_ids = expected_by_scenario[scenario_id]
            present_ids = expected_ids & set(manifest_by_id)
            eligible_ids = expected_ids & p99_eligible_ids
            if eligible_ids == expected_ids:
                complete_by_family[family].append(scenario_id)
            else:
                incomplete_scenarios.append(
                    {
                        "family_id": family,
                        "scenario_id": scenario_id,
                        "canonical_run_count": len(present_ids),
                        "p99_eligible_run_count": len(eligible_ids),
                        "missing_run_count": len(expected_ids - present_ids),
                        "missing_run_ids": sorted(expected_ids - present_ids),
                        "p99_ineligible_run_ids": sorted(
                            present_ids - p99_eligible_ids
                        ),
                    }
                )
    balanced_scenario_count = min(len(complete_by_family[family]) for family in families)
    _require(balanced_scenario_count > 0,
             "no positive complete-scenario balance exists across all families")
    selected_scenarios = {
        family: tuple(complete_by_family[family][:balanced_scenario_count])
        for family in families
    }
    selected_scenario_ids = {
        scenario_id for values in selected_scenarios.values() for scenario_id in values
    }
    selected_ids = {
        run_id
        for scenario_id in selected_scenario_ids
        for run_id in expected_by_scenario[scenario_id]
    }
    _require(selected_ids <= set(manifest_by_id),
             "balanced scenario selection contains a missing run")

    partial_contract = copy.deepcopy(contract)
    population = partial_contract["population"]
    population["scenarios_per_family"] = balanced_scenario_count
    population["paired_unit_count"] = (
        len(families)
        * balanced_scenario_count
        * population["replicates_per_scenario"]
    )
    population["simulation_run_count"] = (
        population["paired_unit_count"] * population["arm_count"]
    )
    _require(population["simulation_run_count"] == len(selected_ids),
             "balanced population arithmetic differs")

    arm_order = {arm: index for index, arm in enumerate(formal.ARM_IDS)}
    family_order = {family: index for index, family in enumerate(families)}
    scenario_order = {
        scenario: index
        for family in families
        for index, scenario in enumerate(selected_scenarios[family])
    }
    jobs: list[dict[str, Any]] = []
    for run_id in selected_ids:
        spec = expected[run_id]
        scenario = spec["scenario"]
        jobs.append(
            {
                "run_id": run_id,
                "run_dir": str((run_root / run_id).resolve()),
                "project_commit": manifest["project_commit"],
                "ns3_upstream_commit": manifest["ns3_upstream_commit"],
                "arm_id": spec["arm_id"],
                "family_id": scenario["family_id"],
                "scenario_id": scenario["scenario_id"],
                "parameter_sample": scenario["parameter_sample"],
                "seed": spec["seed"],
                "run": spec["run"],
                "expected_config": spec["config"],
                "required_secondary_airtime_event_schema_version": (
                    None
                    if spec["arm_id"] == "str_mlo_nmaxinflights_1"
                    else contract["raw_evidence"][
                        "require_secondary_airtime_event_schema_version_on_selective_arms"
                    ]
                ),
            }
        )
    jobs.sort(
        key=lambda row: (
            family_order[row["family_id"]],
            scenario_order[row["scenario_id"]],
            row["seed"],
            row["run"],
            arm_order[row["arm_id"]],
        )
    )
    selected_counts = {
        family: len(selected_scenarios[family]) for family in families
    }
    snapshot = {
        "role": "exploratory_partial_failure_snapshot",
        "warning": WARNING,
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "campaign_valid_run_count": len(manifest_by_id),
        "campaign_missing_run_count": len(expected) - len(manifest_by_id),
        "complete_scenario_count_by_family": {
            family: len(complete_by_family[family]) for family in families
        },
        "balanced_selected_scenario_count_by_family": selected_counts,
        "balanced_selected_run_count": len(jobs),
        "balanced_selected_paired_unit_count": population["paired_unit_count"],
        "minimum_completed_frames_per_run": formal.MINIMUM_COMPLETED_FRAMES,
        "p99_ineligible_canonical_runs": p99_ineligible_runs,
        "selected_scenario_ids_by_family": {
            family: list(selected_scenarios[family]) for family in families
        },
        "complete_but_balance_excluded_scenario_ids_by_family": {
            family: complete_by_family[family][balanced_scenario_count:]
            for family in families
        },
        "incomplete_scenarios": incomplete_scenarios,
        "all_missing_run_ids": sorted(set(expected) - set(manifest_by_id)),
        "selection_rule": (
            "within each frozen family, retain the first N scenarios in frozen "
            "order for which all 12 runs are canonical and each run has at least "
            "the frozen minimum completed-frame count; N is the minimum eligible "
            "scenario count over the six families"
        ),
    }
    manifest_identity = {
        "path": str(manifest_path),
        "sha256": snapshot["manifest_sha256"],
        "run_root": str(run_root),
        "schema_version": manifest["schema_version"],
        "experiment": manifest["experiment"],
        "matrix_sha256": manifest["matrix_sha256"],
        "project_commit": manifest["project_commit"],
        "ns3_upstream_commit": manifest["ns3_upstream_commit"],
        "runtime_contract_id": manifest["runtime_contract_id"],
        "runtime_contract_sha256": manifest["runtime_contract_sha256"],
        "source_artifacts": copy.deepcopy(manifest["source_artifacts"]),
        "complete_run_count": len(manifest_by_id),
        "selected_balanced_run_count": len(jobs),
        "expanded_matrix_identity_verified": True,
        "derived_run_ids_verified": True,
        "scenario_identities_verified": True,
        "formal_matrix_closure": False,
    }
    return manifest_identity, jobs, families, selected_scenarios, partial_contract, snapshot


def _partial_markdown(report: dict[str, Any]) -> str:
    snapshot = report["partial_snapshot"]
    lines = [
        "# Exploratory partial held-out environment result",
        "",
        f"**{WARNING}**",
        "",
        f"The stopped campaign contains {snapshot['campaign_valid_run_count']} valid runs. "
        f"This analysis uses {snapshot['balanced_selected_run_count']} runs in "
        f"{snapshot['balanced_selected_paired_unit_count']} fully paired units.",
        "",
        "## Aggregate balanced-panel results",
        "",
        "| Arm | Miss rate | Completed P99 | Sender airtime | Background throughput |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for arm in formal.ARM_IDS:
        treatment = report["treatments"][arm]
        metrics = treatment["metrics"]
        lines.append(
            f"| {treatment['label']} | "
            f"{100 * metrics['all_generated_deadline_miss_rate']['estimate']:.4f}% | "
            f"{metrics['completed_frame_hf7_p99_us']['estimate'] / 1000:.3f} ms | "
            f"{metrics['sender_airtime_us']['estimate'] / 1000:.3f} ms | "
            f"{metrics['background_throughput_mbps']['estimate']:.3f} Mbit/s |"
        )
    lines.extend(["", "## Exploratory comparisons with STR MLO", ""])
    for entry in report["comparisons"].values():
        if entry["baseline_arm_id"] != "str_mlo_nmaxinflights_1":
            continue
        values = entry["aggregate"]
        relative = values["relative_deadline_miss_reduction"]["estimate"]
        lines.extend(
            [
                f"### {formal.ARM_LABELS[entry['candidate_arm_id']]}",
                "",
                f"- Miss delta: {100 * values['deadline_miss_delta']['estimate']:.4f} "
                f"percentage points (95% interval "
                f"[{100 * values['deadline_miss_delta']['ci95_low']:.4f}, "
                f"{100 * values['deadline_miss_delta']['ci95_high']:.4f}]).",
                f"- Relative miss reduction: "
                f"{100 * relative:.2f}%." if relative is not None else
                "- Relative miss reduction: not assessable.",
                f"- Completed-P99 delta: "
                f"{values['completed_p99_delta_us']['estimate'] / 1000:.3f} ms "
                f"(95% interval [{values['completed_p99_delta_us']['ci95_low'] / 1000:.3f}, "
                f"{values['completed_p99_delta_us']['ci95_high'] / 1000:.3f}]).",
                f"- Sender-airtime ratio: {values['sender_airtime_ratio']['estimate']:.4f} "
                f"(95% interval [{values['sender_airtime_ratio']['ci95_low']:.4f}, "
                f"{values['sender_airtime_ratio']['ci95_high']:.4f}]).",
                f"- Background-throughput loss: "
                f"{100 * values['background_throughput_loss']['estimate']:.3f}% "
                f"(95% interval "
                f"[{100 * values['background_throughput_loss']['ci95_low']:.3f}%, "
                f"{100 * values['background_throughput_loss']['ci95_high']:.3f}%]).",
                "",
            ]
        )
    lines.extend(
        [
            "## Missingness and interpretation",
            "",
            f"- Incomplete scenarios: {len(snapshot['incomplete_scenarios'])}.",
            f"- Missing planned runs: {snapshot['campaign_missing_run_count']}.",
            "- Every selected raw run passed the unchanged strict validator.",
            "- Every selected scenario contains all four replicates and all three arms.",
            "- Completed-frame P99 is survivor-conditioned and must be read with miss rate.",
            "- No formal qualification or promotion decision may use this partial report.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    _require(bool(rows), f"cannot write empty table: {path.name}")
    fields = list(rows[0])
    _require(all(list(row) == fields for row in rows),
             f"table columns differ: {path.name}")
    with path.open("w", newline="", encoding="ascii") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="ascii",
    )


def _write_analysis(
    output: Path,
    report: dict[str, Any],
    observations: Sequence[dict[str, Any]],
    paired: Sequence[dict[str, Any]],
    families: Sequence[dict[str, Any]],
    scenarios: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    _require(not output.exists(), f"analysis output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        paths = {
            "report": temporary / "partial_report.json",
            "markdown": temporary / "partial_report.md",
            "runs": temporary / "run_metrics.csv",
            "paired": temporary / "paired_metrics.csv",
            "families": temporary / "family_metrics.csv",
            "scenarios": temporary / "scenario_metrics.csv",
        }
        _write_json(paths["report"], report)
        paths["markdown"].write_text(_partial_markdown(report), encoding="ascii")
        _write_csv(paths["runs"], formal._run_metric_rows(observations))
        _write_csv(paths["paired"], paired)
        _write_csv(paths["families"], families)
        _write_csv(paths["scenarios"], scenarios)
        artifacts = {
            path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in paths.values()
        }
        manifest = {
            "schema_version": 1,
            "manifest_id": "environment-generalization-partial-analysis-artifacts-v1",
            "analysis": ANALYSIS_ID,
            "warning": WARNING,
            "source_campaign_manifest": report["source_closure"]["campaign_manifest"],
            "analyzer": report["source_closure"]["analyzer"],
            "counts": {
                "strictly_validated_runs": len(observations),
                "paired_units": len(paired),
                "family_comparison_rows": len(families),
                "scenario_comparison_rows": len(scenarios),
            },
            "artifacts": artifacts,
        }
        manifest_path = temporary / "analysis_artifact_manifest.json"
        _write_json(manifest_path, manifest)
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "path": str(output / "analysis_artifact_manifest.json"),
        "bytes": (output / "analysis_artifact_manifest.json").stat().st_size,
        "sha256": _sha256(output / "analysis_artifact_manifest.json"),
    }


def _collect_historical(
    observations: Sequence[dict[str, Any]],
    report: dict[str, Any],
    workers: int,
) -> dict[str, dict[str, Any]]:
    source = report["source_closure"]["campaign_manifest"]
    jobs = [
        {
            "run_id": row["run_id"],
            "run_dir": row["run_dir"],
            "arm_id": row["arm_id"],
            "project_commit": source["project_commit"],
            "ns3_upstream_commit": source["ns3_upstream_commit"],
        }
        for row in observations
    ]
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(qualification_plots._historical_job, job): job
            for job in jobs
        }
        for future in as_completed(futures):
            job = futures[future]
            try:
                results.append(future.result())
            except Exception as error:
                for pending in futures:
                    pending.cancel()
                raise PartialAnalysisError(
                    f"historical validation failed for {job['run_id']}: {error}"
                ) from error
    _require(len(results) == len(jobs), "historical result count differs")
    grouped = {
        arm: {
            "latencies_us": [],
            "bursts": [],
            "generated": 0,
            "completed": 0,
            "misses": 0,
            "run_count": 0,
        }
        for arm in formal.ARM_IDS
    }
    for result in results:
        values = grouped[result["arm_id"]]
        values["latencies_us"].extend(result["latencies_us"])
        values["bursts"].extend(result["bursts"])
        for field in ("generated", "completed", "misses"):
            values[field] += result[field]
        values["run_count"] += 1
    expected_per_arm = len(observations) // len(formal.ARM_IDS)
    _require(
        all(grouped[arm]["run_count"] == expected_per_arm for arm in formal.ARM_IDS),
        "historical arm balance differs",
    )
    return grouped


def _write_plots(
    output: Path,
    report: dict[str, Any],
    family_rows: Sequence[dict[str, Any]],
    scenario_rows: Sequence[dict[str, Any]],
    historical: dict[str, dict[str, Any]],
    analysis_identity: dict[str, Any],
) -> None:
    _require(not output.exists(), f"plot output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    original_save = qualification_plots._save_figure
    original_require = qualification_plots._require

    expected_scenario_count = sum(
        len(values)
        for values in report["partial_snapshot"][
            "selected_scenario_ids_by_family"
        ].values()
    )
    for comparison in qualification_plots.STR_COMPARISONS.values():
        _require(
            sum(row["comparison_id"] == comparison for row in scenario_rows)
            == expected_scenario_count,
            f"partial scenario row count differs for {comparison}",
        )
    _require(
        sum(
            row["comparison_id"] == qualification_plots.DISTRIBUTIONAL_V2_COMPARISON
            for row in scenario_rows
        )
        == expected_scenario_count,
        "partial distributional-versus-V2 scenario row count differs",
    )

    def labeled_save(figure: Any, directory: Path, name: str) -> list[Path]:
        figure.text(
            0.5,
            0.005,
            (
                "EXPLORATORY PARTIAL BALANCED PANEL - "
                f"{report['partial_snapshot']['balanced_selected_run_count']}/576 runs"
            ),
            ha="center",
            va="bottom",
            fontsize=8,
            color="firebrick",
            weight="bold",
        )
        return original_save(figure, directory, name)

    def partial_plot_require(condition: bool, message: str) -> None:
        if message.startswith("scenario row count differs for ") or message == (
            "distributional-versus-V2 scenario row count differs"
        ):
            return
        original_require(condition, message)

    qualification_plots._save_figure = labeled_save
    qualification_plots._require = partial_plot_require
    try:
        artifacts = qualification_plots.render_figures(
            report, family_rows, scenario_rows, historical, temporary
        )
        manifest = {
            "schema_version": 1,
            "manifest_id": "environment-generalization-partial-plots-v1",
            "analysis": ANALYSIS_ID,
            "warning": WARNING,
            "analysis_artifact_manifest": analysis_identity,
            "plotter": {
                "path": str(Path(__file__).resolve()),
                "sha256": _sha256(Path(__file__).resolve()),
                "formal_plotter_sha256": _sha256(
                    formal.ROOT / "tools/plot_environment_generalization_qualification.py"
                ),
            },
            "historical_raw_revalidation": {
                "run_count": sum(
                    historical[arm]["run_count"] for arm in formal.ARM_IDS
                ),
                "all_selected_runs_passed": True,
                "completed_frame_population_is_survivor_conditioned": True,
            },
            "artifacts": {
                path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
                for path in artifacts
            },
        }
        _write_json(temporary / "plot_artifact_manifest.json", manifest)
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    finally:
        qualification_plots._save_figure = original_save
        qualification_plots._require = original_require


def plot_existing(analysis: Path, plot_output: Path, workers: int) -> None:
    report_path = analysis / "partial_report.json"
    run_path = analysis / "run_metrics.csv"
    family_path = analysis / "family_metrics.csv"
    scenario_path = analysis / "scenario_metrics.csv"
    _require(
        all(path.is_file() for path in (report_path, run_path, family_path, scenario_path)),
        "existing partial analysis is incomplete",
    )
    report = formal._read_json(report_path)
    _require(report.get("analysis") == ANALYSIS_ID,
             "existing analysis identity differs")

    def read_csv(path: Path) -> list[dict[str, str]]:
        with path.open(newline="", encoding="ascii") as source:
            rows = list(csv.DictReader(source))
        _require(bool(rows), f"existing table is empty: {path.name}")
        return rows

    run_rows = read_csv(run_path)
    family_rows = read_csv(family_path)
    scenario_rows = read_csv(scenario_path)
    historical = _collect_historical(run_rows, report, workers)
    manifest_path = analysis / "analysis_artifact_manifest.json"
    _write_plots(
        plot_output,
        report,
        family_rows,
        scenario_rows,
        historical,
        {
            "path": str(manifest_path),
            "bytes": manifest_path.stat().st_size,
            "sha256": _sha256(manifest_path),
        },
    )


def analyze(
    campaign_input: Path,
    analysis_output: Path,
    plot_output: Path,
    workers: int,
) -> None:
    contract = formal.load_analysis_contract()
    manifest_identity, jobs, families, scenarios, partial_contract, snapshot = (
        _manifest_and_jobs(campaign_input, contract)
    )
    observations = formal.collect_observations(jobs, workers)
    grid = formal.build_observation_grid(
        observations, families, scenarios, partial_contract
    )
    report, paired, family_rows, scenario_rows = formal.build_report(
        observations,
        grid,
        families,
        scenarios,
        partial_contract,
        manifest_identity,
        {
            "project_commit": manifest_identity["project_commit"],
            "analysis_checkout_head": _git_head(),
            "formal_clean_checkout_claim": False,
        },
    )
    report["analysis"] = ANALYSIS_ID
    report["evidence_role"] = "exploratory partial balanced failure snapshot"
    report["partial_snapshot"] = snapshot
    report["formal_qualification_status"] = {
        "status": "not_assessable",
        "reason": WARNING,
    }
    report["source_closure"]["formal_analyzer"] = report["source_closure"]["analyzer"]
    report["source_closure"]["analyzer"] = {
        "path": str(Path(__file__).resolve()),
        "sha256": _sha256(Path(__file__).resolve()),
    }
    exploratory_gates = copy.deepcopy(report["direct_str_victory"])
    report["exploratory_direct_str_gate_snapshot"] = exploratory_gates
    report["direct_str_victory"] = {
        arm: {"status": "not_assessable", "reason": WARNING}
        for arm in exploratory_gates
    }
    report["parent_promotion_readiness"] = {
        "status": "not_assessable",
        "reason": WARNING,
    }
    analysis_identity = _write_analysis(
        analysis_output,
        report,
        observations,
        paired,
        family_rows,
        scenario_rows,
    )
    historical = _collect_historical(observations, report, workers)
    _write_plots(
        plot_output,
        report,
        family_rows,
        scenario_rows,
        historical,
        analysis_identity,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign_input", type=Path, nargs="?")
    parser.add_argument("--analysis-output", type=Path)
    parser.add_argument("--existing-analysis", type=Path)
    parser.add_argument("--plot-output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=16)
    arguments = parser.parse_args(argv)
    if arguments.workers <= 0:
        parser.error("--workers must be positive")
    if (arguments.existing_analysis is None) == (arguments.campaign_input is None):
        parser.error("provide exactly one of campaign_input or --existing-analysis")
    if arguments.campaign_input is not None and arguments.analysis_output is None:
        parser.error("--analysis-output is required with campaign_input")
    try:
        if arguments.existing_analysis is not None:
            plot_existing(
                arguments.existing_analysis,
                arguments.plot_output,
                arguments.workers,
            )
        else:
            analyze(
                arguments.campaign_input,
                arguments.analysis_output,
                arguments.plot_output,
                arguments.workers,
            )
    except (PartialAnalysisError, formal.QualificationAnalysisError) as error:
        parser.exit(1, f"ERROR: {error}\n")
    print(f"WROTE plots={arguments.plot_output} role={ANALYSIS_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
