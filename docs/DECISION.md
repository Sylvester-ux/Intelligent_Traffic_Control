# Decision engine

Pure, deterministic logic in `decision/`. It reads per-approach metrics and
returns at most one **advisory** recommendation. It never changes a phase and
never sends emergency priority (`priority` is always `NORMAL`).

All thresholds below are **starting values for tuning on real footage**, not
calibrated constants.

## Inputs

`ApproachMetrics` at ~1 Hz per approach:

- `vehicle_count_in_region`, `stationary_count`
- `crossings_last_window`
- `congestion_level` (Low / Moderate / High)
- `mean_wait_s`, `max_wait_s` (how long tracked vehicles have been stationary)
- `frame_age_s`, `healthy`, `timestamp`

## Health policy (default)

An approach is unhealthy when its camera is not `LIVE`, its snapshot is older
than `max_metrics_age_s` (2 s), or its pipeline has stalled. **If any approach is
unhealthy the engine makes no recommendation at all**, and the heartbeat gate
closes too, so the Uno runs its autonomous cycle. Missing data is never treated
as zero traffic.

Alternative (configurable, not default): `require_all_approaches_healthy: false`
scores only the healthy groups and keeps recommending. That is "degrade
gracefully", which risks over-serving an approach whose camera is blind.

## Scoring

```text
score = w_queue      * vehicles_in_region
      + w_stationary * stationary_count
      + w_wait       * max_wait_s
      + w_cong       * congestion_weight      # Low 0, Moderate 1, High 2
```

Defaults: `w_queue=1.0`, `w_stationary=1.5`, `w_wait=0.1`, `w_cong=1.0`.

Metrics are averaged over `smoothing_window_s` (default 5 s) before scoring; the
wait signal uses the maximum in the window so one long waiter is not averaged
away.

In two-phase compatibility mode a wire group can hold several approaches
(`SEC_A` + `SEC_B` → `SECONDARY`). Counts and congestion are combined with
`aggregation: sum` (default) or `max`; waiting is always the group maximum.

## Green time

```text
normalized = min(1, score / score_saturation)      # saturation default 10
green      = clamp(round(base + k * normalized), min_green_s, max_green_s)
```

Defaults: `base=8`, `k=10`, so idle-but-moving demand gets 8–18 s. The clamp is
hard-limited to the protocol's 5–30 s range, and config may narrow it (e.g.
min 8, max 25).

## Fairness: starvation guard

Each group remembers when it was last recommended. If a group with demand
(score at or above the idle threshold) has not been recommended for more than
`max_wait_before_force_s` (default 60 s), it is forced to the top. Ties go to
the group that was not just served, so one busy approach cannot monopolise the
junction. It is still a recommendation: the Uno applies it at its next phase
change.

## Hysteresis

A request is emitted only when **all** of these hold:

- at least `min_interval_s` (default 8 s) since the previous request;
- the movement changed, or green time differs by more than `min_delta_s`
  (default 3 s);
- the new recommendation is not idle.

This matters because the Uno stores a request as *pending* and applies it at the
next phase entry — flooding it with requests achieves nothing.

## Idle

If every group's score is below `idle_score_threshold` (default 1.0), nothing is
emitted and the Uno keeps its default cycle.

## Output

`Recommendation(movement, green_time, priority="NORMAL", reasons, scores)`.
`reasons` explains the scores, the rule that fired, clamping and any suppression,
and is written to the JSONL decision log alongside the gateway reply.

## Three-phase control

`MAIN`, `SEC_A`, `SEC_B` are three approaches; the wire protocol only knows
`MAIN` and `SECONDARY`. The engine is already group-based, so three-phase
control would be a config mapping change (`SEC_A → SIDE_A`,
`SEC_B → SIDE_B`) plus a protocol/firmware adapter. Those movement names do not
exist in the current protocol and were **not** invented here.
