from __future__ import annotations

import gzip
import io
import logging
import lzma
import struct
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Literal

import pandas as pd

from .dl_config import HttpClient
from .utils import ensure_utc

logger = logging.getLogger("duka")
if not logger.handlers:
    from rich.console import Console
    from rich.logging import RichHandler

    logger.setLevel(logging.DEBUG)
    logger.addHandler(RichHandler(console=Console(stderr=True), show_path=False))
    logger.propagate = False


BAR_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time"]
TfAdapter = Literal["TICK", "M1"]


def _duka_symbol(sym: str) -> str:
    """
    Мапування символу користувача → код Dukascopy при потребі.
    Для основних пар повертає як є.
    """
    logger.debug("Мапування символу для Dukascopy: %s", sym)
    # Розширити тут, якщо потрібне кастомне мапування.
    return sym.upper()


def _parse_csv_gz(content: bytes, *, kind: TfAdapter) -> pd.DataFrame:
    """
    Розпарсити gzip CSV у DataFrame зі стандартними колонками.
    kind: "TICK" або "M1".
    """
    logger.info("Розпакування gzip CSV (%s), розмір: %d байт", kind, len(content))
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as f:
            raw = f.read()
        logger.debug("gzip CSV розпаковано, розмір raw: %d байт", len(raw))
        df = pd.read_csv(io.BytesIO(raw))
        logger.info("CSV прочитано: %d рядків, %d колонок", len(df), len(df.columns))
        logger.debug("CSV колонки: %s", list(df.columns))
    except Exception as e:
        logger.error("Помилка при розпакуванні/читанні CSV: %s", e)
        raise

    # Очікувати загальні колонки, спробувати авто-детект
    cols = {c.lower(): c for c in df.columns}
    if kind == "TICK":
        # Очікувані: time/bid/ask/(price?)/volume?
        tcol = (
            "time"
            if "time" in cols
            else next((c for c in df.columns if c.lower().startswith("time")), None)
        )
        if tcol is None:
            logger.error("tick csv: відсутня колонка часу")
            raise ValueError("tick csv: відсутня колонка часу")
        ts = pd.to_datetime(df[tcol], utc=True)
        out = pd.DataFrame({"ts": ts})
        for name in ("bid", "ask", "price", "volume"):
            picks = [c for c in df.columns if c.lower() == name]
            if picks:
                out[name] = df[picks[0]].astype(float).values
                logger.debug("tick csv: колонка %s додана", name)
        logger.info("tick csv: сформовано DataFrame (%d рядків)", len(out))
        return out
    else:
        # M1 OHLCV або fallback на тики з колонки price
        tcol = (
            "time"
            if "time" in cols
            else next((c for c in df.columns if c.lower().startswith("time")), None)
        )
        if tcol is None:
            logger.error("m1 csv: відсутня колонка часу")
            raise ValueError("m1 csv: відсутня колонка часу")
        ts = pd.to_datetime(df[tcol], utc=True)
        # Перевірити, чи є OHLC або лише price (тикові дані в M1 файлі)
        have_ohlc = any(k in cols for k in {"open", "high", "low", "close"})
        have_price = "price" in cols
        if not have_ohlc and have_price:
            # fallback: повертаємо тики для подальшої агрегації
            price_col = [c for c in df.columns if c.lower() == "price"][0]
            out = pd.DataFrame({"ts": ts, "price": df[price_col].astype(float).values})
            vol_cols = [c for c in df.columns if c.lower() == "volume"]
            if vol_cols:
                out["volume"] = df[vol_cols[0]].astype(float).values
            # Відфільтрувати порожні ts і привести типи
            out = out[out["ts"].notna()]
            out["ts"] = pd.to_datetime(out["ts"], utc=True)
            logger.info(
                "m1 csv (fallback ticks): сформовано DataFrame (%d рядків)", len(out)
            )
            return out

    out = pd.DataFrame({"ts": ts})
    for name in ("open", "high", "low", "close", "volume"):
        picks = [c for c in df.columns if c.lower() == name]
        if picks:
            out[name] = df[picks[0]].astype(float).values
            logger.debug("m1 csv: колонка %s додана", name)
    # Видалити рядки без часу та привести ts до UTC
    out = out[out["ts"].notna()]
    out["ts"] = pd.to_datetime(out["ts"], utc=True)
    logger.info("m1 csv: сформовано DataFrame (%d рядків)", len(out))
    return out


