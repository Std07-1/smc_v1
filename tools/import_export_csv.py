"""Конвертує CSV-експорти XAUUSD у snapshot-и UnifiedDataStore."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from config.config import DATASTORE_BASE_DIR

logger = logging.getLogger("tools.import_export_csv")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def _build_source_path(base: Path, symbol: str, interval: str, window: str) -> Path:
    fname = f"{symbol.upper()}_{interval}_{window}.csv"
    return base / fname


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Перетворює CSV у JSONL снапшот для UnifiedDataStore",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("symbol", help="Символ, напр. XAUUSD")
    parser.add_argument("interval", help="Таймфрейм, напр. 1m або 5m")
    parser.add_argument(
        "--window",
        default="30d",
        help="Суфікс файла експорту (7d/14d/30d)",
    )
    parser.add_argument(
        "--source-dir",
        default=str(Path("datastore") / "exports"),
        help="Каталог із CSV-експортами",
    )
    parser.add_argument(
        "--output",
        help="Кастомний шлях для JSONL snapshot. За замовчуванням — datastore/<symbol>_bars_<tf>_snapshot.jsonl",
    )
    return parser.parse_args(argv)


def _normalize(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    mandatory = ["open_time", "open", "high", "low", "close", "volume"]
    missing = [col for col in mandatory if col not in df.columns]
    if missing:
        raise ValueError(f"У CSV відсутні стовпці: {missing}")
    filtered = df[df["symbol"].str.lower() == symbol]
    filtered = filtered.sort_values("open_time").reset_index(drop=True)
    return filtered[mandatory]


def _write_snapshot(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_json(
        path_or_buf=tmp,
        orient="records",
        lines=True,
        date_format="iso",
        date_unit="ms",
        force_ascii=False,
        index=False,
    )
    tmp.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    symbol = args.symbol.lower()
    interval = args.interval.lower()
    window = args.window.lower()

    source_dir = Path(args.source_dir)
    csv_path = _build_source_path(source_dir, args.symbol, args.interval, args.window)
    if not csv_path.exists():
        logger.error("CSV-файл %s не знайдено", csv_path)
        return 1

    logger.info("Зчитую %s", csv_path)
    df = pd.read_csv(csv_path)
    normalized = _normalize(df, symbol)

    if args.output:
        output_path = Path(args.output)
    else:
        base = Path(DATASTORE_BASE_DIR)
        output_path = base / f"{symbol}_bars_{interval}_snapshot.jsonl"
    _write_snapshot(normalized, output_path)
    logger.info("Збережено %s рядків у %s", len(normalized), output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
