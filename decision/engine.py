"""Deterministic demand scoring and green-time recommendation.

Pure logic: no I/O, no clocks (``now`` is always supplied), no threads. The
orchestrator feeds it :class:`ApproachMetrics` and writes whatever it returns.

Two-phase compatibility mode is a configuration concern, not an engine special
case: approaches are mapped to wire movements (``SEC_A`` and ``SEC_B`` both map
to ``SECONDARY``) and the engine groups them, so a future three-phase mode is a
mapping change plus a protocol adapter.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

from decision.models import (
    CONGESTION_WEIGHTS,
    ApproachMetrics,
    Recommendation,
)
from decision.settings import DecisionSettings, Weights

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Decision:
    """Outcome of one evaluation cycle."""

    recommendation: Optional[Recommendation]
    reasons: tuple[str, ...]
    group_scores: Mapping[str, float]
    healthy: bool

    @property
    def emitted(self) -> bool:
        return self.recommendation is not None


@dataclass
class _Sample:
    timestamp: float
    queue: float
    stationary: float
    crossings: float
    wait: float
    congestion: float


class DecisionEngine:
    """Turns per-approach metrics into at most one advisory recommendation."""

    def __init__(
        self,
        approaches: Mapping[str, str],
        settings: Optional[DecisionSettings] = None,
    ) -> None:
        if not approaches:
            raise ValueError("at least one approach must be configured")
        for approach_id, movement in approaches.items():
            if movement not in ("MAIN", "SECONDARY"):
                raise ValueError(
                    f"approach {approach_id!r} maps to unknown movement {movement!r}"
                )
        self.approaches: dict[str, str] = dict(approaches)
        self.settings = settings or DecisionSettings()

        # wire movement -> approach ids, preserving config order
        self.groups: dict[str, list[str]] = {}
        for approach_id, movement in self.approaches.items():
            self.groups.setdefault(movement, []).append(approach_id)

        self._samples: dict[str, deque[_Sample]] = {
            approach_id: deque() for approach_id in self.approaches
        }
        self._started_at: Optional[float] = None
        self._last_served: dict[str, float] = {}
        self._last_request_at: Optional[float] = None
        self._last_green: Optional[int] = None
        self._last_movement: Optional[str] = None

    # -- public -----------------------------------------------------------

    def evaluate(
        self, metrics: Mapping[str, ApproachMetrics], now: float
    ) -> Decision:
        """Score groups and return a decision for this instant."""
        if self._started_at is None:
            self._started_at = now
            for movement in self.groups:
                self._last_served[movement] = now

        healthy_ids, unhealthy_ids = self._classify_health(metrics, now)
        reasons: list[str] = []

        if unhealthy_ids:
            reasons.append("unhealthy approaches: " + ", ".join(sorted(unhealthy_ids)))
            if self.settings.require_all_approaches_healthy:
                reasons.append(
                    "policy: no recommendation while any approach is unhealthy"
                )
                return Decision(None, tuple(reasons), {}, healthy=False)

        if not healthy_ids:
            reasons.append("no healthy approaches")
            return Decision(None, tuple(reasons), {}, healthy=False)

        smoothed = {
            approach_id: self._smooth(metrics[approach_id], now)
            for approach_id in healthy_ids
        }

        scores: dict[str, float] = {}
        details: list[str] = []
        for movement, approach_ids in self.groups.items():
            usable = [aid for aid in approach_ids if aid in smoothed]
            if not usable:
                continue
            score, breakdown = self._score_group(usable, smoothed)
            scores[movement] = round(score, 3)
            details.append(f"{movement}={score:.2f} ({breakdown})")

        if not scores:
            reasons.append("no scored groups")
            return Decision(None, tuple(reasons), {}, healthy=False)

        if max(scores.values()) < self.settings.idle_score_threshold:
            reasons.append(
                f"idle: all group scores below {self.settings.idle_score_threshold}"
            )
            return Decision(None, tuple(reasons), scores, healthy=True)

        winner, forced = self._choose_winner(scores, now)
        forced = forced and winner != self._last_movement
        if forced:
            reasons.append(
                f"starvation guard: {winner} waited more than "
                f"{self.settings.max_wait_before_force_s:.0f}s with demand"
            )
        reasons.append("scores: " + "; ".join(details))

        green_time, clamp_note = self._green_time(scores[winner])
        reasons.append(clamp_note)

        blocked = self._change_gate(winner, green_time, now)
        if blocked is not None:
            reasons.append(blocked)
            return Decision(None, tuple(reasons), scores, healthy=True)

        reasons.append(f"recommend {winner} green {green_time}s (advisory only)")
        recommendation = Recommendation(
            movement=winner,
            green_time=green_time,
            priority="NORMAL",
            reasons=tuple(reasons),
            scores=scores,
        )

        self._record_emission(winner, green_time, now)

        return Decision(
            recommendation=recommendation,
            reasons=tuple(reasons),
            group_scores=scores,
            healthy=True,
        )

    def reset(self) -> None:
        """Forget smoothing and request history (used when the link restarts)."""
        for queue in self._samples.values():
            queue.clear()
        self._started_at = None
        self._last_served.clear()
        self._last_request_at = None
        self._last_green = None
        self._last_movement = None

    # -- health -----------------------------------------------------------

    def _classify_health(
        self, metrics: Mapping[str, ApproachMetrics], now: float
    ) -> tuple[list[str], list[str]]:
        healthy: list[str] = []
        unhealthy: list[str] = []

        for approach_id in self.approaches:
            snapshot = metrics.get(approach_id)
            if snapshot is None:
                unhealthy.append(approach_id)
                continue
            age = now - snapshot.timestamp
            if not snapshot.healthy or age > self.settings.max_metrics_age_s:
                unhealthy.append(approach_id)
            else:
                healthy.append(approach_id)
        return healthy, unhealthy

    # -- smoothing and scoring -------------------------------------------

    def _smooth(self, snapshot: ApproachMetrics, now: float) -> _Sample:
        sample = _Sample(
            timestamp=now,
            queue=float(snapshot.vehicle_count_in_region),
            stationary=float(snapshot.stationary_count),
            crossings=float(snapshot.crossings_last_window),
            wait=float(max(0.0, snapshot.max_wait_s)),
            congestion=CONGESTION_WEIGHTS.get(snapshot.congestion_level, 0.0),
        )
        queue = self._samples[snapshot.approach_id]
        queue.append(sample)

        window = self.settings.smoothing_window_s
        if window <= 0:
            queue.clear()
            return sample
        cutoff = now - window
        while queue and queue[0].timestamp < cutoff:
            queue.popleft()
        if not queue:
            return sample

        count = len(queue)
        return _Sample(
            timestamp=now,
            queue=sum(item.queue for item in queue) / count,
            stationary=sum(item.stationary for item in queue) / count,
            crossings=sum(item.crossings for item in queue) / count,
            wait=max(item.wait for item in queue),
            congestion=sum(item.congestion for item in queue) / count,
        )

    def _score_group(
        self, approach_ids: Iterable[str], smoothed: Mapping[str, _Sample]
    ) -> tuple[float, str]:
        samples = [smoothed[approach_id] for approach_id in approach_ids]
        aggregate = self._aggregate
        weights: Weights = self.settings.weights

        queue = aggregate([sample.queue for sample in samples])
        stationary = aggregate([sample.stationary for sample in samples])
        congestion = aggregate([sample.congestion for sample in samples])
        # Waiting is a max-of-group signal: one long-waiting approach matters
        # even when the others are empty.
        wait = max(sample.wait for sample in samples)

        score = (
            weights.queue * queue
            + weights.stationary * stationary
            + weights.wait * wait
            + weights.congestion * congestion
        )
        breakdown = (
            f"q={queue:.1f} s={stationary:.1f} w={wait:.1f}s c={congestion:.1f}"
        )
        return score, breakdown

    def _aggregate(self, values: list[float]) -> float:
        if not values:
            return 0.0
        if self.settings.aggregation == "max":
            return max(values)
        return sum(values)

    def _choose_winner(
        self, scores: Mapping[str, float], now: float
    ) -> tuple[str, bool]:
        """Highest score, unless a starving group with demand must go first."""
        starving = [
            movement
            for movement, score in scores.items()
            if score >= self.settings.idle_score_threshold
            and now - self._last_served.get(movement, now)
            > self.settings.max_wait_before_force_s
        ]
        if starving:
            # Deterministic: longest wait wins; the group that was just served
            # loses ties so one busy approach cannot monopolise the junction.
            starving.sort(
                key=lambda movement: (
                    self._last_served.get(movement, now),
                    movement == self._last_movement,
                    list(self.groups).index(movement),
                )
            )
            return starving[0], True

        # Deterministic tie-break on configured group order.
        winner = max(
            scores,
            key=lambda movement: (scores[movement], -list(self.groups).index(movement)),
        )
        return winner, False

    def _green_time(self, winner_score: float) -> tuple[int, str]:
        normalized = min(1.0, max(0.0, winner_score) / self.settings.score_saturation)
        raw = self.settings.base_green_s + self.settings.k_green_s * normalized
        clamped = int(round(raw))
        note = f"green raw={raw:.1f}s"
        if clamped < self.settings.min_green_s:
            note += f", raised to configured min {self.settings.min_green_s}s"
            clamped = self.settings.min_green_s
        if clamped > self.settings.max_green_s:
            note += f", lowered to configured max {self.settings.max_green_s}s"
            clamped = self.settings.max_green_s
        clamped = max(5, min(30, clamped))
        return clamped, note

    def _change_gate(
        self, movement: str, green_time: int, now: float
    ) -> Optional[str]:
        if self._last_request_at is None:
            return None
        elapsed = now - self._last_request_at
        if elapsed < self.settings.min_interval_s:
            return (
                f"suppressed: {elapsed:.1f}s since last request "
                f"(min interval {self.settings.min_interval_s:.0f}s)"
            )
        if (
            movement == self._last_movement
            and self._last_green is not None
            and abs(green_time - self._last_green) <= self.settings.min_delta_s
        ):
            return (
                f"suppressed: {movement} green unchanged within "
                f"{self.settings.min_delta_s}s"
            )
        return None

    def _record_emission(self, movement: str, green_time: int, now: float) -> None:
        self._last_request_at = now
        self._last_green = green_time
        self._last_movement = movement
        self._last_served[movement] = now
