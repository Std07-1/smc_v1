"""3.2.5a: знімок + інвентаризація EQ-carriers (EQH/EQL bands).

Скрипт робить один HTTP-запит до UI endpoint `/smc-viewer/snapshot`,
зберігає сирий JSON у `reports/levels_eq_probe/<ts>/snapshot.json`
та генерує `probe.md` з фактами й шляхами (paths) для пошуку truth-carrier'ів EQ.

Ціль:
- НЕ вгадувати, де сидить EQ band truth;
- знайти фактичні носії (carrier) у payload: наприклад `liquidity.magnets[*].pools` з певним `liq_type`,
  або pool-обʼєкти з boundary-полями (`top/bot`, `high/low` тощо).

Використання (PowerShell):
  ; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" tools/levels_eq_probe_3_2_5a.py --symbol XAUUSD

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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import request

_EQ_RE = re.compile(r"\bEQ[HL]?\b|EQL|EQH", flags=re.IGNORECASE)


@dataclass(frozen=True)
class ProbeResult:
    out_dir: Path
    url: str
    state_path_hint: str

    liquidity_pools_count: int
    liquidity_pools_eqish_count: int
    liquidity_pools_bandish_count: int
    liquidity_pools_eqish_preview: list[dict[str, Any]]

    magnets_count: int
    magnets_pools_total_count: int
    magnets_pools_eqish_count: int
    magnets_pools_bandish_count: int
    magnets_pools_eqish_liq_type_freq_top: list[tuple[str, int]]
    magnets_pools_eqish_preview: list[dict[str, Any]]

    levels_shadow_eq_count: int
    levels_shadow_eq_preview: list[dict[str, Any]]


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _sorted_keys(d: dict[str, Any]) -> list[str]:
    return sorted([str(k) for k in d.keys()])


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


def _fetch_json(url: str, timeout_s: int = 10) -> Any:
    with request.urlopen(url, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw)


def _is_eqish_text(value: Any) -> bool:
    text = str(value or "")
    if not text.strip():
        return False
    return _EQ_RE.search(text) is not None


def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not (x == x):
        return None
    return float(x)


def _extract_band_bounds(
    pool: dict[str, Any],
) -> tuple[float | None, float | None, str]:
    """Best-effort: повертає (top, bot, source_key).

    Підтримка варіантів:
    - top/bot
    - high/low
    - upper/lower
    - hi/lo
    """

    candidates = [
        ("top", "bot"),
        ("high", "low"),
        ("upper", "lower"),
        ("hi", "lo"),
    ]

    for a, b in candidates:
        top = _num(pool.get(a))
        bot = _num(pool.get(b))
        if top is None or bot is None:
            continue
        return float(top), float(bot), f"{a}/{b}"

    return None, None, ""


def run_probe(base_url: str, symbol: str) -> ProbeResult:
    url = f"{base_url}/smc-viewer/snapshot?symbol={symbol}"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("reports") / "levels_eq_probe" / ts
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
            liquidity_pools_count=0,
            liquidity_pools_eqish_count=0,
            liquidity_pools_bandish_count=0,
            liquidity_pools_eqish_preview=[],
            magnets_count=0,
            magnets_pools_total_count=0,
            magnets_pools_eqish_count=0,
            magnets_pools_bandish_count=0,
            magnets_pools_eqish_liq_type_freq_top=[],
            magnets_pools_eqish_preview=[],
            levels_shadow_eq_count=0,
            levels_shadow_eq_preview=[],
        )

    liquidity_raw = state.get("liquidity")
    liquidity: dict[str, Any] = liquidity_raw if isinstance(liquidity_raw, dict) else {}

    # --- levels_shadow_v1 (as-is) ---
    shadow_raw = state.get("levels_shadow_v1")
    shadow: list[dict[str, Any]] = []
    if isinstance(shadow_raw, list):
        shadow = [x for x in shadow_raw if isinstance(x, dict)]

    shadow_eq = [
        x
        for x in shadow
        if _upper(x.get("label")) in {"EQH", "EQL"} or _is_eqish_text(x.get("label"))
    ]
    shadow_eq_preview: list[dict[str, Any]] = []
    for x in shadow_eq[:25]:
        shadow_eq_preview.append(
            {
                "tf": x.get("tf"),
                "label": x.get("label"),
                "kind": x.get("kind"),
                "top": x.get("top"),
                "bot": x.get("bot"),
                "price": x.get("price"),
                "source": x.get("source"),
                "render_title": (
                    (x.get("render_hint") or {})
                    if isinstance(x.get("render_hint"), dict)
                    else {}
                ).get("title"),
            }
        )

    # --- liquidity.pools ---
    pools_raw = liquidity.get("pools")
    pools: list[dict[str, Any]] = []
    if isinstance(pools_raw, list):
        pools = [p for p in pools_raw if isinstance(p, dict)]

    pools_eqish: list[dict[str, Any]] = []
    pools_bandish = 0
    pools_eqish_preview: list[dict[str, Any]] = []

    for p in pools:
        t = p.get("type") or p.get("kind")
        if _is_eqish_text(t) or _is_eqish_text(p.get("label")):
            pools_eqish.append(p)

        top, bot, key = _extract_band_bounds(p)
        if top is not None and bot is not None:
            pools_bandish += 1

    for p in pools_eqish[:25]:
        top, bot, key = _extract_band_bounds(p)
        pools_eqish_preview.append(
            {
                "type": p.get("type") or p.get("kind"),
                "role": p.get("role"),
                "tf": p.get("tf") or p.get("timeframe"),
                "price": p.get("price"),
                "top": top,
                "bot": bot,
                "bounds_key": key,
                "keys": _sorted_keys(p)[:20],
            }
        )

    # --- liquidity.magnets[*].pools ---
    magnets_raw = liquidity.get("magnets")
    magnets: list[dict[str, Any]] = []
    if isinstance(magnets_raw, list):
        magnets = [m for m in magnets_raw if isinstance(m, dict)]

    magnets_pools_total_count = 0
    magnets_pools_eqish: list[dict[str, Any]] = []
    magnets_pools_bandish_count = 0
    liq_type_freq: dict[str, int] = {}

    for m in magnets:
        m_pools_raw = m.get("pools")
        if not isinstance(m_pools_raw, list):
            continue
        m_pools = [p for p in m_pools_raw if isinstance(p, dict)]
        magnets_pools_total_count += len(m_pools)

        for p in m_pools:
            liq_type = p.get("liq_type")
            liq_upper = _upper(liq_type)
            if liq_upper:
                liq_type_freq[liq_upper] = int(liq_type_freq.get(liq_upper, 0)) + 1

            # EQ-ish detection: liq_type / type / kind / label / meta.type
            meta_obj = p.get("meta")
            meta = meta_obj if isinstance(meta_obj, dict) else {}

            eqish = (
                _is_eqish_text(liq_type)
                or _is_eqish_text(p.get("type") or p.get("kind"))
                or _is_eqish_text(p.get("label"))
                or _is_eqish_text(meta.get("type"))
            )
            if eqish:
                magnets_pools_eqish.append(p)

            top, bot, key = _extract_band_bounds(p)
            if top is not None and bot is not None:
                magnets_pools_bandish_count += 1

    # Частоти liq_type для EQ-ish pool'ів.
    eqish_liq_freq: dict[str, int] = {}
    for p in magnets_pools_eqish:
        liq_upper = _upper(p.get("liq_type"))
        if liq_upper:
            eqish_liq_freq[liq_upper] = int(eqish_liq_freq.get(liq_upper, 0)) + 1

    magnets_pools_eqish_preview: list[dict[str, Any]] = []
    for p in magnets_pools_eqish[:25]:
        meta_obj = p.get("meta")
        meta = meta_obj if isinstance(meta_obj, dict) else {}
        top, bot, key = _extract_band_bounds(p)
        magnets_pools_eqish_preview.append(
            {
                "liq_type": p.get("liq_type"),
                "level": p.get("level"),
                "price": p.get("price"),
                "role": p.get("role"),
                "tf": p.get("tf") or p.get("timeframe") or meta.get("tf"),
                "side": meta.get("side"),
                "top": top,
                "bot": bot,
                "bounds_key": key,
                "meta_keys": _sorted_keys(meta)[:20],
                "keys": _sorted_keys(p)[:20],
            }
        )

    eqish_liq_top = sorted(eqish_liq_freq.items(), key=lambda x: (-x[1], x[0]))[:20]

    return ProbeResult(
        out_dir=out_dir,
        url=url,
        state_path_hint=state_path,
        liquidity_pools_count=len(pools),
        liquidity_pools_eqish_count=len(pools_eqish),
        liquidity_pools_bandish_count=int(pools_bandish),
        liquidity_pools_eqish_preview=pools_eqish_preview,
        magnets_count=len(magnets),
        magnets_pools_total_count=int(magnets_pools_total_count),
        magnets_pools_eqish_count=len(magnets_pools_eqish),
        magnets_pools_bandish_count=int(magnets_pools_bandish_count),
        magnets_pools_eqish_liq_type_freq_top=eqish_liq_top,
        magnets_pools_eqish_preview=magnets_pools_eqish_preview,
        levels_shadow_eq_count=len(shadow_eq),
        levels_shadow_eq_preview=shadow_eq_preview,
    )


def _write_probe_md(result: ProbeResult) -> None:
    lines: list[str] = []
    lines.append("# 3.2.5a — EQ carrier census")
    lines.append("")
    lines.append(f"- url: {result.url}")
    lines.append(f"- saved: {result.out_dir.as_posix()}/snapshot.json")
    lines.append(f"- state_path_hint: {result.state_path_hint}")
    lines.append("")

    if result.state_path_hint == "NOT_FOUND":
        lines.append("- Не знайдено блоку `state` з `liquidity` у відповіді.")
        (result.out_dir / "probe.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        return

    lines.append("## Факти (as-is vs truth carriers)")
    lines.append(
        f"- levels_shadow_v1: EQH/EQL count={result.levels_shadow_eq_count} (це НЕ truth; це UI-відбір як є)"
    )
    lines.append(
        f"- liquidity.pools: count={result.liquidity_pools_count}; eq-ish={result.liquidity_pools_eqish_count}; bandish(bounds keys present)={result.liquidity_pools_bandish_count}"
    )
    lines.append(
        f"- liquidity.magnets[*].pools: magnets={result.magnets_count}; pools_total={result.magnets_pools_total_count}; eq-ish={result.magnets_pools_eqish_count}; bandish(bounds keys present)={result.magnets_pools_bandish_count}"
    )

    if result.magnets_pools_eqish_liq_type_freq_top:
        lines.append("")
        lines.append("## EQ-ish liq_type частоти (top)")
        for liq_type, n in result.magnets_pools_eqish_liq_type_freq_top:
            lines.append(f"- {liq_type}: {n}")

    if result.levels_shadow_eq_preview:
        lines.append("")
        lines.append("## levels_shadow_v1 EQ preview (перші 25)")
        for i, item in enumerate(result.levels_shadow_eq_preview, start=1):
            lines.append(
                "- "
                + f"#{i}: tf={item.get('tf')} label={item.get('label')} kind={item.get('kind')} "
                + f"top={item.get('top')} bot={item.get('bot')} price={item.get('price')} source={item.get('source')} "
                + f"title={item.get('render_title')}"
            )

    if result.liquidity_pools_eqish_preview:
        lines.append("")
        lines.append("## liquidity.pools EQ-ish preview (перші 25)")
        for i, item in enumerate(result.liquidity_pools_eqish_preview, start=1):
            lines.append(
                "- "
                + f"#{i}: type={item.get('type')} tf={item.get('tf')} role={item.get('role')} "
                + f"price={item.get('price')} top={item.get('top')} bot={item.get('bot')} bounds={item.get('bounds_key')}"
            )

    if result.magnets_pools_eqish_preview:
        lines.append("")
        lines.append("## liquidity.magnets[*].pools EQ-ish preview (перші 25)")
        for i, item in enumerate(result.magnets_pools_eqish_preview, start=1):
            lines.append(
                "- "
                + f"#{i}: liq_type={item.get('liq_type')} level={item.get('level')} role={item.get('role')} "
                + f"tf={item.get('tf')} side={item.get('side')} top={item.get('top')} bot={item.get('bot')} bounds={item.get('bounds_key')}"
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

    result = run_probe(base_url=base_url.rstrip("/"), symbol=str(symbol).upper())
    _write_probe_md(result)

    print(f"OK {result.out_dir.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
