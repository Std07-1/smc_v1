"""Replay/QA: прогін снапшоту бар-за-баром з публікацією у UI_v2 viewer.

Що робить:
- читає *_snapshot.jsonl (наприклад datastore/xauusd_bars_5m_snapshot.jsonl);
- бере останні N барів (типово 800);
- проганяє SMC-core інкрементально (1..N) і на кожному кроці:
  - будує `SmcViewerState` через `UI_v2.viewer_state_builder.build_viewer_state`;
  - пише snapshot у Redis (ключ `REDIS_SNAPSHOT_KEY_SMC_VIEWER` або override);
  - публікує update у канал `REDIS_CHANNEL_SMC_VIEWER_EXTENDED` (щоб WS-клієнти оновлювались).

Навіщо:
- Коли ринок закритий, ти можеш відтворити останні 300–800 барів і «побачити»
  FVG/POI/події на графіку, тестуючи нові пороги/логіку.

Типовий сценарій (PowerShell):
1) Підняти UI_v2 offline:
   ; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" tools/run_ui_v2_offline.py
2) Запустити replay:
   ; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" tools/replay_snapshot_to_viewer.py --path datastore/xauusd_bars_5m_snapshot.jsonl --limit 800 --sleep-ms 80
3) Відкрити UI:
   http://127.0.0.1:8080/?symbol=XAUUSD

Примітки:
- Це QA-режим. Він не замінює live і не враховує спред/комісії.
- Для "прискорення" зменшуй `--sleep-ms` (0 => максимально швидко).

TV-like replay (максимально близько до live):
- Таймлайн може бути 1m (щоб «бачити як з'являється кожна свічка»), але SMC compute
    тригериться тільки на закритті primary TF (типово 5m).
- На кожному кроці ми *не* використовуємо lookahead: в кадр попадають лише бари,
    які на той момент закриті (close_time <= now_ms).
- Вхід для SMC будуємо через `smc_core.input_adapter.build_smc_input_from_frames`,
    щоб нормалізація та session context максимально відповідали live.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd


def _ensure_repo_on_syspath() -> None:
    """Гарантує імпорти з кореня репо при запуску як скрипта.

    У Windows запуск `python tools/xxx.py` не додає корінь проєкту в sys.path,
    тому локальні імпорти (`app`, `config`, `smc_core`, `UI_v2`) можуть не
    знаходитися.
    """

    repo_root = Path(__file__).resolve().parents[1]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def _resolve_snapshot_path(raw: str) -> Path:
    """Резолвить шлях до *_snapshot.jsonl максимально дружньо.

    Дозволяє передавати:
    - повний шлях;
    - шлях від кореня репо;
    - просто ім'я файлу (тоді шукаємо в `datastore/`).
    """

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


logger = logging.getLogger("tools.replay_snapshot_to_viewer")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    try:  # pragma: no cover - опціонально
        from rich.logging import RichHandler

        logger.addHandler(RichHandler(rich_tracebacks=True))
    except Exception:
        logger.addHandler(logging.StreamHandler())


def _infer_symbol_tf(path: Path) -> tuple[str, str]:
    name = path.name.lower()
    parts = name.split("_bars_")
    if len(parts) == 2:
        symbol = parts[0]
        tf = parts[1].split("_snapshot")[0]
        return symbol.upper(), tf
    return "UNKNOWN", "5m"


def _tf_ms(tf: str) -> int:
    tf_norm = str(tf).strip().lower()
    if tf_norm.endswith("m"):
        return int(tf_norm[:-1]) * 60_000
    if tf_norm.endswith("h"):
        return int(tf_norm[:-1]) * 60 * 60_000
    if tf_norm.endswith("d"):
        return int(tf_norm[:-1]) * 24 * 60 * 60_000
    raise ValueError(f"Непідтримуваний TF: {tf}")


# Levels-V1 (3.2.2x): мінімальна глибина барів для prev-day та споріднених кандидатів.
# ВАЖЛИВО: ці бари мають бути лише "complete" (close_time <= asof_ms), без lookahead.
LEVELS_V1_MIN_CLOSED_BARS_BY_TF: dict[str, int] = {
    "5m": 600,
    "1h": 72,
    "4h": 48,
}


def _frame_to_ohlcv_bars_for_viewer(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Конвертує DataFrame OHLCV у список барів (UI /ohlcv-форма).

    Важливо:
    - `time` повертаємо як close_time (ms), якщо він є, інакше як open_time (ms);
    - додаємо `complete=True`, щоб policy могла бути однозначною.
    """
    if frame is None or getattr(frame, "empty", True):
        return []

    # Локальний імпорт: цей модуль імортується як скрипт, і core.serialization
    # у нас підтягується всередині main(); тут робимо best-effort без глобальних side effects.
    from core.serialization import safe_float

    records = frame.to_dict("records")
    out: list[dict[str, Any]] = []
    for r in records:
        if not isinstance(r, dict):
            continue
        t_any = r.get("close_time") or r.get("open_time")
        if t_any is None:
            continue
        try:
            t_ms = int(float(t_any))
        except (TypeError, ValueError):
            continue

        o = safe_float(r.get("open"))
        h = safe_float(r.get("high"))
        low = safe_float(r.get("low"))
        c = safe_float(r.get("close"))
        v = safe_float(r.get("volume")) or 0.0
        if o is None or h is None or low is None or c is None:
            continue
        out.append(
            {
                "time": int(t_ms),
                "open": float(o),
                "high": float(h),
                "low": float(low),
                "close": float(c),
                "volume": float(v),
                "complete": True,
            }
        )

    return out


