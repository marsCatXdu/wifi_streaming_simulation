"""Causal individual-run replay for frozen latency-risk predictors."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import pickle
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import yaml

from prediction.calibration import fit_platt
from prediction.features import FeatureSets, degrade_f1, encode_value
from prediction.models import CANDIDATES, fit_pipeline, ranking_score
from prediction_dataset import derive_age

ONLINE_REPLAY_SCHEMA_VERSION = 1
MODEL_BUNDLE_SCHEMA_VERSION = 1


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of one file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_replay_config(path: Path) -> dict[str, Any]:
    """Load and strictly validate the online replay contract."""
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("online replay YAML root must be a mapping")
    required = {
        "online_replay_schema_version",
        "analysis_schema_version",
        "primary_link",
        "primary_band",
        "stages",
        "decision_policies",
        "pipelines",
        "probability_thresholds",
        "budgets",
        "budget_kinds",
        "token_bucket",
        "replay_split_roles",
        "minimum_useful_lead_time_us",
        "confidence_level",
        "bootstrap_replicates",
        "bootstrap_seed",
    }
    missing = sorted(required - value.keys())
    if missing:
        raise ValueError(f"online replay configuration is missing: {', '.join(missing)}")
    if value["online_replay_schema_version"] != ONLINE_REPLAY_SCHEMA_VERSION:
        raise ValueError("unsupported online replay schema")
    if value["primary_link"] != 1 or value["primary_band"] != "5GHz":
        raise ValueError("online replay is frozen to path 1 / 5GHz")
    stages = value["stages"]
    if stages != ["T0", "T1", "T2", "T4"]:
        raise ValueError("online replay stages must be T0, T1, T2, T4")
    identifiers = [item["pipeline_id"] for item in value["pipelines"]]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate online replay pipeline_id")
    thresholds = list(map(float, value["probability_thresholds"]))
    budgets = list(map(float, value["budgets"]))
    if thresholds != sorted(set(thresholds)) or not all(0 <= item <= 1 for item in thresholds):
        raise ValueError("probability thresholds must be unique, sorted, and in [0, 1]")
    if budgets != sorted(set(budgets)) or not all(0 < item <= 1 for item in budgets):
        raise ValueError("budgets must be unique, sorted, and in (0, 1]")
    if set(value["budget_kinds"]) != {"frames", "bytes"}:
        raise ValueError("budget_kinds must contain frames and bytes")
    policies = value["decision_policies"]
    for policy in policies:
        if not policy.get("stages") or not set(policy["stages"]) <= set(stages):
            raise ValueError(f"invalid decision stages for {policy.get('policy_id')}")
    bucket = value["token_bucket"]
    if bucket.get("initial_fill") != "full":
        raise ValueError("only full initial token buckets are supported")
    if int(bucket.get("burst_horizon_frames", 0)) <= 0:
        raise ValueError("burst_horizon_frames must be positive")
    return value


@dataclass
class FrozenPredictor:
    """One stage-specific fitted model and probability calibrator."""

    pipeline_id: str
    feature_set: str
    evidence_role: str
    stage: str
    feature_names: tuple[str, ...]
    f1_feature_names: tuple[str, ...]
    degradation_profile: dict[str, Any] | None
    model_name: str
    selection_recall: float
    pipeline: Any
    calibrator: Any

    def predict(self, matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return ranking scores and calibrated miss probabilities."""
        score = ranking_score(self.pipeline, matrix)
        return score, self.calibrator.predict(score)


@dataclass
class ModelBundle:
    """Trusted local artifact containing all frozen 5 GHz predictors."""

    schema_version: int
    replay_config_sha256: str
    analysis_config_sha256: str
    dataset_sha256: str
    primary_link: int
    feature_dictionary: dict[str, dict[str, Any]]
    predictors: dict[tuple[str, str], FrozenPredictor]
    median_frame_size_bytes: float
    p99_frame_size_bytes: float


def write_model_bundle(path: Path, bundle: ModelBundle) -> None:
    """Serialize a trusted local model bundle."""
    with path.open("wb") as output:
        pickle.dump(bundle, output, protocol=pickle.HIGHEST_PROTOCOL)


