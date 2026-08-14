"""ガンベル分布による確率雨量の推定（11節）。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy import stats

EULER_MASCHERONI = 0.5772
"""オイラー・マスケローニ定数の近似値。

Excel（r_max_c(manual ver.).xlsm）のrp_inシートの数式`=C2-0.5772*C4`に合わせ、
数学的な厳密値（0.5772156649015329...）ではなくExcelが用いる4桁の近似値を
そのまま使う（積率法の結果をExcelと完全に一致させるため）。"""

STANDARD_RETURN_PERIODS: list[float] = list(range(1, 31)) + [50, 100]

PLOTTING_POSITION_FORMULAS = {
    "gringorten": (0.44, 0.12),
    "weibull": (0.0, 1.0),
    "cunnane": (0.4, 0.2),
}


@dataclass
class GumbelParameters:
    loc_mu: float
    scale_beta: float
    method: str
    n_samples: int


@dataclass
class GoodnessOfFit:
    aic: float | None
    ks_statistic: float | None
    rmse: float | None
    correlation: float | None
    n_samples: int


@dataclass
class GumbelResult:
    parameters: GumbelParameters
    goodness_of_fit: GoodnessOfFit
    return_periods: list[float]
    estimates_mm: list[float]
    excluded_years: list[str] = field(default_factory=list)


def fit_gumbel_mle(annual_maxima: np.ndarray) -> GumbelParameters:
    """最尤法によるガンベル分布パラメータ推定（scipy.stats.gumbel_rを使用）。"""
    data = np.asarray(annual_maxima, dtype=float)
    data = data[~np.isnan(data)]
    if len(data) < 2:
        raise ValueError("最尤推定には少なくとも2年分の年最大値が必要です。")
    loc, scale = stats.gumbel_r.fit(data)
    return GumbelParameters(loc_mu=float(loc), scale_beta=float(scale), method="mle", n_samples=len(data))


def fit_gumbel_moments(annual_maxima: np.ndarray) -> GumbelParameters:
    """積率法によるガンベル分布パラメータ推定（Excel r_max_c(manual ver.).xlsmのrp_inシートと同一の計算式）。

    beta = sqrt(6) * s / pi   （sは母標準偏差、Excelの STDEV.P に相当。ddof=0）
    mu = mean - 0.5772 * beta
    """
    data = np.asarray(annual_maxima, dtype=float)
    data = data[~np.isnan(data)]
    if len(data) < 2:
        raise ValueError("積率法には少なくとも2年分の年最大値が必要です。")
    mean = float(np.mean(data))
    std = float(np.std(data, ddof=0))
    beta = math.sqrt(6.0) * std / math.pi
    mu = mean - EULER_MASCHERONI * beta
    return GumbelParameters(loc_mu=mu, scale_beta=beta, method="moments", n_samples=len(data))


def gumbel_cdf(x: float | np.ndarray, mu: float, beta: float) -> float | np.ndarray:
    return np.exp(-np.exp(-(np.asarray(x) - mu) / beta))


def return_period_value(mu: float, beta: float, return_period_years: float) -> float:
    """確率年Tに対する確率雨量 x_T を計算する。T=1年は算出不可としてNaNを返す。"""
    if return_period_years <= 1.0:
        return float("nan")
    return mu - beta * math.log(-math.log(1.0 - 1.0 / return_period_years))


def return_period_from_value(mu: float, beta: float, x: float) -> float:
    """雨量xに対応する確率年Tを計算する（ガンベル分布の逆関数）。F=1のときはinfを返す。"""
    f = float(gumbel_cdf(x, mu, beta))
    if f >= 1.0:
        return float("inf")
    return 1.0 / (1.0 - f)


def return_period_values(
    mu: float, beta: float, return_periods_years: list[float] | None = None
) -> dict[float, float]:
    return_periods_years = return_periods_years or STANDARD_RETURN_PERIODS
    return {t: return_period_value(mu, beta, t) for t in return_periods_years}


def plotting_positions(
    n: int, method: str = "gringorten"
) -> np.ndarray:
    """プロッティングポジション公式による非超過確率F_mを、昇順順位m=1..nに対して返す。"""
    if method not in PLOTTING_POSITION_FORMULAS:
        raise ValueError(f"未知のプロッティングポジション法: {method}")
    a, b = PLOTTING_POSITION_FORMULAS[method]
    m = np.arange(1, n + 1, dtype=float)
    return (m - a) / (n + b)


def empirical_return_periods(n: int, method: str = "gringorten") -> np.ndarray:
    f_m = plotting_positions(n, method=method)
    with np.errstate(divide="ignore"):
        t_m = 1.0 / (1.0 - f_m)
    return t_m


def goodness_of_fit(annual_maxima: np.ndarray, params: GumbelParameters, plotting_position: str = "gringorten") -> GoodnessOfFit:
    data = np.asarray(annual_maxima, dtype=float)
    data = np.sort(data[~np.isnan(data)])
    n = len(data)
    if n < 2:
        return GoodnessOfFit(aic=None, ks_statistic=None, rmse=None, correlation=None, n_samples=n)

    loglik = float(np.sum(stats.gumbel_r.logpdf(data, loc=params.loc_mu, scale=params.scale_beta)))
    k = 2
    aic = 2 * k - 2 * loglik

    ks_stat = float(
        stats.kstest(data, "gumbel_r", args=(params.loc_mu, params.scale_beta)).statistic
    )

    t_m = empirical_return_periods(n, method=plotting_position)
    predicted = np.array([return_period_value(params.loc_mu, params.scale_beta, t) for t in t_m])
    valid = ~np.isnan(predicted)
    if valid.sum() >= 2:
        rmse = float(np.sqrt(np.mean((data[valid] - predicted[valid]) ** 2)))
        correlation = float(np.corrcoef(data[valid], predicted[valid])[0, 1])
    else:
        rmse = None
        correlation = None

    return GoodnessOfFit(aic=aic, ks_statistic=ks_stat, rmse=rmse, correlation=correlation, n_samples=n)


def analyze_gumbel(
    annual_maxima: np.ndarray,
    method: str = "mle",
    plotting_position: str = "gringorten",
    return_periods_years: list[float] | None = None,
) -> GumbelResult:
    """年最大値系列からガンベル分布を推定し、確率雨量一式を計算する。"""
    if method == "mle":
        params = fit_gumbel_mle(annual_maxima)
    elif method == "moments":
        params = fit_gumbel_moments(annual_maxima)
    else:
        raise ValueError(f"未知の推定法: {method}")

    gof = goodness_of_fit(annual_maxima, params, plotting_position=plotting_position)
    return_periods_years = return_periods_years or STANDARD_RETURN_PERIODS
    estimates = return_period_values(params.loc_mu, params.scale_beta, return_periods_years)

    return GumbelResult(
        parameters=params,
        goodness_of_fit=gof,
        return_periods=list(estimates.keys()),
        estimates_mm=list(estimates.values()),
    )
