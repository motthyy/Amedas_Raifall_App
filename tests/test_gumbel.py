"""ガンベル分布推定・確率雨量のテスト（仕様17.7節）。"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from amedas_rainfall.statistics.bootstrap import bootstrap_return_period_ci
from amedas_rainfall.statistics.gumbel import (
    STANDARD_RETURN_PERIODS,
    analyze_gumbel,
    fit_gumbel_mle,
    fit_gumbel_moments,
    return_period_from_value,
    return_period_value,
)

SAMPLE_DATA = np.array(
    [120.5, 98.2, 145.0, 110.3, 88.7, 200.1, 132.4, 99.9, 155.6, 121.0, 175.3, 105.8]
)


def test_mle_matches_scipy_directly() -> None:
    expected_loc, expected_scale = stats.gumbel_r.fit(SAMPLE_DATA)
    result = fit_gumbel_mle(SAMPLE_DATA)
    assert result.loc_mu == pytest.approx(expected_loc)
    assert result.scale_beta == pytest.approx(expected_scale)


def test_moments_formula_matches_manual_calculation() -> None:
    """Excel（r_max_c(manual ver.).xlsm）のrp_inシートの数式と同一の計算式であることを確認する。

    beta = SQRT(6)/PI() * STDEV.P(...)、mu = mean - 0.5772 * beta。
    """
    mean = SAMPLE_DATA.mean()
    std = SAMPLE_DATA.std(ddof=0)
    expected_beta = math.sqrt(6) * std / math.pi
    expected_mu = mean - 0.5772 * expected_beta
    result = fit_gumbel_moments(SAMPLE_DATA)
    assert result.scale_beta == pytest.approx(expected_beta)
    assert result.loc_mu == pytest.approx(expected_mu)


def test_return_period_1_year_is_nan() -> None:
    value = return_period_value(mu=100.0, beta=20.0, return_period_years=1.0)
    assert math.isnan(value)


def test_standard_return_periods_include_required_values() -> None:
    required = set(range(2, 31)) | {50, 100}
    assert required.issubset(set(STANDARD_RETURN_PERIODS))
    assert max(STANDARD_RETURN_PERIODS) == 100
    assert 1 in STANDARD_RETURN_PERIODS


def test_return_period_value_is_monotonically_increasing() -> None:
    mu, beta = 100.0, 20.0
    values = [return_period_value(mu, beta, t) for t in [2, 5, 10, 20, 50, 100, 200, 500]]
    assert all(b > a for a, b in zip(values, values[1:], strict=False))


def test_return_period_below_one_is_rejected() -> None:
    with pytest.raises(ValueError):
        return_period_value(100.0, 20.0, 0.5)


def test_return_period_from_value_round_trips_with_return_period_value() -> None:
    mu, beta = 100.0, 20.0
    for t in [2, 5, 10, 20, 50, 100, 200]:
        x = return_period_value(mu, beta, t)
        recovered_t = return_period_from_value(mu, beta, x)
        assert recovered_t == pytest.approx(t, rel=1e-6)


def test_return_period_from_value_is_monotonically_increasing() -> None:
    mu, beta = 100.0, 20.0
    values = [return_period_from_value(mu, beta, x) for x in [50, 80, 100, 150, 200, 300]]
    assert all(b > a for a, b in zip(values, values[1:], strict=False))


def test_return_period_from_value_approaches_one_for_small_x() -> None:
    mu, beta = 100.0, 20.0
    t = return_period_from_value(mu, beta, -1000.0)
    assert t == pytest.approx(1.0, abs=1e-6)


def test_analyze_gumbel_returns_all_standard_periods() -> None:
    result = analyze_gumbel(SAMPLE_DATA, method="mle")
    assert result.return_periods == STANDARD_RETURN_PERIODS
    one_year_index = result.return_periods.index(1)
    assert math.isnan(result.estimates_mm[one_year_index])
    assert result.goodness_of_fit.aic is not None


def test_bootstrap_handles_degenerate_resamples_without_crashing() -> None:
    """小標本かつ値の桁差が大きい場合、リサンプルが退化しSciPyがOverflow/RuntimeErrorを
    送出することがある。実データ（4年分の年最大値、うち1件が極端に小さい）で再現した
    ケースに基づく回帰テスト。"""
    tiny_sample = np.array([0.595275, 40.225447, 49.052115, 72.542223])
    result = bootstrap_return_period_ci(tiny_sample, [100], n_iterations=500, random_seed=42)
    assert 100 in result


def test_bootstrap_reproducible_with_same_seed() -> None:
    result1 = bootstrap_return_period_ci(
        SAMPLE_DATA, [10, 100], n_iterations=200, random_seed=123
    )
    result2 = bootstrap_return_period_ci(
        SAMPLE_DATA, [10, 100], n_iterations=200, random_seed=123
    )
    assert result1[10].lower == pytest.approx(result2[10].lower)
    assert result1[10].upper == pytest.approx(result2[10].upper)
    assert result1[100].median == pytest.approx(result2[100].median)


def test_bootstrap_different_seed_can_differ() -> None:
    result1 = bootstrap_return_period_ci(
        SAMPLE_DATA, [50], n_iterations=200, random_seed=1
    )
    result2 = bootstrap_return_period_ci(
        SAMPLE_DATA, [50], n_iterations=200, random_seed=2
    )
    # 異なるシードでは通常異なる結果になる（稀な偶然の一致は許容しない規模の反復数）
    assert result1[50].median != result2[50].median
