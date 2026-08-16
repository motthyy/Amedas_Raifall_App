"""ガンベル分布の確率雨量に対するブートストラップ信頼区間（11.4節）。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from amedas_rainfall.statistics.gumbel import (
    fit_gumbel_mle,
    fit_gumbel_moments,
    return_period_value,
)

SHORT_RECORD_WARNING_YEARS = 10
UNCERTAIN_RECORD_WARNING_YEARS = 20
EXTRAPOLATION_WARNING_FACTOR = 3.0


@dataclass
class BootstrapResult:
    return_period_years: float
    lower: float
    upper: float
    median: float
    n_iterations: int
    confidence_level: float


def bootstrap_return_period_ci(
    annual_maxima: np.ndarray,
    return_periods_years: list[float],
    method: str = "mle",
    n_iterations: int = 1000,
    confidence_level: float = 0.95,
    random_seed: int | None = 42,
) -> dict[float, BootstrapResult]:
    """年最大値のリサンプリングにより確率雨量の信頼区間を推定する。

    Args:
        annual_maxima: 年最大値の配列（NaNは除外される）。
        return_periods_years: 信頼区間を求める確率年のリスト。
        method: "mle" または "moments"。
        n_iterations: ブートストラップ反復回数。
        confidence_level: 信頼水準（例: 0.95）。
        random_seed: 乱数シード（再現性確保のため指定推奨）。
    """
    if method not in ("mle", "moments"):
        raise ValueError(f"未知の推定法: {method}")
    if n_iterations <= 0:
        raise ValueError("n_iterationsは1以上で指定してください。")
    if not 0 < confidence_level < 1:
        raise ValueError("confidence_levelは0より大きく1未満で指定してください。")
    data = np.asarray(annual_maxima, dtype=float)
    data = data[~np.isnan(data)]
    if not np.isfinite(data).all():
        raise ValueError("年最大値に無限値が含まれています。")
    if len(data) < 2:
        raise ValueError("ブートストラップには少なくとも2年分の年最大値が必要です。")
    if not return_periods_years or any(
        not np.isfinite(value) or value < 1 for value in return_periods_years
    ):
        raise ValueError("確率年は1以上の有限値を1件以上指定してください。")
    n = len(data)
    fit_fn = fit_gumbel_mle if method == "mle" else fit_gumbel_moments

    rng = np.random.default_rng(random_seed)
    samples: dict[float, list[float]] = {t: [] for t in return_periods_years}

    for _ in range(n_iterations):
        resample = rng.choice(data, size=n, replace=True)
        try:
            params = fit_fn(resample)
        except (ValueError, OverflowError, RuntimeError, FloatingPointError, ZeroDivisionError):
            # リサンプル結果が退化した標本（分散ゼロ等）になった場合、
            # 数値的にガンベル分布を推定できないことがある。当該反復はスキップする。
            continue
        for t in return_periods_years:
            samples[t].append(return_period_value(params.loc_mu, params.scale_beta, t))

    alpha = 1.0 - confidence_level
    lower_pct = 100 * (alpha / 2)
    upper_pct = 100 * (1 - alpha / 2)

    results: dict[float, BootstrapResult] = {}
    for t, values in samples.items():
        arr = np.array([v for v in values if not np.isnan(v)])
        if len(arr) == 0:
            results[t] = BootstrapResult(t, float("nan"), float("nan"), float("nan"), n_iterations, confidence_level)
            continue
        results[t] = BootstrapResult(
            return_period_years=t,
            lower=float(np.percentile(arr, lower_pct)),
            upper=float(np.percentile(arr, upper_pct)),
            median=float(np.median(arr)),
            n_iterations=n_iterations,
            confidence_level=confidence_level,
        )
    return results


def sample_size_warnings(
    n_years: int,
    return_period_years: float,
    *,
    short_record_years: int = SHORT_RECORD_WARNING_YEARS,
    uncertain_record_years: int = UNCERTAIN_RECORD_WARNING_YEARS,
    extrapolation_factor: float = EXTRAPOLATION_WARNING_FACTOR,
) -> list[str]:
    """標本数・外挿に関する警告メッセージのリストを返す。"""
    warnings: list[str] = []
    if n_years < short_record_years:
        warnings.append(f"観測年数が{short_record_years}年未満のため解析に注意が必要です。")
    elif n_years < uncertain_record_years:
        warnings.append(f"観測年数が{uncertain_record_years}年未満のため不確実性が大きくなります。")
    if n_years > 0 and return_period_years > n_years * extrapolation_factor:
        warnings.append(
            f"確率年が観測年数の{extrapolation_factor:g}倍を超えており、大幅な外挿となります。"
        )
    return warnings