def read_model_bundle(path: Path) -> ModelBundle:
    """Read a trusted local model bundle and check its schema."""
    with path.open("rb") as source:
        value = pickle.load(source)
    if not isinstance(value, ModelBundle):
        raise ValueError("model bundle has the wrong Python type")
    if value.schema_version != MODEL_BUNDLE_SCHEMA_VERSION:
        raise ValueError("unsupported model bundle schema")
    return value


def _pipeline_feature_names(spec: dict[str, Any], sets: FeatureSets) -> tuple[str, ...]:
    feature_set = spec["feature_set"]
    mapping = {
        "F0+F1-degraded": "F0+F1-ideal",
        "F0+F1-degraded+F2-exportable": "F0+F1-ideal+F2-exportable",
        "F0+F1-ideal+F2": "F0+F1-ideal+F2",
    }
    if feature_set not in mapping:
        raise ValueError(f"unsupported online feature set: {feature_set}")
    return sets.sets[mapping[feature_set]]


def _degradation_profile(
    spec: dict[str, Any], analysis: dict[str, Any]
) -> dict[str, Any] | None:
    profile_id = spec.get("degradation_profile")
    if profile_id is None:
        return None
    matches = [
        item for item in analysis["f1_degradation_profiles"] if item["profile_id"] == profile_id
    ]
    if len(matches) != 1:
        raise ValueError(f"unknown or duplicate F1 degradation profile: {profile_id}")
    return dict(matches[0])


def _take_features(
    data: Any,
    mask: np.ndarray,
    names: tuple[str, ...],
    degraded_f1: np.ndarray | None,
    f1_names: tuple[str, ...],
) -> np.ndarray:
    indices = [data.names.index(name) for name in names]
    result = data.matrix[np.ix_(mask, indices)].copy()
    if degraded_f1 is not None:
        f1_lookup = {name: index for index, name in enumerate(f1_names)}
        positions = np.flatnonzero(mask)
        for column, name in enumerate(names):
            if name in f1_lookup:
                result[:, column] = degraded_f1[positions, f1_lookup[name]]
    return result


def fit_frozen_predictor(
    data: Any,
    link: int,
    stage: str,
    spec: dict[str, Any],
    sets: FeatureSets,
    analysis: dict[str, Any],
) -> FrozenPredictor:
    """Select, refit, and calibrate one stage-specific predictor."""
    from evaluate_prediction import ROLES

    names = _pipeline_feature_names(spec, sets)
    f1_names = sets.by_tier["F1-ideal"]
    profile = _degradation_profile(spec, analysis)
    degraded = None
    if profile is not None:
        f1_indices = [data.names.index(name) for name in f1_names]
        degraded, _, _ = degrade_f1(
            data.matrix[:, f1_indices],
            f1_names,
            data.sample_time_us,
            data.run,
            profile,
        )

    def mask(role: str) -> np.ndarray:
        return data.actionable & (data.link == link) & (data.role == ROLES[role])

    train = mask("training")
    selection = mask("validation_selection")
    calibration = mask("validation_calibration")
    categorical = tuple(
        index for index, name in enumerate(names) if name in sets.categorical
    )
    train_x = _take_features(data, train, names, degraded, f1_names)
    selection_x = _take_features(data, selection, names, degraded, f1_names)
    candidates = []
    from prediction.metrics import topk_metrics

    for candidate in CANDIDATES:
        fitted = fit_pipeline(
            candidate,
            train_x,
            data.label[train],
            categorical,
            int(analysis["analysis_seed"]),
        )
        score = ranking_score(fitted, selection_x)
        recall = topk_metrics(
            data.label[selection],
            score,
            float(analysis["screening_budget"]),
            float(analysis["confidence_level"]),
        )["recall"]
        candidates.append((float("-inf") if recall is None else float(recall), candidate))
    order = {name: index for index, name in enumerate(analysis["model_name_order"])}
    selection_recall, chosen = max(
        candidates, key=lambda item: (item[0], -order[item[1].name])
    )
    refit = train | selection
    final = fit_pipeline(
        chosen,
        _take_features(data, refit, names, degraded, f1_names),
        data.label[refit],
        categorical,
        int(analysis["analysis_seed"]),
    )
    calibration_score = ranking_score(
        final, _take_features(data, calibration, names, degraded, f1_names)
    )
    calibrator = fit_platt(
        calibration_score,
        data.label[calibration],
        int(analysis["analysis_seed"]),
    )
    return FrozenPredictor(
        pipeline_id=spec["pipeline_id"],
        feature_set=spec["feature_set"],
        evidence_role=spec["evidence_role"],
        stage=stage,
        feature_names=names,
        f1_feature_names=f1_names,
        degradation_profile=profile,
        model_name=chosen.name,
        selection_recall=selection_recall,
        pipeline=final,
        calibrator=calibrator,
    )


