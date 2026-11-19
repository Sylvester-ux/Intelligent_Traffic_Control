"""Decision engine rules: scoring, clamping, hysteresis, starvation, health."""

from __future__ import annotations

import pytest

from decision.engine import DecisionEngine
from decision.models import ApproachMetrics, ServingUnit
from decision.settings import DecisionSettings

COMPAT_UNITS = (
    ServingUnit("MAIN", ("MAIN",)),
    ServingUnit("SECONDARY", ("SEC_A", "SEC_B")),
)
THREE_UNITS = (
    ServingUnit("MAIN", ("MAIN",)),
    ServingUnit("SEC_A", ("SEC_A",)),
    ServingUnit("SEC_B", ("SEC_B",)),
)


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


def engine(units=COMPAT_UNITS, **settings_kwargs) -> DecisionEngine:
    settings_kwargs.setdefault("smoothing_window_s", 0.0)
    return DecisionEngine(units, DecisionSettings(**settings_kwargs))


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
    assert recommendation.unit_id == "MAIN"
    assert recommendation.priority == "NORMAL"
    assert 5 <= recommendation.green_time <= 30
    assert any("recommend MAIN" in reason for reason in recommendation.reasons)


def test_three_unit_layout_scores_each_approach_on_its_own():
    decision_engine = engine(THREE_UNITS)
    decision = decision_engine.evaluate(
        feed(
            100.0,
            MAIN={"queue": 4},
            SEC_A={"queue": 3},
            SEC_B={"queue": 1},
        ),
        100.0,
    )

    assert decision.unit_scores == {"MAIN": 4.0, "SEC_A": 3.0, "SEC_B": 1.0}
    assert decision.recommendation.unit_id == "MAIN"


def test_two_and_three_unit_layouts_score_the_same_metrics_consistently():
    snapshots = feed(100.0, MAIN={"queue": 5}, SEC_A={"queue": 4}, SEC_B={"queue": 0})

    compat = engine(COMPAT_UNITS).evaluate(snapshots, 100.0)
    three = engine(THREE_UNITS).evaluate(snapshots, 100.0)

    assert compat.unit_scores["MAIN"] == three.unit_scores["MAIN"] == 5.0
    assert compat.unit_scores["SECONDARY"] == pytest.approx(
        three.unit_scores["SEC_A"] + three.unit_scores["SEC_B"]
    )
    # MAIN has the highest single-unit demand in both layouts.
    assert compat.recommendation.unit_id == three.recommendation.unit_id == "MAIN"


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
    assert decision.recommendation.unit_id == "MAIN"
    assert any("units with unhealthy" in reason for reason in decision.reasons)


def test_unit_with_one_blind_camera_is_never_scored_from_partial_data():
    # SEC_B is blind while SEC_A is very busy. The merged unit must not be
    # scored from SEC_A alone, because that would treat missing data as traffic
    # (and, in the other direction, understate the unit).
    decision_engine = engine(require_all_approaches_healthy=False)
    decision = decision_engine.evaluate(
        feed(
            100.0,
            MAIN={"queue": 2},
            SEC_A={"queue": 30},
            SEC_B={"healthy": False},
        ),
        100.0,
    )
    assert decision.emitted, decision.reasons
    assert "SECONDARY" not in decision.unit_scores
    assert decision.recommendation.unit_id == "MAIN"


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
    assert decision.recommendation.unit_id == "MAIN"


def test_starvation_guard_forces_waiting_unit():
    decision_engine = engine(
        min_interval_s=0,
        min_delta_s=0,
        max_wait_before_force_s=60,
    )
    first = decision_engine.evaluate(feed(0.0, MAIN={"queue": 20}), 0.0)
    assert first.recommendation.unit_id == "MAIN"

    # MAIN stays busiest, but SECONDARY has demand and has waited 61 s.
    decision = decision_engine.evaluate(
        feed(61.0, MAIN={"queue": 20}, SEC_A={"queue": 3}), 61.0
    )
    assert decision.emitted, decision.reasons
    assert decision.recommendation.unit_id == "SECONDARY"
    assert any("starvation" in reason for reason in decision.reasons)


def test_no_starvation_without_demand():
    decision_engine = engine(
        min_interval_s=0, min_delta_s=0, max_wait_before_force_s=60
    )
    decision_engine.evaluate(feed(0.0, MAIN={"queue": 20}), 0.0)
    decision = decision_engine.evaluate(feed(61.0, MAIN={"queue": 20}), 61.0)
    # SECONDARY is idle, so the guard must not invent demand for it.
    assert not any("starvation" in reason for reason in decision.reasons)
    assert decision.unit_scores["MAIN"] > decision.unit_scores["SECONDARY"]


def test_compat_layout_aggregates_secondary_demand_as_sum():
    summed = engine(COMPAT_UNITS, aggregation="sum")
    decision = summed.evaluate(
        feed(100.0, MAIN={"queue": 5}, SEC_A={"queue": 4}, SEC_B={"queue": 4}),
        100.0,
    )
    assert decision.unit_scores["SECONDARY"] == pytest.approx(8.0)
    assert decision.recommendation.unit_id == "SECONDARY"

    maxed = engine(COMPAT_UNITS, aggregation="max")
    decision = maxed.evaluate(
        feed(100.0, MAIN={"queue": 5}, SEC_A={"queue": 4}, SEC_B={"queue": 4}),
        100.0,
    )
    assert decision.unit_scores["SECONDARY"] == pytest.approx(4.0)
    assert decision.recommendation.unit_id == "MAIN"


