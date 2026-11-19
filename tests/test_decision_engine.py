"""Decision engine rules: scoring, clamping, hysteresis, starvation, health."""

from __future__ import annotations

import pytest

from decision.engine import DecisionEngine
from decision.models import ApproachMetrics
from decision.settings import DecisionSettings

APPROACHES = {"MAIN": "MAIN", "SEC_A": "SECONDARY", "SEC_B": "SECONDARY"}


def metrics(
    approach_id: str,
    now: float,
    *,
    queue: int = 0,
    stationary: int = 0,
    crossings: int = 0,
    wait: float = 0.0,
    congestion: str = "Low",
    healthy: bool = True,
    timestamp: float | None = None,
) -> ApproachMetrics:
    return ApproachMetrics(
        approach_id=approach_id,
        camera_id=f"cam_{approach_id}",
        timestamp=now if timestamp is None else timestamp,
        healthy=healthy,
        vehicle_count_in_region=queue,
        stationary_count=stationary,
        crossings_last_window=crossings,
        congestion_level=congestion,
        mean_wait_s=wait,
        max_wait_s=wait,
    )


def feed(now: float, **overrides) -> dict[str, ApproachMetrics]:
    base = {
        "MAIN": metrics("MAIN", now),
        "SEC_A": metrics("SEC_A", now),
        "SEC_B": metrics("SEC_B", now),
    }
    for approach_id, values in overrides.items():
        base[approach_id] = metrics(approach_id, now, **values)
    return base


def engine(**settings_kwargs) -> DecisionEngine:
    settings_kwargs.setdefault("smoothing_window_s", 0.0)
    return DecisionEngine(APPROACHES, DecisionSettings(**settings_kwargs))


def test_busy_main_gets_the_green():
    decision_engine = engine()
    decision = decision_engine.evaluate(
        feed(
            100.0,
            MAIN={"queue": 10, "stationary": 5, "wait": 20.0, "congestion": "High"},
            SEC_A={"queue": 1},
        ),
        100.0,
    )

    assert decision.emitted, decision.reasons
    recommendation = decision.recommendation
    assert recommendation.movement == "MAIN"
    assert recommendation.priority == "NORMAL"
    assert 5 <= recommendation.green_time <= 30
    assert any("recommend MAIN" in reason for reason in recommendation.reasons)


def test_green_time_clamps_to_protocol_and_config():
    # Saturate demand -> raw green far above the maximum.
    decision_engine = engine(base_green_s=8, k_green_s=100, max_green_s=30)
    decision = decision_engine.evaluate(
        feed(100.0, MAIN={"queue": 40, "stationary": 20}), 100.0
    )
    assert decision.recommendation.green_time == 30

    # Config can narrow the ceiling.
    narrow = engine(base_green_s=8, k_green_s=100, max_green_s=12)
    decision = narrow.evaluate(feed(100.0, MAIN={"queue": 40}), 100.0)
    assert decision.recommendation.green_time == 12

    # Minimum applies too.
    low = engine(base_green_s=8, k_green_s=0, min_green_s=9)
    decision = low.evaluate(feed(100.0, MAIN={"queue": 2}), 100.0)
    assert decision.recommendation.green_time == 9


def test_all_idle_emits_nothing():
    decision_engine = engine()
    decision = decision_engine.evaluate(feed(100.0), 100.0)
    assert not decision.emitted
    assert any("idle" in reason for reason in decision.reasons)


def test_any_unhealthy_approach_suppresses_recommendation():
    decision_engine = engine()
    decision = decision_engine.evaluate(
        feed(100.0, MAIN={"queue": 12}, SEC_B={"healthy": False}), 100.0
    )
    assert not decision.emitted
    assert any("unhealthy approaches" in reason for reason in decision.reasons)
    assert any("no recommendation" in reason for reason in decision.reasons)


def test_stale_metrics_count_as_unhealthy():
    decision_engine = engine()
    stale = feed(100.0, MAIN={"queue": 12})
    stale["SEC_A"] = metrics("SEC_A", 100.0, timestamp=90.0)
    decision = decision_engine.evaluate(stale, 100.0)
    assert not decision.emitted


def test_graceful_degradation_when_all_health_not_required():
    decision_engine = engine(require_all_approaches_healthy=False)
    decision = decision_engine.evaluate(
        feed(100.0, MAIN={"queue": 12}, SEC_A={"healthy": False}), 100.0
    )
    assert decision.emitted, decision.reasons
    assert decision.recommendation.movement == "MAIN"


