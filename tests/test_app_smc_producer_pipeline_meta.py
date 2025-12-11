"""Тести для допоміжної логіки pipeline_meta в smc_producer."""

from __future__ import annotations

import pytest

from app.smc_producer import _build_pipeline_meta


def test_pipeline_meta_cold_when_zero_ready() -> None:
    meta = _build_pipeline_meta(assets_total=4, ready_assets=0, min_ready=2)

    assert meta["pipeline_state"] == "COLD"
    assert meta["pipeline_ready_assets"] == 0
    assert meta["pipeline_assets_total"] == 4
    assert meta["pipeline_ready_pct"] == 0.0


def test_pipeline_meta_warmup_until_min_ready() -> None:
    meta = _build_pipeline_meta(assets_total=5, ready_assets=2, min_ready=4)

    assert meta["pipeline_state"] == "WARMUP"
    assert meta["pipeline_ready_assets"] == 2
    assert meta["pipeline_min_ready"] == 4
    assert meta["pipeline_assets_total"] == 5
    assert meta["pipeline_ready_pct"] == pytest.approx(0.4)


def test_pipeline_meta_live_after_threshold() -> None:
    meta = _build_pipeline_meta(assets_total=3, ready_assets=3, min_ready=2)

    assert meta["pipeline_state"] == "LIVE"
    assert meta["pipeline_ready_assets"] == 3
    assert meta["pipeline_min_ready"] == 2
    assert meta["pipeline_ready_pct"] == pytest.approx(1.0)
