"""Hypothesen-Register und Multiple-Testing-Korrektur — Befund F1."""

from __future__ import annotations

import math

import pytest

from trading_agent.research.hypotheses import (
    Hypothesis,
    HypothesisRegistry,
    bonferroni_threshold,
    deflated_sharpe,
    expected_max_sharpe,
    norm_cdf,
    norm_ppf,
)


def _h(i: int, sharpe: float = 0.1, n_configs: int = 1) -> Hypothesis:
    return Hypothesis(
        id=f"run:{i}",
        setup=f"S{i}",
        run="run",
        date="2026-09-04",
        n_configs=n_configs,
        result={"sharpe_r": sharpe},
    )


class TestNormal:
    @pytest.mark.parametrize("p", [0.001, 0.025, 0.1, 0.5, 0.9, 0.975, 0.999])
    def test_ppf_inverts_cdf(self, p: float) -> None:
        assert norm_cdf(norm_ppf(p)) == pytest.approx(p, abs=1e-6)

    def test_known_quantiles(self) -> None:
        assert norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-4)
        assert norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)

    def test_ppf_rejects_out_of_range(self) -> None:
        for bad in (0.0, 1.0, -0.1, 1.5):
            with pytest.raises(ValueError):
                norm_ppf(bad)


class TestExpectedMaxSharpe:
    def test_grows_with_number_of_trials(self) -> None:
        """Der Kern der Korrektur: mehr Versuche -> die beste sieht besser aus, ohne Edge."""
        few = expected_max_sharpe(10, 0.04)
        many = expected_max_sharpe(1000, 0.04)
        assert 0 < few < many

    def test_zero_without_spread_or_trials(self) -> None:
        assert expected_max_sharpe(800, 0.0) == 0.0
        assert expected_max_sharpe(1, 0.04) == 0.0


class TestDeflatedSharpe:
    def test_single_trial_beats_many_trials(self) -> None:
        common = dict(sharpe=0.30, n_obs=100, var_sharpe_across_trials=0.04)
        assert deflated_sharpe(n_trials=1, **common) > deflated_sharpe(n_trials=800, **common)

    def test_more_observations_raise_confidence_above_the_noise_bar(self) -> None:
        """Oberhalb des Zufallsmaximums staerkt jede weitere Beobachtung den Befund."""
        common = dict(sharpe=0.60, n_trials=10, var_sharpe_across_trials=0.04)
        assert deflated_sharpe(n_obs=500, **common) > deflated_sharpe(n_obs=30, **common)

    def test_more_observations_lower_confidence_below_the_noise_bar(self) -> None:
        """Unterhalb davon ist mehr Evidenz Evidenz GEGEN den Befund — das ist korrekt."""
        common = dict(sharpe=0.10, n_trials=800, var_sharpe_across_trials=0.04)
        assert deflated_sharpe(n_obs=500, **common) < deflated_sharpe(n_obs=30, **common)

    def test_probability_stays_in_range(self) -> None:
        for n_trials in (1, 10, 798):
            v = deflated_sharpe(
                sharpe=0.2, n_obs=60, n_trials=n_trials, var_sharpe_across_trials=0.04
            )
            assert 0.0 <= v <= 1.0

    def test_too_few_observations_is_zero(self) -> None:
        assert (
            deflated_sharpe(sharpe=1.0, n_obs=2, n_trials=1, var_sharpe_across_trials=0.01) == 0.0
        )

    def test_fat_tails_lower_confidence(self) -> None:
        """Dicke Raender machen den Schaetzer unsicherer — geprueft oberhalb des Zufallsmaximums."""
        common = dict(sharpe=0.60, n_obs=100, n_trials=10, var_sharpe_across_trials=0.04)
        assert deflated_sharpe(kurtosis=12.0, **common) < deflated_sharpe(kurtosis=3.0, **common)

    def test_negative_skew_lowers_confidence(self) -> None:
        common = dict(sharpe=0.60, n_obs=100, n_trials=10, var_sharpe_across_trials=0.04)
        assert deflated_sharpe(skew=-1.0, **common) < deflated_sharpe(skew=1.0, **common)


class TestRegistry:
    def test_counts_every_configuration_not_every_entry(self) -> None:
        """20 Setups x 4 RR sind 80 Versuche, nicht 20."""
        reg = HypothesisRegistry([_h(i, n_configs=4) for i in range(20)])
        assert len(reg.entries) == 20
        assert reg.n_trials == 80

    def test_add_is_idempotent(self) -> None:
        reg = HypothesisRegistry()
        assert reg.add(_h(1)) is True
        assert reg.add(_h(1)) is False
        assert len(reg.entries) == 1

    def test_bonferroni_tightens_with_more_trials(self) -> None:
        small = HypothesisRegistry([_h(i) for i in range(10)])
        large = HypothesisRegistry([_h(i) for i in range(800)])
        assert small.bonferroni() > large.bonferroni()
        assert large.bonferroni() == pytest.approx(0.05 / 800)

    def test_var_sharpe_needs_two_values(self) -> None:
        assert HypothesisRegistry([_h(1, 0.2)]).var_sharpe() == 0.0
        reg = HypothesisRegistry([_h(1, 0.0), _h(2, 0.2), _h(3, 0.4)])
        assert reg.var_sharpe() > 0

    def test_roundtrip_through_disk(self, tmp_path) -> None:
        path = tmp_path / "reg.json"
        reg = HypothesisRegistry([_h(1, n_configs=4), _h(2)], note="hallo")
        reg.save(path)
        back = HypothesisRegistry.load(path)
        assert back.n_trials == 5
        assert back.note == "hallo"
        assert [e.id for e in back.entries] == ["run:1", "run:2"]

    def test_missing_file_loads_empty(self, tmp_path) -> None:
        reg = HypothesisRegistry.load(tmp_path / "nope.json")
        assert reg.entries == []
        assert reg.n_trials == 0

    def test_bonferroni_threshold_helper(self) -> None:
        assert bonferroni_threshold(798) == pytest.approx(0.05 / 798)
        assert bonferroni_threshold(0) == 0.05


def test_shipped_registry_knows_the_real_trial_count() -> None:
    """Das Register muss die echten Versuche kennen, sonst ist jede Korrektur zu milde."""
    reg = HypothesisRegistry.load("config/hypothesis_registry.json")
    assert reg.n_trials > 500, reg.summary()
    assert reg.bonferroni() < 1e-4
    assert math.isfinite(reg.var_sharpe())