def test_three_unit_layout_does_not_aggregate_siblings():
    decision_engine = engine(THREE_UNITS, aggregation="sum")
    decision = decision_engine.evaluate(
        feed(100.0, MAIN={"queue": 1}, SEC_A={"queue": 4}, SEC_B={"queue": 4}),
        100.0,
    )
    assert decision.unit_scores["SEC_A"] == pytest.approx(4.0)
    assert decision.unit_scores["SEC_B"] == pytest.approx(4.0)
    assert decision.recommendation.unit_id == "SEC_A"  # tie -> config order


def test_smoothing_dampens_single_frame_spike():
    decision_engine = engine(smoothing_window_s=5.0, idle_score_threshold=1.0)
    now = 100.0
    decision_engine.evaluate(feed(now, MAIN={"queue": 20}), now)
    # A single quiet frame must not immediately look idle after averaging.
    decision = decision_engine.evaluate(feed(now + 0.2, MAIN={"queue": 0}), now + 0.2)
    # 20 and 0 average to 10, still far above the idle threshold.
    assert decision.unit_scores["MAIN"] == pytest.approx(10.0)
    assert not any("idle" in reason for reason in decision.reasons)


def test_expected_red_uses_the_other_greens_and_clearances():
    decision_engine = engine(yellow_s=3, all_red_s=1)
    decision = decision_engine.evaluate(
        feed(100.0, MAIN={"queue": 20}), 100.0
    )

    assert decision.recommendation.green_time == 18
    # MAIN waits for SECONDARY's expected green (default 8) and one clearance.
    # SECONDARY waits for the green just recommended to MAIN plus one clearance.
    assert decision.expected_red_s == {"MAIN": 12.0, "SECONDARY": 22.0}
    assert decision.recommendation.expected_red_s == decision.expected_red_s


def test_expected_red_falls_back_to_the_default_after_memory_expires():
    decision_engine = engine(yellow_s=3, all_red_s=1, green_memory_s=0)
    decision_engine.evaluate(feed(100.0, MAIN={"queue": 20}), 100.0)

    # MAIN's 18 s green is no longer remembered, so both units expect base 8.
    decision = decision_engine.evaluate(
        feed(101.0, MAIN={"queue": 20}), 101.0
    )
    assert decision.expected_red_s == {"MAIN": 12.0, "SECONDARY": 12.0}


def test_max_red_guard_forces_a_unit_that_would_wait_too_long():
    decision_engine = engine(
        min_interval_s=0,
        min_delta_s=0,
        max_wait_before_force_s=1000,
        yellow_s=3,
        all_red_s=1,
        max_red_s_by_unit={"MAIN": 90.0, "SECONDARY": 10.0},
    )
    decision_engine.evaluate(feed(0.0, MAIN={"queue": 20}), 0.0)

    # SECONDARY is lightly loaded but its estimated red (18 + 4 = 22 s)
    # exceeds its 10 s cap, so it must not be starved by the 60 s wall clock.
    decision = decision_engine.evaluate(
        feed(1.0, MAIN={"queue": 20}, SEC_A={"queue": 2}), 1.0
    )
    assert decision.emitted, decision.reasons
    assert decision.recommendation.unit_id == "SECONDARY"
    assert any(
        "estimated red" in reason and "cap" in reason
        for reason in decision.reasons
    )


def test_max_red_guard_does_not_invent_demand():
    decision_engine = engine(
        min_interval_s=0,
        min_delta_s=0,
        max_wait_before_force_s=1000,
        max_red_s_by_unit={"MAIN": 90.0, "SECONDARY": 10.0},
    )
    decision_engine.evaluate(feed(0.0, MAIN={"queue": 20}), 0.0)
    decision = decision_engine.evaluate(feed(1.0, MAIN={"queue": 20}), 1.0)

    assert decision.unit_scores["MAIN"] > decision.unit_scores["SECONDARY"]
    assert not any("starvation" in reason for reason in decision.reasons)


def test_settings_reject_out_of_protocol_green_range():
    with pytest.raises(ValueError):
        DecisionSettings(min_green_s=3)
    with pytest.raises(ValueError):
        DecisionSettings(max_green_s=45)
    with pytest.raises(ValueError):
        DecisionSettings(min_green_s=10, max_green_s=9)


def test_engine_rejects_ambiguous_unit_layouts():
    with pytest.raises(ValueError):
        DecisionEngine([])
    with pytest.raises(ValueError):
        DecisionEngine(
            (
                ServingUnit("MAIN", ("MAIN",)),
                ServingUnit("MAIN", ("SEC_A",)),
            )
        )
    with pytest.raises(ValueError):
        DecisionEngine(
            (
                ServingUnit("MAIN", ("MAIN",)),
                ServingUnit("SECONDARY", ("MAIN", "SEC_A")),
            )
        )
