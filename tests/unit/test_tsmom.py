"""SETUP-TSMOM-ENSEMBLE-01 — Allokationsregel statt Einstiegs-Setup."""

from __future__ import annotations

import pytest

from trading_agent.strategy.setups.tsmom import (
    SETUP_TSMOM_ENSEMBLE,
    TsmomParams,
    TsmomState,
    disaster_stop,
    evaluate_tsmom,
)

P = TsmomParams()


def _series(
    n: int, *, drift: float = 0.0, start: float = 100.0, wobble: float = 0.0
) -> list[float]:
    out = [start]
    for i in range(1, n):
        out.append(out[-1] * (1 + drift + (wobble if i % 2 else -wobble)))
    return out


def _turning_series() -> list[float]:
    """320 Bars Aufwaertstrend, dann 40 Bars milde Korrektur."""
    s = _series(320, drift=0.004, wobble=0.002)
    tail = s[-1]
    for i in range(1, 41):
        s.append(tail * (1 - 0.004) ** i)
    return s


def _regime_series() -> list[float]:
    """Zwei Regime hintereinander — damit sich die Fenster-Renditen ueber die Zeit aendern."""
    a = _series(300, drift=0.0015, wobble=0.006)
    b = _series(150, drift=-0.003, wobble=0.006, start=a[-1])
    return a + b[1:]


class TestGuards:
    def test_short_series_is_flagged_not_guessed(self) -> None:
        r = evaluate_tsmom(_series(50))
        assert r.state is TsmomState.INSUFFICIENT_DATA
        assert r.target_weight == 0.0
        assert "zu wenige Bars" in r.reasons[0]

    def test_warmup_matches_longest_lookback(self) -> None:
        assert P.warmup_bars() == max(max(P.lookbacks), P.vol_window) + 1
        assert evaluate_tsmom(_series(P.warmup_bars() - 1)).state is TsmomState.INSUFFICIENT_DATA
        assert evaluate_tsmom(_series(P.warmup_bars(), drift=0.001, wobble=0.004)).state is not (
            TsmomState.INSUFFICIENT_DATA
        )

    def test_flat_line_has_no_volatility_and_no_weight(self) -> None:
        r = evaluate_tsmom([100.0] * 400)
        assert r.state is TsmomState.FLAT
        assert r.target_weight == 0.0
        assert "Volatilitaet nicht bestimmbar" in " ".join(r.reasons)


class TestSignal:
    def test_uptrend_is_long_on_every_window(self) -> None:
        r = evaluate_tsmom(_series(400, drift=0.002, wobble=0.004))
        assert r.state is TsmomState.FULL
        assert r.agreement == 1.0
        assert r.is_long and r.target_weight > 0
        assert all(r.per_lookback.values())

    def test_downtrend_is_flat_not_short(self) -> None:
        """Long-only: der Kryptomarkt hat keinen belastbaren Short-Edge (Han et al.)."""
        r = evaluate_tsmom(_series(400, drift=-0.002, wobble=0.004))
        assert r.state is TsmomState.FLAT
        assert r.target_weight == 0.0
        assert not r.is_long

    def test_recent_turn_shows_disagreement(self) -> None:
        """Lange Aufwaerts-, kurze Abwaertsphase: kurze Fenster negativ, lange positiv.

        Genau der Fall, den ein Ensemble sichtbar machen soll, statt ihn wegzumitteln.
        """
        r = evaluate_tsmom(_turning_series())
        assert r.borderline, r.per_lookback
        assert r.per_lookback[28] is False, "kurzes Fenster muss die Wende sehen"
        assert r.per_lookback[180] is True, "langes Fenster traegt den Aufwaertstrend noch"
        assert r.target_weight > 0

    def test_weight_scales_with_agreement(self) -> None:
        up = evaluate_tsmom(_series(400, drift=0.002, wobble=0.004))
        mixed = evaluate_tsmom(_turning_series())
        assert mixed.agreement < up.agreement
        assert mixed.target_weight < up.vol_scalar


class TestVolatilityTargeting:
    def test_calmer_market_gets_a_bigger_weight(self) -> None:
        calm = evaluate_tsmom(_series(400, drift=0.002, wobble=0.002))
        wild = evaluate_tsmom(_series(400, drift=0.002, wobble=0.030))
        assert calm.realized_vol < wild.realized_vol
        assert calm.target_weight > wild.target_weight

    def test_weight_never_exceeds_the_cap(self) -> None:
        r = evaluate_tsmom(_series(400, drift=0.002, wobble=0.0005))
        assert r.target_weight <= P.max_weight
        assert r.vol_scalar <= P.max_weight

    def test_vol_scalar_matches_the_formula(self) -> None:
        r = evaluate_tsmom(_series(400, drift=0.001, wobble=0.010))
        assert r.vol_scalar == pytest.approx(
            min(P.max_weight, P.target_vol / r.realized_vol), rel=1e-6
        )


class TestPointInTime:
    def test_report_depends_only_on_the_bars_it_was_given(self) -> None:
        """Kein Lookahead: dieselbe Historie -> dieselbe Bewertung, egal was danach kommt."""
        s = _regime_series()
        today = evaluate_tsmom(s[:300])
        # Dieselbe Historie, aber die Zukunft existiert bereits im Speicher des Aufrufers.
        assert evaluate_tsmom(s[:300]) == today
        assert evaluate_tsmom(list(s[:300])) == today

    def test_later_bars_do_change_the_report(self) -> None:
        """Gegenprobe: die Bewertung ist nicht einfach konstant."""
        s = _regime_series()
        assert evaluate_tsmom(s).lookback_returns != evaluate_tsmom(s[:300]).lookback_returns


class TestExplainability:
    def test_every_report_states_its_reasons(self) -> None:
        for series in (
            _series(400, drift=0.002, wobble=0.004),
            _series(400, drift=-0.002, wobble=0.004),
            [100.0] * 400,
        ):
            assert evaluate_tsmom(series).reasons

    def test_report_carries_setup_identity(self) -> None:
        r = evaluate_tsmom(_series(400, drift=0.002, wobble=0.004))
        assert r.setup_id == SETUP_TSMOM_ENSEMBLE
        assert r.strategy_version

    def test_each_lookback_is_reported_individually(self) -> None:
        r = evaluate_tsmom(_series(400, drift=0.002, wobble=0.004))
        assert set(r.per_lookback) == set(P.lookbacks)
        assert set(r.lookback_returns) == set(P.lookbacks)


class TestDisasterStop:
    def test_stop_is_far_away_by_design(self) -> None:
        """Ein enger Stop wuerde daraus eine Breakout-Strategie machen — die ist widerlegt."""
        stop = disaster_stop(100.0, 2.0)
        assert stop == pytest.approx(100.0 - 3.0 * 2.0)
        assert (100.0 - stop) / 100.0 > 0.05

    def test_invalid_inputs_return_none_not_a_bad_stop(self) -> None:
        assert disaster_stop(0.0, 2.0) is None
        assert disaster_stop(100.0, 0.0) is None
        assert disaster_stop(1.0, 10.0) is None  # Stop waere negativ


def test_parameters_are_frozen_against_accidental_tuning() -> None:
    with pytest.raises(Exception):
        P.target_vol = 0.5  # type: ignore[misc]


def test_default_lookbacks_are_the_registered_ensemble() -> None:
    """Diese fuenf Fenster stehen im Hypothesen-Register. Aenderung = neue Hypothese."""
    assert P.lookbacks == (28, 56, 90, 120, 180)
