"""Тести для scheduler v0 (capacity guard) у SMC.

Перевіряємо:
- slice-логіку вибору активів за цикл;
- мета-поля processed/skipped.
"""

from __future__ import annotations

from app.smc_producer import _build_capacity_meta, _select_symbols_for_cycle


def test_scheduler_selects_first_n_symbols() -> None:
    ready = ["xauusd", "eurusd", "gbpusd"]
    selected, skipped = _select_symbols_for_cycle(ready_symbols=ready, max_per_cycle=2)

    assert selected == ["xauusd", "eurusd"]
    assert skipped == ["gbpusd"]

    meta = _build_capacity_meta(ready_assets=len(ready), processed_assets=len(selected))
    assert meta["pipeline_processed_assets"] == 2
    assert meta["pipeline_skipped_assets"] == 1


def test_scheduler_legacy_mode_processes_all_when_max_nonpositive() -> None:
    ready = ["xauusd", "eurusd"]
    selected, skipped = _select_symbols_for_cycle(ready_symbols=ready, max_per_cycle=0)

    assert selected == ready
    assert skipped == []

    meta = _build_capacity_meta(ready_assets=len(ready), processed_assets=len(selected))
    assert meta["pipeline_processed_assets"] == 2
    assert meta["pipeline_skipped_assets"] == 0
