"""Офлайн QA Stage4 POI по локальних snapshot-файлах.

Призначення:
- коли ринок закритий і live-пайплайн не стартує, можна перевірити чи:
    - структура/ліквідність/зони рахуються взагалі;
    - `active_poi` з'являється та чому може бути 0;
    - які пороги відсікають кандидати.

Використання (PowerShell):
; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" tools/qa_snapshot_poi.py --path datastore/xauusd_bars_5m_snapshot.jsonl

Опційно:
--limit 800        # скільки останніх рядків взяти
--json out.json    # зберегти повний результат у JSON

Валідація POI (приблизна офлайн-метрика довіри):
--validate-reaction          # порахувати реакцію після touch зони
--validate-window 800        # вікно барів для пошуку touch (за замовчуванням 800)
--validate-lookahead 48      # скільки барів дивитися вперед після touch (за замовчуванням 48)

Псевдо-бектест POI (heuristic, не продакшн-стратегія):
- entry: touch зони + підтвердження 1 баром (close у напрямку угоди)
- SL: за межею зони + buffer_atr*ATR
- TP: 1R або 2R
- max holding: validate-lookahead барів
--backtest-poi
--backtest-buffer-atr 0.10
--backtest-buffer-grid 0.02,0.05,0.10,0.20
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path
from typing import Any

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.serialization import json_dumps, safe_float
from smc_core import SmcCoreConfig, SmcCoreEngine, SmcInput

_AUTO_LIMIT_BY_TF: dict[str, int] = {
    # За замовчуванням беремо «достатньо» барів для подій/зон.
    # Користувацьке побажання: 5m до 5k, 1m до 30k.
    "5m": 5000,
    "1m": 30000,
    # Для годинних TF зазвичай вистачає менше, але хай буде симетрично.
    "1h": 5000,
    "4h": 5000,
}


def _as_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    return {}


def _read_jsonl(path: Path, limit: int | None) -> pd.DataFrame:
    # Для великих limit (наприклад 30k) тримаємо тільки хвіст через deque.
    buffer: deque[dict[str, Any]] | list[dict[str, Any]]
    if limit is not None and limit > 0:
        buffer = deque(maxlen=int(limit))
    else:
        buffer = []

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(buffer, deque):
                buffer.append(row)
            else:
                buffer.append(row)

    rows = list(buffer)
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Нормалізація як у input_adapter: timestamp, сортування, reset_index.
    if "open_time" in df.columns:
        open_time = pd.to_numeric(df["open_time"], errors="coerce")
        df["timestamp"] = pd.to_datetime(
            open_time, unit="ms", errors="coerce", utc=True
        )
        df = df.dropna(subset=["timestamp"]).copy()
        if not df.empty:
            df = df.sort_values("open_time", kind="stable")
    return df.reset_index(drop=True)


def _infer_symbol_tf(path: Path) -> tuple[str, str]:
    # filename: xauusd_bars_5m_snapshot.jsonl
    name = path.name.lower()
    parts = name.split("_bars_")
    if len(parts) == 2:
        symbol = parts[0]
        rest = parts[1]
        tf = rest.split("_snapshot")[0]
        return symbol.upper(), tf
    return "UNKNOWN", "5m"


def _iter_snapshot_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        files = sorted(path.glob("*_snapshot.jsonl"))
        return [p for p in files if p.is_file()]
    return []


def _pick_limit(tf: str, limit: int | None) -> int | None:
    if limit is None:
        return _AUTO_LIMIT_BY_TF.get(tf, 900)
    if limit <= 0:
        return None
    return int(limit)


def _should_escalate(zones_meta: dict[str, Any]) -> bool:
    # Ескалуємо, коли все «нулі» — це типовий симптом недостатнього lookback.
    return bool(
        (zones_meta.get("zone_count") or 0) == 0
        and (zones_meta.get("orderblocks_total") or 0) == 0
        and (zones_meta.get("breaker_total") or 0) == 0
        and (zones_meta.get("fvg_total") or 0) == 0
    )


def _validate_poi_reaction(
    *,
    frame: pd.DataFrame,
    active_poi: list[dict[str, Any]],
    atr: float | None,
    window_bars: int,
    lookahead_bars: int,
) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    if not active_poi:
        return []
    atr_f = safe_float(atr)
    if atr_f is None or atr_f <= 0:
        return []

    window = max(1, int(window_bars))
    lookahead = max(1, int(lookahead_bars))

    df = frame.tail(window).reset_index(drop=True)
    if not {"low", "high", "close"}.issubset(set(df.columns)):
        return []

    thresholds = (0.5, 1.0)
    results: list[dict[str, Any]] = []

    for poi in active_poi:
        if not isinstance(poi, dict):
            continue
        direction = str(poi.get("direction") or "").upper()
        pmin = safe_float(poi.get("price_min"))
        pmax = safe_float(poi.get("price_max"))
        if pmin is None or pmax is None:
            continue
        if pmin > pmax:
            pmin, pmax = pmax, pmin
        if direction not in {"LONG", "SHORT"}:
            continue

        touches = 0
        favorable_moves_atr: list[float] = []
        react_counts = {thr: 0 for thr in thresholds}

        lows = df["low"].astype(float)
        highs = df["high"].astype(float)

        # Touch: бар перетинає зону [pmin..pmax].
        touch_mask = (lows <= float(pmax)) & (highs >= float(pmin))
        touch_idx = [int(i) for i, v in enumerate(touch_mask.tolist()) if bool(v)]

        for i in touch_idx:
            entry = safe_float(df["close"].iloc[i])
            if entry is None:
                continue
            j0 = i + 1
            j1 = min(len(df), i + 1 + lookahead)
            if j0 >= j1:
                continue
            future = df.iloc[j0:j1]
            touches += 1

            if direction == "LONG":
                max_high = safe_float(future["high"].max())
                if max_high is None:
                    continue
                mfe = (float(max_high) - float(entry)) / float(atr_f)
            else:
                min_low = safe_float(future["low"].min())
                if min_low is None:
                    continue
                mfe = (float(entry) - float(min_low)) / float(atr_f)

            favorable_moves_atr.append(float(mfe))
            for thr in thresholds:
                if mfe >= thr:
                    react_counts[thr] += 1

        if touches <= 0:
            results.append(
                {
                    "type": poi.get("type"),
                    "direction": direction,
                    "price_min": float(pmin),
                    "price_max": float(pmax),
                    "touches": 0,
                    "react_0_5atr_rate": None,
                    "react_1_0atr_rate": None,
                    "median_mfe_atr": None,
                    "lookahead_bars": int(lookahead),
                    "window_bars": int(window),
                }
            )
            continue

        favorable_moves_atr_sorted = sorted(favorable_moves_atr)
        median_mfe = favorable_moves_atr_sorted[len(favorable_moves_atr_sorted) // 2]

        results.append(
            {
                "type": poi.get("type"),
                "direction": direction,
                "price_min": float(pmin),
                "price_max": float(pmax),
                "touches": int(touches),
                "react_0_5atr_rate": round(react_counts[0.5] / touches, 4),
                "react_1_0atr_rate": round(react_counts[1.0] / touches, 4),
                "median_mfe_atr": round(float(median_mfe), 4),
                "lookahead_bars": int(lookahead),
                "window_bars": int(window),
            }
        )

    return results


def _backtest_poi(
    *,
    frame: pd.DataFrame,
    active_poi: list[dict[str, Any]],
    atr: float | None,
    window_bars: int,
    lookahead_bars: int,
    buffer_atr: float,
) -> list[dict[str, Any]]:
    """Мінімальний псевдо-бектест по кожному POI окремо.

    Важливо:
    - це НЕ PnL/backtest рівня стратегії (нема спреду/комісій/ліквідності);
    - якщо TP і SL в одному барі — консервативно рахуємо як SL.
    """

    if frame is None or frame.empty:
        return []
    if not active_poi:
        return []
    atr_f = safe_float(atr)
    if atr_f is None or atr_f <= 0:
        return []

    window = max(3, int(window_bars))
    lookahead = max(2, int(lookahead_bars))
    buf_atr = max(0.0, float(buffer_atr))

    df = frame.tail(window).reset_index(drop=True)
    if not {"low", "high", "close"}.issubset(set(df.columns)):
        return []

    lows = df["low"].astype(float)
    highs = df["high"].astype(float)
    closes = df["close"].astype(float)

    out: list[dict[str, Any]] = []
    for poi in active_poi:
        if not isinstance(poi, dict):
            continue
        direction = str(poi.get("direction") or "").upper()
        pmin = safe_float(poi.get("price_min"))
        pmax = safe_float(poi.get("price_max"))
        if pmin is None or pmax is None:
            continue
        if pmin > pmax:
            pmin, pmax = pmax, pmin
        if direction not in {"LONG", "SHORT"}:
            continue

        buffer = float(buf_atr) * float(atr_f)

        # Touch: бар перетинає зону.
        touch_mask = (lows <= float(pmax)) & (highs >= float(pmin))
        touch_idx = [int(i) for i, v in enumerate(touch_mask.tolist()) if bool(v)]

        trades = 0
        no_confirm = 0
        wins_1r = 0
        wins_2r = 0
        rs_1r: list[float] = []
        rs_2r: list[float] = []

        i = 0
        touch_set = set(touch_idx)
        while i < len(df) - 2:
            if i not in touch_set:
                i += 1
                continue

            # Підтвердження 1 баром: беремо наступний бар.
            j = i + 1
            if j >= len(df):
                break
            c0 = float(closes.iloc[i])
            c1 = float(closes.iloc[j])
            if direction == "LONG" and not (c1 > c0):
                no_confirm += 1
                i += 1
                continue
            if direction == "SHORT" and not (c1 < c0):
                no_confirm += 1
                i += 1
                continue

            entry = c1
            if direction == "LONG":
                sl = float(pmin) - buffer
                risk = max(entry - sl, 1e-9)
                tp_1r = entry + 1.0 * risk
                tp_2r = entry + 2.0 * risk
            else:
                sl = float(pmax) + buffer
                risk = max(sl - entry, 1e-9)
                tp_1r = entry - 1.0 * risk
                tp_2r = entry - 2.0 * risk

            # Симуляція вперед (без оверлапів).
            k0 = j + 1
            k1 = min(len(df), j + 1 + lookahead)
            if k0 >= k1:
                break

            trades += 1
            hit_1r = False
            hit_2r = False
            hit_sl = False
            exit_r_1r = 0.0
            exit_r_2r = 0.0
            exit_k = k1 - 1

            for k in range(k0, k1):
                hi = float(highs.iloc[k])
                lo = float(lows.iloc[k])

                # Консервативно: якщо в одному барі могли торкнути SL і TP — рахуємо як SL.
                if direction == "LONG":
                    if lo <= sl:
                        hit_sl = True
                        exit_k = k
                        break
                    if not hit_1r and hi >= tp_1r:
                        hit_1r = True
                    if not hit_2r and hi >= tp_2r:
                        hit_2r = True
                        # якщо 2R взято — вихід для 2R
                else:
                    if hi >= sl:
                        hit_sl = True
                        exit_k = k
                        break
                    if not hit_1r and lo <= tp_1r:
                        hit_1r = True
                    if not hit_2r and lo <= tp_2r:
                        hit_2r = True

                # Якщо 2R досягнуто — можемо завершити для 2R (але для 1R теж win).
                if hit_2r:
                    exit_k = k
                    break

            if hit_sl:
                exit_r_1r = -1.0
                exit_r_2r = -1.0
            else:
                if hit_1r:
                    wins_1r += 1
                    exit_r_1r = 1.0
                else:
                    # Не дійшли до TP: фіксуємо R по close останнього бару (mark-to-market).
                    c_exit = float(closes.iloc[exit_k])
                    if direction == "LONG":
                        exit_r_1r = (c_exit - entry) / risk
                    else:
                        exit_r_1r = (entry - c_exit) / risk

                if hit_2r:
                    wins_2r += 1
                    exit_r_2r = 2.0
                else:
                    c_exit = float(closes.iloc[exit_k])
                    if direction == "LONG":
                        exit_r_2r = (c_exit - entry) / risk
                    else:
                        exit_r_2r = (entry - c_exit) / risk

            rs_1r.append(float(exit_r_1r))
            rs_2r.append(float(exit_r_2r))

            # Уникаємо перекриття угод: перескакуємо до exit_k.
            i = max(i + 1, exit_k)

        def _avg(xs: list[float]) -> float | None:
            if not xs:
                return None
            return float(sum(xs) / len(xs))

        def _median(xs: list[float]) -> float | None:
            if not xs:
                return None
            ys = sorted(xs)
            return float(ys[len(ys) // 2])

        avg_1r = _avg(rs_1r)
        med_1r = _median(rs_1r)
        avg_2r = _avg(rs_2r)
        med_2r = _median(rs_2r)

        out.append(
            {
                "type": poi.get("type"),
                "direction": direction,
                "price_min": float(pmin),
                "price_max": float(pmax),
                "trades": int(trades),
                "no_confirm": int(no_confirm),
                "buffer_atr": float(buf_atr),
                "window_bars": int(window),
                "lookahead_bars": int(lookahead),
                "tp_1r": {
                    "wins": int(wins_1r),
                    "winrate": (round(wins_1r / trades, 4) if trades else None),
                    "avg_r": (round(float(avg_1r), 4) if avg_1r is not None else None),
                    "median_r": (
                        round(float(med_1r), 4) if med_1r is not None else None
                    ),
                },
                "tp_2r": {
                    "wins": int(wins_2r),
                    "winrate": (round(wins_2r / trades, 4) if trades else None),
                    "avg_r": (round(float(avg_2r), 4) if avg_2r is not None else None),
                    "median_r": (
                        round(float(med_2r), 4) if med_2r is not None else None
                    ),
                },
            }
        )

    return out


def _parse_buffer_grid(raw: str) -> list[float]:
    """Парсить grid-рядок виду "0.02,0.05,0.10" у список float.

    Невалідні елементи ігноруються.
    """

    if not raw:
        return []
    parts = [p.strip() for p in str(raw).split(",")]
    out: list[float] = []
    for p in parts:
        try:
            v = float(p)
        except Exception:
            continue
        if v < 0:
            continue
        out.append(v)
    # Унікалізуємо з мінімальною стабільністю.
    uniq: list[float] = []
    seen: set[float] = set()
    for v in out:
        if v in seen:
            continue
        uniq.append(v)
        seen.add(v)
    return uniq


def _aggregate_backtest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Зводить backtest-рядки в один підсумок (зваження по кількості угод)."""

    total_trades = 0
    total_no_confirm = 0

    wins_1r = 0
    wins_2r = 0
    sum_r_1r = 0.0
    sum_r_2r = 0.0

    for row in rows:
        trades = int(row.get("trades") or 0)
        if trades <= 0:
            total_no_confirm += int(row.get("no_confirm") or 0)
            continue
        total_trades += trades
        total_no_confirm += int(row.get("no_confirm") or 0)

        tp1 = _as_dict(row.get("tp_1r"))
        tp2 = _as_dict(row.get("tp_2r"))

        wins_1r += int(tp1.get("wins") or 0)
        wins_2r += int(tp2.get("wins") or 0)

        avg_r_1r = safe_float(tp1.get("avg_r"))
        avg_r_2r = safe_float(tp2.get("avg_r"))
        if avg_r_1r is not None:
            sum_r_1r += float(avg_r_1r) * trades
        if avg_r_2r is not None:
            sum_r_2r += float(avg_r_2r) * trades

    def _rate(wins: int, denom: int) -> float | None:
        if denom <= 0:
            return None
        return round(wins / denom, 4)

    def _avg(sum_r: float, denom: int) -> float | None:
        if denom <= 0:
            return None
        return round(sum_r / denom, 4)

    return {
        "trades": int(total_trades),
        "no_confirm": int(total_no_confirm),
        "tp_1r": {
            "wins": int(wins_1r),
            "winrate": _rate(wins_1r, total_trades),
            "avg_r": _avg(sum_r_1r, total_trades),
        },
        "tp_2r": {
            "wins": int(wins_2r),
            "winrate": _rate(wins_2r, total_trades),
            "avg_r": _avg(sum_r_2r, total_trades),
        },
    }


