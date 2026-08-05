#!/usr/bin/env python3
"""Generate the frozen environment-generalization scenario artifacts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Any, Sequence

import yaml

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from run_experiments import (  # noqa: E402
    cli_arguments,
    expand_config,
    load_yaml,
    matrix_sha256,
)


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_CONTRACT = (
    ROOT / "experiments/model-selection/environment-generalization-v1.json"
)
SCHEMA_VERSION = 1
SAMPLING_ALGORITHM = "sha256_latin_hypercube_v1"
PHASE_ORDER = (
    "randomized_collection",
    "closed_loop_qualification",
    "preflight",
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
SCENARIO_ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
ALLOWED_PARAMETER_PREFIXES = {"background", "obss", "propagation", "stream"}
DERIVED_KIND = "derived_frame_period_us"


class GeneralizationContractError(ValueError):
    """Raised when the frozen generalization contract is inconsistent."""


def canonical_json(value: Any) -> str:
    """Return the repository's compact canonical JSON representation."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_bytes(value: bytes) -> str:
    """Hash one byte string."""

    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    """Hash one regular file without loading it all into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GeneralizationContractError(message)


def _exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    _require(isinstance(value, dict), f"{label} must be an object")
    _require(set(value) == expected, f"{label} has missing or unknown fields")
    return value


def _positive_int(value: Any, label: str) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value > 0,
        f"{label} must be a positive integer",
    )
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0,
        f"{label} must be a nonnegative integer",
    )
    return value


def _finite_number(value: Any, label: str) -> float:
    _require(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value)),
        f"{label} must be finite",
    )
    return float(value)


def _sha256(value: Any, label: str) -> str:
    _require(
        isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None,
        f"{label} must be a lowercase SHA-256 digest",
    )
    return value


def _project_path(
    root: Path,
    declared: Any,
    label: str,
    *,
    must_exist: bool,
) -> Path:
    _require(
        isinstance(declared, str) and bool(declared),
        f"{label} must be a nonempty project-relative path",
    )
    relative = Path(declared)
    _require(not relative.is_absolute(), f"{label} must be project-relative")
    _require(
        all(part not in {"", ".", ".."} for part in relative.parts),
        f"{label} must be canonical",
    )
    root = root.resolve()
    cursor = root
    for part in relative.parts:
        cursor /= part
        _require(not cursor.is_symlink(), f"{label} may not traverse a symlink")
    candidate = root / relative
    try:
        resolved = candidate.resolve(strict=must_exist)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, ValueError) as error:
        raise GeneralizationContractError(
            f"{label} is missing or outside the project"
        ) from error
    if must_exist:
        _require(resolved.is_file(), f"{label} must be a regular file")
    else:
        _require(candidate.parent.is_dir(), f"{label} parent directory is missing")
        _require(not candidate.is_symlink(), f"{label} may not be a symlink")
    return candidate


def _sampling_digest(*parts: Any) -> bytes:
    encoded = canonical_json(list(parts)).encode("ascii")
    return hashlib.sha256(encoded).digest()


def latin_hypercube_strata(
    salt: str,
    phase_name: str,
    family_id: str,
    parameter_name: str,
    sample_count: int,
) -> tuple[int, ...]:
    """Return each local sample's deterministic Latin-hypercube stratum."""

    ordered = sorted(
        range(sample_count),
        key=lambda index: _sampling_digest(
            salt,
            phase_name,
            family_id,
            parameter_name,
            "permutation",
            index,
        ),
    )
    strata = [0] * sample_count
    for stratum, sample_index in enumerate(ordered):
        strata[sample_index] = stratum
    return tuple(strata)


def _unit_fraction(
    salt: str,
    phase_name: str,
    family_id: str,
    parameter_name: str,
    local_sample: int,
    global_sample: int,
    stratum: int,
    sample_count: int,
) -> tuple[int, int]:
    jitter = int.from_bytes(
        _sampling_digest(
            salt,
            phase_name,
            family_id,
            parameter_name,
            "jitter",
            local_sample,
            global_sample,
        )[:8],
        byteorder="big",
    )
    denominator = sample_count * (1 << 64)
    numerator = stratum * (1 << 64) + jitter
    return numerator, denominator


