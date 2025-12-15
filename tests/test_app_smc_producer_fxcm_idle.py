"""Тести idle-логіки SMC по fxcm:status.

Ціль: коли ринок закритий або фід деградований, SMC не має ганяти важкі цикли,
але система має залишатися живою й публікувати статус.
"""

from __future__ import annotations

import time

from app.smc_producer import _should_run_smc_cycle_by_fxcm_status
from data import fxcm_status_listener as status_listener
from data.fxcm_models import parse_fxcm_aggregated_status


def test_smc_cycle_idle_when_market_closed() -> None:
    status_listener._reset_fxcm_feed_state_for_tests()
    status = parse_fxcm_aggregated_status(
        {"ts": time.time(), "market": "closed", "price": "ok", "ohlcv": "ok"}
    )
    status_listener._apply_status_snapshot(status)

    should_run, reason = _should_run_smc_cycle_by_fxcm_status()
    assert should_run is True
    assert reason == "fxcm_market_closed_but_ticks_ok"


def test_smc_cycle_runs_when_market_open_ok() -> None:
    status_listener._reset_fxcm_feed_state_for_tests()
    status = parse_fxcm_aggregated_status(
        {"ts": 1, "market": "open", "price": "ok", "ohlcv": "ok"}
    )
    status_listener._apply_status_snapshot(status)

    should_run, reason = _should_run_smc_cycle_by_fxcm_status()
    assert should_run is True
    assert reason == "fxcm_ok"


def test_smc_cycle_idle_when_price_not_ok() -> None:
    status_listener._reset_fxcm_feed_state_for_tests()
    status = parse_fxcm_aggregated_status(
        {"ts": 1, "market": "open", "price": "stale", "ohlcv": "ok"}
    )
    status_listener._apply_status_snapshot(status)

    should_run, reason = _should_run_smc_cycle_by_fxcm_status()
    assert should_run is False
    assert reason == "fxcm_price_stale"


def test_smc_cycle_runs_when_ohlcv_not_ok_but_price_ok() -> None:
    status_listener._reset_fxcm_feed_state_for_tests()
    status = parse_fxcm_aggregated_status(
        {"ts": 1, "market": "open", "price": "ok", "ohlcv": "lag"}
    )
    status_listener._apply_status_snapshot(status)

    should_run, reason = _should_run_smc_cycle_by_fxcm_status()
    assert should_run is True
    assert reason == "fxcm_ohlcv_lag_ignored"