def _run_one(
    path: Path,
    *,
    limit: int | None,
    json_out: str,
    validate_reaction: bool,
    validate_window: int,
    validate_lookahead: int,
    backtest_poi: bool,
    backtest_buffer_atr: float,
    backtest_buffer_grid: str,
) -> int:
    symbol, tf = _infer_symbol_tf(path)
    df = _read_jsonl(path, limit=limit)
    if df.empty:
        print("ПОМИЛКА: не вдалося зчитати snapshot (порожньо)")
        return 3

    snapshot = SmcInput(
        symbol=symbol,
        tf_primary=tf,
        ohlc_by_tf={tf: df},
        context={},
    )

    engine = SmcCoreEngine(cfg=SmcCoreConfig())
    hint = engine.process_snapshot(snapshot)

    zones_meta = (hint.zones.meta or {}) if hint.zones is not None else {}
    poi_meta = zones_meta.get("poi") if isinstance(zones_meta.get("poi"), dict) else {}
    active_poi_obj = zones_meta.get("active_poi")
    active_poi_raw: list[Any] = (
        active_poi_obj if isinstance(active_poi_obj, list) else []
    )
    active_poi: list[dict[str, Any]] = [
        x for x in active_poi_raw if isinstance(x, dict)
    ]

    print("=== SMC Snapshot QA (Stage4 POI) ===")
    print(f"file: {path}")
    print(f"symbol/tf: {symbol}/{tf}")
    print(f"bars: {len(df)}")
    lc = _last_close(df)
    if lc is not None:
        print(f"last_close: {lc}")

    # Структура
    st = hint.structure
    print("--- structure ---")
    if st is None:
        print("structure: -")
    else:
        print(
            f"legs: {len(st.legs)} swings: {len(st.swings)} events_recent: {len(st.events)} events_history: {len(st.event_history)}"
        )
        print(f"bias: {st.bias} trend: {st.trend}")
        atr_last = safe_float((st.meta or {}).get("atr_last"))
        atr_median = safe_float((st.meta or {}).get("atr_median"))
        print(f"atr_last: {atr_last} atr_median: {atr_median}")

    # Ліквідність
    liq = hint.liquidity
    print("--- liquidity ---")
    if liq is None:
        print("liquidity: -")
    else:
        pools = getattr(liq, "pools", [])
        magnets = getattr(liq, "magnets", [])
        print(f"pools: {len(pools)} magnets: {len(magnets)}")
        lt = (liq.meta or {}).get("liquidity_targets")
        if isinstance(lt, list):
            print(f"liquidity_targets: {len(lt)}")
        else:
            print("liquidity_targets: -")

    # Зони
    print("--- zones ---")
    print(
        f"orderblocks_total: {zones_meta.get('orderblocks_total')} breakers_total: {zones_meta.get('breaker_total')} fvg_total: {zones_meta.get('fvg_total')}"
    )
    print(
        f"zone_count: {zones_meta.get('zone_count')} active_zone_count: {zones_meta.get('active_zone_count')}"
    )

    # POI
    print("--- poi ---")
    if isinstance(poi_meta, dict) and poi_meta:
        print(
            f"candidates: {poi_meta.get('poi_candidates')} active: {poi_meta.get('poi_active')} archived: {poi_meta.get('poi_archived')}"
        )
        # Деталізація відсіву (допомагає зрозуміти, чи "губимо" щось корисне).
        details = (
            f"cap_drop: {poi_meta.get('poi_dropped_due_cap')} | "
            f"arch_invalid: {poi_meta.get('poi_archived_invalidated')} "
            f"arch_filled: {poi_meta.get('poi_archived_filled')} "
            f"arch_score<=0: {poi_meta.get('poi_archived_score_le_0')}"
        )
        print(details)
    else:
        print("poi_meta: -")

    if not active_poi:
        print("active_poi: []")
    else:
        print(f"active_poi: {len(active_poi)}")
        for i, item in enumerate(active_poi, start=1):
            print(
                f"#{i} {item.get('type')} {item.get('direction')} [{item.get('price_min')}..{item.get('price_max')}]"
                f" score={item.get('score')} filled={item.get('filled_pct')}"
            )
            why = item.get("why")
            if isinstance(why, list) and why:
                print("  why:")
                for w in why[:8]:
                    print(f"   - {w}")

    poi_validation: list[dict[str, Any]] = []
    poi_backtest: list[dict[str, Any]] = []
    poi_backtest_grid: list[dict[str, Any]] = []
    if validate_reaction:
        atr_for_validation = None
        if hint.structure is not None:
            atr_for_validation = safe_float(
                (hint.structure.meta or {}).get("atr_last")
            ) or safe_float((hint.structure.meta or {}).get("atr_median"))
        poi_validation = _validate_poi_reaction(
            frame=df,
            active_poi=active_poi,
            atr=atr_for_validation,
            window_bars=validate_window,
            lookahead_bars=validate_lookahead,
        )
        print("--- poi_validation (reaction після touch) ---")
        if not poi_validation:
            print("poi_validation: -")
        else:
            for row in poi_validation:
                print(
                    f"{row.get('type')} {row.get('direction')} [{row.get('price_min')}..{row.get('price_max')}] "
                    f"touches={row.get('touches')} r0.5={row.get('react_0_5atr_rate')} r1.0={row.get('react_1_0atr_rate')} "
                    f"median_mfe_atr={row.get('median_mfe_atr')}"
                )

    if backtest_poi:
        atr_for_bt = None
        if hint.structure is not None:
            atr_for_bt = safe_float(
                (hint.structure.meta or {}).get("atr_last")
            ) or safe_float((hint.structure.meta or {}).get("atr_median"))
        grid = _parse_buffer_grid(backtest_buffer_grid)
        if grid:
            print("--- poi_backtest_grid (touch + підтвердження 1 баром) ---")
            for buf in grid:
                rows = _backtest_poi(
                    frame=df,
                    active_poi=active_poi,
                    atr=atr_for_bt,
                    window_bars=validate_window,
                    lookahead_bars=validate_lookahead,
                    buffer_atr=float(buf),
                )
                agg = _aggregate_backtest(rows)
                poi_backtest_grid.append(
                    {
                        "buffer_atr": float(buf),
                        "aggregate": agg,
                        "per_poi": rows,
                    }
                )
                tp1 = (agg.get("tp_1r") or {}) if isinstance(agg, dict) else {}
                tp2 = (agg.get("tp_2r") or {}) if isinstance(agg, dict) else {}
                print(
                    f"buffer_atr={float(buf)} trades={agg.get('trades')} no_confirm={agg.get('no_confirm')} | "
                    f"1R winrate={tp1.get('winrate')} avgR={tp1.get('avg_r')} | "
                    f"2R winrate={tp2.get('winrate')} avgR={tp2.get('avg_r')}"
                )
        else:
            poi_backtest = _backtest_poi(
                frame=df,
                active_poi=active_poi,
                atr=atr_for_bt,
                window_bars=validate_window,
                lookahead_bars=validate_lookahead,
                buffer_atr=backtest_buffer_atr,
            )
            print("--- poi_backtest (touch + підтвердження 1 баром) ---")
            if not poi_backtest:
                print("poi_backtest: -")
            else:
                for row in poi_backtest:
                    tp1 = row.get("tp_1r") or {}
                    tp2 = row.get("tp_2r") or {}
                    print(
                        f"{row.get('type')} {row.get('direction')} [{row.get('price_min')}..{row.get('price_max')}] "
                        f"trades={row.get('trades')} no_confirm={row.get('no_confirm')} "
                        f"1R winrate={tp1.get('winrate')} avgR={tp1.get('avg_r')} | "
                        f"2R winrate={tp2.get('winrate')} avgR={tp2.get('avg_r')}"
                    )

    payload = {
        "symbol": symbol,
        "tf": tf,
        "bars": int(len(df)),
        "last_close": lc,
        "structure_meta": (hint.structure.meta if hint.structure is not None else None),
        "liquidity_meta": (hint.liquidity.meta if hint.liquidity is not None else None),
        "zones_meta": zones_meta,
        "poi_validation": poi_validation,
        "poi_backtest": poi_backtest,
        "poi_backtest_grid": poi_backtest_grid,
        "zones": [
            {
                "type": str(z.zone_type),
                "direction": z.direction,
                "role": z.role,
                "price_min": float(z.price_min),
                "price_max": float(z.price_max),
                "origin_time": (
                    z.origin_time.isoformat()
                    if getattr(z, "origin_time", None) is not None
                    else None
                ),
                "score": (z.meta or {}).get("score"),
                "filled_pct": (z.meta or {}).get("filled_pct"),
                "poi_type": (z.meta or {}).get("poi_type"),
                "why": (z.meta or {}).get("why"),
                "zone_id": z.zone_id,
            }
            for z in ((hint.zones.poi_zones or []) if hint.zones is not None else [])
        ],
    }

    if json_out:
        out = Path(json_out)
        # Якщо запускаємо по папці — додаємо суфікс tf, щоб не перезаписати.
        if out.suffix.lower() == ".json" and out.name == "out.json" and path.is_file():
            out = out.with_name(f"{out.stem}_{symbol.lower()}_{tf}{out.suffix}")
        out.write_text(json_dumps(payload, pretty=True), encoding="utf-8")
        print(f"JSON збережено: {out}")

    return 0


