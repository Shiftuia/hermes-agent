"""Regression for #58226 / t_2d888fe1: Anthropic OAuth utilization is already a
0-100 percent, not a fraction. A per-value ``<= 1`` heuristic can't distinguish a
genuine 1% reading from a 1.0 fraction, so it must not exist at all.
"""
from agent.account_usage import _usage_windows


def test_low_utilization_is_not_rescaled_to_100_percent():
    payload = {
        "five_hour": {"utilization": 1.0, "resets_at": None},
        "seven_day": {"utilization": 1.0, "resets_at": None},
    }
    windows = _usage_windows(
        payload, (("five_hour", "Current session"), ("seven_day", "Current week")), "utilization", "resets_at",
    )
    assert {w.label: w.used_percent for w in windows} == {"Current session": 1.0, "Current week": 1.0}


def test_high_utilization_passes_through_verbatim():
    payload = {"seven_day": {"utilization": 94.0, "resets_at": None}}
    windows = _usage_windows(payload, (("seven_day", "Current week"),), "utilization", "resets_at")
    assert windows[0].used_percent == 94.0
