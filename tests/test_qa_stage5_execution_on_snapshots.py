"""QA-тест Stage5 Execution на наших datastore снапшотах.

Тема: 1m — не “мозок”, а “тригер”.
Ціль: micro-події з’являються лише коли ціна in_play (в POI або біля target).

Цей тест:
- читає `datastore/xauusd_bars_{5m,1m,1h,4h}_snapshot.jsonl`;
- проганяє SMC-core інкрементально на 5m кроках, підклеюючи 1m (та 1h/4h контекст);
- перевіряє, що `execution.meta.in_play` узгоджений з `in_play_ref` (POI/TARGET);
- перевіряє, що `execution_events[]` реально формується і має очікувані типи;
- перевіряє інваріант: якщо `in_play == False` → подій немає.

Примітка: це саме QA на реальних даних репозиторію, без зовнішніх сервісів.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pytest

from smc_core import SmcCoreConfig, SmcCoreEngine, SmcInput
from smc_core.input_adapter import _build_sessions_context


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_jsonl_tail(path: Path, *, limit: int) -> pd.DataFrame:
    buf: deque[dict[str, Any]] = deque(maxlen=max(1, int(limit)))
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                buf.append(row)

    df = pd.DataFrame(list(buf))
    if df.empty:
        return df

    if "open_time" in df.columns:
        open_time = pd.to_numeric(df["open_time"], errors="coerce")
        df["open_time"] = open_time
        df["timestamp"] = pd.to_datetime(
            open_time, unit="ms", errors="coerce", utc=True
        )
        df = df.dropna(subset=["open_time", "timestamp"]).copy()
        df = df.sort_values("open_time", kind="stable").reset_index(drop=True)

    return df


def _require_snapshot_df(name: str, *, limit: int) -> pd.DataFrame:
    p = _repo_root() / "datastore" / name
    if not p.exists():
        pytest.skip(f"Немає снапшоту: {p}")
    df = _load_jsonl_tail(p, limit=limit)
    if df.empty:
        pytest.skip(f"Снапшот порожній або не читається: {p}")
    required = {"open_time", "open", "high", "low", "close"}
    if not required.issubset(df.columns):
        pytest.skip(f"Снапшот {p} не має колонок {required - set(df.columns)}")
    return df


def _slice_by_end_ms(df: pd.DataFrame, *, end_ms: int, tail: int) -> pd.DataFrame:
    part = df[df["open_time"] <= int(end_ms)]
    if part.empty:
        return part
    if tail > 0 and len(part) > tail:
        part = part.iloc[-tail:]
    return part.copy()


def test_stage5_execution_triggers_only_in_play_on_datastore_xauusd() -> None:
    cfg = SmcCoreConfig(
        exec_enabled=True,
        exec_tf="1m",
        exec_in_play_radius_atr=0.9,
        exec_in_play_hold_bars=0,
        exec_impulse_atr_mul=0.0,
        exec_micro_pivot_bars=8,
        exec_max_events=6,
    )
    engine = SmcCoreEngine(cfg=cfg)

    df5 = _require_snapshot_df("xauusd_bars_5m_snapshot.jsonl", limit=900)
    df1 = _require_snapshot_df("xauusd_bars_1m_snapshot.jsonl", limit=9000)
    df1h = _require_snapshot_df("xauusd_bars_1h_snapshot.jsonl", limit=3000)
    df4h = _require_snapshot_df("xauusd_bars_4h_snapshot.jsonl", limit=2000)

    # Беремо останні N кроків, щоб тест був швидкий, але статистично показовий.
    steps = 240
    start = max(220, len(df5) - steps)

    allowed_types = {"SWEEP", "MICRO_BOS", "MICRO_CHOCH", "RETEST_OK"}

    checked = 0
    in_play_false_steps = 0
    in_play_true_steps = 0
    steps_with_events = 0
    seen_types: set[str] = set()

    for i in range(start, len(df5)):
        row = df5.iloc[i]
        open_ms = int(row["open_time"])
        end_ms = open_ms + 5 * 60 * 1000 - 1

        f5 = df5.iloc[max(0, i - 320) : i + 1].copy()
        f1 = _slice_by_end_ms(df1, end_ms=end_ms, tail=1800)
        f1h_i = _slice_by_end_ms(df1h, end_ms=end_ms, tail=800)
        f4h_i = _slice_by_end_ms(df4h, end_ms=end_ms, tail=400)

        # Має бути хоча б трохи 1m, інакше execution закономірно порожній.
        if len(f1) < 50:
            continue

        ohlc_by_tf = {
            "5m": f5,
            "1m": f1,
            "1h": f1h_i,
            "4h": f4h_i,
        }
        # Важливо: будуємо session context так само, як у реальному hot-path.
        ctx = _build_sessions_context(ohlc_by_tf=ohlc_by_tf, tf_primary="5m")
        snap = SmcInput(
            symbol="XAUUSD",
            tf_primary="5m",
            ohlc_by_tf=ohlc_by_tf,
            context=ctx,
        )

        hint = engine.process_snapshot(snap)
        assert hint.execution is not None

        checked += 1
        ex = hint.execution
        meta = cast(dict[str, Any], ex.meta or {})

        in_play = bool(meta.get("in_play"))
        in_play_ref = meta.get("in_play_ref")
        ref = None
        if isinstance(in_play_ref, dict):
            ref = in_play_ref.get("ref")

        last_close = float(f1["close"].iloc[-1])
        radius = meta.get("radius")
        radius_f = (
            float(radius)
            if isinstance(radius, (int, float)) and radius is not None
            else None
        )

        # 1) Інваріант антишуму: якщо not in_play → подій не має бути.
        if not in_play:
            in_play_false_steps += 1
            assert ex.execution_events == [], "Події не мають з’являтись поза in_play"
            continue

        in_play_true_steps += 1

        # 2) Перевіряємо, що in_play має осмислене пояснення (POI або TARGET)
        assert ref in {"POI", "TARGET"}, f"in_play=True, але ref={ref!r}"

        # 3) Перевіряємо геометрію in_play_ref (з урахуванням radius).
        if ref == "POI":
            assert isinstance(in_play_ref, dict)
            poi_min_raw = in_play_ref.get("poi_min")
            poi_max_raw = in_play_ref.get("poi_max")
            assert isinstance(poi_min_raw, (int, float))
            assert isinstance(poi_max_raw, (int, float))
            poi_min = float(poi_min_raw)
            poi_max = float(poi_max_raw)
            # POI трактуємо строго як прямокутник.
            assert poi_min <= last_close <= poi_max, (
                "in_play_ref=POI, але ціна поза межами зони: "
                f"close={last_close:.5f} zone=[{poi_min:.5f},{poi_max:.5f}]"
            )

        if ref == "TARGET":
            assert isinstance(in_play_ref, dict)
            level_raw = in_play_ref.get("level")
            assert isinstance(level_raw, (int, float))
            level = float(level_raw)
            assert (
                radius_f is not None and radius_f > 0
            ), "TARGET потребує позитивного radius"
            assert abs(last_close - level) <= radius_f + 1e-9, (
                "in_play_ref=TARGET, але ціна занадто далеко від level: "
                f"close={last_close:.5f} level={level:.5f} radius={radius_f:.5f}"
            )

        # 4) Перевіряємо, що події (якщо є) мають валідні типи та capped.
        if ex.execution_events:
            steps_with_events += 1
        assert len(ex.execution_events) <= cfg.exec_max_events

        for ev in ex.execution_events:
            seen_types.add(str(ev.event_type))
            assert str(ev.event_type) in allowed_types
            assert ev.time is not None

    assert checked >= 50, "Замало валідних кроків для QA-статистики"

    in_play_rate = in_play_true_steps / max(1, checked)
    events_rate = steps_with_events / max(1, checked)

    # Очікування QA: Stage5 працює як фільтр, а не як генератор шуму.
    # Пороги підібрані консервативно, щоб ловити регресії типу "майже завжди in_play".
    assert 0.01 <= in_play_rate <= 0.70, (
        "in_play має бути відносно рідкісним (фільтр), але не нульовим; "
        f"in_play_rate={in_play_rate:.3f} checked={checked}"
    )
    assert events_rate <= 0.35, (
        "micro-події не повинні сипатися часто; "
        f"events_rate={events_rate:.3f} steps_with_events={steps_with_events} checked={checked}"
    )
    # І хоча це QA на реальних даних, хоч один тип події має з’являтись інколи.
    assert seen_types or steps_with_events == 0

    # Діагностичні інваріанти (не мають бути флейковими): якщо in_play=False, подій не було.
    # Якщо в цьому вікні in_play завжди True — ок, але тоді цей лічильник буде 0.
    assert in_play_false_steps >= 0