def _add_time_cols(df: pd.DataFrame, *, tf: str) -> pd.DataFrame:
    """Додає open_time/close_time як ms, якщо можливо.

    Для коректного TV-like replay нам потрібні close_time (коли бар стає «відомим»).
    """

    if df is None or df.empty:
        return df

    out = df.copy()
    if "open_time" in out.columns:
        out["open_time"] = pd.to_numeric(out["open_time"], errors="coerce")
    elif "timestamp" in out.columns:
        # Fallback: якщо немає open_time, спробуємо відновити з timestamp.
        # Це best-effort для QA/реплею.
        ts = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
        open_ns = ts.astype("int64")
        open_ms = open_ns // 1_000_000
        # NaT дає дуже велике від’ємне число — прибираємо, щоб dropna спрацював.
        out["open_time"] = open_ms.where(open_ns > 0)
    else:
        out["open_time"] = pd.NA

    tf_delta = _tf_ms(tf)
    out["close_time"] = pd.to_numeric(out["open_time"], errors="coerce") + int(tf_delta)

    # Уніфікуємо timestamp для дебагу/логів.
    try:
        out["timestamp"] = pd.to_datetime(out["open_time"], unit="ms", utc=True)
    except Exception:
        pass

    out = out.dropna(subset=["open_time", "close_time"]).copy()
    if out.empty:
        return out

    out["open_time"] = out["open_time"].astype("int64")
    out["close_time"] = out["close_time"].astype("int64")
    out = out.sort_values("open_time", kind="stable").reset_index(drop=True)
    return out


