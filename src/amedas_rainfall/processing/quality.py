"""品質情報に基づく重複時刻の解決ロジック（6.1節）。"""

from __future__ import annotations

from dataclasses import dataclass

QUALITY_TIER = {
    "8": 2,  # 正常
    "5": 1,  # 準正常（資料充足）
    "4": 1,  # 準正常（資料不足）
    "2": 1,  # 準正常（値が疑わしい）
    "1": 0,  # 欠測
    "0": 0,  # 非対象（観測なし）
    None: -1,
}


@dataclass
class CandidateRecord:
    rainfall_raw_mm: float | None
    quality_code: str | None
    homogeneity_number: int | None
    source_file: str


@dataclass
class ResolvedRecord:
    rainfall_raw_mm: float | None
    quality_code: str | None
    homogeneity_number: int | None
    source_file: str
    is_conflicting: bool
    conflict_candidates: list[CandidateRecord]


def _tier(quality_code: str | None) -> int:
    return QUALITY_TIER.get(quality_code, -1)


def resolve_duplicates(candidates: list[CandidateRecord]) -> ResolvedRecord:
    """同一時刻に複数の観測値候補がある場合、品質情報に基づき1件へ解決する。

    優先順位:
        1. 正常品質値
        2. 準正常値
        3. 欠測値
    品質が同一で値が異なる場合は競合として記録し、黙って上書きしない
    （先勝ちで代表値を選ぶが、is_conflicting=Trueを立てて呼び出し側に通知する）。
    """
    if not candidates:
        raise ValueError("候補が空です。")
    if len(candidates) == 1:
        c = candidates[0]
        return ResolvedRecord(
            rainfall_raw_mm=c.rainfall_raw_mm,
            quality_code=c.quality_code,
            homogeneity_number=c.homogeneity_number,
            source_file=c.source_file,
            is_conflicting=False,
            conflict_candidates=[],
        )

    best_tier = max(_tier(c.quality_code) for c in candidates)
    top_candidates = [c for c in candidates if _tier(c.quality_code) == best_tier]

    distinct_values = {c.rainfall_raw_mm for c in top_candidates}
    is_conflicting = len(distinct_values) > 1

    # 同一品質で「値あり」と「値なし」が混在する異常データでは、値ありを優先する。
    # それでも値が異なる場合は従来どおり競合として残し、決定順を安定させる。
    chosen = next((c for c in top_candidates if c.rainfall_raw_mm is not None), top_candidates[0])
    return ResolvedRecord(
        rainfall_raw_mm=chosen.rainfall_raw_mm,
        quality_code=chosen.quality_code,
        homogeneity_number=chosen.homogeneity_number,
        source_file=chosen.source_file,
        is_conflicting=is_conflicting,
        conflict_candidates=top_candidates if is_conflicting else [],
    )
