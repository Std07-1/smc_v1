"""Порівняння top-POI між двома офлайн QA прогонами.

Сценарій:
- запускаємо `tools/qa_snapshot_poi.py` з різними `--limit` і `--json`.
- цим скриптом порівнюємо `zones_meta.active_poi` між двома JSON.

Використання (PowerShell):
; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" tools/qa_compare_poi_limits.py \
    --a tmp/qa_poi_5m_limit800.json --a-name 800 \
    --b tmp/qa_poi_5m_limit5000.json --b-name 5000
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _norm_item(item: dict[str, Any]) -> dict[str, Any]:
    def _f(x: Any) -> float | None:
        try:
            return float(x)
        except Exception:
            return None

    return {
        "type": item.get("type"),
        "direction": item.get("direction"),
        "role": item.get("role"),
        "price_min": _f(item.get("price_min")),
        "price_max": _f(item.get("price_max")),
        "score": _f(item.get("score")),
        "filled_pct": _f(item.get("filled_pct")),
    }


def _key(item: dict[str, Any]) -> tuple[Any, Any, float, float]:
    # Ключ «ідентичності» POI: тип+напрям+діапазон.
    # Округлення прибирає шум float.
    pmin = float(item.get("price_min") or 0.0)
    pmax = float(item.get("price_max") or 0.0)
    return (
        item.get("type"),
        item.get("direction"),
        round(pmin, 2),
        round(pmax, 2),
    )


def _load_active_poi(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    zones_meta = data.get("zones_meta") or {}
    raw = zones_meta.get("active_poi") or []
    items = [_norm_item(x) for x in raw if isinstance(x, dict)]
    # Сортуємо за score (desc), None в кінець.
    return sorted(items, key=lambda x: (x["score"] is None, -(x["score"] or 0.0)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="Перший QA JSON")
    ap.add_argument("--b", required=True, help="Другий QA JSON")
    ap.add_argument("--a-name", default="A", help="Мітка для першого прогону")
    ap.add_argument("--b-name", default="B", help="Мітка для другого прогону")
    args = ap.parse_args()

    pa = Path(args.a)
    pb = Path(args.b)
    if not pa.exists():
        print(f"ПОМИЛКА: файл не знайдено: {pa}")
        return 2
    if not pb.exists():
        print(f"ПОМИЛКА: файл не знайдено: {pb}")
        return 2

    a = _load_active_poi(pa)
    b = _load_active_poi(pb)

    print(f"== active_poi top (limit={args.a_name}) ==")
    for i, it in enumerate(a, start=1):
        print(
            f"#{i} {it['type']} {it['direction']} [{it['price_min']}..{it['price_max']}] "
            f"score={it['score']} filled={it['filled_pct']}"
        )

    print(f"== active_poi top (limit={args.b_name}) ==")
    for i, it in enumerate(b, start=1):
        print(
            f"#{i} {it['type']} {it['direction']} [{it['price_min']}..{it['price_max']}] "
            f"score={it['score']} filled={it['filled_pct']}"
        )

    set_a = {_key(it) for it in a}
    set_b = {_key(it) for it in b}

    print("== порівняння ==")
    print(f"спільні: {len(set_a & set_b)}")
    print(f"тільки {args.a_name}: {sorted(set_a - set_b)}")
    print(f"тільки {args.b_name}: {sorted(set_b - set_a)}")

    # Для спільних: показуємо Δscore/Δfilled
    common = sorted(set_a & set_b)
    for k in common:
        it_a = next(it for it in a if _key(it) == k)
        it_b = next(it for it in b if _key(it) == k)
        ds = None
        if it_a.get("score") is not None and it_b.get("score") is not None:
            ds = float(it_b["score"]) - float(it_a["score"])
        df = None
        if it_a.get("filled_pct") is not None and it_b.get("filled_pct") is not None:
            df = float(it_b["filled_pct"]) - float(it_a["filled_pct"])
        print(
            f"Δ {k}: score {it_a.get('score')} -> {it_b.get('score')} (Δ={ds}); "
            f"filled {it_a.get('filled_pct')} -> {it_b.get('filled_pct')} (Δ={df})"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
