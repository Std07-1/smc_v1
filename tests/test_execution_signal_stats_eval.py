"""Тести для QA статистики Stage5 execution.

Ціль: зафіксувати логіку першого торкання TP/SL (послідовний скан барів).
"""

from __future__ import annotations

import pandas as pd

from tools.qa_execution_signal_stats import _eval_forward_path


def _df(*rows: tuple[float, float]) -> pd.DataFrame:
    # rows: (high, low)
    return pd.DataFrame({"high": [r[0] for r in rows], "low": [r[1] for r in rows]})


def test_eval_long_tp_hits_first() -> None:
    fwd = _df(
        (101.1, 99.9),
        (102.1, 100.5),
    )
    res = _eval_forward_path(
        direction="LONG", entry=100.0, atr=1.0, frame_fwd=fwd, tp_atr=2.0, sl_atr=1.0
    )
    assert res.outcome == "WIN"
    assert res.bars_to_outcome == 2


def test_eval_long_sl_hits_first() -> None:
    fwd = _df(
        (100.8, 98.9),
        (103.0, 100.0),
    )
    res = _eval_forward_path(
        direction="LONG", entry=100.0, atr=1.0, frame_fwd=fwd, tp_atr=2.0, sl_atr=1.0
    )
    assert res.outcome == "LOSS"
    assert res.bars_to_outcome == 1


def test_eval_long_both_same_bar() -> None:
    fwd = _df(
        (102.1, 98.9),
    )
    res = _eval_forward_path(
        direction="LONG", entry=100.0, atr=1.0, frame_fwd=fwd, tp_atr=2.0, sl_atr=1.0
    )
    assert res.outcome == "BOTH_SAME_BAR"
    assert res.bars_to_outcome == 1


def test_eval_short_tp_hits_first() -> None:
    # SHORT TP нижче entry.
    fwd = _df(
        (100.5, 99.5),
        (100.2, 97.9),
    )
    res = _eval_forward_path(
        direction="SHORT", entry=100.0, atr=1.0, frame_fwd=fwd, tp_atr=2.0, sl_atr=1.0
    )
    assert res.outcome == "WIN"
    assert res.bars_to_outcome == 2


def test_eval_short_sl_hits_first() -> None:
    fwd = _df(
        (101.2, 99.8),
        (99.0, 97.0),
    )
    res = _eval_forward_path(
        direction="SHORT", entry=100.0, atr=1.0, frame_fwd=fwd, tp_atr=2.0, sl_atr=1.0
    )
    assert res.outcome == "LOSS"
    assert res.bars_to_outcome == 1


def test_eval_mfe_mae_never_negative() -> None:
    # Неконсистентний кейс: low вище за entry для LONG.
    # Важливо: метрики MFE/MAE не мають бути від’ємними.
    fwd = _df(
        (101.0, 100.5),
        (101.0, 100.5),
    )
    res = _eval_forward_path(
        direction="LONG", entry=100.0, atr=1.0, frame_fwd=fwd, tp_atr=2.0, sl_atr=1.0
    )
    assert res.mfe_atr is not None
    assert res.mae_atr is not None
    assert res.mfe_atr >= 0.0
    assert res.mae_atr >= 0.0
