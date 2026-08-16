from __future__ import annotations

import numpy as np
import pandas as pd

from amedas_rainfall.visualization.timeseries import _downsample_for_display


def test_downsampling_keeps_real_jst_timestamps_and_independent_peaks():
    index = pd.date_range("2024-01-01", periods=240, freq="h", tz="Asia/Tokyo")
    frame = pd.DataFrame(
        {
            "rainfall_raw_mm": np.zeros(len(index)),
            "effective_rainfall_6h_mm": np.zeros(len(index)),
        },
        index=index,
    )
    frame.loc[index[55], "rainfall_raw_mm"] = 100.0
    frame.loc[index[58], "effective_rainfall_6h_mm"] = 200.0
    missing = pd.Series(False, index=index)
    missing.loc[index[57]] = True

    sampled, sampled_missing = _downsample_for_display(
        frame,
        "rainfall_raw_mm",
        ["effective_rainfall_6h_mm"],
        missing,
        max_points=40,
    )

    assert sampled.index.tz is not None
    assert set(sampled.index).issubset(set(index))
    assert index[55] in sampled.index
    assert index[58] in sampled.index
    assert sampled.loc[index[55], "effective_rainfall_6h_mm"] == 0.0
    assert sampled.loc[index[58], "rainfall_raw_mm"] == 0.0
    assert sampled_missing.loc[index[57]]
