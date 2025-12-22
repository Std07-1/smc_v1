"""QA: статистика довіри до Stage5 execution сигналів на реальних снапшотах.

Ідея
-----
Stage5 дає micro-події (SWEEP / MICRO_BOS / MICRO_CHOCH / RETEST_OK) лише коли `in_play=True`.
Щоб зрозуміти, чи можна їм довіряти, рахуємо просту пост-фактум метрику:

- Беремо момент події (entry = close останнього 1m бара на кроці).
- Дивимось вперед `horizon_bars` (1m барів).
- Міряємо чи ціна спершу дійшла до TP або до SL.
- TP/SL задаємо в одиницях ATR (беремо `execution.meta['atr_ref']` як базовий ATR).

Вихід
------
Друкує summary у stdout та за бажанням пише Markdown у `reports/`.

Приклад (PowerShell)
-------------------
; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m tools.qa_execution_signal_stats \
    --path datastore/xauusd_bars_5m_snapshot.jsonl --steps 240 \
    --horizon-bars 60 --tp-atr 1.0 --sl-atr 1.0 \
    --out reports/execution_signal_stats_xauusd.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import pandas as pd

Outcome = Literal["WIN", "LOSS", "NO_HIT", "BOTH_SAME_BAR", "NO_ATR"]

AlignTag = Literal["ALIGNED", "COUNTER", "NA"]
RefKind = Literal["POI", "TARGET", "NONE"]


def _parse_csv_floats(raw: str) -> list[float]:
    out: list[float] = []
    for part in str(raw).split(","):
        s = part.strip()
        if not s:
            continue
        out.append(float(s))
    return out


def _parse_csv_ints(raw: str) -> list[int]:
    out: list[int] = []
    for part in str(raw).split(","):
        s = part.strip()
        if not s:
            continue
        out.append(int(s))
    return out


def _ensure_repo_on_syspath() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def _resolve_snapshot_path(raw: str) -> Path:
    p = Path(str(raw).strip())
    repo_root = Path(__file__).resolve().parents[1]

    candidates: list[Path] = [p]
    if not p.is_absolute():
        candidates.append(repo_root / p)
        candidates.append(repo_root / "datastore" / p)
        candidates.append(repo_root / "datastore" / p.name)

    for c in candidates:
        try:
            if c.exists() and c.is_file():
                return c
        except OSError:
            continue
    return p


def _infer_symbol(path: Path) -> str:
    name = path.name.lower()
    parts = name.split("_bars_")
    if len(parts) == 2:
        return parts[0].upper()
    return "UNKNOWN"


def _infer_ref_kind(meta: dict[str, Any]) -> RefKind:
    ref_obj = meta.get("in_play_ref")
    if isinstance(ref_obj, dict):
        ref = str(ref_obj.get("ref") or "").upper()
        if ref in ("POI", "TARGET"):
            return ref  # type: ignore[return-value]
    return "NONE"


def _align_tag(*, bias: str | None, direction: Literal["LONG", "SHORT"]) -> AlignTag:
    b = str(bias or "").upper()
    if b not in ("LONG", "SHORT"):
        return "NA"
    return "ALIGNED" if b == direction else "COUNTER"


def _read_jsonl_tail(path: Path, limit: int) -> pd.DataFrame:
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

    if "open_time" not in df.columns:
        return pd.DataFrame()

    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce")
    df["timestamp"] = pd.to_datetime(
        df["open_time"], unit="ms", errors="coerce", utc=True
    )
    df = df.dropna(subset=["open_time", "timestamp"]).copy()
    if df.empty:
        return pd.DataFrame()
    df = df.sort_values("open_time", kind="stable").reset_index(drop=True)
    return df


def _slice_by_end_ms(df: pd.DataFrame, *, end_ms: int, tail: int) -> pd.DataFrame:
    part = df[df["open_time"] <= int(end_ms)]
    if part.empty:
        return part
    if tail > 0 and len(part) > tail:
        part = part.iloc[-tail:]
    return part.copy()


@dataclass(frozen=True, slots=True)
class EvalResult:
    outcome: Outcome
    bars_to_outcome: int | None
    mfe_atr: float | None
    mae_atr: float | None


def _eval_forward_path(
    *,
    direction: Literal["LONG", "SHORT"],
    entry: float,
    atr: float,
    frame_fwd: pd.DataFrame,
    tp_atr: float,
    sl_atr: float,
) -> EvalResult:
    """Оцінює подію по майбутніх 1m барах.

    Правило:
    - рухаємося послідовно по барах; якщо в одному барі доторкнули і TP і SL,
      повертаємо BOTH_SAME_BAR (це сигнал про неоднозначність).
    - якщо ні TP ні SL не зачепилися — NO_HIT.

    MFE/MAE в ATR по всьому горизонту (після entry).
    """

    if atr <= 0:
        return EvalResult(
            outcome="NO_ATR", bars_to_outcome=None, mfe_atr=None, mae_atr=None
        )

    tp_dist = float(tp_atr) * float(atr)
    sl_dist = float(sl_atr) * float(atr)

    if direction == "LONG":
        tp = float(entry) + tp_dist
        sl = float(entry) - sl_dist
        max_high = (
            float(frame_fwd["high"].astype(float).max())
            if not frame_fwd.empty
            else float(entry)
        )
        min_low = (
            float(frame_fwd["low"].astype(float).min())
            if not frame_fwd.empty
            else float(entry)
        )
        mfe = max(0.0, (max_high - float(entry)) / float(atr))
        mae = max(0.0, (float(entry) - min_low) / float(atr))

        for i, r in enumerate(frame_fwd.itertuples(index=False), start=1):
            h = float(cast(float, r.high))
            lo = float(cast(float, r.low))
            hit_tp = h >= tp
            hit_sl = lo <= sl
            if hit_tp and hit_sl:
                return EvalResult("BOTH_SAME_BAR", i, mfe, mae)
            if hit_tp:
                return EvalResult("WIN", i, mfe, mae)
            if hit_sl:
                return EvalResult("LOSS", i, mfe, mae)

        return EvalResult("NO_HIT", None, mfe, mae)

    # SHORT
    tp = float(entry) - tp_dist
    sl = float(entry) + sl_dist
    min_low = (
        float(frame_fwd["low"].astype(float).min())
        if not frame_fwd.empty
        else float(entry)
    )
    max_high = (
        float(frame_fwd["high"].astype(float).max())
        if not frame_fwd.empty
        else float(entry)
    )
    mfe = max(0.0, (float(entry) - min_low) / float(atr))
    mae = max(0.0, (max_high - float(entry)) / float(atr))

    for i, r in enumerate(frame_fwd.itertuples(index=False), start=1):
        h = float(cast(float, r.high))
        lo = float(cast(float, r.low))
        hit_tp = lo <= tp
        hit_sl = h >= sl
        if hit_tp and hit_sl:
            return EvalResult("BOTH_SAME_BAR", i, mfe, mae)
        if hit_tp:
            return EvalResult("WIN", i, mfe, mae)
        if hit_sl:
            return EvalResult("LOSS", i, mfe, mae)

    return EvalResult("NO_HIT", None, mfe, mae)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="QA статистика успішності Stage5 execution подій на снапшотах.",
    )
    p.add_argument(
        "--path",
        default=None,
        help="Шлях до 5m *_snapshot.jsonl (або ім'я з datastore/). Якщо задано разом з --paths — буде додано до списку.",
    )
    p.add_argument(
        "--paths",
        default=None,
        help="CSV список 5m *_snapshot.jsonl для розширення вибірки (можна міксувати символи).",
    )
    p.add_argument(
        "--steps",
        type=int,
        default=240,
        help="Скільки останніх 5m-кроків прогнати (після warmup).",
    )
    p.add_argument(
        "--warmup", type=int, default=220, help="Скільки 5m барів пропустити на старті."
    )

    p.add_argument("--limit-5m", type=int, default=900, help="Tail 5m bars.")
    p.add_argument("--limit-1m", type=int, default=9000, help="Tail 1m bars.")
    p.add_argument("--limit-1h", type=int, default=3000, help="Tail 1h bars.")
    p.add_argument("--limit-4h", type=int, default=2000, help="Tail 4h bars.")

    p.add_argument(
        "--horizon-bars",
        type=int,
        default=60,
        help="Горизонт оцінки після сигналу (1m барів).",
    )
    p.add_argument("--tp-atr", type=float, default=1.0, help="TP у множниках ATR.")
    p.add_argument("--sl-atr", type=float, default=1.0, help="SL у множниках ATR.")

    p.add_argument("--out", default=None, help="Markdown-репорт у reports/ (опційно).")

    p.add_argument(
        "--grid-horizons",
        default=None,
        help="CSV список горизонтів (1m барів), напр: 15,30,60,120. Якщо задано — робимо grid-репорт.",
    )
    p.add_argument(
        "--grid-tp-atrs",
        default=None,
        help="CSV список TP у ATR, напр: 0.5,1.0,1.5.",
    )
    p.add_argument(
        "--grid-sl-atrs",
        default=None,
        help="CSV список SL у ATR, напр: 0.5,1.0,1.5.",
    )

    # Stage5 конфіг — тримаємо синхронним з журналом.
    p.add_argument(
        "--radius-atr", type=float, default=0.9, help="exec_in_play_radius_atr"
    )
    p.add_argument("--hold-bars", type=int, default=0, help="exec_in_play_hold_bars")
    p.add_argument(
        "--impulse-atr-mul", type=float, default=0.0, help="exec_impulse_atr_mul"
    )
    p.add_argument(
        "--micro-pivot-bars", type=int, default=8, help="exec_micro_pivot_bars"
    )
    p.add_argument("--max-events", type=int, default=6, help="exec_max_events")

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _ensure_repo_on_syspath()

    from smc_core import SmcCoreConfig, SmcCoreEngine, SmcInput
    from smc_core.input_adapter import _build_sessions_context

    args = parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    raw_paths: list[str] = []
    if args.paths:
        raw_paths.extend([p.strip() for p in str(args.paths).split(",") if p.strip()])
    if args.path:
        raw_paths.append(str(args.path).strip())
    if not raw_paths:
        raise SystemExit("Потрібно задати --path або --paths")

    paths5: list[Path] = []
    for rp in raw_paths:
        p5 = _resolve_snapshot_path(rp)
        if not p5.exists():
            raise SystemExit(f"Не знайдено файл: {p5}")
        paths5.append(p5)

    cfg = SmcCoreConfig(
        exec_enabled=True,
        exec_tf="1m",
        exec_in_play_radius_atr=float(args.radius_atr),
        exec_in_play_hold_bars=int(args.hold_bars),
        exec_impulse_atr_mul=float(args.impulse_atr_mul),
        exec_micro_pivot_bars=int(args.micro_pivot_bars),
        exec_max_events=int(args.max_events),
    )
    engine = SmcCoreEngine(cfg=cfg)

    grid_horizons = (
        _parse_csv_ints(args.grid_horizons)
        if args.grid_horizons is not None
        else [int(args.horizon_bars)]
    )
    grid_tps = (
        _parse_csv_floats(args.grid_tp_atrs)
        if args.grid_tp_atrs is not None
        else [float(args.tp_atr)]
    )
    grid_sls = (
        _parse_csv_floats(args.grid_sl_atrs)
        if args.grid_sl_atrs is not None
        else [float(args.sl_atr)]
    )

    grid_horizons = [h for h in grid_horizons if h > 0]
    grid_tps = [v for v in grid_tps if v > 0]
    grid_sls = [v for v in grid_sls if v > 0]
    if not grid_horizons:
        raise SystemExit("grid_horizons порожній або невалідний")
    if not grid_tps:
        raise SystemExit("grid_tps порожній або невалідний")
    if not grid_sls:
        raise SystemExit("grid_sls порожній або невалідний")

    max_horizon = int(max(grid_horizons))

    checked_steps = 0

    @dataclass(frozen=True, slots=True)
    class _EventSample:
        symbol: str
        ref_kind: RefKind
        align: AlignTag
        event_type: str
        direction: Literal["LONG", "SHORT"]
        entry: float
        atr_ref: float
        fwd: pd.DataFrame  # high/low на max_horizon

    samples: list[_EventSample] = []

    symbols_seen: set[str] = set()
    for path5 in paths5:
        symbol = _infer_symbol(path5)
        symbols_seen.add(symbol)
        symbol_l = symbol.lower()

        path1 = repo_root / "datastore" / f"{symbol_l}_bars_1m_snapshot.jsonl"
        path1h = repo_root / "datastore" / f"{symbol_l}_bars_1h_snapshot.jsonl"
        path4h = repo_root / "datastore" / f"{symbol_l}_bars_4h_snapshot.jsonl"

        if not path1.exists():
            raise SystemExit(f"Не знайдено 1m снапшот: {path1}")
        if not path1h.exists():
            raise SystemExit(f"Не знайдено 1h снапшот: {path1h}")
        if not path4h.exists():
            raise SystemExit(f"Не знайдено 4h снапшот: {path4h}")

        df5 = _read_jsonl_tail(path5, int(args.limit_5m))
        df1 = _read_jsonl_tail(path1, int(args.limit_1m))
        df1h = _read_jsonl_tail(path1h, int(args.limit_1h))
        df4h = _read_jsonl_tail(path4h, int(args.limit_4h))
        if df5.empty or df1.empty:
            continue

        start = max(int(args.warmup), len(df5) - int(args.steps))

        for i in range(start, len(df5)):
            row5 = df5.iloc[i]
            end_ms = int(row5["open_time"]) + 5 * 60 * 1000 - 1

            f5 = df5.iloc[max(0, i - 320) : i + 1].copy()
            f1_hist = _slice_by_end_ms(df1, end_ms=end_ms, tail=1800)
            f1h_i = _slice_by_end_ms(df1h, end_ms=end_ms, tail=800)
            f4h_i = _slice_by_end_ms(df4h, end_ms=end_ms, tail=400)
            if len(f1_hist) < 80:
                continue

            checked_steps += 1

            ohlc_by_tf = {"5m": f5, "1m": f1_hist, "1h": f1h_i, "4h": f4h_i}
            ctx = _build_sessions_context(ohlc_by_tf=ohlc_by_tf, tf_primary="5m")

            snap = SmcInput(
                symbol=symbol, tf_primary="5m", ohlc_by_tf=ohlc_by_tf, context=ctx
            )
            hint = engine.process_snapshot(snap)
            ex = hint.execution
            if ex is None:
                continue

            meta = dict(ex.meta or {})
            if not bool(meta.get("in_play")):
                continue

            atr = meta.get("atr_ref")
            atr_f = float(atr) if isinstance(atr, (int, float)) else 0.0
            ref_kind: RefKind = _infer_ref_kind(meta)
            bias = (
                getattr(hint.structure, "bias", None)
                if hint.structure is not None
                else None
            )

            events = list(ex.execution_events or [])
            if not events:
                continue

            entry = float(f1_hist["close"].iloc[-1])

            fwd = df1[df1["open_time"] > int(end_ms)].iloc[:max_horizon].copy()
            if fwd.empty:
                continue

            for ev in events:
                et = str(getattr(ev, "event_type", ""))
                direction = str(getattr(ev, "direction", ""))
                if not et or direction not in ("LONG", "SHORT"):
                    continue

                align: AlignTag = _align_tag(
                    bias=str(bias) if bias is not None else None,
                    direction=direction,  # type: ignore[arg-type]
                )

                samples.append(
                    _EventSample(
                        symbol=symbol,
                        ref_kind=ref_kind,
                        align=align,
                        event_type=et,
                        direction=direction,  # type: ignore[arg-type]
                        entry=float(entry),
                        atr_ref=float(atr_f),
                        fwd=fwd[["high", "low"]].copy(),
                    )
                )

    def _winrate_for(counter: Counter[Outcome]) -> float | None:
        win = counter.get("WIN", 0)
        loss = counter.get("LOSS", 0)
        denom = win + loss
        if denom <= 0:
            return None
        return win / denom

    def _run_scenario(*, horizon: int, tp_atr: float, sl_atr: float) -> tuple[
        int,
        dict[tuple[str, str, str, str], Counter[Outcome]],
        dict[tuple[str, str, str, str], float],
        dict[tuple[str, str, str, str], float],
        dict[tuple[str, str, str, str], int],
        dict[tuple[str, str, str, str], int],
    ]:
        total_events = 0
        # key: (ref_kind, align, event_type, direction)
        outcomes: dict[tuple[str, str, str, str], Counter[Outcome]] = defaultdict(
            Counter
        )
        mfe_sums: dict[tuple[str, str, str, str], float] = defaultdict(float)
        mae_sums: dict[tuple[str, str, str, str], float] = defaultdict(float)
        mfe_n: dict[tuple[str, str, str, str], int] = defaultdict(int)
        mae_n: dict[tuple[str, str, str, str], int] = defaultdict(int)

        for s in samples:
            fwd = s.fwd.iloc[:horizon].copy()
            if fwd.empty:
                continue
            total_events += 1
            res = _eval_forward_path(
                direction=s.direction,
                entry=float(s.entry),
                atr=float(s.atr_ref),
                frame_fwd=fwd,
                tp_atr=float(tp_atr),
                sl_atr=float(sl_atr),
            )
            k = (s.ref_kind, s.align, s.event_type, s.direction)
            outcomes[k][res.outcome] += 1
            if res.mfe_atr is not None:
                mfe_sums[k] += float(res.mfe_atr)
                mfe_n[k] += 1
            if res.mae_atr is not None:
                mae_sums[k] += float(res.mae_atr)
                mae_n[k] += 1

        return total_events, outcomes, mfe_sums, mae_sums, mfe_n, mae_n

    def _expectancy_r(
        *,
        c: Counter[Outcome],
        tp_atr: float,
        sl_atr: float,
    ) -> float | None:
        denom = (
            int(c.get("WIN", 0))
            + int(c.get("LOSS", 0))
            + int(c.get("NO_HIT", 0))
            + int(c.get("BOTH_SAME_BAR", 0))
        )
        if denom <= 0:
            return None
        if sl_atr <= 0:
            return None
        r_win = float(tp_atr) / float(sl_atr)
        exp = (int(c.get("WIN", 0)) * r_win) - int(c.get("LOSS", 0))
        return float(exp) / float(denom)

    def _p(x: int, denom: int) -> str:
        return f"{(float(x) / float(denom)):.3f}" if denom > 0 else ""

    scenarios: list[tuple[int, float, float]] = []
    for h in grid_horizons:
        for tp in grid_tps:
            for sl in grid_sls:
                scenarios.append((int(h), float(tp), float(sl)))

    symbols_s = ",".join(sorted(symbols_seen)) if symbols_seen else "-"
    print(f"symbols={symbols_s}")
    print(f"checked_steps={checked_steps}")
    print(f"event_samples={len(samples)}")
    if len(scenarios) == 1:
        h, tp, sl = scenarios[0]
        total_events, outcomes, mfe_sums, mae_sums, mfe_n, mae_n = _run_scenario(
            horizon=h, tp_atr=tp, sl_atr=sl
        )
        keys_sorted = sorted(outcomes.keys(), key=lambda x: (x[0], x[1], x[2], x[3]))
        print(f"horizon_bars={h} tp_atr={tp} sl_atr={sl}")
        print(f"total_events={total_events}")
        for k in keys_sorted:
            c = outcomes[k]
            wr = _winrate_for(c)
            wr_s = f"{wr:.3f}" if wr is not None else "-"
            mfe_avg = (mfe_sums[k] / mfe_n[k]) if mfe_n[k] else None
            mae_avg = (mae_sums[k] / mae_n[k]) if mae_n[k] else None
            mfe_s = f"{mfe_avg:.2f}" if mfe_avg is not None else "-"
            mae_s = f"{mae_avg:.2f}" if mae_avg is not None else "-"
            denom = (
                int(c.get("WIN", 0))
                + int(c.get("LOSS", 0))
                + int(c.get("NO_HIT", 0))
                + int(c.get("BOTH_SAME_BAR", 0))
            )
            exp_r = _expectancy_r(c=c, tp_atr=float(tp), sl_atr=float(sl))
            exp_s = f"{exp_r:.3f}" if exp_r is not None else "-"
            print(
                f"{k[0]}/{k[1]} {k[2]} {k[3]}: n={denom} win={c.get('WIN',0)} loss={c.get('LOSS',0)} no_hit={c.get('NO_HIT',0)} both={c.get('BOTH_SAME_BAR',0)} no_atr={c.get('NO_ATR',0)} p_win={_p(int(c.get('WIN',0)), denom)} p_loss={_p(int(c.get('LOSS',0)), denom)} p_no_hit={_p(int(c.get('NO_HIT',0)), denom)} exp_R={exp_s} winrate={wr_s} mfe_atr={mfe_s} mae_atr={mae_s}"
            )

    # Markdown репорт (single або grid)
    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = repo_root / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now(tz=UTC)
        lines: list[str] = []
        lines.append("# QA статистика Stage5 execution")
        lines.append("")
        lines.append(f"Дата (UTC): {now.isoformat()}")
        lines.append("")
        lines.append(f"Символи: **{symbols_s}**")
        lines.append("")
        lines.append("## Параметри")
        lines.append("")
        lines.append(f"- Снапшоти 5m: **{len(paths5)}**")
        lines.append(f"- checked_steps: **{checked_steps}**")
        lines.append(f"- event_samples: **{len(samples)}**")
        lines.append("")

        # Вивід по сценаріях
        for h, tp, sl in scenarios:
            total_events, outcomes, mfe_sums, mae_sums, mfe_n, mae_n = _run_scenario(
                horizon=int(h), tp_atr=float(tp), sl_atr=float(sl)
            )
            keys_sorted = sorted(
                outcomes.keys(), key=lambda x: (x[0], x[1], x[2], x[3])
            )

            lines.append(f"## Сценарій: horizon={h}m, TP={tp} ATR, SL={sl} ATR")
            lines.append("")
            lines.append(f"- total_events: **{total_events}**")
            lines.append("")
            lines.append(
                "| ref | align | event_type | dir | n | win | loss | no_hit | both_same_bar | no_atr | p_win | p_loss | p_no_hit | p_both | expectancy_R | winrate(win/(win+loss)) | avg_MFE(ATR) | avg_MAE(ATR) |"
            )
            lines.append(
                "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
            )

            if not keys_sorted:
                lines.append(
                    "| - | - | - | - | 0 | 0 | 0 | 0 | 0 | 0 |  |  |  |  |  |  |  |  |"
                )
                lines.append("")
                continue

            for k in keys_sorted:
                ref_kind_s, align_s, et, direction = k
                c = outcomes[k]
                wr = _winrate_for(c)
                wr_v = f"{wr:.3f}" if wr is not None else ""
                denom = (
                    int(c.get("WIN", 0))
                    + int(c.get("LOSS", 0))
                    + int(c.get("NO_HIT", 0))
                    + int(c.get("BOTH_SAME_BAR", 0))
                )
                exp_r = _expectancy_r(c=c, tp_atr=float(tp), sl_atr=float(sl))
                exp_v = f"{exp_r:.3f}" if exp_r is not None else ""
                mfe_avg = (mfe_sums[k] / mfe_n[k]) if mfe_n[k] else None
                mae_avg = (mae_sums[k] / mae_n[k]) if mae_n[k] else None
                mfe_v = f"{mfe_avg:.2f}" if mfe_avg is not None else ""
                mae_v = f"{mae_avg:.2f}" if mae_avg is not None else ""
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            str(ref_kind_s),
                            str(align_s),
                            str(et),
                            str(direction),
                            str(denom),
                            str(c.get("WIN", 0)),
                            str(c.get("LOSS", 0)),
                            str(c.get("NO_HIT", 0)),
                            str(c.get("BOTH_SAME_BAR", 0)),
                            str(c.get("NO_ATR", 0)),
                            _p(int(c.get("WIN", 0)), denom),
                            _p(int(c.get("LOSS", 0)), denom),
                            _p(int(c.get("NO_HIT", 0)), denom),
                            _p(int(c.get("BOTH_SAME_BAR", 0)), denom),
                            exp_v,
                            wr_v,
                            mfe_v,
                            mae_v,
                        ]
                    )
                    + " |"
                )
            lines.append("")

        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(str(out_path))

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
