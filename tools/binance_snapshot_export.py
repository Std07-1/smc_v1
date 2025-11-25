"""
Утиліта для експорту OHLCV з Binance у формат JSONL для datastore.
Приклад використання:
python.exe" -m tools.binance_snapshot_export XAUUSD --interval 5m --limit 2000 --out datastore\xauusd_bars_5m_snapshot.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import aiohttp
import pandas as pd

from data.raw_data import BINANCE_V3_URL, OptimizedDataFetcher


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Завантажує останні klines з Binance та зберігає у JSONL."
    )
    parser.add_argument("symbol", help="Тікер, наприклад XAUUSD")
    parser.add_argument(
        "--interval",
        default="5m",
        help="Інтервал свічок Binance (default: 5m)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=2000,
        help="Кількість барів для завантаження (<= 1000 за запит)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Шлях до JSONL-файлу (буде перезаписаний)",
    )
    parser.add_argument(
        "--base-url",
        default=BINANCE_V3_URL,
        help="API endpoint Binance (при необхідності можна перевизначити)",
    )
    return parser.parse_args()


async def _fetch_df(
    symbol: str, interval: str, limit: int, base_url: str
) -> pd.DataFrame:
    async with aiohttp.ClientSession() as session:
        fetcher = OptimizedDataFetcher(session, base_url=base_url)
        return await fetcher.get_data(symbol, interval, limit=limit)


def _interval_to_ms(interval: str) -> int:
    mapping = {
        "1m": 60_000,
        "3m": 180_000,
        "5m": 300_000,
        "15m": 900_000,
        "30m": 1_800_000,
        "1h": 3_600_000,
        "2h": 7_200_000,
        "4h": 14_400_000,
        "6h": 21_600_000,
        "8h": 28_800_000,
        "12h": 43_200_000,
        "1d": 86_400_000,
    }
    return mapping.get(interval, 300_000)


def _row_to_record(row: Any, interval_ms: int) -> dict[str, Any]:
    payload = row._asdict() if hasattr(row, "_asdict") else dict(row)
    open_time = int(payload["timestamp"])
    close_time = open_time + interval_ms - 1
    return {
        "open_time": open_time,
        "open": float(payload["open"]),
        "high": float(payload["high"]),
        "low": float(payload["low"]),
        "close": float(payload["close"]),
        "volume": float(payload["volume"]),
        "close_time": close_time,
        "quote_asset_volume": 0.0,
        "trades": 0,
        "taker_buy_base": "0",
        "taker_buy_quote": "0",
        "ignore": "0",
        "is_closed": True,
    }


async def main() -> None:
    args = _parse_args()
    df = await _fetch_df(args.symbol, args.interval, args.limit, args.base_url)
    if df is None or df.empty:
        raise SystemExit("Не вдалося завантажити дані з Binance")

    interval_ms = _interval_to_ms(args.interval)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8") as fh:
        for row in df.itertuples(index=False):
            record = _row_to_record(row, interval_ms)
            fh.write(json.dumps(record, ensure_ascii=False))
            fh.write("\n")

    print(
        f"Збережено {len(df)} рядків у {out_path} (symbol={args.symbol}, interval={args.interval})."
    )


if __name__ == "__main__":
    asyncio.run(main())
