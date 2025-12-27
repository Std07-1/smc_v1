"""3.2.4b0: знімок + інвентаризація RANGE-carriers.

Скрипт робить один HTTP-запит до UI endpoint `/smc-viewer/snapshot`,
зберігає сирий JSON у `reports/levels_range_probe/<ts>/snapshot.json`
та генерує `probe.md` з фактами й шляхами (paths) для RANGE.

Використання (PowerShell):
  ; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" tools/levels_range_probe_b0.py --symbol XAUUSD

Опційно:
  --base-url http://127.0.0.1:8080

ENV (як дефолти):
  SMC_VIEWER_BASE_URL
  SMC_VIEWER_SYMBOL
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProbeResult:
    out_dir: Path
    url: str
    state_path_hint: str
    liquidity_meta_present: bool
    liquidity_meta_keys: list[str]
    liquidity_meta_range_keys: list[str]
    key_levels_present: bool
    key_levels_keys: list[str]
    key_levels_range_keys: list[str]
    pools_count: int
    range_extreme_count: int
    range_extreme_preview: list[dict[str, Any]]

    magnets_count: int
    magnets_pools_total_count: int
    magnets_range_extreme_count: int
    magnets_range_extreme_preview: list[dict[str, Any]]


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _find_state(obj: Any, path: str = "$") -> tuple[dict[str, Any] | None, str]:
    """Повертає перший dict, який виглядає як `state` (має `liquidity`)."""

    if isinstance(obj, dict):
        if "liquidity" in obj and isinstance(obj.get("liquidity"), dict):
            return obj, path

        for key in ("state", "viewer_state", "data", "result"):
            child = obj.get(key)
            if isinstance(child, dict):
                found, found_path = _find_state(child, f"{path}.{key}")
                if found is not None:
                    return found, found_path

        for k, child in obj.items():
            found, found_path = _find_state(child, f"{path}.{k}")
            if found is not None:
                return found, found_path

    if isinstance(obj, list):
        for i, child in enumerate(obj):
            found, found_path = _find_state(child, f"{path}[{i}]")
            if found is not None:
                return found, found_path

    return None, ""


def _sorted_keys(d: dict[str, Any]) -> list[str]:
    return sorted([str(k) for k in d.keys()])


def _rangeish_keys(keys: list[str]) -> list[str]:
    return [k for k in keys if re.search(r"(range|dr)", k, flags=re.IGNORECASE)]


def _fetch_json(url: str, timeout_s: int = 10) -> Any:
    with urllib.request.urlopen(url, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw)


def run_probe(base_url: str, symbol: str) -> ProbeResult:
    url = f"{base_url}/smc-viewer/snapshot?symbol={symbol}"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("reports") / "levels_range_probe" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = _fetch_json(url)
    (out_dir / "snapshot.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    state, state_path = _find_state(payload)
    if not isinstance(state, dict):
        return ProbeResult(
            out_dir=out_dir,
            url=url,
            state_path_hint="NOT_FOUND",
            liquidity_meta_present=False,
            liquidity_meta_keys=[],
            liquidity_meta_range_keys=[],
            key_levels_present=False,
            key_levels_keys=[],
            key_levels_range_keys=[],
            pools_count=0,
            range_extreme_count=0,
            range_extreme_preview=[],
            magnets_count=0,
            magnets_pools_total_count=0,
            magnets_range_extreme_count=0,
            magnets_range_extreme_preview=[],
        )

    liquidity_raw = state.get("liquidity")
    liquidity: dict[str, Any] = liquidity_raw if isinstance(liquidity_raw, dict) else {}

    meta_raw = liquidity.get("meta")
    meta = meta_raw if isinstance(meta_raw, dict) else None

    key_levels_raw = state.get("key_levels")
    key_levels = key_levels_raw if isinstance(key_levels_raw, dict) else None

    liquidity_meta_present = isinstance(meta, dict)
    liquidity_meta_keys = _sorted_keys(meta) if isinstance(meta, dict) else []
    liquidity_meta_range_keys = _rangeish_keys(liquidity_meta_keys)

    key_levels_present = isinstance(key_levels, dict)
    key_levels_keys = _sorted_keys(key_levels) if isinstance(key_levels, dict) else []
    key_levels_range_keys = _rangeish_keys(key_levels_keys)

    pools: list[dict[str, Any]] = []
    pools_raw = liquidity.get("pools")
    if isinstance(pools_raw, list):
        pools = [p for p in pools_raw if isinstance(p, dict)]

    range_extreme = [
        p for p in pools if _upper(p.get("type") or p.get("kind")) == "RANGE_EXTREME"
    ]

    preview: list[dict[str, Any]] = []
    for p in range_extreme[:25]:
        preview.append(
            {
                "price": p.get("price"),
                "role": p.get("role"),
                "direction": p.get("direction") or p.get("dir"),
                "tf": p.get("tf") or p.get("timeframe"),
            }
        )

    magnets_raw = liquidity.get("magnets")
    magnets: list[dict[str, Any]] = []
    if isinstance(magnets_raw, list):
        magnets = [m for m in magnets_raw if isinstance(m, dict)]

    magnets_pools_total_count = 0
    magnets_range_extreme: list[dict[str, Any]] = []

    for m in magnets:
        m_pools_raw = m.get("pools")
        if not isinstance(m_pools_raw, list):
            continue
        m_pools = [p for p in m_pools_raw if isinstance(p, dict)]
        magnets_pools_total_count += len(m_pools)
        for p in m_pools:
            if _upper(p.get("liq_type")) == "RANGE_EXTREME":
                magnets_range_extreme.append(p)

    magnets_range_extreme_preview: list[dict[str, Any]] = []
    for p in magnets_range_extreme[:25]:
        meta_obj = p.get("meta")
        meta = meta_obj if isinstance(meta_obj, dict) else {}
        magnets_range_extreme_preview.append(
            {
                "level": p.get("level"),
                "liq_type": p.get("liq_type"),
                "role": p.get("role"),
                "side": meta.get("side"),
                "source": meta.get("source"),
            }
        )

    return ProbeResult(
        out_dir=out_dir,
        url=url,
        state_path_hint=state_path,
        liquidity_meta_present=liquidity_meta_present,
        liquidity_meta_keys=liquidity_meta_keys,
        liquidity_meta_range_keys=liquidity_meta_range_keys,
        key_levels_present=key_levels_present,
        key_levels_keys=key_levels_keys,
        key_levels_range_keys=key_levels_range_keys,
        pools_count=len(pools),
        range_extreme_count=len(range_extreme),
        range_extreme_preview=preview,
        magnets_count=len(magnets),
        magnets_pools_total_count=magnets_pools_total_count,
        magnets_range_extreme_count=len(magnets_range_extreme),
        magnets_range_extreme_preview=magnets_range_extreme_preview,
    )


def _write_probe_md(result: ProbeResult) -> None:
    lines: list[str] = []
    lines.append("# 3.2.4b0 — Range carrier census")
    lines.append("")
    lines.append(f"- url: {result.url}")
    lines.append(f"- saved: {result.out_dir.as_posix()}/snapshot.json")
    lines.append(f"- state_path_hint: {result.state_path_hint}")
    lines.append("")

    lines.append("## Поля (шляхи) + факти")
    if result.state_path_hint == "NOT_FOUND":
        lines.append("- Не знайдено блоку `state` з `liquidity` у відповіді.")
    else:
        lines.append(
            f"- {result.state_path_hint}.liquidity.meta: "
            f"{'PRESENT' if result.liquidity_meta_present else 'ABSENT'}"
        )
        if result.liquidity_meta_present:
            lines.append(f"  - keys={len(result.liquidity_meta_keys)}")
            lines.append(f"  - range/dr keys: {result.liquidity_meta_range_keys}")

        lines.append(
            f"- {result.state_path_hint}.key_levels: "
            f"{'PRESENT' if result.key_levels_present else 'ABSENT'}"
        )
        if result.key_levels_present:
            lines.append(f"  - keys={len(result.key_levels_keys)}")
            lines.append(f"  - range/dr keys: {result.key_levels_range_keys}")

        lines.append(
            f"- {result.state_path_hint}.liquidity.pools: count={result.pools_count}; "
            f"RANGE_EXTREME={result.range_extreme_count}"
        )

        lines.append(
            f"- {result.state_path_hint}.liquidity.magnets: count={result.magnets_count}; "
            f"pools_total={result.magnets_pools_total_count}; "
            f"RANGE_EXTREME(liq_type)={result.magnets_range_extreme_count}"
        )

    if result.range_extreme_preview:
        lines.append("")
        lines.append("## RANGE_EXTREME preview (перші 25)")
        for i, item in enumerate(result.range_extreme_preview, start=1):
            lines.append(
                "- "
                + f"#{i}: price={item.get('price')} role={item.get('role')} "
                + f"direction={item.get('direction')} tf={item.get('tf')}"
            )

    if result.magnets_range_extreme_preview:
        lines.append("")
        lines.append("## RANGE_EXTREME в liquidity.magnets[*].pools preview (перші 25)")
        for i, item in enumerate(result.magnets_range_extreme_preview, start=1):
            lines.append(
                "- "
                + f"#{i}: level={item.get('level')} liq_type={item.get('liq_type')} "
                + f"role={item.get('role')} side={item.get('side')} source={item.get('source')}"
            )

    (result.out_dir / "probe.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=None,
        help="База UI (default: ENV SMC_VIEWER_BASE_URL або http://127.0.0.1:8080)",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Символ (default: ENV SMC_VIEWER_SYMBOL або XAUUSD)",
    )
    args = parser.parse_args()

    base_url = args.base_url or ""
    if not base_url:
        base_url = "http://127.0.0.1:8080"
        base_url = (
            base_url
            if "SMC_VIEWER_BASE_URL" not in __import__("os").environ
            else __import__("os").environ["SMC_VIEWER_BASE_URL"]
        )

    symbol = args.symbol or ""
    if not symbol:
        symbol = "XAUUSD"
        symbol = (
            symbol
            if "SMC_VIEWER_SYMBOL" not in __import__("os").environ
            else __import__("os").environ["SMC_VIEWER_SYMBOL"]
        )

    result = run_probe(base_url=base_url, symbol=symbol)
    _write_probe_md(result)

    print(f"OK {result.out_dir.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
