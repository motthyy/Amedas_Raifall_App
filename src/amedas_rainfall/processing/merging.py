"""複数期間のダウンロード結果を1つの連続時系列へ統合する処理。"""

from __future__ import annotations

import json

import pandas as pd

from amedas_rainfall.processing.quality import CandidateRecord, resolve_duplicates


def merge_hourly_frames(frames: list[pd.DataFrame], source_files: list[str]) -> pd.DataFrame:
    """複数のパース済みDataFrameを、品質情報に基づき1本の時系列へ統合する。

    Args:
        frames: 各要素は ``rainfall_raw_mm`` / ``quality_code`` / ``homogeneity_number``
            列を持つDataFrame（インデックスはJSTの ``datetime_jst``）。
        source_files: framesと対応する元ファイル名のリスト。

    Returns:
        統合後のDataFrame。列:
            rainfall_raw_mm, quality_code, homogeneity_number, source_file,
            is_missing, is_conflicting
    """
    if len(frames) != len(source_files):
        raise ValueError("framesとsource_filesの長さが一致していません。")
    if not frames:
        return pd.DataFrame(
            columns=[
                "rainfall_raw_mm",
                "quality_code",
                "homogeneity_number",
                "source_file",
                "is_missing",
                "is_conflicting",
                "conflict_values",
                "conflict_sources",
            ]
        )

    grouped: dict[pd.Timestamp, list[CandidateRecord]] = {}
    for frame, source in zip(frames, source_files, strict=True):
        for ts, row in frame.iterrows():
            grouped.setdefault(ts, []).append(
                CandidateRecord(
                    rainfall_raw_mm=None if pd.isna(row["rainfall_raw_mm"]) else float(row["rainfall_raw_mm"]),
                    quality_code=row.get("quality_code"),
                    homogeneity_number=(
                        None if pd.isna(row.get("homogeneity_number")) else int(row.get("homogeneity_number"))
                    ),
                    source_file=source,
                )
            )

    records = []
    for ts in sorted(grouped.keys()):
        resolved = resolve_duplicates(grouped[ts])
        records.append(
            {
                "datetime_jst": ts,
                "rainfall_raw_mm": resolved.rainfall_raw_mm,
                "quality_code": resolved.quality_code,
                "homogeneity_number": resolved.homogeneity_number,
                "source_file": resolved.source_file,
                "is_missing": resolved.quality_code in (None, "1", "0") or resolved.rainfall_raw_mm is None,
                "is_conflicting": resolved.is_conflicting,
                "conflict_values": (
                    json.dumps(
                        [candidate.rainfall_raw_mm for candidate in resolved.conflict_candidates],
                        ensure_ascii=False,
                    )
                    if resolved.is_conflicting
                    else None
                ),
                "conflict_sources": (
                    json.dumps(
                        [candidate.source_file for candidate in resolved.conflict_candidates],
                        ensure_ascii=False,
                    )
                    if resolved.is_conflicting
                    else None
                ),
            }
        )

    result = pd.DataFrame.from_records(records).set_index("datetime_jst").sort_index()
    return result