def _slice_closed_tail(
    df: pd.DataFrame,
    *,
    now_ms: int,
    window: int,
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if "close_time" not in df.columns:
        return pd.DataFrame()

    part = df[df["close_time"] <= int(now_ms)]
    if part.empty:
        return pd.DataFrame()
    if window > 0 and len(part) > window:
        part = part.iloc[-max(10, int(window)) :]
    return part.copy()


def _build_ohlcv_frames_by_tf_for_levels(
    df_by_tf: dict[str, pd.DataFrame],
    *,
    asof_ms: int,
) -> dict[str, list[dict[str, Any]]]:
    """Будує `asset["ohlcv_frames_by_tf"]` для Levels-V1.

    Гарантує:
    - anti-lookahead: беремо тільки закриті бари (close_time <= asof_ms);
    - мінімальні хвости за TF (див. LEVELS_V1_MIN_CLOSED_BARS_BY_TF);
    - стабільний контракт барів (UI /ohlcv-форма + complete=True).
    """

    out: dict[str, list[dict[str, Any]]] = {}
    for tf, min_bars in LEVELS_V1_MIN_CLOSED_BARS_BY_TF.items():
        df = df_by_tf.get(tf)
        if df is None or df.empty:
            out[tf] = []
            continue
        frame = _slice_closed_tail(df, now_ms=int(asof_ms), window=int(min_bars))
        out[tf] = _frame_to_ohlcv_bars_for_viewer(frame)
    return out


def _aggregate_partial_primary_from_1m(
    df_1m: pd.DataFrame,
    *,
    primary_open_ms: int,
    primary_close_ms: int,
    preview_now_ms: int,
) -> pd.DataFrame:
    """Будує partial primary-бар із 1m барів.

    Preview визначення:
    - той самий primary TF (зазвичай 5m), але останній бар ще не закритий;
    - будується з 1m у межах [primary_open_ms, primary_close_ms);
    - використовуємо лише 1m, які закриті на момент preview_now_ms.

    Повертає DataFrame з одним рядком та колонками open/high/low/close/open_time/close_time.
    Якщо 1m недостатньо — повертає порожній DataFrame.
    """

    if df_1m is None or df_1m.empty:
        return pd.DataFrame()
    if "open_time" not in df_1m.columns or "close_time" not in df_1m.columns:
        return pd.DataFrame()

    bucket = df_1m[
        (df_1m["open_time"] >= int(primary_open_ms))
        & (df_1m["open_time"] < int(primary_close_ms))
        & (df_1m["close_time"] <= int(preview_now_ms))
    ].copy()
    if bucket.empty:
        return pd.DataFrame()
    for c in ("open", "high", "low", "close"):
        if c not in bucket.columns:
            return pd.DataFrame()

    try:
        o = float(bucket["open"].iloc[0])
        h = float(bucket["high"].max())
        low_v = float(bucket["low"].min())
        c = float(bucket["close"].iloc[-1])
    except Exception:
        return pd.DataFrame()

    return pd.DataFrame(
        [
            {
                "open": o,
                "high": h,
                "low": low_v,
                "close": c,
                "open_time": int(primary_open_ms),
                # Важливо: close_time = майбутнє закриття primary кошика.
                "close_time": int(primary_close_ms),
            }
        ]
    )


def _infer_peer_snapshot_path(*, symbol: str, tf: str) -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    fname = f"{symbol.lower()}_bars_{str(tf).lower()}_snapshot.jsonl"
    return repo_root / "datastore" / fname


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
            buf.append(row)

    df = pd.DataFrame(list(buf))
    if df.empty:
        return df

    if "open_time" in df.columns:
        open_time = pd.to_numeric(df["open_time"], errors="coerce")
        df["timestamp"] = pd.to_datetime(
            open_time, unit="ms", errors="coerce", utc=True
        )
        df = df.dropna(subset=["timestamp"]).copy()
        if not df.empty:
            df = df.sort_values("open_time", kind="stable")
    return df.reset_index(drop=True)


async def _load_existing_snapshot(redis, snapshot_key: str) -> dict[str, Any]:
    try:
        raw = await redis.get(snapshot_key)
    except Exception:
        return {}
    if not raw:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


async def main() -> int:
    _ensure_repo_on_syspath()
    from app.runtime import create_redis_client
    from config.config import (
        REDIS_CHANNEL_SMC_VIEWER_EXTENDED,
        REDIS_SNAPSHOT_KEY_SMC_VIEWER,
    )
    from core.contracts.viewer_state import UiSmcAssetPayload, UiSmcMeta
    from core.serialization import json_dumps, safe_float, utc_now_iso_z
    from smc_core import SmcCoreConfig, SmcCoreEngine, SmcInput
    from UI_v2.viewer_state_builder import ViewerStateCache, build_viewer_state

    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True, help="Шлях до *_snapshot.jsonl")
    ap.add_argument(
        "--limit", type=int, default=800, help="Скільки останніх барів прогнати"
    )
    ap.add_argument(
        "--sleep-ms",
        type=int,
        default=60,
        help="Пауза між кроками (0 => максимально швидко)",
    )
    ap.add_argument(
        "--window",
        type=int,
        default=800,
        help=(
            "Скільки останніх барів тримати в кадрі на кожному кроці "
            "(для швидкості; 0/від’ємне => весь доступний кадр)"
        ),
    )
    ap.add_argument(
        "--snapshot-key",
        default="",
        help="Override ключа Redis snapshot (дефолт: REDIS_SNAPSHOT_KEY_SMC_VIEWER)",
    )
    ap.add_argument(
        "--channel",
        default="",
        help="Override Redis каналу update (дефолт: REDIS_CHANNEL_SMC_VIEWER_EXTENDED)",
    )
    ap.add_argument(
        "--publish-once",
        action="store_true",
        help=(
            "Холодний старт: порахувати стан на останніх барах і опублікувати лише 1 snapshot "
            "(без покрокового replay)"
        ),
    )
    ap.add_argument(
        "--publish-once-asof-ms",
        type=int,
        default=0,
        help=(
            "(QA) Для --publish-once: зафіксувати 'asof' (now_ms) на конкретному close_time (ms) "
            "і взяти кадр лише з барів, які закриті до цього моменту. "
            "Корисно, щоб перевіряти prev-day кандидати (PDH/PDL), якщо останній день у снапшоті — свято/вихідний."
        ),
    )
    ap.add_argument(
        "--tv-like",
        action="store_true",
        help=(
            "TV-like replay: таймлайн (типово 1m) + compute лише на закритті primary TF (типово 5m), "
            "без lookahead і з multi-TF зрізом по now_ms."
        ),
    )
    ap.add_argument(
        "--timeline-tf",
        default="",
        help=(
            "TF кроку для TV-like (наприклад 1m). Якщо порожньо: 1m якщо є снапшот, інакше = TF з --path."
        ),
    )
    ap.add_argument(
        "--compute-tf",
        default="",
        help=(
            "TF для SMC compute (типово: TF з --path, або 5m у TV-like якщо доступно)."
        ),
    )
    ap.add_argument(
        "--no-multi-tf",
        action="store_true",
        help=(
            "Вимкнути multi-TF: подавати в SMC лише один TF (як було раніше). Рекомендується лише для дебагу."
        ),
    )
    ap.add_argument(
        "--publish-every-step",
        action="store_true",
        help=(
            "У TV-like: публікувати viewer_state на кожному кроці таймлайну (навіть коли compute не тригерився). "
            "Корисно для візуального 'програвання', але SMC-об'єкти змінюються лише на compute-кроках."
        ),
    )
    ap.add_argument(
        "--journal-dir",
        default="",
        help=(
            "(QA) Увімкнути SMC Lifecycle Journal і писати JSONL у вказану папку. "
            "Приклад: reports/smc_journal"
        ),
    )
    ap.add_argument(
        "--with-preview",
        action="store_true",
        help=(
            "(QA) У TV-like: рахувати preview (інтра-бар) для primary TF перед close. "
            "Preview рахується в окремому engine (без мутації основного) і пишеться як frame+події."
        ),
    )
    args = ap.parse_args()

    path = _resolve_snapshot_path(args.path)
    if not path.exists() or not path.is_file():
        print(f"ПОМИЛКА: файл не знайдено: {path}")
        return 2

    limit = max(10, int(args.limit))
    sleep_ms = max(0, int(args.sleep_ms))
    window = int(args.window)

    journal_dir = str(args.journal_dir or "").strip()
    journal_enabled = bool(journal_dir)
    journal = None
    journal_writer = None
    frames_writer = None

    def _ensure_journal(tf: str):  # type: ignore[no-untyped-def]
        nonlocal journal, journal_writer, frames_writer
        if not journal_enabled:
            return None, None, None
        if journal_writer is None:
            from smc_core.lifecycle_journal import JsonlJournalWriter

            journal_writer = JsonlJournalWriter(base_dir=Path(journal_dir))
        if journal is None:
            from smc_core.lifecycle_journal import (
                PrometheusMetricsSink,
                SmcLifecycleJournal,
            )

            journal = SmcLifecycleJournal(
                symbol=str(symbol or "UNKNOWN"),
                tf=str(tf),
                metrics=PrometheusMetricsSink(),
                removed_confirm_close_steps=2,
            )
        if frames_writer is None:
            from smc_core.lifecycle_journal import JsonlFramesWriter

            frames_writer = JsonlFramesWriter(base_dir=Path(journal_dir) / "frames")
        return journal, journal_writer, frames_writer

    def _day_utc_from_ms(ms: int) -> str:
        try:
            return datetime.fromtimestamp(int(ms) / 1000.0, tz=UTC).date().isoformat()
        except Exception:
            return "-"

    symbol, tf_from_path = _infer_symbol_tf(path)
    df = _read_jsonl_tail(path, limit=limit)
    if df.empty:
        print("ПОМИЛКА: snapshot порожній або не читається")
        return 3

    # TV-like: визначаємо timeline_tf та compute_tf.
    timeline_tf_arg = str(args.timeline_tf or "").strip().lower()
    compute_tf_arg = str(args.compute_tf or "").strip().lower()

    # Якщо користувач не задав — беремо максимально «як live» для SMC: compute=5m.
    # А timeline (те, як "рухається час" в UI) за замовчуванням робимо рівним compute TF,
    # щоб стандартний сценарій був "5тф" (без 1m-кроків).
    if bool(args.tv_like):
        if not compute_tf_arg:
            compute_tf_arg = "5m"
        if not timeline_tf_arg:
            timeline_tf_arg = compute_tf_arg

    compute_tf = compute_tf_arg or str(tf_from_path).lower()
    timeline_tf = timeline_tf_arg or str(tf_from_path).lower()

    # Підказка про execution (1m-шар).
    if str(timeline_tf).lower() != "1m" and str(compute_tf).lower() != "1m":
        logger.warning(
            "УВАГА: timeline_tf=%s compute_tf=%s. Stage5 execution рахується на 1m, тому execution_events/стрілочки можуть бути відсутні. "
            "Для перевірки стрілок увімкни TV-like з timeline_tf=1m або передай 1m снапшот.",
            timeline_tf,
            compute_tf,
        )

    snapshot_key = str(args.snapshot_key or "").strip() or REDIS_SNAPSHOT_KEY_SMC_VIEWER
    channel = str(args.channel or "").strip() or REDIS_CHANNEL_SMC_VIEWER_EXTENDED

    redis_conn, source = create_redis_client(decode_responses=True)
    logger.info("Redis підключено (%s)", source)
    logger.info(
        "Replay старт: symbol=%s path_tf=%s timeline_tf=%s compute_tf=%s bars=%d sleep_ms=%d window=%d tv_like=%s multi_tf=%s",
        symbol,
        tf_from_path,
        timeline_tf,
        compute_tf,
        len(df),
        sleep_ms,
        window,
        bool(args.tv_like),
        (not bool(args.no_multi_tf)),
    )

    engine = SmcCoreEngine(cfg=SmcCoreConfig())
    cache = ViewerStateCache()

    state_map: dict[str, Any] = await _load_existing_snapshot(redis_conn, snapshot_key)

    # Підготовка фреймів.
    # У non-TV режимі залишаємо стару поведінку (один TF із --path).
    # У TV-like режимі будуємо мульти-TF кадр, синхронізований по close_time.

    # Зберігаємо початковий df як "path tf".
    df_path_tf = _add_time_cols(df, tf=tf_from_path)

    def _load_tf_or_empty(tf: str, *, tail_limit: int) -> pd.DataFrame:
        if str(tf).lower() == str(tf_from_path).lower():
            return df_path_tf
        p = _infer_peer_snapshot_path(symbol=symbol, tf=tf)
        try:
            if p.exists() and p.is_file():
                return _add_time_cols(_read_jsonl_tail(p, limit=tail_limit), tf=tf)
        except Exception:
            return pd.DataFrame()
        return pd.DataFrame()

    if bool(args.publish_once):
        # 1) Беремо останній кадр (window) і рахуємо SMC один раз.
        base_tf = compute_tf if bool(args.tv_like) else tf_from_path
        df_base = _load_tf_or_empty(base_tf, tail_limit=limit)
        if df_base.empty:
            print(f"ПОМИЛКА: немає даних для base_tf={base_tf} (publish_once)")
            return 3

        asof_ms_override = int(args.publish_once_asof_ms or 0)
        if asof_ms_override > 0 and "close_time" in df_base.columns:
            df_base = df_base[
                pd.to_numeric(df_base["close_time"], errors="coerce")
                <= asof_ms_override
            ].copy()
            if df_base.empty:
                print(
                    f"ПОМИЛКА: після фільтру publish_once_asof_ms={asof_ms_override} не залишилось барів (base_tf={base_tf})"
                )
                return 3

        if window <= 0:
            frame_base = df_base.copy()
        else:
            frame_base = df_base.iloc[max(0, len(df_base) - max(10, window)) :].copy()

        last_close = (
            safe_float(frame_base["close"].iloc[-1])
            if "close" in frame_base.columns
            else None
        )

        ohlc_by_tf: dict[str, pd.DataFrame]
        if bool(args.no_multi_tf) or (not bool(args.tv_like)):
            ohlc_by_tf = {str(base_tf): frame_base}
        else:
            # Тягнемо exec + HTF контекст, якщо доступні.
            df_1m = _load_tf_or_empty("1m", tail_limit=max(limit * 5, 500))
            df_1h = _load_tf_or_empty("1h", tail_limit=max(limit, 300))
            df_4h = _load_tf_or_empty("4h", tail_limit=max(limit, 200))
            now_ms = int(frame_base["close_time"].iloc[-1])
            if asof_ms_override > 0:
                now_ms = int(asof_ms_override)
            ohlc_by_tf = {
                str(base_tf): _slice_closed_tail(df_base, now_ms=now_ms, window=window),
            }
            if not df_1m.empty:
                ohlc_by_tf["1m"] = _slice_closed_tail(
                    df_1m, now_ms=now_ms, window=max(window * 5, 300)
                )
            if not df_1h.empty:
                ohlc_by_tf["1h"] = _slice_closed_tail(
                    df_1h,
                    now_ms=now_ms,
                    window=max(120, int(window / 2) if window > 0 else 240),
                )
            if not df_4h.empty:
                ohlc_by_tf["4h"] = _slice_closed_tail(
                    df_4h,
                    now_ms=now_ms,
                    window=max(80, int(window / 3) if window > 0 else 160),
                )

        try:
            from smc_core.input_adapter import build_smc_input_from_frames

            smc_input = build_smc_input_from_frames(
                symbol=symbol,
                tf_primary=str(base_tf),
                ohlc_by_tf=ohlc_by_tf,
                context=None,
            )
        except Exception:
            # Fallback: мінімальний SmcInput без нормалізації.
            smc_input = SmcInput(
                symbol=symbol,
                tf_primary=str(base_tf),
                ohlc_by_tf=ohlc_by_tf,
                context={},
            )
        hint = engine.process_snapshot(smc_input)

        try:
            from smc_core.serializers import to_plain_smc_hint

            hint_plain = to_plain_smc_hint(hint)
        except Exception as exc:
            logger.error("Не вдалося серіалізувати SmcHintPlain: %s", exc)
            return 4
        if not isinstance(hint_plain, dict):
            return 4

        if journal_enabled:
            try:
                from smc_core.lifecycle_journal import BarSnapshot, build_frame_record

                j, w, fw = _ensure_journal(str(base_tf))
                if j is not None and w is not None and fw is not None:
                    bar = BarSnapshot(
                        open=float(frame_base["open"].iloc[-1]),
                        high=float(frame_base["high"].iloc[-1]),
                        low=float(frame_base["low"].iloc[-1]),
                        close=float(frame_base["close"].iloc[-1]),
                        close_time_ms=int(frame_base["close_time"].iloc[-1]),
                        complete=True,
                    )
                    now_ms = int(bar.close_time_ms)
                    events = j.process_snapshot(
                        hint=hint_plain,
                        now_ms=now_ms,
                        bar=bar,
                        compute_kind="close",
                        primary_close_ms=int(bar.close_time_ms),
                    )
                    w.append_events(
                        symbol=symbol, day_utc=_day_utc_from_ms(now_ms), events=events
                    )

                    frame_rec = build_frame_record(
                        symbol=symbol,
                        tf=str(base_tf),
                        now_ms=now_ms,
                        kind="close",
                        primary_close_ms=int(bar.close_time_ms),
                        bar_complete=True,
                        hint=hint_plain,
                    )
                    fw.append_frame(
                        symbol=symbol,
                        day_utc=_day_utc_from_ms(now_ms),
                        frame=frame_rec,
                    )
            except Exception as exc:
                logger.debug("Journal (publish-once) пропущено: %s", exc)

        meta_any: dict[str, Any] = {
            "ts": utc_now_iso_z(),
            "seq": int(len(df)),
            "fxcm": {"market_state": "CLOSED", "process_state": "replay_once"},
            "replay_mode": "once",
            "replay_cursor_ms": int(frame_base["close_time"].iloc[-1]),
            "replay_timeline_tf": str(base_tf),
            "replay_compute_tf": str(base_tf),
        }
        asset_any: dict[str, Any] = {
            "symbol": symbol,
            "price": float(last_close) if last_close is not None else None,
            "smc_hint": hint_plain,
        }

        # Для Levels-V1 candidates (3.2.2x): гарантуємо мінімальний lookback
        # "до asof_ms" і *без* lookahead (лише complete бари).
        try:
            levels_asof_ms = int(asof_ms_override or frame_base["close_time"].iloc[-1])
            df_5m = _load_tf_or_empty(
                "5m",
                tail_limit=max(
                    LEVELS_V1_MIN_CLOSED_BARS_BY_TF["5m"] * 2, limit * 2, 1400
                ),
            )
            df_1h_levels = _load_tf_or_empty(
                "1h",
                tail_limit=max(LEVELS_V1_MIN_CLOSED_BARS_BY_TF["1h"] * 2, 200),
            )
            df_4h_levels = _load_tf_or_empty(
                "4h",
                tail_limit=max(LEVELS_V1_MIN_CLOSED_BARS_BY_TF["4h"] * 2, 120),
            )
            asset_any["ohlcv_frames_by_tf"] = _build_ohlcv_frames_by_tf_for_levels(
                {"5m": df_5m, "1h": df_1h_levels, "4h": df_4h_levels},
                asof_ms=levels_asof_ms,
            )
        except Exception:
            asset_any["ohlcv_frames_by_tf"] = {}

        viewer_state = build_viewer_state(
            cast(UiSmcAssetPayload, asset_any),
            cast(UiSmcMeta, meta_any),
            cache=cache,
        )
        symbol_key = symbol.upper()
        state_map[symbol_key] = viewer_state

        await redis_conn.set(snapshot_key, json_dumps(state_map, pretty=False))
        await redis_conn.publish(
            channel,
            json_dumps(
                {"symbol": symbol_key, "viewer_state": viewer_state}, pretty=False
            ),
        )
        logger.info(
            "Publish-once завершено: %s/%s bars=%d window=%d",
            symbol,
            base_tf,
            len(df_base),
            window,
        )
        return 0

    if not bool(args.tv_like):
        # Legacy replay: як було раніше (один TF з --path).

        # 3.2.2x: готуємо DF-джерела для Levels-V1 один раз (щоб не перечитувати jsonl).
        try:
            df_5m_levels = (
                df_path_tf
                if str(tf_from_path).lower() == "5m"
                else _load_tf_or_empty(
                    "5m",
                    tail_limit=max(
                        LEVELS_V1_MIN_CLOSED_BARS_BY_TF["5m"] * 2,
                        limit * 2,
                        1400,
                    ),
                )
            )
            df_1h_levels = _load_tf_or_empty(
                "1h",
                tail_limit=max(LEVELS_V1_MIN_CLOSED_BARS_BY_TF["1h"] * 2, 200),
            )
            df_4h_levels = _load_tf_or_empty(
                "4h",
                tail_limit=max(LEVELS_V1_MIN_CLOSED_BARS_BY_TF["4h"] * 2, 120),
            )
        except Exception:
            df_5m_levels = pd.DataFrame()
            df_1h_levels = pd.DataFrame()
            df_4h_levels = pd.DataFrame()

        for i in range(2, len(df_path_tf) + 1):
            if window <= 0:
                start = 0
            else:
                start = max(0, i - max(10, window))
            frame = df_path_tf.iloc[start:i].copy()
            last_close = (
                safe_float(frame["close"].iloc[-1])
                if "close" in frame.columns
                else None
            )

            smc_input = SmcInput(
                symbol=symbol,
                tf_primary=tf_from_path,
                ohlc_by_tf={tf_from_path: frame},
                context={},
            )
            hint = engine.process_snapshot(smc_input)

            # Мінімальний UiSmcMeta для viewer_state_builder.
            meta_any: dict[str, Any] = {
                "ts": utc_now_iso_z(),
                "seq": int(i),
                # Коли відтворюємо історію — корисно явно показати, що це replay/QA.
                "fxcm": {"market_state": "CLOSED", "process_state": "replay"},
                "replay_mode": "legacy",
                "replay_cursor_ms": int(frame["close_time"].iloc[-1]),
                "replay_timeline_tf": str(tf_from_path),
                "replay_compute_tf": str(tf_from_path),
            }

            # Важливо: viewer_state_builder очікує plain JSON hint.
            # SmcCoreEngine у цьому репо віддає dataclass-структури; у проді ми їх
            # серіалізуємо через smc_core.serializers.to_plain_smc_hint.
            try:
                from smc_core.serializers import to_plain_smc_hint

                hint_plain = to_plain_smc_hint(hint)
            except Exception as exc:
                logger.error("Не вдалося серіалізувати SmcHintPlain: %s", exc)
                continue
            if not isinstance(hint_plain, dict):
                continue

            if journal_enabled:
                try:
                    from smc_core.lifecycle_journal import (
                        BarSnapshot,
                        build_frame_record,
                    )

                    j, w, fw = _ensure_journal(str(tf_from_path))
                    if j is not None and w is not None and fw is not None:
                        bar = BarSnapshot(
                            open=float(frame["open"].iloc[-1]),
                            high=float(frame["high"].iloc[-1]),
                            low=float(frame["low"].iloc[-1]),
                            close=float(frame["close"].iloc[-1]),
                            close_time_ms=int(frame["close_time"].iloc[-1]),
                            complete=True,
                        )
                        now_ms = int(bar.close_time_ms)
                        events = j.process_snapshot(
                            hint=hint_plain,
                            now_ms=now_ms,
                            bar=bar,
                            compute_kind="close",
                            primary_close_ms=int(bar.close_time_ms),
                        )
                        w.append_events(
                            symbol=symbol,
                            day_utc=_day_utc_from_ms(now_ms),
                            events=events,
                        )

                        frame_rec = build_frame_record(
                            symbol=symbol,
                            tf=str(tf_from_path),
                            now_ms=now_ms,
                            kind="close",
                            primary_close_ms=int(bar.close_time_ms),
                            bar_complete=True,
                            hint=hint_plain,
                        )
                        fw.append_frame(
                            symbol=symbol,
                            day_utc=_day_utc_from_ms(now_ms),
                            frame=frame_rec,
                        )
                except Exception as exc:
                    logger.debug("Journal (legacy) пропущено: %s", exc)

            asset_any: dict[str, Any] = {
                "symbol": symbol,
                "price": float(last_close) if last_close is not None else None,
                "smc_hint": hint_plain,
            }

            # 3.2.2x: рівні (PDH/PDL) потребують достатньої історії, тому
            # підкладаємо хвости 5m/1h/4h "до asof_ms" без lookahead.
            try:
                levels_asof_ms = int(frame["close_time"].iloc[-1])
                asset_any["ohlcv_frames_by_tf"] = _build_ohlcv_frames_by_tf_for_levels(
                    {"5m": df_5m_levels, "1h": df_1h_levels, "4h": df_4h_levels},
                    asof_ms=levels_asof_ms,
                )
            except Exception:
                asset_any["ohlcv_frames_by_tf"] = {}

            viewer_state = build_viewer_state(
                cast(UiSmcAssetPayload, asset_any),
                cast(UiSmcMeta, meta_any),
                cache=cache,
            )
            symbol_key = symbol.upper()
            state_map[symbol_key] = viewer_state

            # 1) snapshot: щоб HTTP /smc-viewer/snapshot працював.
            await redis_conn.set(snapshot_key, json_dumps(state_map, pretty=False))

            # 2) channel update: щоб WS /smc-viewer/stream?symbol=... оновлювався.
            await redis_conn.publish(
                channel,
                json_dumps(
                    {"symbol": symbol_key, "viewer_state": viewer_state},
                    pretty=False,
                ),
            )

            if i % 25 == 0 or i == len(df_path_tf):
                logger.info(
                    "Replay прогрес: step=%d/%d (legacy; window=%s)",
                    i,
                    len(df_path_tf),
                    window,
                )

            if sleep_ms > 0:
                await asyncio.sleep(sleep_ms / 1000.0)

        logger.info("Replay завершено (legacy): steps=%d", len(df_path_tf) - 1)
        return 0

    # ── TV-like replay ─────────────────────────────────────────────────────
    # timeline_df визначає «плин часу» (типово 1m), compute виконуємо коли
    # зʼявився новий закритий бар compute_tf.

    df_timeline = _load_tf_or_empty(timeline_tf, tail_limit=max(limit * 5, 1200))
    if df_timeline.empty:
        logger.warning(
            "TV-like: немає timeline_tf=%s, fallback на path_tf=%s",
            timeline_tf,
            tf_from_path,
        )
        df_timeline = df_path_tf
        timeline_tf = str(tf_from_path).lower()

    df_compute = _load_tf_or_empty(compute_tf, tail_limit=max(limit, 800))
    if df_compute.empty:
        logger.warning(
            "TV-like: немає compute_tf=%s, fallback на path_tf=%s",
            compute_tf,
            tf_from_path,
        )
        df_compute = df_path_tf
        compute_tf = str(tf_from_path).lower()

    # Додаткові TF (best-effort): 1m/1h/4h.
    df_1m = _load_tf_or_empty("1m", tail_limit=max(limit * 5, 1200))
    df_1h = _load_tf_or_empty(
        "1h",
        tail_limit=max(limit, LEVELS_V1_MIN_CLOSED_BARS_BY_TF["1h"] * 2, 400),
    )
    df_4h = _load_tf_or_empty(
        "4h",
        tail_limit=max(limit, LEVELS_V1_MIN_CLOSED_BARS_BY_TF["4h"] * 2, 250),
    )
    df_5m_levels = _load_tf_or_empty(
        "5m",
        tail_limit=max(limit * 2, LEVELS_V1_MIN_CLOSED_BARS_BY_TF["5m"] * 2, 1400),
    )

    # Відсікаємо timeline до limit барів (останній хвіст).
    if len(df_timeline) > limit:
        df_timeline = df_timeline.iloc[-limit:].reset_index(drop=True)

    # Стан: останній закритий compute бар.
    last_compute_visible = 0
    last_compute_close_ms: int | None = None
    last_hint_plain: dict[str, Any] | None = None
    last_price: float | None = None

    logger.info(
        "TV-like режим: timeline_tf=%s steps=%d compute_tf=%s multi_tf=%s publish_every_step=%s",
        timeline_tf,
        len(df_timeline),
        compute_tf,
        (not bool(args.no_multi_tf)),
        bool(args.publish_every_step),
    )

    for step_idx in range(0, len(df_timeline)):
        now_ms = int(df_timeline["close_time"].iloc[step_idx])

        frame_compute = _slice_closed_tail(df_compute, now_ms=now_ms, window=window)
        compute_visible = int(len(frame_compute))
        compute_last_close_ms: int | None = None
        if compute_visible > 0 and "close_time" in frame_compute.columns:
            try:
                compute_last_close_ms = int(frame_compute["close_time"].iloc[-1])
            except Exception:
                compute_last_close_ms = None

        # Оновлюємо "price" (для UI), якщо є 1m.
        frame_price_src = (
            _slice_closed_tail(df_1m, now_ms=now_ms, window=1)
            if not df_1m.empty
            else _slice_closed_tail(df_timeline, now_ms=now_ms, window=1)
        )
        if not frame_price_src.empty and "close" in frame_price_src.columns:
            last_price = safe_float(frame_price_src["close"].iloc[-1])

        did_compute = False
        # Важливо: window може робити compute_visible сталим. Тому тригеримо compute
        # по зміні часу останнього доступного закритого бару.
        if (
            compute_visible > 0
            and compute_last_close_ms is not None
            and compute_last_close_ms != last_compute_close_ms
        ):
            # (QA) Preview compute: один preview на primary бар.
            # Важливо: preview рахуємо в окремому engine, щоб не мутувати stateful engine.
            if (
                bool(args.with_preview)
                and journal_enabled
                and compute_last_close_ms is not None
                and not df_1m.empty
            ):
                try:
                    preview_now_ms = int(compute_last_close_ms) - 60_000
                    if preview_now_ms > 0:
                        primary_open_ms = int(compute_last_close_ms) - _tf_ms(
                            str(compute_tf)
                        )

                        frame_closed = _slice_closed_tail(
                            df_compute, now_ms=preview_now_ms, window=window
                        )
                        partial = _aggregate_partial_primary_from_1m(
                            df_1m,
                            primary_open_ms=primary_open_ms,
                            primary_close_ms=int(compute_last_close_ms),
                            preview_now_ms=preview_now_ms,
                        )
                        if not partial.empty:
                            try:
                                partial["timestamp"] = pd.to_datetime(
                                    partial["open_time"], unit="ms", utc=True
                                )
                            except Exception:
                                pass

                            if frame_closed.empty:
                                frame_preview = partial
                            else:
                                frame_preview = pd.concat(
                                    [frame_closed, partial], ignore_index=True
                                )
                                if "open_time" in frame_preview.columns:
                                    frame_preview = frame_preview.sort_values(
                                        "open_time", kind="stable"
                                    ).reset_index(drop=True)

                            ohlc_preview: dict[str, pd.DataFrame] = {
                                str(compute_tf): frame_preview
                            }
                            if not bool(args.no_multi_tf):
                                ohlc_preview["1m"] = _slice_closed_tail(
                                    df_1m,
                                    now_ms=preview_now_ms,
                                    window=max(window * 5, 300),
                                )
                                if not df_1h.empty:
                                    ohlc_preview["1h"] = _slice_closed_tail(
                                        df_1h,
                                        now_ms=preview_now_ms,
                                        window=max(
                                            120,
                                            int(window / 2) if window > 0 else 240,
                                        ),
                                    )
                                if not df_4h.empty:
                                    ohlc_preview["4h"] = _slice_closed_tail(
                                        df_4h,
                                        now_ms=preview_now_ms,
                                        window=max(
                                            80,
                                            int(window / 3) if window > 0 else 160,
                                        ),
                                    )

                            try:
                                from smc_core.input_adapter import (
                                    build_smc_input_from_frames,
                                )

                                smc_input_preview = build_smc_input_from_frames(
                                    symbol=symbol,
                                    tf_primary=str(compute_tf),
                                    ohlc_by_tf=ohlc_preview,
                                    context={
                                        "smc_compute_kind": "preview",
                                        "prev_wick_clusters": (
                                            (last_hint_plain or {})
                                            .get("liquidity", {})
                                            .get("meta", {})
                                            .get("wick_clusters")
                                        ),
                                    },
                                )
                            except Exception:
                                smc_input_preview = SmcInput(
                                    symbol=symbol,
                                    tf_primary=str(compute_tf),
                                    ohlc_by_tf=ohlc_preview,
                                    context={
                                        "smc_compute_kind": "preview",
                                        "prev_wick_clusters": (
                                            (last_hint_plain or {})
                                            .get("liquidity", {})
                                            .get("meta", {})
                                            .get("wick_clusters")
                                        ),
                                    },
                                )

                            preview_engine = SmcCoreEngine(cfg=SmcCoreConfig())
                            hint_preview = preview_engine.process_snapshot(
                                smc_input_preview
                            )

                            from smc_core.serializers import to_plain_smc_hint

                            hint_preview_plain = to_plain_smc_hint(hint_preview)
                            if isinstance(hint_preview_plain, dict):
                                from smc_core.lifecycle_journal import (
                                    BarSnapshot,
                                    build_frame_record,
                                )

                                j, w, fw = _ensure_journal(str(compute_tf))
                                if j is not None and w is not None and fw is not None:
                                    bar_preview = BarSnapshot(
                                        open=float(partial["open"].iloc[0]),
                                        high=float(partial["high"].iloc[0]),
                                        low=float(partial["low"].iloc[0]),
                                        close=float(partial["close"].iloc[0]),
                                        close_time_ms=int(compute_last_close_ms),
                                        complete=False,
                                    )
                                    ev_preview = j.process_snapshot(
                                        hint=hint_preview_plain,
                                        now_ms=int(preview_now_ms),
                                        bar=bar_preview,
                                        compute_kind="preview",
                                        primary_close_ms=int(compute_last_close_ms),
                                    )
                                    w.append_events(
                                        symbol=symbol,
                                        day_utc=_day_utc_from_ms(int(preview_now_ms)),
                                        events=ev_preview,
                                    )

                                    frame_preview_rec = build_frame_record(
                                        symbol=symbol,
                                        tf=str(compute_tf),
                                        now_ms=int(preview_now_ms),
                                        kind="preview",
                                        primary_close_ms=int(compute_last_close_ms),
                                        bar_complete=False,
                                        hint=hint_preview_plain,
                                    )
                                    fw.append_frame(
                                        symbol=symbol,
                                        day_utc=_day_utc_from_ms(int(preview_now_ms)),
                                        frame=frame_preview_rec,
                                    )
                except Exception as exc:
                    logger.debug("Journal preview пропущено: %s", exc)

            # Новий закритий compute-бар став доступний — запускаємо SMC.
            ohlc_by_tf: dict[str, pd.DataFrame] = {str(compute_tf): frame_compute}
            if not bool(args.no_multi_tf):
                if not df_1m.empty:
                    ohlc_by_tf["1m"] = _slice_closed_tail(
                        df_1m, now_ms=now_ms, window=max(window * 5, 300)
                    )
                if not df_1h.empty:
                    ohlc_by_tf["1h"] = _slice_closed_tail(
                        df_1h,
                        now_ms=now_ms,
                        window=max(120, int(window / 2) if window > 0 else 240),
                    )
                if not df_4h.empty:
                    ohlc_by_tf["4h"] = _slice_closed_tail(
                        df_4h,
                        now_ms=now_ms,
                        window=max(80, int(window / 3) if window > 0 else 160),
                    )

            try:
                from smc_core.input_adapter import build_smc_input_from_frames

                smc_input = build_smc_input_from_frames(
                    symbol=symbol,
                    tf_primary=str(compute_tf),
                    ohlc_by_tf=ohlc_by_tf,
                    context={
                        "smc_compute_kind": "close",
                        "prev_wick_clusters": (
                            (last_hint_plain or {})
                            .get("liquidity", {})
                            .get("meta", {})
                            .get("wick_clusters")
                        ),
                    },
                )
            except Exception:
                smc_input = SmcInput(
                    symbol=symbol,
                    tf_primary=str(compute_tf),
                    ohlc_by_tf=ohlc_by_tf,
                    context={
                        "smc_compute_kind": "close",
                        "prev_wick_clusters": (
                            (last_hint_plain or {})
                            .get("liquidity", {})
                            .get("meta", {})
                            .get("wick_clusters")
                        ),
                    },
                )

            hint = engine.process_snapshot(smc_input)
            try:
                from smc_core.serializers import to_plain_smc_hint

                hint_plain_any = to_plain_smc_hint(hint)
            except Exception as exc:
                logger.error("Не вдалося серіалізувати SmcHintPlain: %s", exc)
                hint_plain_any = None

            if isinstance(hint_plain_any, dict):
                last_hint_plain = cast(dict[str, Any], hint_plain_any)
                did_compute = True
                last_compute_visible = compute_visible
                last_compute_close_ms = compute_last_close_ms

                if (
                    journal_enabled
                    and compute_last_close_ms is not None
                    and not frame_compute.empty
                ):
                    try:
                        from smc_core.lifecycle_journal import (
                            BarSnapshot,
                            build_frame_record,
                        )

                        j, w, fw = _ensure_journal(str(compute_tf))
                        if j is not None and w is not None and fw is not None:
                            bar = BarSnapshot(
                                open=float(frame_compute["open"].iloc[-1]),
                                high=float(frame_compute["high"].iloc[-1]),
                                low=float(frame_compute["low"].iloc[-1]),
                                close=float(frame_compute["close"].iloc[-1]),
                                close_time_ms=int(compute_last_close_ms),
                                complete=True,
                            )
                            now_ms_j = int(compute_last_close_ms)
                            events = j.process_snapshot(
                                hint=last_hint_plain,
                                now_ms=now_ms_j,
                                bar=bar,
                                compute_kind="close",
                                primary_close_ms=int(compute_last_close_ms),
                            )
                            w.append_events(
                                symbol=symbol,
                                day_utc=_day_utc_from_ms(now_ms_j),
                                events=events,
                            )

                            frame_close = build_frame_record(
                                symbol=symbol,
                                tf=str(compute_tf),
                                now_ms=now_ms_j,
                                kind="close",
                                primary_close_ms=int(compute_last_close_ms),
                                bar_complete=True,
                                hint=last_hint_plain,
                            )
                            fw.append_frame(
                                symbol=symbol,
                                day_utc=_day_utc_from_ms(now_ms_j),
                                frame=frame_close,
                            )
                    except Exception as exc:
                        logger.debug("Journal (tv-like) пропущено: %s", exc)

        # Публікація: або після compute, або на кожному кроці (якщо user попросив).
        if did_compute or bool(args.publish_every_step):
            if last_hint_plain is None:
                # Нема що публікувати (ще не було compute) — пропускаємо.
                if sleep_ms > 0:
                    await asyncio.sleep(sleep_ms / 1000.0)
                continue

            meta_any = {
                "ts": utc_now_iso_z(),
                "seq": int(step_idx + 1),
                "fxcm": {
                    "market_state": "CLOSED",
                    "process_state": "replay_tv_like",
                    "timeline_tf": str(timeline_tf),
                    "compute_tf": str(compute_tf),
                    "did_compute": bool(did_compute),
                },
                "replay_mode": "tv_like",
                "replay_cursor_ms": int(now_ms),
                "replay_timeline_tf": str(timeline_tf),
                "replay_compute_tf": str(compute_tf),
            }

            asset_any = {
                "symbol": symbol,
                "price": float(last_price) if last_price is not None else None,
                "smc_hint": last_hint_plain,
            }

            # 3.2.2x: даємо UI builder-у достатню історію для PDH/PDL (та ін. daily),
            # синхронізовано по часу replay (anti-lookahead).
            try:
                asset_any["ohlcv_frames_by_tf"] = _build_ohlcv_frames_by_tf_for_levels(
                    {"5m": df_5m_levels, "1h": df_1h, "4h": df_4h},
                    asof_ms=int(now_ms),
                )
            except Exception:
                asset_any["ohlcv_frames_by_tf"] = {}

            viewer_state = build_viewer_state(
                cast(UiSmcAssetPayload, asset_any),
                cast(UiSmcMeta, meta_any),
                cache=cache,
            )

            symbol_key = symbol.upper()
            state_map[symbol_key] = viewer_state
            await redis_conn.set(snapshot_key, json_dumps(state_map, pretty=False))
            await redis_conn.publish(
                channel,
                json_dumps(
                    {"symbol": symbol_key, "viewer_state": viewer_state}, pretty=False
                ),
            )

        if (step_idx + 1) % 100 == 0 or (step_idx + 1) == len(df_timeline):
            logger.info(
                "TV-like прогрес: step=%d/%d compute_bars=%d last_compute_close_ms=%s did_compute=%s",
                step_idx + 1,
                len(df_timeline),
                last_compute_visible,
                (
                    str(last_compute_close_ms)
                    if last_compute_close_ms is not None
                    else "-"
                ),
                bool(did_compute),
            )

        if sleep_ms > 0:
            await asyncio.sleep(sleep_ms / 1000.0)

    logger.info("Replay завершено (TV-like): steps=%d", len(df_timeline))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
