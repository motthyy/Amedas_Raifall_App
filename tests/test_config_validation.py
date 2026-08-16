from __future__ import annotations

import copy

import pytest

from amedas_rainfall.config import AppConfig, get_default_config


def test_default_configuration_is_valid():
    get_default_config().validate()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("rainfall", "dry_hours_reset"), 0),
        (("rainfall", "ten_minute_disaggregation"), "unknown"),
        (("download", "mode"), "unknown"),
        (("annual_maxima", "completeness_threshold_percent"), 101),
        (("gumbel", "default_estimation_method"), "unknown"),
    ],
)
def test_invalid_user_configuration_fails_fast(path, value):
    raw = copy.deepcopy(get_default_config().raw)
    node = raw
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value

    with pytest.raises(ValueError):
        AppConfig(raw).validate()