def _sample_parameter(
    specification: dict[str, Any],
    *,
    salt: str,
    phase_name: str,
    family_id: str,
    parameter_name: str,
    local_sample: int,
    global_sample: int,
    sample_count: int,
) -> Any:
    strata = latin_hypercube_strata(
        salt, phase_name, family_id, parameter_name, sample_count
    )
    stratum = strata[local_sample]
    numerator, denominator = _unit_fraction(
        salt,
        phase_name,
        family_id,
        parameter_name,
        local_sample,
        global_sample,
        stratum,
        sample_count,
    )
    kind = specification["kind"]
    if kind == "choice":
        values = specification["values"]
        rotation = int.from_bytes(
            _sampling_digest(
                salt,
                phase_name,
                family_id,
                parameter_name,
                "choice_rotation",
            )[:8],
            byteorder="big",
        ) % len(values)
        return copy.deepcopy(values[(stratum + rotation) % len(values)])
    if kind == "uniform_int":
        minimum = specification["minimum"]
        maximum = specification["maximum"]
        step = specification["step"]
        value_count = (maximum - minimum) // step + 1
        index = min((numerator * value_count) // denominator, value_count - 1)
        return minimum + index * step
    if kind == "uniform_float":
        digits = specification["digits"]
        with localcontext() as context:
            context.prec = 50
            minimum = Decimal(str(specification["minimum"]))
            span = Decimal(str(specification["maximum"])) - minimum
            value = minimum + span * Decimal(numerator) / Decimal(denominator)
            quantum = Decimal(1).scaleb(-digits)
            return float(value.quantize(quantum, rounding=ROUND_HALF_UP))
    raise GeneralizationContractError(
        f"cannot directly sample {parameter_name} kind {kind}"
    )


def _validate_parameter(
    parameter_name: str,
    specification: Any,
    all_parameters: dict[str, Any],
    label: str,
) -> None:
    _require(
        isinstance(parameter_name, str)
        and "." in parameter_name
        and parameter_name.split(".", 1)[0] in ALLOWED_PARAMETER_PREFIXES,
        f"{label} has an invalid dotted parameter path",
    )
    _require(isinstance(specification, dict), f"{label} must be an object")
    kind = specification.get("kind")
    if kind == "uniform_float":
        _exact_keys(
            specification,
            {"kind", "minimum", "maximum", "digits"},
            label,
        )
        minimum = _finite_number(specification["minimum"], f"{label}.minimum")
        maximum = _finite_number(specification["maximum"], f"{label}.maximum")
        digits = _nonnegative_int(specification["digits"], f"{label}.digits")
        _require(minimum < maximum, f"{label} bounds must be increasing")
        _require(digits <= 9, f"{label}.digits is unreasonably large")
    elif kind == "uniform_int":
        _exact_keys(
            specification,
            {"kind", "minimum", "maximum", "step"},
            label,
        )
        minimum = _positive_int(specification["minimum"], f"{label}.minimum")
        maximum = _positive_int(specification["maximum"], f"{label}.maximum")
        step = _positive_int(specification["step"], f"{label}.step")
        _require(minimum < maximum, f"{label} bounds must be increasing")
        _require(
            (maximum - minimum) % step == 0,
            f"{label} bounds must align to step",
        )
    elif kind == "choice":
        _exact_keys(specification, {"kind", "values"}, label)
        values = specification["values"]
        _require(
            isinstance(values, list) and len(values) >= 2,
            f"{label}.values must contain at least two choices",
        )
        _require(
            all(
                isinstance(value, (str, int, float))
                and not isinstance(value, bool)
                and (not isinstance(value, float) or math.isfinite(value))
                for value in values
            ),
            f"{label}.values contains an invalid choice",
        )
        _require(
            len({canonical_json(value) for value in values}) == len(values),
            f"{label}.values contains duplicates",
        )
    elif kind == DERIVED_KIND:
        _exact_keys(specification, {"kind", "source"}, label)
        source = specification["source"]
        _require(
            isinstance(source, str)
            and source in all_parameters
            and all_parameters[source].get("kind") == "choice",
            f"{label}.source must name a sampled choice parameter",
        )
        _require(
            all(
                isinstance(value, (int, float)) and float(value) > 0
                for value in all_parameters[source]["values"]
            ),
            f"{label}.source choices must be positive numbers",
        )
    else:
        raise GeneralizationContractError(f"{label} has unknown kind {kind!r}")


def _validate_scenario_families(contract: dict[str, Any]) -> None:
    sampling = contract["sampling"]
    families = contract["scenario_families"]
    family_order = sampling["family_order"]
    _require(
        isinstance(families, dict) and len(families) >= 3,
        "scenario_families must contain at least three families",
    )
    _require(
        isinstance(family_order, list)
        and family_order
        and len(set(family_order)) == len(family_order)
        and set(family_order) == set(families),
        "sampling.family_order must name every family exactly once",
    )
    for family_id in family_order:
        _require(
            isinstance(family_id, str)
            and SCENARIO_ID_PATTERN.fullmatch(family_id.replace("_", "-")) is not None,
            f"invalid family id {family_id!r}",
        )
        family = _exact_keys(
            families[family_id],
            {"description", "fixed_overrides", "parameters"},
            f"scenario_families.{family_id}",
        )
        _require(
            isinstance(family["description"], str) and family["description"],
            f"scenario_families.{family_id}.description must be nonempty",
        )
        fixed = family["fixed_overrides"]
        parameters = family["parameters"]
        _require(isinstance(fixed, dict), f"{family_id}.fixed_overrides must be an object")
        _require(
            isinstance(parameters, dict) and parameters,
            f"{family_id}.parameters must be a nonempty object",
        )
        _require(
            not set(fixed) & set(parameters),
            f"{family_id} fixes and samples the same parameter",
        )
        for path, value in fixed.items():
            _require(
                isinstance(path, str)
                and "." in path
                and path.split(".", 1)[0] in ALLOWED_PARAMETER_PREFIXES,
                f"{family_id} has an invalid fixed dotted path",
            )
            _require(
                isinstance(value, (str, int, float, bool))
                and (not isinstance(value, float) or math.isfinite(value)),
                f"{family_id}.{path} has an invalid fixed value",
            )
        for parameter_name, specification in parameters.items():
            _validate_parameter(
                parameter_name,
                specification,
                parameters,
                f"scenario_families.{family_id}.parameters.{parameter_name}",
            )


def _validate_evaluation_contract(contract: dict[str, Any]) -> None:
    evaluation = contract["model_evaluation"]
    _require(
        evaluation.get("outer_split") == "leave_one_scenario_family_out"
        and evaluation.get("outer_split_unit") == "family_id"
        and evaluation.get("inner_split_unit") == "scenario_id",
        "model_evaluation must use family-outer and scenario-inner splits",
    )
    for key in (
        "seed_replicates_must_not_cross_inner_splits",
        "family_id_is_forbidden_as_model_input",
        "scenario_id_is_forbidden_as_model_input",
        "latent_simulator_parameters_are_forbidden_as_model_inputs",
        "prediction_metrics_are_diagnostic_not_qualification_gates",
    ):
        _require(evaluation.get(key) is True, f"model_evaluation.{key} must be true")
    features = evaluation.get("observable_environment_features")
    _require(
        isinstance(features, list)
        and len(features) >= 5
        and len(set(features)) == len(features)
        and all(isinstance(feature, str) and feature for feature in features),
        "observable environment features must be unique nonempty names",
    )
    required_application_context = {
        "env_stream_fps",
        "env_interframe_size_bytes",
        "env_gop_length",
        "env_keyframe_size_multiplier",
        "env_deadline_us",
    }
    _require(
        required_application_context <= set(features),
        "observable environment features omit application context",
    )

    ood = contract["ood_policy"]
    _require(
        ood.get("fit_scope") == "outer_training_families_only"
        and ood.get("fallback_policy") == "no_secondary_copy",
        "OOD detector must fit on training families and fail resource-conservatively",
    )
    ood_probability = _finite_number(
        ood.get("maximum_ood_exploration_action_probability"),
        "ood exploration probability",
    )
    hard_probability = _finite_number(
        ood.get("hard_failure_exploration_action_probability"),
        "hard-failure exploration probability",
    )
    _require(0 <= hard_probability <= ood_probability <= 0.01, "invalid OOD exploration")

    exploration = contract["deployment_exploration"]
    probabilities = [
        _finite_number(exploration.get(key), f"deployment_exploration.{key}")
        for key in (
            "forced_t2_action_probability",
            "forced_control_probability",
            "base_policy_probability",
        )
    ]
    _require(
        all(0 <= value <= 1 for value in probabilities)
        and math.isclose(sum(probabilities), 1.0, rel_tol=0, abs_tol=1e-12),
        "deployment exploration probabilities must partition one",
    )
    _require(
        exploration.get("log_assignment_propensity") is True
        and exploration.get("log_execution_compliance") is True,
        "deployment exploration must log propensity and compliance",
    )

    qualification = contract["closed_loop_qualification"]
    arms = qualification.get("actual_simulation_arms")
    _require(isinstance(arms, list) and arms, "actual simulation arms are missing")
    arm_ids = [arm.get("arm_id") for arm in arms if isinstance(arm, dict)]
    _require(
        len(arm_ids) == len(arms)
        and len(set(arm_ids)) == len(arm_ids)
        and {
            "str_mlo_nmaxinflights_1",
            "score_aware_t2_v2",
            "distributional_shadow_t2",
        }
        <= set(arm_ids),
        "actual simulation arms must include STR, V2, and distributional shadow",
    )
    _require(
        all(
            arm.get("policy") not in {"fixed_link_1", "full_duplication"}
            for arm in arms
        ),
        "held-out campaign must not add fixed-5-GHz or full-duplication arms",
    )
    _require(
        qualification.get("reserved_neutral_confirmation_is_separate") is True,
        "neutral confirmation must remain separate",
    )


def validate_contract(
    contract: Any,
    contract_path: Path = CANONICAL_CONTRACT,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Fail closed unless the full frozen generalization contract is coherent."""

    contract = _exact_keys(
        contract,
        {
            "schema_version",
            "contract_id",
            "purpose",
            "reserved_confirmation_seeds",
            "sampling",
            "randomized_collection",
            "generated_artifacts",
            "scenario_families",
            "model_evaluation",
            "ood_policy",
            "deployment_exploration",
            "closed_loop_qualification",
        },
        "contract",
    )
    _require(contract["schema_version"] == SCHEMA_VERSION, "unknown contract schema")
    _require(
        contract["contract_id"] == "environment-generalization-v1",
        "unexpected contract id",
    )
    _require(
        isinstance(contract["purpose"], str) and contract["purpose"],
        "contract purpose must be nonempty",
    )
    contract_path = contract_path.resolve()
    root = root.resolve()
    _require(
        contract_path.is_file()
        and not contract_path.is_symlink()
        and contract_path.is_relative_to(root),
        "contract path must be a regular project file",
    )

    reserved = _exact_keys(
        contract["reserved_confirmation_seeds"],
        {"first", "last", "must_remain_unopened"},
        "reserved_confirmation_seeds",
    )
    reserved_first = _positive_int(reserved["first"], "reserved first seed")
    reserved_last = _positive_int(reserved["last"], "reserved last seed")
    _require(
        reserved_first <= reserved_last and reserved["must_remain_unopened"] is True,
        "reserved confirmation seed interval is invalid",
    )

    sampling = _exact_keys(
        contract["sampling"],
        {"algorithm", "salt", "family_order", "phases"},
        "sampling",
    )
    _require(sampling["algorithm"] == SAMPLING_ALGORITHM, "unknown sampling algorithm")
    _require(
        isinstance(sampling["salt"], str) and sampling["salt"],
        "sampling salt must be nonempty",
    )
    _validate_scenario_families(contract)
    family_count = len(sampling["family_order"])
    phases = sampling["phases"]
    _require(
        isinstance(phases, dict) and set(phases) == set(PHASE_ORDER),
        "sampling phases must be exactly collection, qualification, and preflight",
    )
    sample_intervals: list[tuple[int, int, str]] = []
    seed_intervals: list[tuple[int, int, str]] = []
    qualification_arms = len(
        contract["closed_loop_qualification"]["actual_simulation_arms"]
    )
    for phase_name in PHASE_ORDER:
        phase = phases[phase_name]
        common_keys = {
            "phase_tag",
            "parameter_samples_per_family",
            "replicates_per_parameter_sample",
            "parameter_sample_offset",
            "simulation_seed_base",
        }
        if phase_name == "closed_loop_qualification":
            expected_keys = common_keys | {
                "expected_paired_units",
                "actual_simulation_arms",
                "expected_simulation_run_count",
                "expected_64_worker_waves",
            }
        elif phase_name == "randomized_collection":
            expected_keys = common_keys | {
                "expected_run_count",
                "expected_64_worker_waves",
            }
        else:
            expected_keys = common_keys | {"expected_run_count"}
        phase = _exact_keys(phase, expected_keys, f"sampling.phases.{phase_name}")
        _require(
            isinstance(phase["phase_tag"], str)
            and SCENARIO_ID_PATTERN.fullmatch(phase["phase_tag"]) is not None,
            f"{phase_name} phase tag is invalid",
        )
        samples = _positive_int(
            phase["parameter_samples_per_family"], f"{phase_name} sample count"
        )
        replicates = _positive_int(
            phase["replicates_per_parameter_sample"],
            f"{phase_name} replicate count",
        )
        offset = _nonnegative_int(
            phase["parameter_sample_offset"], f"{phase_name} sample offset"
        )
        seed_base = _positive_int(
            phase["simulation_seed_base"], f"{phase_name} seed base"
        )
        paired_units = family_count * samples * replicates
        if phase_name == "closed_loop_qualification":
            _require(
                phase["expected_paired_units"] == paired_units,
                "qualification paired-unit count differs from its dimensions",
            )
            _require(
                phase["actual_simulation_arms"] == qualification_arms,
                "qualification arm count differs from the arm contract",
            )
            simulation_runs = paired_units * qualification_arms
            _require(
                phase["expected_simulation_run_count"] == simulation_runs,
                "qualification simulation count differs from its dimensions",
            )
            _require(
                simulation_runs % 64 == 0
                and phase["expected_64_worker_waves"] == simulation_runs // 64,
                "qualification campaign is not an exact 64-worker multiple",
            )
        else:
            _require(
                phase["expected_run_count"] == paired_units,
                f"{phase_name} run count differs from its dimensions",
            )
            if phase_name == "randomized_collection":
                _require(
                    paired_units % 64 == 0
                    and phase["expected_64_worker_waves"] == paired_units // 64,
                    "randomized collection is not an exact 64-worker multiple",
                )
        sample_intervals.append((offset, offset + samples - 1, phase_name))
        seed_intervals.append((seed_base, seed_base + paired_units - 1, phase_name))

    for index, left in enumerate(sample_intervals):
        for right in sample_intervals[index + 1 :]:
            _require(
                left[1] < right[0] or right[1] < left[0],
                f"parameter-sample intervals overlap: {left[2]} and {right[2]}",
            )
    for index, left in enumerate(seed_intervals):
        _require(
            left[1] < reserved_first or reserved_last < left[0],
            f"{left[2]} consumes reserved confirmation seeds",
        )
        for right in seed_intervals[index + 1 :]:
            _require(
                left[1] < right[0] or right[1] < left[0],
                f"simulation-seed intervals overlap: {left[2]} and {right[2]}",
            )

    randomized = _exact_keys(
        contract["randomized_collection"],
        {
            "base_config_path",
            "base_config_file_sha256",
            "resolved_base_config_sha256",
            "experiment_name",
            "output_root",
            "preflight_experiment_name",
            "preflight_output_root",
            "policy",
            "topology",
            "assignment",
            "maximum_keyframe_bytes",
            "workers",
        },
        "randomized_collection",
    )
    base_path = _project_path(
        root,
        randomized["base_config_path"],
        "randomized base config",
        must_exist=True,
    )
    expected_base_file_sha = _sha256(
        randomized["base_config_file_sha256"], "base config file hash"
    )
    _require(
        sha256_file(base_path) == expected_base_file_sha,
        "randomized base config file hash drifted",
    )
    expected_resolved_sha = _sha256(
        randomized["resolved_base_config_sha256"], "resolved base config hash"
    )
    _require(
        matrix_sha256(load_yaml(base_path)) == expected_resolved_sha,
        "resolved randomized base config hash drifted",
    )
    _positive_int(randomized["maximum_keyframe_bytes"], "maximum keyframe bytes")
    workers = _positive_int(randomized["workers"], "randomized workers")
    _require(workers == 64, "generalization campaign must use 64 workers")
    assignment = _exact_keys(
        randomized["assignment"],
        {
            "algorithm",
            "salt",
            "t2_probability",
            "t4_probability",
            "control_probability",
            "stop_guard_us",
            "token_gate_enabled",
        },
        "randomized_collection.assignment",
    )
    _require(
        assignment["algorithm"] == "splitmix64-threshold-v1",
        "unknown randomized assignment algorithm",
    )
    _positive_int(assignment["salt"], "randomized assignment salt")
    t2_probability = _finite_number(assignment["t2_probability"], "T2 probability")
    t4_probability = _finite_number(assignment["t4_probability"], "T4 probability")
    control_probability = _finite_number(
        assignment["control_probability"], "control probability"
    )
    _require(
        t2_probability > 0
        and t4_probability > 0
        and control_probability > 0
        and math.isclose(
            t2_probability + t4_probability + control_probability,
            1.0,
            rel_tol=0,
            abs_tol=1e-12,
        ),
        "randomized probabilities must be positive and partition one",
    )
    _positive_int(assignment["stop_guard_us"], "assignment stop guard")
    _require(
        assignment["token_gate_enabled"] is False,
        "randomized collection must not gate assigned treatments",
    )

    outputs = _exact_keys(
        contract["generated_artifacts"],
        {
            "randomized_collection_config",
            "randomized_preflight_config",
            "scenario_catalog",
            "artifact_manifest",
        },
        "generated_artifacts",
    )
    output_paths = [
        _project_path(root, value, f"generated_artifacts.{key}", must_exist=False)
        for key, value in outputs.items()
    ]
    _require(len(set(output_paths)) == len(output_paths), "generated paths must be unique")
    _require(base_path not in output_paths, "generated output may not overwrite the base config")
    _require(contract_path not in output_paths, "generated output may not overwrite the contract")
    _validate_evaluation_contract(contract)
    return contract


def load_contract(
    path: Path = CANONICAL_CONTRACT,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Load and validate the contract from one regular JSON file."""

    path = path.resolve()
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GeneralizationContractError(f"cannot read contract {path}: {error}") from error
    return validate_contract(contract, path, root)


def _derived_frame_period_us(fps: Any) -> int:
    with localcontext() as context:
        context.prec = 50
        period = Decimal(1000000) / Decimal(str(fps))
        return int(period.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _resolved_leaf(base: dict[str, Any], overlay: dict[str, Any], dotted: str) -> Any:
    if dotted in overlay:
        return overlay[dotted]
    node: Any = base
    for part in dotted.split("."):
        _require(isinstance(node, dict) and part in node, f"base lacks {dotted}")
        node = node[part]
    return node


def _validate_sampled_overlay(
    contract: dict[str, Any],
    base: dict[str, Any],
    family_id: str,
    overlay: dict[str, Any],
) -> None:
    expected_paths = set(contract["scenario_families"][family_id]["fixed_overrides"])
    expected_paths.update(contract["scenario_families"][family_id]["parameters"])
    _require(set(overlay) == expected_paths, f"{family_id} overlay paths differ from contract")
    frame_size = _finite_number(
        _resolved_leaf(base, overlay, "stream.frame_size"), "sampled frame size"
    )
    multiplier = _finite_number(
        _resolved_leaf(base, overlay, "stream.keyframe_size_multiplier"),
        "sampled keyframe multiplier",
    )
    _require(
        frame_size * multiplier <= contract["randomized_collection"]["maximum_keyframe_bytes"],
        f"{family_id} sampled keyframe exceeds the frozen bound",
    )
    deadline = _positive_int(
        _resolved_leaf(base, overlay, "stream.deadline_us"), "sampled deadline"
    )
    _require(deadline > 4000, f"{family_id} deadline does not permit T4 assignment")
    distance_1 = _finite_number(
        _resolved_leaf(base, overlay, "propagation.nakagami_distance_1_m"),
        "Nakagami distance 1",
    )
    distance_2 = _finite_number(
        _resolved_leaf(base, overlay, "propagation.nakagami_distance_2_m"),
        "Nakagami distance 2",
    )
    _require(distance_1 < distance_2, f"{family_id} sampled Nakagami bounds cross")
    for minimum_path, maximum_path in (
        ("obss.obss_ul_min_rate_mbps", "obss.obss_ul_max_rate_mbps"),
        ("obss.obss_dl_min_rate_mbps", "obss.obss_dl_max_rate_mbps"),
        ("obss.obss_area_min_x_m", "obss.obss_area_max_x_m"),
        ("obss.obss_area_min_y_m", "obss.obss_area_max_y_m"),
        ("obss.obss_sta_min_distance_m", "obss.obss_sta_max_distance_m"),
    ):
        minimum = _finite_number(
            _resolved_leaf(base, overlay, minimum_path), f"sampled {minimum_path}"
        )
        maximum = _finite_number(
            _resolved_leaf(base, overlay, maximum_path), f"sampled {maximum_path}"
        )
        _require(minimum < maximum, f"{family_id} sampled bounds cross for {minimum_path}")


def generate_phase_scenarios(
    contract: dict[str, Any],
    phase_name: str,
    base_document: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate one phase's deterministic named scenario instances."""

    _require(phase_name in PHASE_ORDER, f"unknown phase {phase_name}")
    if base_document is None:
        base_path = ROOT / contract["randomized_collection"]["base_config_path"]
        base_document = load_yaml(base_path)
    base = base_document["base"]
    phase = contract["sampling"]["phases"][phase_name]
    sample_count = phase["parameter_samples_per_family"]
    replicates = phase["replicates_per_parameter_sample"]
    sample_offset = phase["parameter_sample_offset"]
    seed_base = phase["simulation_seed_base"]
    salt = contract["sampling"]["salt"]
    result: list[dict[str, Any]] = []
    for family_index, family_id in enumerate(contract["sampling"]["family_order"]):
        family = contract["scenario_families"][family_id]
        for local_sample in range(sample_count):
            global_sample = sample_offset + local_sample
            overlay = copy.deepcopy(family["fixed_overrides"])
            derived: list[tuple[str, dict[str, Any]]] = []
            for parameter_name in sorted(family["parameters"]):
                specification = family["parameters"][parameter_name]
                if specification["kind"] == DERIVED_KIND:
                    derived.append((parameter_name, specification))
                    continue
                overlay[parameter_name] = _sample_parameter(
                    specification,
                    salt=salt,
                    phase_name=phase_name,
                    family_id=family_id,
                    parameter_name=parameter_name,
                    local_sample=local_sample,
                    global_sample=global_sample,
                    sample_count=sample_count,
                )
            for parameter_name, specification in derived:
                source = specification["source"]
                source_value = _resolved_leaf(base, overlay, source)
                overlay[parameter_name] = _derived_frame_period_us(source_value)
            overlay = {key: overlay[key] for key in sorted(overlay)}
            _validate_sampled_overlay(contract, base, family_id, overlay)
            linear_scenario = family_index * sample_count + local_sample
            first_seed = seed_base + linear_scenario * replicates
            scenario_id = (
                f"{family_id.replace('_', '-')}-{phase['phase_tag']}-p{global_sample:02d}"
            )
            result.append(
                {
                    "scenario_id": scenario_id,
                    "family_id": family_id,
                    "parameter_sample": global_sample,
                    "config": overlay,
                    "seeds": list(range(first_seed, first_seed + replicates)),
                    "runs": [1],
                }
            )
    expected_scenarios = len(contract["sampling"]["family_order"]) * sample_count
    _require(len(result) == expected_scenarios, f"{phase_name} scenario count differs")
    _require(
        len({item["scenario_id"] for item in result}) == len(result),
        f"{phase_name} scenario ids are not unique",
    )
    return result


def _merge_documents(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if key == "extends":
            continue
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_documents(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _randomized_policy(contract: dict[str, Any], base_document: dict[str, Any]) -> dict[str, Any]:
    policy_name = contract["randomized_collection"]["policy"]
    matching = [
        policy
        for policy in base_document["policies"]
        if isinstance(policy, dict) and policy.get("name") == policy_name
    ]
    _require(len(matching) == 1, "randomized base policy is missing or ambiguous")
    policy = copy.deepcopy(matching[0])
    assignment = contract["randomized_collection"]["assignment"]
    config = policy["config"]
    config["prediction.randomized_assignment_salt"] = assignment["salt"]
    config["prediction.randomized_t2_probability"] = assignment["t2_probability"]
    config["prediction.randomized_t4_probability"] = assignment["t4_probability"]
    config["prediction.randomized_assignment_stop_guard_us"] = assignment["stop_guard_us"]
    return policy


def _render_yaml(value: dict[str, Any]) -> bytes:
    body = yaml.safe_dump(
        value,
        allow_unicode=False,
        default_flow_style=False,
        sort_keys=False,
        width=100,
    )
    return (
        "# Generated by tools/generate_environment_generalization_v1.py.\n"
        "# Do not edit; update the frozen contract and regenerate.\n"
        + body
    ).encode("utf-8")


def _render_json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode(
        "utf-8"
    )


def _phase_document(
    contract: dict[str, Any],
    contract_path: Path,
    contract_sha256: str,
    base_path: Path,
    base_document: dict[str, Any],
    phase_name: str,
    scenarios: list[dict[str, Any]],
    output_path: Path,
) -> dict[str, Any]:
    randomized = contract["randomized_collection"]
    if phase_name == "randomized_collection":
        experiment_name = randomized["experiment_name"]
        output_root = randomized["output_root"]
    elif phase_name == "preflight":
        experiment_name = randomized["preflight_experiment_name"]
        output_root = randomized["preflight_output_root"]
    else:
        raise GeneralizationContractError("only randomized collection and preflight are executable")
    return {
        "extends": os.path.relpath(base_path, output_path.parent),
        "name": experiment_name,
        "output_root": output_root,
        "workers": randomized["workers"],
        "generalization_contract": {
            "id": contract["contract_id"],
            "path": str(contract_path.relative_to(ROOT)),
            "sha256": contract_sha256,
            "phase": phase_name,
        },
        "seeds": [1],
        "runs": [1],
        "scenario_instances": scenarios,
        "topologies": [{"name": randomized["topology"]}],
        "policies": [_randomized_policy(contract, base_document)],
    }


def _validate_executable_document(
    contract: dict[str, Any],
    document: dict[str, Any],
    base_document: dict[str, Any],
    phase_name: str,
    config_dir: Path,
) -> None:
    resolved_document = _merge_documents(base_document, document)
    specs = expand_config(resolved_document)
    expected = contract["sampling"]["phases"][phase_name]["expected_run_count"]
    _require(len(specs) == expected, f"{phase_name} executable expansion count differs")
    expected_scenario_ids = {
        scenario["scenario_id"] for scenario in document["scenario_instances"]
    }
    observed_scenario_ids = {spec["scenario"]["scenario_id"] for spec in specs}
    _require(
        expected_scenario_ids == observed_scenario_ids,
        f"{phase_name} scenario identity was lost during expansion",
    )
    reserved = contract["reserved_confirmation_seeds"]
    for spec in specs:
        _require(
            not reserved["first"] <= spec["seed"] <= reserved["last"],
            f"{phase_name} expanded a reserved confirmation seed",
        )
        _require(
            "scenario_id" not in spec["config"]
            and "family_id" not in spec["config"]
            and "parameter_sample" not in spec["config"],
            "latent scenario identity leaked into simulator configuration",
        )
        arguments = cli_arguments(spec["config"], config_dir)
        _require(
            not any(
                argument.startswith(("--scenario", "--family", "--parameterSample"))
                for argument in arguments
            ),
            "latent scenario identity leaked into simulator CLI",
        )


def build_artifacts(
    contract_path: Path = CANONICAL_CONTRACT,
    root: Path = ROOT,
) -> dict[Path, bytes]:
    """Build every deterministic artifact without modifying the filesystem."""

    root = root.resolve()
    contract_path = contract_path.resolve()
    contract = load_contract(contract_path, root)
    contract_sha = sha256_file(contract_path)
    generator_path = Path(__file__).resolve()
    generator_sha = sha256_file(generator_path)
    randomized = contract["randomized_collection"]
    base_path = (root / randomized["base_config_path"]).resolve()
    base_document = load_yaml(base_path)
    scenarios = {
        phase_name: generate_phase_scenarios(contract, phase_name, base_document)
        for phase_name in PHASE_ORDER
    }
    output_paths = {
        key: (root / value).resolve()
        for key, value in contract["generated_artifacts"].items()
    }
    collection_document = _phase_document(
        contract,
        contract_path,
        contract_sha,
        base_path,
        base_document,
        "randomized_collection",
        scenarios["randomized_collection"],
        output_paths["randomized_collection_config"],
    )
    preflight_document = _phase_document(
        contract,
        contract_path,
        contract_sha,
        base_path,
        base_document,
        "preflight",
        scenarios["preflight"],
        output_paths["randomized_preflight_config"],
    )
    _validate_executable_document(
        contract,
        collection_document,
        base_document,
        "randomized_collection",
        output_paths["randomized_collection_config"].parent,
    )
    _validate_executable_document(
        contract,
        preflight_document,
        base_document,
        "preflight",
        output_paths["randomized_preflight_config"].parent,
    )

    phase_catalog: dict[str, Any] = {}
    all_seeds: set[int] = set()
    for phase_name in PHASE_ORDER:
        phase_scenarios = scenarios[phase_name]
        phase_seeds = [seed for scenario in phase_scenarios for seed in scenario["seeds"]]
        _require(
            len(phase_seeds) == len(set(phase_seeds)),
            f"{phase_name} reuses a simulation seed",
        )
        _require(not all_seeds & set(phase_seeds), "phases reuse simulation seeds")
        all_seeds.update(phase_seeds)
        phase_catalog[phase_name] = {
            "phase_tag": contract["sampling"]["phases"][phase_name]["phase_tag"],
            "scenario_count": len(phase_scenarios),
            "paired_unit_count": len(phase_seeds),
            "scenario_list_sha256": sha256_bytes(
                canonical_json(phase_scenarios).encode("ascii")
            ),
            "scenarios": phase_scenarios,
        }
    catalog = {
        "schema_version": 1,
        "catalog_id": "environment-generalization-scenarios-v1",
        "contract": {
            "id": contract["contract_id"],
            "path": str(contract_path.relative_to(root)),
            "sha256": contract_sha,
        },
        "generator": {
            "path": str(generator_path.relative_to(root)),
            "sha256": generator_sha,
        },
        "base_config": {
            "path": randomized["base_config_path"],
            "file_sha256": randomized["base_config_file_sha256"],
            "resolved_sha256": randomized["resolved_base_config_sha256"],
        },
        "sampling": {
            "algorithm": contract["sampling"]["algorithm"],
            "salt": contract["sampling"]["salt"],
            "family_order": contract["sampling"]["family_order"],
        },
        "phases": phase_catalog,
        "reserved_confirmation_seed_audit": {
            **contract["reserved_confirmation_seeds"],
            "overlap_count": sum(
                contract["reserved_confirmation_seeds"]["first"]
                <= seed
                <= contract["reserved_confirmation_seeds"]["last"]
                for seed in all_seeds
            ),
        },
    }
    artifacts: dict[Path, bytes] = {
        output_paths["randomized_collection_config"]: _render_yaml(collection_document),
        output_paths["randomized_preflight_config"]: _render_yaml(preflight_document),
        output_paths["scenario_catalog"]: _render_json(catalog),
    }
    generated_entries = {
        logical_name: {
            "path": str(path.relative_to(root)),
            "sha256": sha256_bytes(artifacts[path]),
            "bytes": len(artifacts[path]),
        }
        for logical_name, path in output_paths.items()
        if logical_name != "artifact_manifest"
    }
    phases = contract["sampling"]["phases"]
    manifest = {
        "schema_version": 1,
        "manifest_id": "environment-generalization-artifacts-v1",
        "contract": {
            "id": contract["contract_id"],
            "path": str(contract_path.relative_to(root)),
            "sha256": contract_sha,
        },
        "generator": {
            "path": str(generator_path.relative_to(root)),
            "sha256": generator_sha,
        },
        "source_artifacts": {
            "randomized_base_config": {
                "path": randomized["base_config_path"],
                "file_sha256": randomized["base_config_file_sha256"],
                "resolved_sha256": randomized["resolved_base_config_sha256"],
            }
        },
        "generated_artifacts": generated_entries,
        "campaign_counts": {
            "randomized_collection_scenarios": len(scenarios["randomized_collection"]),
            "randomized_collection_runs": phases["randomized_collection"]["expected_run_count"],
            "randomized_collection_64_worker_waves": phases["randomized_collection"][
                "expected_64_worker_waves"
            ],
            "closed_loop_qualification_scenarios": len(
                scenarios["closed_loop_qualification"]
            ),
            "closed_loop_qualification_paired_units": phases[
                "closed_loop_qualification"
            ]["expected_paired_units"],
            "closed_loop_qualification_simulation_runs": phases[
                "closed_loop_qualification"
            ]["expected_simulation_run_count"],
            "closed_loop_qualification_64_worker_waves": phases[
                "closed_loop_qualification"
            ]["expected_64_worker_waves"],
            "preflight_scenarios": len(scenarios["preflight"]),
            "preflight_runs": phases["preflight"]["expected_run_count"],
        },
        "invariants": {
            "collection_and_qualification_are_64_worker_multiples": True,
            "phase_seed_sets_are_disjoint": True,
            "reserved_confirmation_seed_overlap_count": 0,
            "scenario_identity_is_not_a_simulator_cli_input": True,
            "qualification_catalog_generated_before_results": True,
        },
        "reproduction": [
            "python3 tools/generate_environment_generalization_v1.py --check"
        ],
    }
    artifacts[output_paths["artifact_manifest"]] = _render_json(manifest)
    return artifacts


def write_or_check(path: Path, content: bytes, check: bool) -> None:
    """Atomically write one artifact or reject a stale tracked copy."""

    if path.is_symlink():
        raise GeneralizationContractError(f"refusing symlink output: {path}")
    if check:
        try:
            observed = path.read_bytes()
        except OSError as error:
            raise GeneralizationContractError(f"generated output is absent: {path}") from error
        if observed != content:
            raise GeneralizationContractError(f"generated output is stale: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=CANONICAL_CONTRACT)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    artifacts = build_artifacts(args.contract)
    for path, content in artifacts.items():
        write_or_check(path, content, args.check)
    print(
        json.dumps(
            {
                "artifact_count": len(artifacts),
                "artifacts": {
                    str(path.relative_to(ROOT)): sha256_bytes(content)
                    for path, content in artifacts.items()
                },
                "check": args.check,
                "contract_sha256": sha256_file(args.contract.resolve()),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GeneralizationContractError as error:
        raise SystemExit(f"error: {error}") from error
