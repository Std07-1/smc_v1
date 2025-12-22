"""QA: статистика довіри до Stage6 (4.2 vs 4.3) на реальних снапшотах.

Stage6 — це «технічний розбір», а не сигнал. Але щоб зрозуміти, чи можна
йому довіряти, потрібні:
- частоти сценаріїв (4_2/4_3/UNCLEAR);
- гейти (чому UNCLEAR), джерела HTF bias;
- стабільність після анти-фліпу (TTL/confirm/switch_delta);
- проста пост-фактум перевірка «напрям узгодився з рухом» на горизонті.

Цей скрипт:
- бере 5m *_snapshot.jsonl;
- автоматично підтягує 1m/1h/4h снапшоти того ж символа (з `datastore/`);
- проганяє SMC-core по останніх `--steps` 5m кроках;
- застосовує Stage6 анти-фліп через `app.SmcStateManager.apply_stage6_hysteresis`;
- рахує win/loss за правилами TP/SL в ATR на 1m горизонті (опційно).

Приклад (PowerShell):
; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m tools.qa_stage6_scenario_stats \
    --path datastore/xauusd_bars_5m_snapshot.jsonl --steps 240 --horizon-bars 60 \
    --tp-atr 1.0 --sl-atr 1.0 --out reports/stage6_stats_xauusd.md

Примітки:
- Це QA для довіри до класифікації, не бектест торгової системи.
- Якщо 1m снапшоту немає — скрипт зможе порахувати частоти/гейти/фліпи,
  але не зможе порахувати TP/SL outcome.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd

Outcome = Literal["WIN", "LOSS", "NO_HIT", "BOTH_SAME_BAR", "NO_ATR", "NO_1M"]


def _clip_list(values: Any, n: int) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for x in values[: max(0, int(n))]:
        out.append(str(x))
    return out


def _pick_levels(meta_any: Any) -> dict[str, float]:
    if not isinstance(meta_any, dict):
        return {}
    out: dict[str, float] = {}
    for k in (
        "range_high",
        "range_low",
        "range_eq",
        "hold_level_up",
        "hold_level_dn",
        "swept_level",
        "htf_dr_high",
        "htf_dr_low",
        "htf_dr_mid",
        "htf_atr14",
    ):
        v = _try_float(meta_any.get(k))
        if v is not None:
            out[str(k)] = float(v)
    return out


def _try_float(x: Any) -> float | None:
    """Безпечно конвертує значення до float.

    Потрібно, бо `dict.get()` і pandas/numpy інколи повертають типи, які Pylance
    не може звузити до `float` (наприклад, `Scalar`).
    """

    try:
        return float(x)
    except Exception:
        return None


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


def _atr14(frame: pd.DataFrame) -> float | None:
    if frame is None or frame.empty:
        return None
    for c in ("high", "low", "close"):
        if c not in frame.columns:
            return None
    h = pd.to_numeric(frame["high"], errors="coerce")
    lo = pd.to_numeric(frame["low"], errors="coerce")
    cl = pd.to_numeric(frame["close"], errors="coerce")
    if len(frame) < 16:
        return None
    prev_close = cl.shift(1)
    tr = pd.concat(
        [(h - lo).abs(), (h - prev_close).abs(), (lo - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    try:
        v = float(atr)
    except Exception:
        return None
    if v > 0 and v == v and v not in (float("inf"), float("-inf")):
        return v
    return None


@dataclass(frozen=True, slots=True)
class _StepRow:
    idx: int
    ts: str
    close_5m: float
    scenario_raw: str
    conf_raw: float | None
    gates: str
    unclear_reason: str
    htf_bias: str
    htf_src: str
    scenario_stable: str
    conf_stable: float
    pending: str
    flip: str
    outcome_raw: Outcome
    outcome_stable: Outcome


def _eval_outcome(
    *,
    direction: Literal["LONG", "SHORT"],
    entry: float,
    atr: float | None,
    fwd_1m: pd.DataFrame,
    tp_atr: float,
    sl_atr: float,
) -> Outcome:
    if fwd_1m is None or fwd_1m.empty:
        return "NO_HIT"
    if atr is None or atr <= 0:
        return "NO_ATR"

    tp_dist = float(tp_atr) * float(atr)
    sl_dist = float(sl_atr) * float(atr)

    def _to_float_required(x: Any) -> float:
        # У реальних снапшотах high/low мають бути числа.
        return float(x)

    if direction == "LONG":
        tp = float(entry) + tp_dist
        sl = float(entry) - sl_dist
        for r in fwd_1m.itertuples(index=False):
            h = _to_float_required(r.high)
            lo = _to_float_required(r.low)
            hit_tp = h >= tp
            hit_sl = lo <= sl
            if hit_tp and hit_sl:
                return "BOTH_SAME_BAR"
            if hit_tp:
                return "WIN"
            if hit_sl:
                return "LOSS"
        return "NO_HIT"

    # SHORT
    tp = float(entry) - tp_dist
    sl = float(entry) + sl_dist
    for r in fwd_1m.itertuples(index=False):
        h = _to_float_required(r.high)
        lo = _to_float_required(r.low)
        hit_tp = lo <= tp
        hit_sl = h >= sl
        if hit_tp and hit_sl:
            return "BOTH_SAME_BAR"
        if hit_tp:
            return "WIN"
        if hit_sl:
            return "LOSS"
    return "NO_HIT"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="QA статистика Stage6 (4.2 vs 4.3) на снапшотах.",
    )
    p.add_argument(
        "--path",
        required=True,
        help="Шлях до 5m *_snapshot.jsonl (або ім'я з datastore/).",
    )
    p.add_argument(
        "--steps",
        type=int,
        default=240,
        help="Скільки останніх 5m-кроків прогнати (після warmup).",
    )
    p.add_argument(
        "--warmup",
        type=int,
        default=220,
        help="Скільки 5m барів пропустити на старті (щоб було достатньо історії).",
    )

    p.add_argument("--limit-5m", type=int, default=900, help="Tail 5m bars.")
    p.add_argument("--limit-1m", type=int, default=9000, help="Tail 1m bars.")
    p.add_argument("--limit-1h", type=int, default=3000, help="Tail 1h bars.")
    p.add_argument("--limit-4h", type=int, default=2000, help="Tail 4h bars.")

    p.add_argument(
        "--horizon-bars",
        type=int,
        default=60,
        help="Горизонт оцінки (1m барів) для TP/SL outcome.",
    )
    p.add_argument("--tp-atr", type=float, default=1.0, help="TP у множниках ATR (1m).")
    p.add_argument("--sl-atr", type=float, default=1.0, help="SL у множниках ATR (1m).")

    p.add_argument(
        "--out",
        default=None,
        help="Markdown-репорт у reports/ (опційно).",
    )

    # Stage6 анти-фліп (має збігатись з прод-налаштуваннями або бути явним у QA)
    p.add_argument("--ttl-sec", type=int, default=180, help="Stage6 TTL (сек).")
    p.add_argument("--confirm-bars", type=int, default=2, help="Stage6 confirm bars.")
    p.add_argument(
        "--switch-delta", type=float, default=0.08, help="Stage6 switch delta."
    )

    p.add_argument(
        "--max-rows",
        type=int,
        default=180,
        help="Скільки останніх кроків включити в таблицю журналу у markdown.",
    )
    p.add_argument(
        "--exemplars",
        type=int,
        default=12,
        help="Скільки прикладів (flips/UNCLEAR) додати у markdown для пояснення 'чому'.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _ensure_repo_on_syspath()

    from app.smc_state_manager import SmcStateManager
    from smc_core import SmcCoreConfig, SmcCoreEngine, SmcInput
    from smc_core.input_adapter import _build_sessions_context
    from smc_core.serializers import to_plain_smc_hint

    args = parse_args(argv)

    path5 = _resolve_snapshot_path(args.path)
    if not path5.exists():
        raise SystemExit(f"Не знайдено файл: {path5}")

    symbol = _infer_symbol(path5)
    repo_root = Path(__file__).resolve().parents[1]
    symbol_l = symbol.lower()

    path1 = repo_root / "datastore" / f"{symbol_l}_bars_1m_snapshot.jsonl"
    path1h = repo_root / "datastore" / f"{symbol_l}_bars_1h_snapshot.jsonl"
    path4h = repo_root / "datastore" / f"{symbol_l}_bars_4h_snapshot.jsonl"

    df5 = _read_jsonl_tail(path5, int(args.limit_5m))
    df1 = (
        _read_jsonl_tail(path1, int(args.limit_1m))
        if path1.exists()
        else pd.DataFrame()
    )
    df1h = (
        _read_jsonl_tail(path1h, int(args.limit_1h))
        if path1h.exists()
        else pd.DataFrame()
    )
    df4h = (
        _read_jsonl_tail(path4h, int(args.limit_4h))
        if path4h.exists()
        else pd.DataFrame()
    )

    if df5.empty:
        raise SystemExit("Порожні дані (5m).")

    # Execution для Stage6 QA не потрібен — вимикаємо для швидкості.
    engine = SmcCoreEngine(cfg=SmcCoreConfig(exec_enabled=False))

    sm = SmcStateManager(initial_assets=[symbol])

    steps = int(args.steps)
    warmup = int(args.warmup)
    start = max(warmup, len(df5) - steps)

    # counters
    raw_counts: Counter[str] = Counter()
    stable_counts: Counter[str] = Counter()
    gate_counts: Counter[str] = Counter()
    unclear_reason_counts: Counter[str] = Counter()
    bias_src_counts: Counter[str] = Counter()
    bias_counts: Counter[str] = Counter()
    flip_total = 0
    flip_pair_counts: Counter[str] = Counter()
    flip_reason_counts: Counter[str] = Counter()
    flip_pair_reason_counts: Counter[str] = Counter()
    hard_invalidation_count = 0

    exemplars: list[dict[str, Any]] = []
    exemplars_raw43_blocked: list[dict[str, Any]] = []

    raw43_total = 0
    raw43_stable_counts: Counter[str] = Counter()
    raw43_pending_counts: Counter[str] = Counter()

    # outcomes
    outcome_raw: Counter[tuple[str, Outcome]] = Counter()  # (scenario_id, outcome)
    outcome_stable: Counter[tuple[str, Outcome]] = Counter()

    rows: list[_StepRow] = []

    checked = 0
    horizon = max(1, int(args.horizon_bars))

    for i in range(start, len(df5)):
        row5 = df5.iloc[i]
        end_ms = int(row5["open_time"]) + 5 * 60 * 1000 - 1

        f5 = df5.iloc[max(0, i - 320) : i + 1].copy()
        f1_hist = (
            _slice_by_end_ms(df1, end_ms=end_ms, tail=1800)
            if not df1.empty
            else pd.DataFrame()
        )
        f1h_i = (
            _slice_by_end_ms(df1h, end_ms=end_ms, tail=800)
            if not df1h.empty
            else pd.DataFrame()
        )
        f4h_i = (
            _slice_by_end_ms(df4h, end_ms=end_ms, tail=400)
            if not df4h.empty
            else pd.DataFrame()
        )

        checked += 1

        ohlc_by_tf: dict[str, pd.DataFrame] = {"5m": f5}
        if not f1_hist.empty:
            ohlc_by_tf["1m"] = f1_hist
        if not f1h_i.empty:
            ohlc_by_tf["1h"] = f1h_i
        if not f4h_i.empty:
            ohlc_by_tf["4h"] = f4h_i

        ctx = _build_sessions_context(ohlc_by_tf=ohlc_by_tf, tf_primary="5m")

        snap = SmcInput(
            symbol=symbol, tf_primary="5m", ohlc_by_tf=ohlc_by_tf, context=ctx
        )
        hint = engine.process_snapshot(snap)

        plain_hint = to_plain_smc_hint(hint) or {}

        # raw (з signals)
        raw_signal = None
        for s in plain_hint.get("signals") or []:
            if isinstance(s, dict) and str(s.get("type") or "").upper() == "SCENARIO":
                raw_signal = s
                break

        raw_id = "UNCLEAR"
        raw_conf: float | None = None
        raw_dir = "NEUTRAL"
        gates_s = "-"
        unclear_reason_s = "-"
        htf_bias = "UNKNOWN"
        htf_src = "-"

        if isinstance(raw_signal, dict):
            meta_any = raw_signal.get("meta")
            meta: dict[str, Any] = meta_any if isinstance(meta_any, dict) else {}

            telemetry_any = meta.get("telemetry")
            telemetry: dict[str, Any] = (
                telemetry_any if isinstance(telemetry_any, dict) else {}
            )

            raw_id = str(meta.get("scenario_id") or "UNCLEAR")
            raw_dir = str(raw_signal.get("direction") or "NEUTRAL")

            raw_conf = _try_float(raw_signal.get("confidence"))

            gates_any = telemetry.get("gates")
            gates: list[Any] = gates_any if isinstance(gates_any, list) else []
            gates_s = ",".join([str(x) for x in gates]) if gates else "-"

            unclear_reason = telemetry.get("unclear_reason")
            if isinstance(unclear_reason, str) and unclear_reason.strip():
                unclear_reason_s = str(unclear_reason).strip()
                if raw_id.upper() == "UNCLEAR":
                    unclear_reason_counts[unclear_reason_s.upper()] += 1
            htf_bias = str(telemetry.get("htf_bias") or "UNKNOWN")
            htf_src = str(telemetry.get("htf_bias_src") or "-")

            for g in gates:
                gate_counts[str(g)] += 1
            bias_counts[htf_bias] += 1
            bias_src_counts[htf_src] += 1

        raw_counts[raw_id] += 1

        # stable (анти-фліп)
        # Важливо: `SmcStateManager` очікує `now_unix` в секундах (unix time),
        # інакше TTL у секундах буде спотворений.
        now_unix = float(end_ms) / 1000.0
        stage6_stats = sm.apply_stage6_hysteresis(
            symbol,
            plain_hint,
            ttl_sec=int(args.ttl_sec),
            confirm_bars=int(args.confirm_bars),
            switch_delta=float(args.switch_delta),
            now_unix=now_unix,
        )

        stable_id = str(stage6_stats.get("scenario_id") or "UNCLEAR")
        stable_counts[stable_id] += 1

        if stable_id.upper() == "UNCLEAR":
            st_reason = stage6_stats.get("scenario_unclear_reason")
            if isinstance(st_reason, str) and st_reason.strip():
                unclear_reason_counts[str(st_reason).strip().upper()] += 1

        flip = stage6_stats.get("scenario_flip")
        flip_s = "-"
        if isinstance(flip, dict):
            flip_total += 1
            flip_s = f"{flip.get('from')}→{flip.get('to')}"
            flip_pair_counts[flip_s] += 1
            r = flip.get("reason")
            if isinstance(r, str) and r.strip():
                rr = r.strip()
                flip_reason_counts[rr] += 1
                flip_pair_reason_counts[f"{flip_s}::{rr}"] += 1
                if rr.startswith("hard_invalidation:"):
                    hard_invalidation_count += 1

        pending_id = stage6_stats.get("scenario_pending_id")
        pending_count = int(stage6_stats.get("scenario_pending_count") or 0)
        pending_s = "-" if not pending_id else f"{pending_id}({pending_count})"

        if raw_id == "4_3":
            raw43_total += 1
            raw43_stable_counts[stable_id] += 1
            raw43_pending_counts[str(pending_s)] += 1

            # Окремі приклади: raw=4_3, але stable!=4_3.
            # Це ключова діагностика для P1: чи не «затискаємо» 4_3 надто сильно.
            if stable_id != "4_3" and len(exemplars_raw43_blocked) < max(
                0, min(6, int(args.exemplars))
            ):
                exemplars_raw43_blocked.append(
                    {
                        "idx": int(checked),
                        "ts": str(ts_s),
                        "close_5m": float(close_5m),
                        "raw": {
                            "id": str(stage6_stats.get("scenario_raw_id") or raw_id),
                            "conf": _try_float(
                                stage6_stats.get("scenario_raw_confidence")
                            ),
                            "conf_base": _try_float(
                                stage6_stats.get("scenario_raw_confidence_base")
                            ),
                            "gates": _clip_list(
                                stage6_stats.get("scenario_raw_gates"), 8
                            ),
                            "unclear_reason": str(
                                stage6_stats.get("scenario_raw_unclear_reason")
                                or unclear_reason_s
                                or "-"
                            ),
                            "why": _clip_list(stage6_stats.get("scenario_raw_why"), 6),
                            "key_levels": _pick_levels(
                                stage6_stats.get("scenario_raw_key_levels")
                            ),
                        },
                        "stable": {
                            "id": str(stage6_stats.get("scenario_id") or stable_id),
                            "conf": _try_float(stage6_stats.get("scenario_confidence")),
                            "pending": str(pending_s),
                            "unclear_reason": str(
                                stage6_stats.get("scenario_unclear_reason") or "-"
                            ),
                        },
                        "flip": flip if isinstance(flip, dict) else None,
                    }
                )

        # outcome
        out_raw: Outcome = "NO_1M"
        out_stable: Outcome = "NO_1M"
        if (
            not df1.empty
            and "high" in df1.columns
            and "low" in df1.columns
            and not f1_hist.empty
        ):
            entry = float(f1_hist["close"].iloc[-1])
            atr = _atr14(f1_hist)
            fwd = df1[df1["open_time"] > int(end_ms)].iloc[:horizon].copy()
            if raw_id in ("4_2", "4_3") and raw_dir in ("LONG", "SHORT"):
                out_raw = _eval_outcome(
                    direction=raw_dir,  # type: ignore[arg-type]
                    entry=entry,
                    atr=atr,
                    fwd_1m=fwd[["high", "low"]].copy() if not fwd.empty else fwd,
                    tp_atr=float(args.tp_atr),
                    sl_atr=float(args.sl_atr),
                )
                outcome_raw[(raw_id, out_raw)] += 1
            else:
                out_raw = "NO_HIT"

            stable_dir = str(stage6_stats.get("scenario_direction") or "NEUTRAL")
            if stable_id in ("4_2", "4_3") and stable_dir in ("LONG", "SHORT"):
                out_stable = _eval_outcome(
                    direction=stable_dir,  # type: ignore[arg-type]
                    entry=entry,
                    atr=atr,
                    fwd_1m=fwd[["high", "low"]].copy() if not fwd.empty else fwd,
                    tp_atr=float(args.tp_atr),
                    sl_atr=float(args.sl_atr),
                )
                outcome_stable[(stable_id, out_stable)] += 1
            else:
                out_stable = "NO_HIT"

        ts = f5["timestamp"].iloc[-1]
        ts_s = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        close_5m = float(f5["close"].iloc[-1])

        rows.append(
            _StepRow(
                idx=checked,
                ts=ts_s,
                close_5m=close_5m,
                scenario_raw=raw_id,
                conf_raw=raw_conf,
                gates=gates_s,
                unclear_reason=unclear_reason_s,
                htf_bias=htf_bias,
                htf_src=htf_src,
                scenario_stable=stable_id,
                conf_stable=float(stage6_stats.get("scenario_confidence") or 0.0),
                pending=pending_s,
                flip=flip_s,
                outcome_raw=out_raw,
                outcome_stable=out_stable,
            )
        )

        # Exemplars: щоб у звіті було видно «чому» на фліпах і UNCLEAR.
        want_ex = False
        if isinstance(flip, dict):
            want_ex = True
        if raw_id.upper() == "UNCLEAR" or stable_id.upper() == "UNCLEAR":
            want_ex = True
        if want_ex and len(exemplars) < max(0, int(args.exemplars)):
            exemplars.append(
                {
                    "idx": int(checked),
                    "ts": str(ts_s),
                    "close_5m": float(close_5m),
                    "raw": {
                        "id": str(stage6_stats.get("scenario_raw_id") or raw_id),
                        "conf": _try_float(stage6_stats.get("scenario_raw_confidence")),
                        "conf_base": _try_float(
                            stage6_stats.get("scenario_raw_confidence_base")
                        ),
                        "gates": _clip_list(stage6_stats.get("scenario_raw_gates"), 8),
                        "unclear_reason": str(
                            stage6_stats.get("scenario_raw_unclear_reason")
                            or unclear_reason_s
                            or "-"
                        ),
                        "why": _clip_list(stage6_stats.get("scenario_raw_why"), 6),
                        "key_levels": _pick_levels(
                            stage6_stats.get("scenario_raw_key_levels")
                        ),
                    },
                    "stable": {
                        "id": str(stage6_stats.get("scenario_id") or stable_id),
                        "conf": _try_float(stage6_stats.get("scenario_confidence")),
                        "pending": str(pending_s),
                        "unclear_reason": str(
                            stage6_stats.get("scenario_unclear_reason") or "-"
                        ),
                    },
                    "flip": flip if isinstance(flip, dict) else None,
                }
            )

    def _rate(num: int, den: int) -> float:
        return 0.0 if den <= 0 else num / den

    raw_total = sum(raw_counts.values())
    stable_total = sum(stable_counts.values())

    def _winrate(
        outcomes: Counter[tuple[str, Outcome]], scenario_id: str
    ) -> float | None:
        win = outcomes.get((scenario_id, "WIN"), 0)
        loss = outcomes.get((scenario_id, "LOSS"), 0)
        denom = win + loss
        if denom <= 0:
            return None
        return win / denom

    wr_raw_42 = _winrate(outcome_raw, "4_2")
    wr_raw_43 = _winrate(outcome_raw, "4_3")
    wr_st_42 = _winrate(outcome_stable, "4_2")
    wr_st_43 = _winrate(outcome_stable, "4_3")

    now = datetime.now(tz=UTC)
    ts_tag = now.strftime("%Y%m%d_%H%M%S")

    out_path: Path
    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = repo_root / out_path
    else:
        out_path = repo_root / "reports" / f"stage6_stats_{symbol_l}_{ts_tag}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # markdown
    lines: list[str] = []
    lines.append(f"# Stage6 QA: {symbol} (5m primary)")
    lines.append("")
    lines.append(f"- кроків (перевірено): {checked}")
    lines.append(f"- raw: {dict(raw_counts)}")
    lines.append(f"- stable(after hysteresis): {dict(stable_counts)}")
    lines.append(f"- flips: {flip_total}")
    lines.append("")

    lines.append("## Flips")
    lines.append("")
    lines.append(f"- flip_pairs: {dict(flip_pair_counts)}")
    lines.append(f"- flip_reasons: {dict(flip_reason_counts)}")
    lines.append(f"- hard_invalidation_count: {int(hard_invalidation_count)}")
    lines.append(f"- flip_pairs_by_reason: {dict(flip_pair_reason_counts)}")
    lines.append("")

    lines.append("## 4_3 кандидати (raw)")
    lines.append("")
    lines.append(f"- raw_4_3_total: {raw43_total}")
    lines.append(f"- stable_given_raw_4_3: {dict(raw43_stable_counts)}")
    lines.append(f"- pending_given_raw_4_3: {dict(raw43_pending_counts)}")
    lines.append("")

    if raw_total > 0:
        lines.append("## Частоти")
        lines.append("")
        lines.append(
            f"- raw UNCLEAR rate: {_rate(raw_counts.get('UNCLEAR', 0), raw_total):.2%}"
        )
        lines.append(
            f"- stable UNCLEAR rate: {_rate(stable_counts.get('UNCLEAR', 0), stable_total):.2%}"
        )
        lines.append("")

    lines.append("## Гейти")
    lines.append("")
    lines.append(f"- gates: {dict(gate_counts)}")
    lines.append("")

    lines.append("## UNCLEAR reasons")
    lines.append("")
    lines.append(f"- unclear_reason_counts: {dict(unclear_reason_counts)}")
    lines.append("")

    if exemplars:
        lines.append("## Приклади (exemplars)")
        lines.append("")
        lines.append(
            "Ціль: мати 8–12 конкретних кейсів для ручної валідації: чому відбувся flip/UNCLEAR."
        )
        lines.append("")
        for ex in exemplars:
            flip_any = ex.get("flip")
            flip_reason = "-"
            if isinstance(flip_any, dict):
                flip_reason = str(flip_any.get("reason") or "-")
            lines.append(
                f"- #{ex.get('idx')} ts={ex.get('ts')} close={ex.get('close_5m')} "
                f"raw={ex.get('raw',{}).get('id')} stable={ex.get('stable',{}).get('id')} "
                f"flip_reason={flip_reason}"
            )
            raw = ex.get("raw") if isinstance(ex.get("raw"), dict) else {}
            st = ex.get("stable") if isinstance(ex.get("stable"), dict) else {}
            lines.append(
                "  - raw: "
                f"conf={raw.get('conf')} base={raw.get('conf_base')} "
                f"gates={raw.get('gates')} reason={raw.get('unclear_reason')}"
            )
            lines.append(f"  - raw_why: {raw.get('why')}")
            lines.append(f"  - raw_key_levels: {raw.get('key_levels')}")
            lines.append(
                "  - stable: "
                f"conf={st.get('conf')} pending={st.get('pending')} reason={st.get('unclear_reason')}"
            )
        lines.append("")

    if exemplars_raw43_blocked:
        lines.append("## Приклади: raw=4_3, але stable≠4_3")
        lines.append("")
        lines.append(
            "Ціль: швидко зрозуміти, чому 4_3 не закріплюється як stable (pending/TTL/confirm або інші причини)."
        )
        lines.append("")
        for ex in exemplars_raw43_blocked:
            flip_any = ex.get("flip")
            flip_reason = "-"
            if isinstance(flip_any, dict):
                flip_reason = str(flip_any.get("reason") or "-")
            lines.append(
                f"- #{ex.get('idx')} ts={ex.get('ts')} close={ex.get('close_5m')} "
                f"raw={ex.get('raw',{}).get('id')} stable={ex.get('stable',{}).get('id')} "
                f"flip_reason={flip_reason}"
            )
            raw = ex.get("raw") if isinstance(ex.get("raw"), dict) else {}
            st = ex.get("stable") if isinstance(ex.get("stable"), dict) else {}
            lines.append(
                "  - raw: "
                f"conf={raw.get('conf')} base={raw.get('conf_base')} "
                f"gates={raw.get('gates')} reason={raw.get('unclear_reason')}"
            )
            lines.append(f"  - raw_why: {raw.get('why')}")
            lines.append(f"  - raw_key_levels: {raw.get('key_levels')}")
            lines.append(
                "  - stable: "
                f"conf={st.get('conf')} pending={st.get('pending')} reason={st.get('unclear_reason')}"
            )
        lines.append("")

    lines.append("## HTF bias")
    lines.append("")
    lines.append(f"- htf_bias: {dict(bias_counts)}")
    lines.append(f"- htf_bias_src: {dict(bias_src_counts)}")
    lines.append("")

    lines.append("## Пост-фактум (TP/SL на 1m)")
    lines.append("")
    if df1.empty:
        lines.append("- 1m snapshot відсутній → outcome не рахувався.")
    else:
        lines.append(f"- horizon_bars(1m): {horizon}")
        lines.append(
            f"- tp_atr/sl_atr: {float(args.tp_atr):.2f}/{float(args.sl_atr):.2f}"
        )
        lines.append(
            f"- winrate raw 4_2: {('-' if wr_raw_42 is None else f'{wr_raw_42:.2%}')}"
        )
        lines.append(
            f"- winrate raw 4_3: {('-' if wr_raw_43 is None else f'{wr_raw_43:.2%}')}"
        )
        lines.append(
            f"- winrate stable 4_2: {('-' if wr_st_42 is None else f'{wr_st_42:.2%}')}"
        )
        lines.append(
            f"- winrate stable 4_3: {('-' if wr_st_43 is None else f'{wr_st_43:.2%}')}"
        )

    # journal table
    lines.append("")
    lines.append("## Журнал (останні кроки)")
    lines.append("")
    lines.append(
        "| # | ts | close(5m) | raw | conf | gates | reason | htf | src | stable | conf_s | pending | flip | out_raw | out_stable |"
    )
    lines.append(
        "|---:|---|---:|:---:|---:|---|---|:---:|---|:---:|---:|---|---|:---:|:---:|"
    )

    tail_n = max(1, int(args.max_rows))
    for r in rows[-tail_n:]:
        conf_s = "-" if r.conf_raw is None else f"{r.conf_raw:.2f}"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(r.idx),
                    str(r.ts).replace("|", "\\|"),
                    f"{r.close_5m:.2f}",
                    r.scenario_raw,
                    conf_s,
                    r.gates.replace("|", "\\|"),
                    r.unclear_reason.replace("|", "\\|"),
                    r.htf_bias,
                    r.htf_src.replace("|", "\\|"),
                    r.scenario_stable,
                    f"{r.conf_stable:.2f}",
                    r.pending.replace("|", "\\|"),
                    r.flip.replace("|", "\\|"),
                    r.outcome_raw,
                    r.outcome_stable,
                ]
            )
            + " |"
        )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Stage6 QA готово: {out_path}")
    print(
        f"raw_counts={dict(raw_counts)} stable_counts={dict(stable_counts)} flips={flip_total}"
    )
    if not df1.empty:
        print(f"winrate_raw_4_2={wr_raw_42} winrate_raw_4_3={wr_raw_43}")
        print(f"winrate_stable_4_2={wr_st_42} winrate_stable_4_3={wr_st_43}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
