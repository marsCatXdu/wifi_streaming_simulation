"""Platform-stable mirror of randomized frame assignment in ns-3.

The implementation intentionally mirrors ``RandomizedFrameAssignment`` in
``contrib/wifi-streaming``.  Keep changes synchronized with that C++ contract;
validation and causal-analysis tools use this module to reproduce assignments
without consuming a random-number stream.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from numbers import Integral, Real

ALGORITHM_ID = "splitmix64_v1"

_UINT64_MASK = (1 << 64) - 1
_SPLITMIX_INCREMENT = 0x9E3779B97F4A7C15
_SPLITMIX_MULTIPLIER_1 = 0xBF58476D1CE4E5B9
_SPLITMIX_MULTIPLIER_2 = 0x94D049BB133111EB
_UNIT_DRAW_SCALE = 2.0**-53


class ExplorationArm(str, Enum):
    """Stable arm labels written by the randomized intervention controller."""

    CONTROL = "CONTROL"
    FULL_COPY_T2 = "FULL_COPY_T2"
    FULL_COPY_T4 = "FULL_COPY_T4"


@dataclass(frozen=True, slots=True)
class FrameAssignment:
    """Immutable deterministic assignment for one frame."""

    raw_draw: int
    unit_draw: float
    arm: ExplorationArm
    arm_probability: float


def _require_uint64(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be an unsigned 64-bit integer")
    integer = int(value)
    if integer < 0 or integer > _UINT64_MASK:
        raise ValueError(f"{name} must be an unsigned 64-bit integer")
    return integer


def _require_probability(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite, nonnegative number")
    probability = float(value)
    if not math.isfinite(probability) or probability < 0.0:
        raise ValueError(f"{name} must be a finite, nonnegative number")
    return probability


def _splitmix64(value: int) -> int:
    value = (value + _SPLITMIX_INCREMENT) & _UINT64_MASK
    value = ((value ^ (value >> 30)) * _SPLITMIX_MULTIPLIER_1) & _UINT64_MASK
    value = ((value ^ (value >> 27)) * _SPLITMIX_MULTIPLIER_2) & _UINT64_MASK
    return (value ^ (value >> 31)) & _UINT64_MASK


def assign_frame(
    salt: int,
    seed: int,
    run: int,
    frame_id: int,
    t2_probability: float,
    t4_probability: float,
) -> FrameAssignment:
    """Return the exact ``splitmix64_v1`` assignment for one frame.

    The unsigned-64 tuple fold is salt, seed, run, then frame ID.  The top 53
    result bits form a draw in ``[0, 1)``.  Arm intervals are half-open:
    ``[0, pT2)``, ``[pT2, pT2 + pT4)``, and the remaining control interval.

    Raises:
        ValueError: If a tuple field is not a uint64, either probability is a
            bool, non-real, non-finite, or negative value, or their sum exceeds
            one.
    """

    salt = _require_uint64("salt", salt)
    seed = _require_uint64("seed", seed)
    run = _require_uint64("run", run)
    frame_id = _require_uint64("frame_id", frame_id)
    t2_probability = _require_probability("t2_probability", t2_probability)
    t4_probability = _require_probability("t4_probability", t4_probability)
    if t4_probability > 1.0 - t2_probability:
        raise ValueError("randomized exploration probabilities must sum to at most one")

    state = _splitmix64(salt)
    state = _splitmix64(state ^ seed)
    state = _splitmix64(state ^ run)
    raw_draw = _splitmix64(state ^ frame_id)
    unit_draw = (raw_draw >> 11) * _UNIT_DRAW_SCALE

    if unit_draw < t2_probability:
        arm = ExplorationArm.FULL_COPY_T2
        arm_probability = t2_probability
    elif unit_draw < t2_probability + t4_probability:
        arm = ExplorationArm.FULL_COPY_T4
        arm_probability = t4_probability
    else:
        arm = ExplorationArm.CONTROL
        arm_probability = (1.0 - t2_probability) - t4_probability

    return FrameAssignment(raw_draw, unit_draw, arm, arm_probability)