def _empty_bars_df() -> pd.DataFrame:
    return pd.DataFrame(columns=BAR_COLS)


def ticks_to_m1(frame: pd.DataFrame) -> pd.DataFrame:
    """Агрегує тикові дані у хвилинні бари з open/close time у мілісекундах."""

    if frame is None or frame.empty:
        return _empty_bars_df()
    if "ts" not in frame.columns:
        raise ValueError("tick frame має містити колонку 'ts'")
    work = frame.copy()
    work["ts"] = pd.to_datetime(work["ts"], utc=True, errors="coerce")
    work = work.dropna(subset=["ts"]).sort_values("ts")
    if work.empty:
        return _empty_bars_df()
    price_col = next((c for c in ("price", "bid", "ask") if c in work.columns), None)
    if price_col is None:
        raise ValueError("tick frame має містити колонку price/bid/ask")
    work = work.set_index("ts")
    ohlc = work[price_col].resample("1T", label="left", closed="left").ohlc()
    if ohlc.empty:
        return _empty_bars_df()
    if "volume" in work.columns:
        volume = (
            work["volume"]
            .resample("1T", label="left", closed="left")
            .sum()
            .reindex(ohlc.index, fill_value=0.0)
        )
    else:
        volume = pd.Series(0.0, index=ohlc.index)
    result = ohlc.dropna(subset=["open", "high", "low", "close"], how="all").copy()
    if result.empty:
        return _empty_bars_df()
    result["volume"] = volume.astype(float)
    result["open_time"] = (result.index.view("int64") // 1_000_000).astype("int64")
    result["close_time"] = result["open_time"] + 60_000 - 1
    ordered = result.reset_index(drop=True)[BAR_COLS]
    return ordered


def fetch_duka(
    symbol: str,
    tf: TfAdapter,
    start_dt: datetime,
    end_dt: datetime,
    url_template: str | None = None,
    *,
    http: HttpClient | None = None,
) -> Iterator[dict]:
    """
    Завантажує дані Dukascopy (tick/M1) у форматі Bar або Tick.
    Параметри:
        symbol (str): Символ інструменту (наприклад, 'EURUSD').
        tf (TfAdapter): Таймфрейм для завантаження.
            'TICK' — тики (якщо доступно),
            'M1'   — хвилинні бари (або агрегація з тіків),
            'M5'/'H1' — НЕ підтримується напряму. Використовуйте ресемплінг з M1 поза адаптером.
        start_dt (datetime): Початкова дата-час (включно).
        end_dt (datetime): Кінцева дата-час (невключно).
        url_template (str | None, optional): Кастомний шаблон URL джерела. Якщо не вказано — використовується офіційний .bi5 формат.
        http (HttpClient | None, optional): Необов'язковий екземпляр HTTP-клієнта.
    Yield:
        dict: Для 'TICK' — словники з ключами: 'ts', 'price', 'bid', 'ask', 'volume'.
              Для 'M1' — словники з ключами: 'ts', 'open', 'high', 'low', 'close', 'volume'.
    Примітки:
        - Якщо url_template не заданий, функція завантажує офіційні .bi5 файли Dukascopy.
        - Для 'M1', якщо доступні лише тики, вони агрегуються у хвилинні бари.
        - Для 'M5'/'H1' пряме завантаження не підтримується; використовуйте ресемплінг з M1.
        - Повернуті дати-часи завжди у UTC.
        - Якщо дані відсутні — нічого не повертає.
    """
    logger.info(
        "Початок отримання Dukascopy: %s tf=%s %s..%s url_template=%s",
        symbol,
        tf,
        start_dt,
        end_dt,
        url_template,
    )
    if start_dt >= end_dt:
        logger.debug("Порожній діапазон для Duka %s %s", symbol, tf)
        return iter(())
    start_dt = ensure_utc(start_dt)
    end_dt = ensure_utc(end_dt)

    # --- .bi5 fallback ---
    if url_template is None:
        logger.info("Duka bi5 fallback активовано: спроба читати офіційні .bi5 файли")
        client = http if http is not None else HttpClient()

        def _hour_bins(a: datetime, b: datetime):
            cur = a.replace(minute=0, second=0, microsecond=0, tzinfo=UTC)
            while cur < b:
                yield cur
                cur += timedelta(hours=1)

        sym = _duka_symbol(symbol)
        for h in _hour_bins(start_dt, end_dt):
            # Увага: у Dukascopy місяці 0-індексовані (січень = 00, грудень = 11)
            # Ключовий блок: виправлення формату місяця для Dukascopy (0-індексація)
            month_idx = h.month - 1
            if month_idx < 0:
                logger.warning(
                    "Виправлення індексації місяця: отримано month=%d, встановлено 0",
                    h.month,
                )
                month_idx = 0
            logger.debug(
                "Формування URL для bi5: рік=%d, місяць(0-індекс)=%02d, день=%02d, година=%02d",
                h.year,
                month_idx,
                h.day,
                h.hour,
            )
            url = (
                f"https://datafeed.dukascopy.com/datafeed/{sym}/{h.year:04d}/"
                f"{month_idx:02d}/{h.day:02d}/{h.hour:02d}h_ticks.bi5"
            )

            blob: bytes | None = None
            raw: bytes | None = None
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                try:
                    blob = client.get(url)
                    raw = lzma.decompress(blob)
                    logger.debug(
                        "bi5 файл отримано: %s (%d байт) з %d-ї спроби",
                        url,
                        len(raw),
                        attempt,
                    )
                    break
                except lzma.LZMAError as e:
                    logger.warning(
                        "Помилка декомпресії bi5 %s (спроба %d/%d): %s",
                        url,
                        attempt,
                        max_attempts,
                        e,
                    )
                except Exception as e:
                    logger.warning(
                        "Не вдалося отримати bi5 %s (спроба %d/%d): %s",
                        url,
                        attempt,
                        max_attempts,
                        e,
                    )
                if attempt < max_attempts:
                    sleep_s = min(5.0, 0.5 * (2 ** (attempt - 1)))
                    time.sleep(sleep_s)
            if raw is None:
                logger.error(
                    "Пропускаю bi5 %s після %d невдалих спроб", url, max_attempts
                )
                continue

            rec_sz = 20
            base_ms = int(h.timestamp() * 1000)
            rows: list[dict] = []
            for i in range(0, len(raw), rec_sz):
                chunk = raw[i : i + rec_sz]
                if len(chunk) < rec_sz:
                    break
                try:
                    ms_off, ask_i, bid_i, av_i, bv_i = struct.unpack(">Iiiii", chunk)
                except struct.error as e:
                    logger.warning("struct.error у bi5: %s", e)
                    break
                ts_ms = base_ms + int(ms_off)
                if ts_ms < int(start_dt.timestamp() * 1000) or ts_ms >= int(
                    end_dt.timestamp() * 1000
                ):
                    continue
                bid = bid_i / 100000.0
                ask = ask_i / 100000.0
                rows.append(
                    {
                        "ts": pd.to_datetime(ts_ms, unit="ms", utc=True),
                        "price": (bid + ask) / 2.0,
                        "bid": bid,
                        "ask": ask,
                        "volume": float(max(0, av_i) + max(0, bv_i)),
                    }
                )

            if not rows:
                # ця година не містить корисних даних
                logger.debug("bi5: відсутні дані для %s at %s", symbol, h)
                continue

            df_hour = (
                pd.DataFrame.from_records(rows)
                .drop_duplicates(subset="ts")
                .sort_values("ts")
            )
            logger.info("bi5: отримано %d рядків для %s at %s", len(df_hour), symbol, h)

            if tf == "TICK":
                for _, r in df_hour.iterrows():
                    yield {
                        "ts": ensure_utc(pd.Timestamp(r["ts"]).to_pydatetime()),
                        "price": float(r["price"]),
                        "bid": float(r["bid"]),
                        "ask": float(r["ask"]),
                        "volume": float(r["volume"]),
                    }
                continue

            # Агрегація тіків цієї години у M1 та yield
            m1 = ticks_to_m1(df_hour[["ts", "price", "volume"]])
            if m1.empty:
                logger.debug(
                    "bi5: порожній результат агрегації M1 для %s at %s", symbol, h
                )
                continue
            s_ms = int(start_dt.timestamp() * 1000)
            e_ms = int(end_dt.timestamp() * 1000)
            m1 = m1[(m1["open_time"] >= s_ms) & (m1["open_time"] < e_ms)]
            for _, row in m1.iterrows():
                yield {
                    "ts": ensure_utc(
                        pd.to_datetime(int(row["open_time"]), unit="ms").to_pydatetime()
                    ),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0.0)),
                }
        # завершено цикл bi5
        return

    # --- CSV.GZ через url_template ---
    client = http if http is not None else HttpClient()

    def iter_hour_bins(a: datetime, b: datetime) -> Iterator[datetime]:
        cur = a.replace(minute=0, second=0, microsecond=0, tzinfo=UTC)
        while cur < b:
            yield cur
            cur += timedelta(hours=1)

    kind = "TICK" if tf == "TICK" else "M1"
    for h in iter_hour_bins(start_dt, end_dt):
        url = url_template.format(
            symbol=symbol,
            kind=kind.lower(),
            yyyy=f"{h.year:04d}",
            mm=f"{h.month:02d}",
            dd=f"{h.day:02d}",
            hh=f"{h.hour:02d}",
        )
        logger.debug("Формування URL для CSV.GZ: %s", url)
        try:
            blob = client.get(url)
            df = _parse_csv_gz(blob, kind=kind)
            if df is None or df.empty:
                logger.info("Duka fragment порожній: %s", url)
                continue
            logger.info("Duka fragment отримано: %d рядків з %s", len(df), h)
        except Exception as e:
            logger.warning("Duka fragment пропущено %s: %s", url, e)
            continue

        # Якщо отримали прямі M1 рядки (мають open/high), гарантуємо колонку open_time в ms
        if "open" in df.columns and "high" in df.columns:
            if "open_time" not in df.columns:
                if "ts" in df.columns:
                    df["open_time"] = (
                        pd.to_datetime(df["ts"], utc=True).astype("int64") // 10**6
                    ).astype("int64")
                else:
                    df_reset = df.reset_index()
                    df["open_time"] = (
                        pd.to_datetime(df_reset["index"], utc=True).astype("int64")
                        // 10**6
                    ).astype("int64")

            # yield M1 bars from this fragment
            start_ms = int(start_dt.timestamp() * 1000)
            end_ms = int(end_dt.timestamp() * 1000)
            m1 = df[(df["open_time"] >= start_ms) & (df["open_time"] < end_ms)]
            for _, row in m1.iterrows():
                yield {
                    "ts": ensure_utc(
                        pd.to_datetime(int(row["open_time"]), unit="ms").to_pydatetime()
                    ),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0.0)),
                }
            continue

        # інакше трактуємо як тики та або повертаємо тики, або агрегуємо у M1
        # гарантуємо наявність ts та видаляємо дублікати
        df = df[df["ts"].notna()]
        df = df.drop_duplicates(subset="ts").sort_values("ts")

        if tf == "TICK":
            for _, row in df.iterrows():
                item: dict[str, object] = {
                    "ts": ensure_utc(pd.Timestamp(row["ts"]).to_pydatetime())
                }
                for c in ("bid", "ask", "price", "volume"):
                    if c in df.columns:
                        val = row.get(c)
                        if pd.notna(val):
                            item[c] = float(val)
                yield item
            continue

        # tf == M1: агрегація тіків цього фрагменту у M1 та yield
        m1 = ticks_to_m1(df)
        if m1.empty:
            logger.debug(
                "CSV.GZ: порожній результат агрегації M1 для %s at %s", symbol, h
            )
            continue
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)
        m1 = m1[(m1["open_time"] >= start_ms) & (m1["open_time"] < end_ms)]
        for _, row in m1.iterrows():
            yield {
                "ts": ensure_utc(
                    pd.to_datetime(int(row["open_time"]), unit="ms").to_pydatetime()
                ),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0.0)),
            }
    # завершено цикл CSV.GZ
    return