def _last_close(df: pd.DataFrame) -> float | None:
    if df is None or df.empty:
        return None
    if "close" not in df.columns:
        return None
    return safe_float(df["close"].iloc[-1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--path",
        required=True,
        help="Шлях до *_snapshot.jsonl або до папки зі snapshot-ами (наприклад datastore)",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Скільки останніх рядків взяти (0 => auto за TF: 5m=5000, 1m=30000)",
    )
    ap.add_argument(
        "--escalate",
        action="store_true",
        help="Якщо все 0 — перезапустити з auto-limit для цього TF",
    )
    ap.add_argument(
        "--json", dest="json_path", default="", help="Зберегти повний результат у JSON"
    )
    ap.add_argument(
        "--validate-reaction",
        action="store_true",
        help="Порахувати реакцію після touch зони (MFE в ATR) для active_poi",
    )
    ap.add_argument(
        "--validate-window",
        type=int,
        default=800,
        help="Вікно барів для пошуку touch (за замовчуванням 800)",
    )
    ap.add_argument(
        "--validate-lookahead",
        type=int,
        default=48,
        help="Скільки барів дивитися вперед після touch (за замовчуванням 48)",
    )
    ap.add_argument(
        "--backtest-poi",
        action="store_true",
        help="Псевдо-бектест POI: touch + підтвердження 1 баром, SL за межею зони + buffer, TP=1R/2R",
    )
    ap.add_argument(
        "--backtest-buffer-atr",
        type=float,
        default=0.10,
        help="SL buffer у частках ATR (за замовчуванням 0.10)",
    )
    ap.add_argument(
        "--backtest-buffer-grid",
        default="",
        help="Grid для SL buffer у частках ATR (через кому), напр. 0.02,0.05,0.10,0.20",
    )
    args = ap.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"ПОМИЛКА: файл не знайдено: {path}")
        return 2

    files = _iter_snapshot_files(path)
    if not files:
        print(f"ПОМИЛКА: не знайдено snapshot-файлів у: {path}")
        return 4

    overall_rc = 0
    for file_path in files:
        symbol, tf = _infer_symbol_tf(file_path)
        chosen_limit = _pick_limit(tf, args.limit)
        # Перший прогін.
        rc = _run_one(
            file_path,
            limit=chosen_limit,
            json_out=args.json_path,
            validate_reaction=bool(args.validate_reaction),
            validate_window=int(args.validate_window),
            validate_lookahead=int(args.validate_lookahead),
            backtest_poi=bool(args.backtest_poi),
            backtest_buffer_atr=float(args.backtest_buffer_atr),
            backtest_buffer_grid=str(args.backtest_buffer_grid or ""),
        )
        overall_rc = max(overall_rc, rc)

        # Ескалація, якщо просили і є сенс.
        if not args.escalate:
            continue

        # Мінімальний повтор: якщо все 0 і ми не на auto-limit.
        if args.limit == 0:
            # Вже auto => немає куди ескалювати.
            continue

        # Перевіряємо, чи варто ескалювати: для цього робимо легкий прогін meta.
        # (Читаємо повторно, але це офлайн QA і робиться лише коли попросили.)
        df_meta = _read_jsonl(file_path, limit=chosen_limit)
        snapshot_meta = SmcInput(
            symbol=symbol,
            tf_primary=tf,
            ohlc_by_tf={tf: df_meta},
            context={},
        )
        hint_meta = SmcCoreEngine(cfg=SmcCoreConfig()).process_snapshot(snapshot_meta)
        zones_meta = (hint_meta.zones.meta or {}) if hint_meta.zones is not None else {}
        if _should_escalate(zones_meta):
            escalated = _AUTO_LIMIT_BY_TF.get(tf)
            if escalated and (chosen_limit is None or escalated > chosen_limit):
                print(f"--- escalate: {tf} limit {chosen_limit} -> {escalated} ---")
                rc2 = _run_one(
                    file_path,
                    limit=escalated,
                    json_out=args.json_path,
                    validate_reaction=bool(args.validate_reaction),
                    validate_window=int(args.validate_window),
                    validate_lookahead=int(args.validate_lookahead),
                    backtest_poi=bool(args.backtest_poi),
                    backtest_buffer_atr=float(args.backtest_buffer_atr),
                    backtest_buffer_grid=str(args.backtest_buffer_grid or ""),
                )
                overall_rc = max(overall_rc, rc2)

    return overall_rc


if __name__ == "__main__":
    raise SystemExit(main())