def raw_feature_row(sample: dict[str, str]) -> dict[str, str]:
    """Add the same causal derived fields used by the dataset builder."""
    sample_time_ns = int(sample["sample_time_ns"])
    packet_count = int(sample["frame_packet_count"])
    succeeded = int(sample["frame_packets_tx_succeeded"])
    if succeeded > packet_count:
        raise ValueError("acknowledged packets exceed frame packet count")
    return {
        **sample,
        "last_positive_ack_age_us": derive_age(
            sample_time_ns,
            sample["last_positive_ack_time_ns"],
            "last_positive_ack_time_ns",
        ),
        "last_attempt_age_us": derive_age(
            sample_time_ns,
            sample["last_tx_attempt_time_ns"],
            "last_tx_attempt_time_ns",
        ),
        "queue_oldest_age_us": derive_age(
            sample_time_ns,
            sample["mac_queue_oldest_enqueue_time_ns"],
            "mac_queue_oldest_enqueue_time_ns",
        ),
        "frame_packets_not_acknowledged": str(packet_count - succeeded),
    }


def score_individual_run(
    run_dir: Path,
    bundle: ModelBundle,
    primary_link: int,
) -> list[dict[str, Any]]:
    """Score one raw run without consulting its frame outcomes."""
    by_stage: dict[str, list[dict[str, str]]] = defaultdict(list)
    with (run_dir / "prediction_samples.csv").open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        for raw in reader:
            if int(raw["path_id"]) == primary_link:
                by_stage[raw["sample_stage"]].append(raw_feature_row(raw))
    scores: list[dict[str, Any]] = []
    for (pipeline_id, stage), predictor in sorted(bundle.predictors.items()):
        rows = sorted(
            by_stage[stage],
            key=lambda row: (int(row["sample_time_ns"]), int(row["frame_id"])),
        )
        if not rows:
            raise ValueError(f"{run_dir}: no path {primary_link} rows for {stage}")
        matrix = np.asarray(
            [
                [encode_value(name, row.get(name, "")) for name in predictor.feature_names]
                for row in rows
            ],
            dtype=np.float32,
        )
        if predictor.degradation_profile is not None:
            f1_positions = [
                predictor.feature_names.index(name)
                for name in predictor.f1_feature_names
                if name in predictor.feature_names
            ]
            f1_names = tuple(predictor.feature_names[index] for index in f1_positions)
            degraded, _, _ = degrade_f1(
                matrix[:, f1_positions],
                f1_names,
                np.asarray([int(row["sample_time_ns"]) // 1000 for row in rows]),
                np.zeros(len(rows), dtype=np.int8),
                predictor.degradation_profile,
            )
            matrix[:, f1_positions] = degraded
        ranking, probability = predictor.predict(matrix)
        for row, raw_score, risk in zip(rows, ranking, probability):
            scores.append(
                {
                    "run_id": row["run_id"],
                    "frame_id": int(row["frame_id"]),
                    "pipeline_id": pipeline_id,
                    "feature_set": predictor.feature_set,
                    "evidence_role": predictor.evidence_role,
                    "model": predictor.model_name,
                    "stage": stage,
                    "sample_time_ns": int(row["sample_time_ns"]),
                    "generation_time_ns": int(row["generation_time_ns"]),
                    "deadline_time_ns": int(row["deadline_time_ns"]),
                    "frame_size_bytes": int(row["frame_size_bytes"]),
                    "actionable": int(row["actionable"]) == 1,
                    "ranking_score": float(raw_score),
                    "calibrated_probability": float(risk),
                }
            )
    return scores


def read_frame_labels(run_dir: Path) -> dict[int, int]:
    """Read immutable outcomes separately from predictor inputs."""
    result: dict[int, int] = {}
    with (run_dir / "frames.csv").open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            frame_id = int(row["frame_id"])
            if frame_id in result:
                raise ValueError(f"{run_dir}: duplicate frame outcome {frame_id}")
            result[frame_id] = int(row["deadline_miss"])
    return result


@dataclass
class TokenBucket:
    """A fractional credit bucket with a fixed burst capacity."""

    capacity: float
    balance: float

    @classmethod
    def full(cls, capacity: float) -> "TokenBucket":
        if not math.isfinite(capacity) or capacity <= 0:
            raise ValueError("token capacity must be finite and positive")
        return cls(capacity, capacity)

    def refill(self, amount: float) -> None:
        if amount < 0 or not math.isfinite(amount):
            raise ValueError("token refill must be finite and nonnegative")
        self.balance = min(self.capacity, self.balance + amount)

    def consume(self, cost: float) -> bool:
        if cost <= 0 or not math.isfinite(cost):
            raise ValueError("token cost must be finite and positive")
        if self.balance + 1e-12 < cost:
            return False
        self.balance = max(0.0, self.balance - cost)
        return True


COUNT_FIELDS = (
    "eligible_frames",
    "eligible_misses",
    "threshold_crossings",
    "actions",
    "true_positive_actions",
    "false_positive_actions",
    "budget_suppressions",
    "budget_suppressed_misses",
    "threshold_negative_misses",
    "useful_lead_true_positives",
    "action_bytes",
    "eligible_source_bytes",
)


def _finalize_counts(row: dict[str, Any]) -> dict[str, Any]:
    misses = row["eligible_misses"]
    actions = row["actions"]
    frames = row["eligible_frames"]
    source_bytes = row["eligible_source_bytes"]
    row.update(
        {
            "recall": row["true_positive_actions"] / misses if misses else None,
            "precision": row["true_positive_actions"] / actions if actions else None,
            "false_alarm_rate": row["false_positive_actions"] / actions if actions else None,
            "realized_action_rate": actions / frames if frames else None,
            "realized_byte_overhead": row["action_bytes"] / source_bytes
            if source_bytes
            else None,
            "useful_lead_recall": row["useful_lead_true_positives"] / misses
            if misses
            else None,
        }
    )
    return row


def replay_scores(
    scores: list[dict[str, Any]],
    labels: dict[int, int],
    config: dict[str, Any],
    median_frame_size: float,
    p99_frame_size: float,
    run_metadata: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Replay all frozen operating points for one individual run."""
    lookup: dict[tuple[str, int, str], dict[str, Any]] = {}
    pipelines = sorted({row["pipeline_id"] for row in scores})
    frame_metadata: dict[int, dict[str, Any]] = {}
    for row in scores:
        key = (row["pipeline_id"], row["frame_id"], row["stage"])
        if key in lookup:
            raise ValueError(f"duplicate score row: {key}")
        lookup[key] = row
        frame_metadata.setdefault(row["frame_id"], row)
    if set(frame_metadata) != set(labels):
        raise ValueError("scored frame identities do not match frames.csv")
    ordered_frames = sorted(
        frame_metadata,
        key=lambda frame_id: (
            frame_metadata[frame_id]["generation_time_ns"],
            frame_id,
        ),
    )
    bucket_config = config["token_bucket"]
    horizon = int(bucket_config["burst_horizon_frames"])
    minimum_frame_capacity = float(bucket_config["minimum_frame_capacity"])
    minimum_lead = int(config["minimum_useful_lead_time_us"])
    metrics: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    audit_points = {
        (
            float(item["probability_threshold"]),
            item["budget_kind"],
            float(item["budget"]),
        )
        for item in config.get("audit_operating_points", [])
    }
    for pipeline_id in pipelines:
        for policy in config["decision_policies"]:
            policy_id = policy["policy_id"]
            policy_stages = policy["stages"]
            for threshold in map(float, config["probability_thresholds"]):
                for budget_kind in config["budget_kinds"]:
                    for budget in map(float, config["budgets"]):
                        if budget_kind == "frames":
                            capacity = max(minimum_frame_capacity, budget * horizon)
                        else:
                            capacity = max(p99_frame_size, budget * horizon * median_frame_size)
                        bucket = TokenBucket.full(capacity)
                        counts = {name: 0 for name in COUNT_FIELDS}
                        lead_times: list[float] = []
                        audit = (threshold, budget_kind, budget) in audit_points
                        for frame_id in ordered_frames:
                            frame = frame_metadata[frame_id]
                            size = int(frame["frame_size_bytes"])
                            refill = budget if budget_kind == "frames" else budget * size
                            cost = 1.0 if budget_kind == "frames" else float(size)
                            bucket.refill(refill)
                            candidates = [
                                lookup[(pipeline_id, frame_id, stage)]
                                for stage in policy_stages
                            ]
                            actionable = [item for item in candidates if item["actionable"]]
                            if not actionable:
                                continue
                            label = labels[frame_id]
                            counts["eligible_frames"] += 1
                            counts["eligible_misses"] += label
                            counts["eligible_source_bytes"] += size
                            crossing = next(
                                (
                                    item
                                    for item in actionable
                                    if item["calibrated_probability"] >= threshold
                                ),
                                None,
                            )
                            acted = False
                            reason = "below_threshold"
                            lead_time = None
                            if crossing is None:
                                counts["threshold_negative_misses"] += label
                            else:
                                counts["threshold_crossings"] += 1
                                if bucket.consume(cost):
                                    acted = True
                                    reason = "action"
                                    counts["actions"] += 1
                                    counts["action_bytes"] += size
                                    counts["true_positive_actions"] += label
                                    counts["false_positive_actions"] += 1 - label
                                    lead_time = (
                                        crossing["deadline_time_ns"]
                                        - crossing["sample_time_ns"]
                                    ) / 1000.0
                                    lead_times.append(lead_time)
                                    if label and lead_time >= minimum_lead:
                                        counts["useful_lead_true_positives"] += 1
                                else:
                                    reason = "budget_suppressed"
                                    counts["budget_suppressions"] += 1
                                    counts["budget_suppressed_misses"] += label
                            if audit:
                                audits.append(
                                    {
                                        **run_metadata,
                                        "frame_id": frame_id,
                                        "pipeline_id": pipeline_id,
                                        "decision_policy": policy_id,
                                        "probability_threshold": threshold,
                                        "budget_kind": budget_kind,
                                        "budget": budget,
                                        "deadline_miss": label,
                                        "crossing_stage": ""
                                        if crossing is None
                                        else crossing["stage"],
                                        "calibrated_probability": ""
                                        if crossing is None
                                        else crossing["calibrated_probability"],
                                        "decision": reason,
                                        "acted": int(acted),
                                        "token_balance_after": bucket.balance,
                                        "token_capacity": bucket.capacity,
                                        "warning_lead_time_us": ""
                                        if lead_time is None
                                        else lead_time,
                                    }
                                )
                        base = {
                            **run_metadata,
                            "pipeline_id": pipeline_id,
                            "decision_policy": policy_id,
                            "probability_threshold": threshold,
                            "budget_kind": budget_kind,
                            "budget": budget,
                            "token_capacity": capacity,
                            **counts,
                            "mean_warning_lead_time_us": float(np.mean(lead_times))
                            if lead_times
                            else None,
                        }
                        metrics.append(_finalize_counts(base))
    return metrics, audits


def write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Write heterogeneous dictionaries with a deterministic union header."""
    materialized = list(rows)
    columns = sorted({key for row in materialized for key in row})
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(
            {
                key: "" if value is None else value
                for key, value in row.items()
            }
            for row in materialized
        )


def aggregate_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Combine replay counts across any selected set of individual runs."""
    dimensions = (
        "split_role",
        "scenario_name",
        "pipeline_id",
        "decision_policy",
        "probability_threshold",
        "budget_kind",
        "budget",
    )
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for source in rows:
        for scenario in (source["scenario_name"], "__all_selected__"):
            values = dict(source)
            values["scenario_name"] = scenario
            key = tuple(values[name] for name in dimensions)
            target = grouped.setdefault(
                key,
                {
                    **{name: values[name] for name in dimensions},
                    **{name: 0 for name in COUNT_FIELDS},
                    "run_count": 0,
                    "run_group_ids": set(),
                },
            )
            target["run_count"] += 1
            target["run_group_ids"].add(source["run_group_id"])
            for name in COUNT_FIELDS:
                target[name] += int(source[name])
    result = []
    for target in grouped.values():
        target["run_group_count"] = len(target.pop("run_group_ids"))
        result.append(_finalize_counts(target))
    return sorted(result, key=lambda row: tuple(str(row[name]) for name in dimensions))


def write_json(path: Path, value: Any) -> None:
    """Write deterministic human-readable JSON."""
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
