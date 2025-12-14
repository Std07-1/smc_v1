"""Тести для локального (per-symbol) pipeline-стану в smc_producer."""

from __future__ import annotations

import pytest

from app.smc_producer import _classify_pipeline_state_local, _local_pipeline_payload


@pytest.mark.parametrize(
    ("bars", "min_ready", "target", "expected"),
    [
        (0, 800, 2000, "COLD"),
        (-5, 800, 2000, "COLD"),
        (799, 800, 2000, "COLD"),
        (800, 800, 2000, "WARMUP"),
        (1999, 800, 2000, "WARMUP"),
        (2000, 800, 2000, "LIVE"),
        (2500, 800, 2000, "LIVE"),
    ],
)
def test_classify_pipeline_state_local(
    bars: int, min_ready: int, target: int, expected: str
) -> None:
    assert (
        _classify_pipeline_state_local(
            bars=bars, min_ready_bars=min_ready, target_bars=target
        )
        == expected
    )


def test_local_pipeline_payload_fields_and_ratio() -> None:
    payload = _local_pipeline_payload(bars=800, min_ready_bars=800, target_bars=2000)
    assert payload["state"] == "WARMUP"
    assert payload["ready_bars"] == 800
    assert payload["required_bars"] == 2000
    assert payload["required_bars_min"] == 800
    assert payload["ready_ratio"] == pytest.approx(0.4)