def test_min_interval_suppresses_repeat_requests():
    decision_engine = engine(min_interval_s=8, min_delta_s=3)
    first = decision_engine.evaluate(feed(100.0, MAIN={"queue": 12}), 100.0)
    assert first.emitted

    second = decision_engine.evaluate(feed(102.0, MAIN={"queue": 12}), 102.0)
    assert not second.emitted
    assert any("min interval" in reason for reason in second.reasons)

    third = decision_engine.evaluate(feed(120.0, MAIN={"queue": 12}), 120.0)
    assert not third.emitted
    assert any("unchanged" in reason for reason in third.reasons)


def test_material_change_reissues_request():
    decision_engine = engine(min_interval_s=8, min_delta_s=3)
    decision_engine.evaluate(feed(100.0, MAIN={"queue": 30}), 100.0)

    # Green would change by more than min_delta_s: allowed again.
    decision = decision_engine.evaluate(feed(110.0, MAIN={"queue": 3}), 110.0)
    assert decision.emitted, decision.reasons
    assert decision.recommendation.movement == "MAIN"


def test_starvation_guard_forces_waiting_group():
    decision_engine = engine(
        min_interval_s=0,
        min_delta_s=0,
        max_wait_before_force_s=60,
    )
    first = decision_engine.evaluate(feed(0.0, MAIN={"queue": 20}), 0.0)
    assert first.recommendation.movement == "MAIN"

    # MAIN stays busiest, but SECONDARY has demand and has waited 61 s.
    decision = decision_engine.evaluate(
        feed(61.0, MAIN={"queue": 20}, SEC_A={"queue": 3}), 61.0
    )
    assert decision.emitted, decision.reasons
    assert decision.recommendation.movement == "SECONDARY"
    assert any("starvation" in reason for reason in decision.reasons)


def test_no_starvation_without_demand():
    decision_engine = engine(
        min_interval_s=0, min_delta_s=0, max_wait_before_force_s=60
    )
    decision_engine.evaluate(feed(0.0, MAIN={"queue": 20}), 0.0)
    decision = decision_engine.evaluate(feed(61.0, MAIN={"queue": 20}), 61.0)
    # SECONDARY is idle, so the guard must not invent demand for it.
    assert not any("starvation" in reason for reason in decision.reasons)
    assert decision.group_scores["MAIN"] > decision.group_scores["SECONDARY"]


def test_compat_mode_aggregates_secondary_demand_as_sum():
    summed = engine(aggregation="sum")
    decision = summed.evaluate(
        feed(100.0, MAIN={"queue": 5}, SEC_A={"queue": 4}, SEC_B={"queue": 4}),
        100.0,
    )
    assert decision.group_scores["SECONDARY"] == pytest.approx(8.0)
    assert decision.recommendation.movement == "SECONDARY"

    maxed = engine(aggregation="max")
    decision = maxed.evaluate(
        feed(100.0, MAIN={"queue": 5}, SEC_A={"queue": 4}, SEC_B={"queue": 4}),
        100.0,
    )
    assert decision.group_scores["SECONDARY"] == pytest.approx(4.0)
    assert decision.recommendation.movement == "MAIN"


def test_smoothing_dampens_single_frame_spike():
    decision_engine = engine(smoothing_window_s=5.0, idle_score_threshold=1.0)
    now = 100.0
    decision_engine.evaluate(feed(now, MAIN={"queue": 20}), now)
    # A single quiet frame must not immediately look idle after averaging.
    decision = decision_engine.evaluate(feed(now + 0.2, MAIN={"queue": 0}), now + 0.2)
    # 20 and 0 average to 10, still far above the idle threshold.
    assert decision.group_scores["MAIN"] == pytest.approx(10.0)
    assert not any("idle" in reason for reason in decision.reasons)


def test_settings_reject_out_of_protocol_green_range():
    with pytest.raises(ValueError):
        DecisionSettings(min_green_s=3)
    with pytest.raises(ValueError):
        DecisionSettings(max_green_s=45)
    with pytest.raises(ValueError):
        DecisionSettings(min_green_s=10, max_green_s=9)


def test_unknown_wire_movement_is_rejected():
    with pytest.raises(ValueError):
        DecisionEngine({"MAIN": "NORTH"})
