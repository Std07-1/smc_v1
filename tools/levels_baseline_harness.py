"""Baseline harness для Levels-V1 (as-is).

Призначення:
- Зафіксувати "як є зараз" у числах/JSON до будь-яких змін presentation-логіки Levels.
- Бере `viewer_state` через HTTP endpoint UI_v2 (`/smc-viewer/snapshot`) і емулює
  поточний UI-відбір "рівнів" як підмножину `viewer_state.liquidity.pools`.

Важливо:
- Це інструмент вимірювання, а не нова production-логіка.
- У цій хвилі ми не змінюємо SMC-core; лише фіксуємо baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.config import SMC_SESSION_WINDOWS_UTC


def _ensure_repo_on_syspath() -> None:
    """Гарантує імпорти з кореня репо при запуску як скрипта.

    У Windows запуск `python tools/xxx.py` додає в sys.path лише папку `tools/`,
    тому локальні імпорти (`config`, `core`) можуть не знаходитися.
    """

    if __package__:
        return
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))


_ensure_repo_on_syspath()


def _utc_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _http_get_json(url: str, *, timeout_sec: float = 10.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise RuntimeError(f"HTTP {e.code} для {url}. body={body[:4000]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Не вдалося підключитись до {url}: {e}") from e

    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        head = raw[:200].decode("utf-8", errors="replace")
        raise RuntimeError(f"Не JSON у відповіді {url}. head={head!r}") from e


def _clamp01(value: Any) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not (num == num):
        return 0.0
    return max(0.0, min(1.0, num))


def _role_weight(role: Any) -> float:
    r = str(role or "").upper()
    if r == "PRIMARY":
        return 1.0
    if r == "COUNTER":
        return 0.6
    return 0.5


def _estimate_price_window_abs(
    *, ref_price: float, ohlcv_bars: list[dict[str, Any]]
) -> float:
    # JS (chart_adapter.js): refComponent=abs(ref)*0.0015; tail=last 80 bars;
    # atrLike=(span/n)*14; volComponent=atrLike*0.6; max(...,0.5)
    ref_component = abs(float(ref_price)) * 0.0015
    tail = (ohlcv_bars or [])[-80:]
    max_high: float | None = None
    min_low: float | None = None

    for bar in tail:
        if not isinstance(bar, dict):
            continue
        high_raw = bar.get("high")
        low_raw = bar.get("low")
        try:
            high = float(high_raw) if high_raw is not None else None
            low = float(low_raw) if low_raw is not None else None
        except (TypeError, ValueError):
            continue
        if high is None or low is None:
            continue
        if not (high == high and low == low):
            continue
        max_high = high if max_high is None else max(max_high, high)
        min_low = low if min_low is None else min(min_low, low)

    n = max(1, len(tail))
    span = 0.0
    if max_high is not None and min_low is not None:
        span = max(0.0, max_high - min_low)

    atr_like = (span / n) * 14.0
    vol_component = atr_like * 0.6
    return max(ref_component, vol_component, 0.5)


def _estimate_merge_tol_abs(*, ref_price: float, price_window_abs: float) -> float:
    # JS: max(abs(ref)*0.00025, priceWindowAbs*0.08, 0.2)
    ref_component = abs(float(ref_price)) * 0.00025
    window_component = float(price_window_abs) * 0.08
    return max(ref_component, window_component, 0.2)


def _pool_score(
    pool: dict[str, Any], *, ref_price: float, price_window_abs: float
) -> float:
    price_raw = pool.get("price")
    if price_raw is None:
        return float("-inf")
    try:
        price = float(price_raw)
    except (TypeError, ValueError):
        return float("-inf")
    if not (price == price):
        return float("-inf")

    ref = float(ref_price)
    strength_raw = pool.get("strength")
    if strength_raw is None:
        strength = float("nan")
    else:
        try:
            strength = float(strength_raw)
        except (TypeError, ValueError):
            strength = float("nan")
    strength_norm = _clamp01(strength / 100.0) if (strength == strength) else 0.3

    dist_norm_raw = abs(price - ref) / max(1e-9, float(price_window_abs) or 1.0)
    dist_norm = min(6.0, max(0.0, dist_norm_raw))

    return _role_weight(pool.get("role")) * (1.0 + strength_norm) / (1.0 + dist_norm)


def _choose_better_pool(
    a: dict[str, Any], b: dict[str, Any], *, ref_price: float
) -> dict[str, Any]:
    ra = _role_weight(a.get("role"))
    rb = _role_weight(b.get("role"))
    if ra != rb:
        return a if ra > rb else b

    def _num(v: Any) -> float:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return float("-inf")
        return x if (x == x) else float("-inf")

    sa = _num(a.get("strength"))
    sb = _num(b.get("strength"))
    if sa != sb:
        return a if sa > sb else b

    ta = _num(a.get("touches"))
    tb = _num(b.get("touches"))
    if ta != tb:
        return a if ta > tb else b

    ref = float(ref_price)
    # Безпечне перетворення price з явною перевіркою None, щоб статичний аналізатор не скаржився
    try:
        pa_raw = a.get("price")
        if pa_raw is None:
            raise TypeError("no price")
        da = abs(float(pa_raw) - ref)
    except (TypeError, ValueError):
        da = float("inf")
    try:
        pb_raw = b.get("price")
        if pb_raw is None:
            raise TypeError("no price")
        db = abs(float(pb_raw) - ref)
    except (TypeError, ValueError):
        db = float("inf")

    return a if da <= db else b


def _dedup_pools_by_price(
    pools: Iterable[dict[str, Any]], *, merge_tol_abs: float, ref_price: float
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for p in pools:
        if not isinstance(p, dict):
            continue
        raw_price = p.get("price")
        if raw_price is None:
            continue
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            continue
        if not (price == price):
            continue
        copied = dict(p)
        copied["price"] = price
        cleaned.append(copied)

    cleaned.sort(key=lambda x: float(x["price"]))
    if not cleaned:
        return []

    tol = max(0.0, float(merge_tol_abs) or 0.0)
    out: list[dict[str, Any]] = []
    for p in cleaned:
        if not out:
            out.append(p)
            continue
        last = out[-1]
        if abs(float(p["price"]) - float(last["price"])) <= tol:
            out[-1] = _choose_better_pool(last, p, ref_price=ref_price)
        else:
            out.append(p)
    return out


def _short_pool_title(pool: dict[str, Any]) -> str:
    # JS: type = pool.type || pool.kind || "POOL"; role mark P/C; type sliced to 6.
    t = str(pool.get("type") or pool.get("kind") or "POOL").upper()
    role = str(pool.get("role") or "").upper()
    role_mark = "P" if role == "PRIMARY" else "C" if role == "COUNTER" else ""
    type_short = t[:6] if len(t) > 6 else t
    return f"{type_short}{(' ' + role_mark) if role_mark else ''}".strip()


@dataclass(frozen=True)
class UiPoolsSelectionAsLevels:
    local: list[dict[str, Any]]
    global_: list[dict[str, Any]]
    ref_price: float
    price_window_abs: float
    merge_tol_abs: float


def select_pools_for_render_as_levels(
    pools: list[dict[str, Any]] | None,
    *,
    ref_price: float | None,
    ohlcv_bars: list[dict[str, Any]] | None,
) -> UiPoolsSelectionAsLevels:
    """Емулює UI_v2 `selectPoolsForRender(pools)` як baseline визначення "levels".

    Це не production-відбір Levels-V1, а "as-is" контрольна точка.
    """

    if ref_price is None or not isinstance(ref_price, (int, float)):
        return UiPoolsSelectionAsLevels([], [], 0.0, 1.0, 0.2)

    ref = float(ref_price)
    price_window_abs = _estimate_price_window_abs(
        ref_price=ref, ohlcv_bars=ohlcv_bars or []
    )
    merge_tol_abs = _estimate_merge_tol_abs(
        ref_price=ref, price_window_abs=price_window_abs
    )

    def is_strong_enough_for_chart(p: dict[str, Any]) -> bool:
        if not isinstance(p, dict):
            return False
        if bool(p.get("_isTarget")):
            return True
        role = str(p.get("role") or "").upper()
        if role in {"PRIMARY", "P"}:
            return True

        strength = p.get("strength", p.get("strength_score"))
        touches = p.get("touches", p.get("touch_count"))

        if touches is None:
            touches_n = float("nan")
        else:
            try:
                touches_n = float(touches)
            except (TypeError, ValueError):
                touches_n = float("nan")
        if touches_n == touches_n and touches_n >= 2:
            return True

        if strength is None:
            strength_n = float("nan")
        else:
            try:
                strength_n = float(strength)
            except (TypeError, ValueError):
                strength_n = float("nan")
        if strength_n == strength_n and strength_n >= 20:
            return True

        return False

    prefiltered = [
        p
        for p in (pools or [])
        if isinstance(p, dict) and is_strong_enough_for_chart(p)
    ]
    deduped = _dedup_pools_by_price(
        prefiltered, merge_tol_abs=merge_tol_abs, ref_price=ref
    )

    above = [p for p in deduped if float(p["price"]) >= ref]
    below = [p for p in deduped if float(p["price"]) < ref]

    def scored(arr: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float]]:
        rows = [
            (p, _pool_score(p, ref_price=ref, price_window_abs=price_window_abs))
            for p in arr
        ]
        rows = [(p, s) for (p, s) in rows if s == s and s != float("-inf")]
        rows.sort(key=lambda x: x[1], reverse=True)
        return rows

    above_scored = scored(above)
    below_scored = scored(below)

    def pick_primary(rows: list[tuple[dict[str, Any], float]]) -> dict[str, Any] | None:
        for p, _s in rows:
            if str(p.get("role") or "").upper() == "PRIMARY":
                return p
        return None

    local_above: list[dict[str, Any]] = []
    local_below: list[dict[str, Any]] = []

    primary_above = pick_primary(above_scored)
    primary_below = pick_primary(below_scored)
    if primary_above is not None:
        local_above.append(primary_above)
    if primary_below is not None:
        local_below.append(primary_below)

    def fill_side(
        rows: list[tuple[dict[str, Any], float]],
        target: list[dict[str, Any]],
        max_count: int,
    ) -> None:
        for p, _s in rows:
            if len(target) >= max_count:
                break
            if any(float(x["price"]) == float(p["price"]) for x in target):
                continue
            target.append(p)

    fill_side(above_scored, local_above, 3)
    fill_side(below_scored, local_below, 3)

    local = [*local_above, *local_below]

    def nearest(arr: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not arr:
            return None
        return sorted(arr, key=lambda p: abs(float(p["price"]) - ref))[0]

    local_nearest_above = nearest(local_above)
    local_nearest_below = nearest(local_below)

    def is_local(p: dict[str, Any]) -> bool:
        return any(float(x["price"]) == float(p["price"]) for x in local)

    def far_enough(p: dict[str, Any]) -> bool:
        return abs(float(p["price"]) - ref) >= price_window_abs * 1.2

    def pick_global(rows: list[tuple[dict[str, Any], float]]) -> dict[str, Any] | None:
        for p, _s in rows:
            if is_local(p):
                continue
            if not far_enough(p):
                continue
            return p
        return None

    global_out: list[dict[str, Any]] = []
    global_above = pick_global(above_scored)
    global_below = pick_global(below_scored)
    if global_above is not None:
        global_out.append(global_above)
    if global_below is not None:
        global_out.append(global_below)

    def decorate_local(p: dict[str, Any]) -> dict[str, Any]:
        price = float(p["price"])
        axis_label = (
            local_nearest_above is not None
            and price == float(local_nearest_above["price"])
        ) or (
            local_nearest_below is not None
            and price == float(local_nearest_below["price"])  # noqa: W503
        )
        out = dict(p)
        out["_axisLabel"] = bool(axis_label)
        out["_lineVisible"] = True
        return out

    def decorate_global(p: dict[str, Any]) -> dict[str, Any]:
        out = dict(p)
        out["_axisLabel"] = True
        out["_lineVisible"] = False
        return out

    return UiPoolsSelectionAsLevels(
        local=[decorate_local(p) for p in local],
        global_=[decorate_global(p) for p in global_out],
        ref_price=ref,
        price_window_abs=float(price_window_abs),
        merge_tol_abs=float(merge_tol_abs),
    )


def rendered_levels_items(selection: UiPoolsSelectionAsLevels) -> list[dict[str, Any]]:
    """Перетворює selection у список елементів, які UI реально рендерить як "рівні"."""

    items: list[dict[str, Any]] = []
    for p in [*(selection.local or []), *(selection.global_ or [])]:
        if not isinstance(p, dict):
            continue
        price_raw = p.get("price")
        if price_raw is None:
            continue
        try:
            price = float(price_raw)
        except (TypeError, ValueError):
            continue
        if not (price == price):
            continue

        title = _short_pool_title(p)
        items.append(
            {
                "price": price,
                "title": title,
                "type": str(p.get("type") or p.get("kind") or "").upper() or None,
                "role": str(p.get("role") or "").upper() or None,
                "axis_label": bool(p.get("_axisLabel")),
                "line_visible": bool(p.get("_lineVisible")),
                "is_target": bool(p.get("_isTarget")),
            }
        )

    # Стабілізуємо порядок для хеша/звітів.
    items.sort(key=lambda x: (round(float(x["price"]), 6), str(x.get("title") or "")))
    return items


def geometry_hash(items: list[dict[str, Any]]) -> str:
    payload = [
        {
            "price": round(float(it["price"]), 6),
            "title": str(it.get("title") or ""),
            "axis_label": bool(it.get("axis_label")),
            "line_visible": bool(it.get("line_visible")),
        }
        for it in (items or [])
    ]
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def shadow_items_from_viewer_state(
    viewer_state: dict[str, Any], *, tf: str
) -> list[dict[str, Any]]:
    """Витягує `levels_shadow_v1` і перетворює у формат, сумісний з geometry_hash()."""

    raw = viewer_state.get("levels_shadow_v1")
    if not isinstance(raw, list):
        return []

    items: list[dict[str, Any]] = []
    for lvl in raw:
        if not isinstance(lvl, dict):
            continue
        if str(lvl.get("tf") or "").lower() != str(tf).lower():
            continue

        kind = str(lvl.get("kind") or "").lower()
        label = str(lvl.get("label") or "").upper()
        role = str(lvl.get("role") or "").upper()

        price: float | None
        if kind == "band":
            top = lvl.get("top")
            bot = lvl.get("bot")
            try:
                t = float(top) if top is not None else None
                b = float(bot) if bot is not None else None
            except (TypeError, ValueError):
                t = None
                b = None
            if t is None and b is None:
                price = None
            elif t is None:
                price = b
            elif b is None:
                price = t
            else:
                price = (t + b) * 0.5
        else:
            raw_price = lvl.get("price")
            try:
                price = float(raw_price) if raw_price is not None else None
            except (TypeError, ValueError):
                price = None

        if price is None or not (price == price):
            continue

        rh = lvl.get("render_hint")
        if isinstance(rh, dict) and rh.get("title"):
            title = str(rh.get("title"))
            axis_label = bool(rh.get("axis_label"))
            line_visible = bool(rh.get("line_visible"))
        else:
            type_short = label[:6] if len(label) > 6 else label
            role_mark = "P" if role == "PRIMARY" else "C" if role == "COUNTER" else ""
            title = f"{type_short}{(' ' + role_mark) if role_mark else ''}".strip()
            axis_label = False
            line_visible = True

        items.append(
            {
                "price": float(price),
                "title": title,
                "axis_label": axis_label,
                "line_visible": line_visible,
            }
        )

    items.sort(key=lambda x: (round(float(x["price"]), 6), str(x.get("title") or "")))
    return items


def candidates_items_from_viewer_state(
    viewer_state: dict[str, Any], *, owner_tf: str
) -> list[dict[str, Any]]:
    """Витягує `levels_candidates_v1` і нормалізує для хешу/звітів.

    На 3.2.1 список може бути відсутнім або порожнім — це валідно.
    """

    raw = viewer_state.get("levels_candidates_v1")
    if not isinstance(raw, list):
        return []

    out: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        if str(c.get("owner_tf") or "").lower() != str(owner_tf).lower():
            continue

        kind = str(c.get("kind") or "").lower()
        label = str(c.get("label") or "").upper()
        source = str(c.get("source") or "").upper()

        price = c.get("price")
        top = c.get("top")
        bot = c.get("bot")

        def _f(v: Any) -> float | None:
            try:
                x = float(v) if v is not None else None
            except (TypeError, ValueError):
                return None
            return x if (x is not None and x == x) else None

        out.append(
            {
                "kind": kind,
                "label": label,
                "source": source,
                "price": _f(price),
                "top": _f(top),
                "bot": _f(bot),
            }
        )

    out.sort(
        key=lambda x: (
            str(x.get("kind") or ""),
            str(x.get("label") or ""),
            round(float(x.get("price") or 0.0), 6),
            round(float(x.get("bot") or 0.0), 6),
            round(float(x.get("top") or 0.0), 6),
            str(x.get("source") or ""),
        )
    )
    return out


def selected_items_from_viewer_state(
    viewer_state: dict[str, Any], *, owner_tf: str
) -> list[dict[str, Any]]:
    """Витягує `levels_selected_v1` і нормалізує для хешу/звітів.

    На 3.3a список може бути відсутнім або порожнім — це валідно.
    """

    raw = viewer_state.get("levels_selected_v1")
    if not isinstance(raw, list):
        return []

    out: list[dict[str, Any]] = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        if str(s.get("owner_tf") or "").lower() != str(owner_tf).lower():
            continue

        kind = str(s.get("kind") or "").lower()
        label = str(s.get("label") or "").upper()
        source = str(s.get("source") or "").upper()

        def _f(v: Any) -> float | None:
            try:
                x = float(v) if v is not None else None
            except (TypeError, ValueError):
                return None
            return x if (x is not None and x == x) else None

        rank_raw = s.get("rank")
        try:
            rank = int(rank_raw) if rank_raw is not None else 0
        except (TypeError, ValueError):
            rank = 0

        reason_any = s.get("reason")
        if isinstance(reason_any, list):
            reasons = [str(r) for r in reason_any if r is not None]
        elif reason_any is None:
            reasons = []
        else:
            reasons = [str(reason_any)]

        out.append(
            {
                "kind": kind,
                "label": label,
                "source": source,
                "price": _f(s.get("price")),
                "top": _f(s.get("top")),
                "bot": _f(s.get("bot")),
                "rank": rank,
                "reason": sorted(reasons),
                "distance_at_select": _f(s.get("distance_at_select")),
                "selected_at_close_ts": _f(s.get("selected_at_close_ts")),
            }
        )

    out.sort(
        key=lambda x: (
            str(x.get("kind") or ""),
            str(x.get("label") or ""),
            round(float(x.get("price") or 0.0), 6),
            round(float(x.get("bot") or 0.0), 6),
            round(float(x.get("top") or 0.0), 6),
            int(x.get("rank") or 0),
            str(x.get("source") or ""),
            "|".join([str(r) for r in (x.get("reason") or [])]),
        )
    )
    return out


def validate_selected_merge_from_candidates_v1_strict(
    *,
    candidates_items: list[dict[str, Any]],
    selected_items: list[dict[str, Any]],
    require_present: bool,
) -> list[str]:
    """Strict-гейт 3.3b: selected має бути детермінованим merge з candidates.

    Перевіряємо (мінімально):
    - count збігається;
    - геометрія/лейбли/джерело збігаються 1:1 (ігноруємо selection meta);
    - rank є 1..N без дірок;
    - reason містить маркер merge.
    """

    issues: list[str] = []

    if require_present and not candidates_items:
        issues.append("candidates відсутні (require_present=true)")
        return issues

    if require_present and not selected_items:
        issues.append("selected відсутні (require_present=true)")
        return issues

    if len(selected_items) != len(candidates_items):
        issues.append(
            f"count mismatch: selected={len(selected_items)} candidates={len(candidates_items)}"
        )

    def _key(
        it: dict[str, Any],
    ) -> tuple[str, str, str, float | None, float | None, float | None]:
        return (
            str(it.get("kind") or ""),
            str(it.get("label") or ""),
            str(it.get("source") or ""),
            (
                it.get("price")
                if isinstance(it.get("price"), (int, float))
                else it.get("price")
            ),
            it.get("bot") if isinstance(it.get("bot"), (int, float)) else it.get("bot"),
            it.get("top") if isinstance(it.get("top"), (int, float)) else it.get("top"),
        )

    cand_keys = sorted([_key(it) for it in (candidates_items or [])])
    sel_keys = sorted([_key(it) for it in (selected_items or [])])
    if cand_keys != sel_keys:
        issues.append("геометрія selected != candidates (merge порушено)")

    ranks = [int(it.get("rank") or 0) for it in (selected_items or [])]
    if selected_items:
        if any(r <= 0 for r in ranks):
            issues.append(f"rank має бути >0: ranks={sorted(set(ranks))}")
        else:
            exp = list(range(1, len(selected_items) + 1))
            if sorted(ranks) != exp:
                issues.append(
                    "rank має бути 1..N без пропусків: "
                    f"got={sorted(ranks)} expected={exp}"
                )

    for it in selected_items or []:
        reasons = it.get("reason")
        if not isinstance(reasons, list) or not any(
            str(r) == "MERGE_FROM_CANDIDATE_V1" for r in reasons
        ):
            issues.append("reason не містить MERGE_FROM_CANDIDATE_V1")
            break

    return issues


def raw_candidates_from_viewer_state(
    viewer_state: dict[str, Any], *, owner_tf: str
) -> list[dict[str, Any]]:
    """Витягує сирі `levels_candidates_v1` для перевірок інваріантів.

    Використовується лише в harness-валідаціях (не для geometry_hash),
    щоб не робити "фальшивого" згладжування полів.
    """

    raw = viewer_state.get("levels_candidates_v1")
    if not isinstance(raw, list):
        return []

    out: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        if str(c.get("owner_tf") or "").lower() != str(owner_tf).lower():
            continue
        out.append(c)
    return out


def validate_selected_caps_v1(viewer_state: dict[str, Any]) -> list[str]:
    """3.3c: строгий гейт на caps для selected_v1.

    Перевіряємо лише caps (не distance/prio), щоб мати простий і стабільний QA-гейт.

    Очікування:
    - TF=5m: lines<=3, bands<=2
    - TF=1h/4h: lines<=6, bands<=2
    - TF=1m: selected відсутній або count=0
    """

    issues: list[str] = []

    raw = viewer_state.get("levels_selected_v1")
    if raw is None:
        return issues
    if not isinstance(raw, list):
        return ["levels_selected_v1 не є list"]

    counts: dict[str, dict[str, int]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        tf = str(item.get("owner_tf") or "").strip().lower()
        if not tf:
            continue
        kind = str(item.get("kind") or "").strip().lower()
        bucket = counts.setdefault(tf, {"line": 0, "band": 0, "other": 0})
        if kind == "line":
            bucket["line"] += 1
        elif kind == "band":
            bucket["band"] += 1
        else:
            bucket["other"] += 1

    # 1m: заборона selected.
    if counts.get("1m", {"line": 0, "band": 0, "other": 0}) != {
        "line": 0,
        "band": 0,
        "other": 0,
    }:
        issues.append(f"3.3c caps: TF=1m має бути 0, але отримали {counts.get('1m')}")

    def _check(tf: str, *, max_lines: int, max_bands: int) -> None:
        got = counts.get(tf, {"line": 0, "band": 0, "other": 0})
        if int(got.get("other") or 0) > 0:
            issues.append(
                f"3.3c caps: TF={tf} має невідомі kind other={got.get('other')}"
            )
        if int(got.get("line") or 0) > int(max_lines):
            issues.append(f"3.3c caps: TF={tf} lines={got.get('line')} > {max_lines}")
        if int(got.get("band") or 0) > int(max_bands):
            issues.append(f"3.3c caps: TF={tf} bands={got.get('band')} > {max_bands}")

    _check("5m", max_lines=3, max_bands=2)
    _check("1h", max_lines=6, max_bands=2)
    _check("4h", max_lines=6, max_bands=2)
    return issues


def _parse_iso_utc(ts: Any) -> datetime | None:
    if not isinstance(ts, str) or not ts.strip():
        return None
    s = ts.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt


def _active_session_tag_utc(payload_ts: Any) -> str | None:
    dt = _parse_iso_utc(payload_ts)
    if dt is None:
        return None
    hour = int(dt.astimezone(UTC).hour) % 24

    for tag in ("ASIA", "LONDON", "NY"):
        if tag not in dict(SMC_SESSION_WINDOWS_UTC):
            continue
        start_h, end_h = dict(SMC_SESSION_WINDOWS_UTC)[tag]
        s = int(start_h) % 24
        e = int(end_h) % 24
        if s == e:
            continue
        if s < e:
            if s <= hour < e:
                return tag
        else:
            if hour >= s or hour < e:
                return tag
    return None


def _compute_atr_from_ohlcv(
    ohlcv: list[dict[str, Any]], *, period: int = 14
) -> float | None:
    bars = [b for b in (ohlcv or []) if isinstance(b, dict)]
    if len(bars) < (period + 1):
        return None

    def _f(v: Any) -> float | None:
        try:
            x = float(v) if v is not None else None
        except (TypeError, ValueError):
            return None
        return x if (x is not None and x == x) else None

    tail = bars[-(period + 1) :]
    prev_close: float | None = None
    tr_values: list[float] = []
    for b in tail:
        h = _f(b.get("high"))
        lo = _f(b.get("low"))
        c = _f(b.get("close"))
        if h is None or lo is None:
            continue
        if prev_close is None:
            tr = float(h) - float(lo)
        else:
            tr = max(
                float(h) - float(lo),
                abs(float(h) - float(prev_close)),
                abs(float(lo) - float(prev_close)),
            )
        if tr == tr and tr > 0:
            tr_values.append(float(tr))
        if c is not None:
            prev_close = float(c)

    if len(tr_values) < period:
        return None
    atr = sum(tr_values[-period:]) / float(period)
    if atr == atr and atr > 0:
        return float(atr)
    return None


def validate_selected_composition_5m_v1(
    *,
    base_url: str,
    symbol: str,
    viewer_state: dict[str, Any],
    selected_items_5m: list[dict[str, Any]],
    candidates_raw_5m: list[dict[str, Any]],
    ohlcv_limit: int,
) -> list[str]:
    """3.3f: strict-гейт композиції selected на TF=5m.

    Перевіряємо:
    - bands<=2, lines<=3
    - якщо є session candidates (active session) і вони в gate → selected містить пару (H+L)
    - якщо PDH/PDL присутні і в gate → selected містить хоча б 1 з них
    - reason[] слот-специфічний для ключових типів
    """

    issues: list[str] = []

    # Базові ліміти.
    counts = {"line": 0, "band": 0, "other": 0}
    for it in selected_items_5m or []:
        k = str((it or {}).get("kind") or "").lower()
        if k == "line":
            counts["line"] += 1
        elif k == "band":
            counts["band"] += 1
        else:
            counts["other"] += 1

    if counts["other"] > 0:
        issues.append(f"5m composition: unknown kind other={counts['other']}")
    if counts["band"] > 2:
        issues.append(f"5m composition: bands={counts['band']} > 2")
    if counts["line"] > 3:
        issues.append(f"5m composition: lines={counts['line']} > 3")

    # Gate: ATR(5m)*2.5
    try:
        raw_price = viewer_state.get("price")
        ref_price = float(raw_price) if raw_price is not None else None
    except (TypeError, ValueError):
        ref_price = None

    gate_abs: float | None = None
    if ref_price is not None:
        ohlcv = _fetch_ohlcv(base_url, symbol, "5m", limit=max(50, int(ohlcv_limit)))
        atr = _compute_atr_from_ohlcv(ohlcv, period=14)
        if atr is not None:
            gate_abs = float(atr) * 2.5

    def _cand_in_gate(c: dict[str, Any]) -> bool:
        if ref_price is None or gate_abs is None:
            return False
        try:
            raw = c.get("price")
            price = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return False
        if price is None:
            return False
        d = abs(float(price) - float(ref_price))
        return d <= float(gate_abs)

    selected_labels = set(
        [str(it.get("label") or "").upper() for it in selected_items_5m]
    )

    # Active session pair requirement.
    active_tag = _active_session_tag_utc(viewer_state.get("payload_ts"))
    session_pair_by_tag = {
        "ASIA": ("ASH", "ASL"),
        "LONDON": ("LSH", "LSL"),
        "NY": ("NYH", "NYL"),
    }
    if active_tag in session_pair_by_tag:
        hi, lo = session_pair_by_tag[str(active_tag)]
        sess = [
            c
            for c in (candidates_raw_5m or [])
            if isinstance(c, dict)
            and str(c.get("kind") or "").lower() == "line"
            and str(c.get("source") or "").upper() == "SESSION"
            and str(c.get("label") or "").upper() in {hi, lo}
        ]
        sess_in_gate = [c for c in sess if _cand_in_gate(c)]
        has_hi = any(str(c.get("label") or "").upper() == hi for c in sess_in_gate)
        has_lo = any(str(c.get("label") or "").upper() == lo for c in sess_in_gate)
        if has_hi and has_lo:
            if hi not in selected_labels or lo not in selected_labels:
                issues.append(
                    f"5m composition: session pair required (active={active_tag}) але selected не містить {hi}/{lo}"
                )

    # PDH/PDL requirement.
    pd = [
        c
        for c in (candidates_raw_5m or [])
        if isinstance(c, dict)
        and str(c.get("kind") or "").lower() == "line"
        and str(c.get("source") or "").upper() == "DAILY"
        and str(c.get("label") or "").upper() in {"PDH", "PDL"}
    ]
    pd_in_gate = [c for c in pd if _cand_in_gate(c)]
    if pd_in_gate:
        if not ("PDH" in selected_labels or "PDL" in selected_labels):
            issues.append(
                "5m composition: PDH/PDL in gate, але selected не містить жодного"
            )

    # Slot-specific reasons.
    for it in selected_items_5m or []:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label") or "").upper()
        kind = str(it.get("kind") or "").lower()
        reasons = it.get("reason")
        reason_set = (
            set([str(r) for r in reasons]) if isinstance(reasons, list) else set()
        )

        if kind == "band" and label in {"EQH", "EQL"}:
            if "BAND_NEAR_PRICE" not in reason_set:
                issues.append(f"5m reason: {label} band має містити BAND_NEAR_PRICE")

        if kind == "line" and label in {"RANGE_H", "RANGE_L"}:
            if "RANGE_NEAREST" not in reason_set:
                issues.append(f"5m reason: {label} має містити RANGE_NEAREST")

        if kind == "line" and label in {"PDH", "PDL"}:
            if "PINNED_PDH_PDL" not in reason_set:
                issues.append(f"5m reason: {label} має містити PINNED_PDH_PDL")

        if kind == "line" and label in {"EDH", "EDL"}:
            if "FALLBACK_EDH_EDL" not in reason_set:
                issues.append(f"5m reason: {label} має містити FALLBACK_EDH_EDL")

        if kind == "line" and label in {"ASH", "ASL", "LSH", "LSL", "NYH", "NYL"}:
            if "PINNED_SESSION_ACTIVE" not in reason_set:
                issues.append(f"5m reason: {label} має містити PINNED_SESSION_ACTIVE")

    return issues


def validate_prev_day_pdh_pdl_candidates_v1(
    candidates_raw: list[dict[str, Any]],
) -> dict[str, Any]:
    """Валідація інваріантів 3.2.2b (prev-day PDH/PDL) для одного owner_tf.

    Важливо: ця функція **очікує**, що на вхід подано вже відфільтрований піднабір
    PDH/PDL (а не весь список candidates).

    Очікування:
    - або кандидати відсутні (0), або рівно 2 (PDH+PDL);
    - `kind` = line;
    - `source` = DAILY;
    - `window_ts` присутній і однаковий для обох;
    - `window_ts` відповідає prev-day window відносно `asof_ts`.
    """

    issues: list[str] = []

    labels: list[str] = []
    kinds: list[str] = []
    sources: list[str] = []
    window_ts_values: list[tuple[int, int]] = []
    asof_values: list[float] = []
    prices: dict[str, float] = {}

    for c in candidates_raw or []:
        labels.append(str(c.get("label") or "").upper())
        kinds.append(str(c.get("kind") or "").lower())
        sources.append(str(c.get("source") or "").upper())

        a = c.get("asof_ts")
        if isinstance(a, (int, float)):
            asof_values.append(float(a))

        lab = str(c.get("label") or "").upper()
        if lab in {"PDH", "PDL"}:
            raw_price = c.get("price")
            if raw_price is None:
                p = float("nan")
            else:
                try:
                    p = float(raw_price)
                except (TypeError, ValueError):
                    p = float("nan")
            if p == p:
                prices[lab] = float(p)

        w = c.get("window_ts")
        w_pair: tuple[int, int] | None = None
        if isinstance(w, (list, tuple)) and len(w) == 2:
            a, b = w[0], w[1]
            try:
                a_i = int(float(a))
                b_i = int(float(b))
                if a_i > 0 and b_i > 0 and a_i < b_i:
                    w_pair = (a_i, b_i)
            except (TypeError, ValueError):
                w_pair = None
        if w_pair is not None:
            window_ts_values.append(w_pair)

    count = len(candidates_raw or [])
    unique_labels = sorted(set([x for x in labels if x]))
    unique_kinds = sorted(set([x for x in kinds if x]))
    unique_sources = sorted(set([x for x in sources if x]))
    unique_window_ts = sorted(set(window_ts_values))

    if count not in {0, 2}:
        issues.append(f"Невалідний count={count} (очікуємо 0 або 2)")

    if count == 2:
        if set(unique_labels) != {"PDH", "PDL"}:
            issues.append(f"Невалідні labels={unique_labels} (очікуємо PDH+PDL)")
        if unique_kinds != ["line"]:
            issues.append(f"Невалідні kind={unique_kinds} (очікуємо line)")
        if unique_sources != ["DAILY"]:
            issues.append(f"Невалідні source={unique_sources} (очікуємо DAILY)")
        if len(unique_window_ts) != 1:
            issues.append(f"Невалідні window_ts={unique_window_ts} (очікуємо 1 пару)")

        # Перевірка prev-day window_ts == prev_day_window(asof_ts)
        if not asof_values:
            issues.append(
                "Відсутній asof_ts у candidates (потрібно для перевірки prev_day_window)"
            )
        else:
            asof_min = min(asof_values)
            asof_max = max(asof_values)
            if abs(asof_max - asof_min) > 1e-6:
                issues.append(
                    f"asof_ts неузгоджений між pair: min={asof_min} max={asof_max}"
                )

        if len(unique_window_ts) == 1 and asof_values:
            got_start, got_end = unique_window_ts[0]
            try:
                from config.config import SMC_DAILY_START_HOUR_UTC
                from core.contracts import get_prev_day_window_utc

                exp_start, exp_end = get_prev_day_window_utc(
                    float(max(asof_values)),
                    day_start_hour_utc=int(SMC_DAILY_START_HOUR_UTC),
                )
                exp_start_i = int(float(exp_start))
                exp_end_i = int(float(exp_end))
                if exp_start_i != int(got_start) or exp_end_i != int(got_end):
                    issues.append(
                        "window_ts не відповідає prev_day_window(asof_ts): "
                        f"expected=({exp_start_i},{exp_end_i}) got=({int(got_start)},{int(got_end)})"
                    )
            except Exception as e:
                issues.append(
                    f"Не вдалося перевірити prev_day_window(asof_ts): {type(e).__name__}: {e}"
                )

        # (Опційно, але корисно як sanity-check)
        pdh = prices.get("PDH")
        pdl = prices.get("PDL")
        if pdh is not None and pdl is not None and not (float(pdh) > float(pdl)):
            issues.append(f"Невалідний порядок: PDH={pdh} PDL={pdl} (очікуємо PDH>PDL)")

    return {
        "count": count,
        "unique_labels": unique_labels,
        "unique_kinds": unique_kinds,
        "unique_sources": unique_sources,
        "unique_window_ts": unique_window_ts,
        "issues": issues,
    }


def validate_prev_day_pdh_pdl_candidates_v1_strict(
    candidates_raw: list[dict[str, Any]],
    *,
    require_present: bool,
) -> list[str]:
    """Повертає список issues для strict-гейта (3.2.2b)."""

    v = validate_prev_day_pdh_pdl_candidates_v1(candidates_raw)
    issues = [str(x) for x in (v.get("issues") or [])]
    if require_present and int(v.get("count") or 0) == 0:
        issues.append("candidates відсутні (require_present=true)")
    return issues


def validate_range_high_low_candidates_v1(
    candidates_raw: list[dict[str, Any]],
) -> dict[str, Any]:
    """Валідація інваріантів 3.2.4b1 (RANGE) для одного owner_tf.

    Очікування:
    - або кандидати відсутні (0), або рівно 2 (RANGE_H+RANGE_L);
    - `kind` = line;
    - `source` = RANGE;
    - `window_ts` має бути None (бо RANGE не прив’язаний до UI-selected pools);
    - `price(RANGE_H) > price(RANGE_L)`.
    """

    issues: list[str] = []
    labels: list[str] = []
    kinds: list[str] = []
    sources: list[str] = []
    window_values: list[Any] = []
    price_by_label: dict[str, float] = {}

    for c in candidates_raw or []:
        label = str(c.get("label") or "").upper()
        labels.append(label)
        kinds.append(str(c.get("kind") or "").lower())
        sources.append(str(c.get("source") or "").upper())
        window_values.append(c.get("window_ts"))

        if label in {"RANGE_H", "RANGE_L"}:
            raw_price = c.get("price")
            if raw_price is None:
                continue
            try:
                p = float(raw_price)
            except (TypeError, ValueError):
                continue
            if p == p:
                price_by_label[label] = float(p)

    count = len(candidates_raw or [])
    unique_labels = sorted(set([x for x in labels if x]))
    unique_kinds = sorted(set([x for x in kinds if x]))
    unique_sources = sorted(set([x for x in sources if x]))
    unique_window = sorted(
        set(["<None>" if x is None else "<non-null>" for x in window_values])
    )

    if count not in {0, 2}:
        issues.append(f"Невалідний count={count} (очікуємо 0 або 2)")

    if count == 2:
        if set(unique_labels) != {"RANGE_H", "RANGE_L"}:
            issues.append(
                f"Невалідні labels={unique_labels} (очікуємо RANGE_H+RANGE_L)"
            )
        if unique_kinds != ["line"]:
            issues.append(f"Невалідні kind={unique_kinds} (очікуємо line)")
        if unique_sources != ["RANGE"]:
            issues.append(f"Невалідні source={unique_sources} (очікуємо RANGE)")
        if unique_window != ["<None>"]:
            issues.append("Невалідні window_ts: очікуємо None для обох")

        rh = price_by_label.get("RANGE_H")
        rl = price_by_label.get("RANGE_L")
        if rh is None or rl is None:
            issues.append("Відсутні/нечислові price для RANGE_H/RANGE_L")
        elif not (float(rh) > float(rl)):
            issues.append(
                f"Невалідний порядок: RANGE_H={rh} RANGE_L={rl} (очікуємо H>L)"
            )

    return {
        "count": count,
        "unique_labels": unique_labels,
        "unique_kinds": unique_kinds,
        "unique_sources": unique_sources,
        "unique_window_ts": unique_window,
        "issues": issues,
    }


def validate_range_high_low_candidates_v1_strict(
    candidates_raw: list[dict[str, Any]],
    *,
    require_present: bool,
) -> list[str]:
    """Повертає список issues для strict-гейта (3.2.4b1 RANGE)."""

    v = validate_range_high_low_candidates_v1(candidates_raw)
    issues = [str(x) for x in (v.get("issues") or [])]
    if require_present and int(v.get("count") or 0) == 0:
        issues.append("candidates відсутні (require_present=true)")
    return issues


def validate_eqh_eql_band_candidates_v1(
    candidates_raw: list[dict[str, Any]],
) -> dict[str, Any]:
    """Валідація інваріантів 3.2.5b (EQH/EQL bands) для одного owner_tf.

    Очікування:
    - або кандидати відсутні (0), або рівно 2 (EQH+EQL);
    - `kind` = band;
    - `source` = POOL_DERIVED;
    - `window_ts` має бути None;
    - `price` має бути None (бо це band);
    - `top > bot` для обох.
    """

    issues: list[str] = []
    labels: list[str] = []
    kinds: list[str] = []
    sources: list[str] = []
    window_values: list[Any] = []
    price_values: list[Any] = []
    band_by_label: dict[str, tuple[float, float]] = {}

    for c in candidates_raw or []:
        label = str(c.get("label") or "").upper()
        labels.append(label)
        kinds.append(str(c.get("kind") or "").lower())
        sources.append(str(c.get("source") or "").upper())
        window_values.append(c.get("window_ts"))
        price_values.append(c.get("price"))

        top_raw = c.get("top")
        bot_raw = c.get("bot")
        if top_raw is None or bot_raw is None:
            continue
        try:
            top = float(top_raw)
            bot = float(bot_raw)
        except (TypeError, ValueError):
            continue
        if top == top and bot == bot:
            band_by_label[label] = (top, bot)

    count = len(candidates_raw or [])
    unique_labels = sorted(set([x for x in labels if x]))
    unique_kinds = sorted(set([x for x in kinds if x]))
    unique_sources = sorted(set([x for x in sources if x]))
    unique_window = sorted(
        set(["<None>" if x is None else "<non-null>" for x in window_values])
    )
    unique_price = sorted(
        set(["<None>" if x is None else "<non-null>" for x in price_values])
    )

    if count not in {0, 2}:
        issues.append(f"Невалідний count={count} (очікуємо 0 або 2)")

    if count == 2:
        if set(unique_labels) != {"EQH", "EQL"}:
            issues.append(f"Невалідні labels={unique_labels} (очікуємо EQH+EQL)")
        if unique_kinds != ["band"]:
            issues.append(f"Невалідні kind={unique_kinds} (очікуємо band)")
        if unique_sources != ["POOL_DERIVED"]:
            issues.append(f"Невалідні source={unique_sources} (очікуємо POOL_DERIVED)")
        if unique_window != ["<None>"]:
            issues.append("Невалідні window_ts: очікуємо None для обох")
        if unique_price != ["<None>"]:
            issues.append("Невалідні price: очікуємо None для band")

        eqh = band_by_label.get("EQH")
        eql = band_by_label.get("EQL")
        if eqh is None or eql is None:
            issues.append("Відсутні/нечислові top/bot для EQH або EQL")
        else:
            top_h, bot_h = eqh
            top_l, bot_l = eql
            if not (float(top_h) > float(bot_h)):
                issues.append(
                    f"Невалідна геометрія EQH: top={top_h} bot={bot_h} (очікуємо top>bot)"
                )
            if not (float(top_l) > float(bot_l)):
                issues.append(
                    f"Невалідна геометрія EQL: top={top_l} bot={bot_l} (очікуємо top>bot)"
                )

    return {
        "count": count,
        "unique_labels": unique_labels,
        "unique_kinds": unique_kinds,
        "unique_sources": unique_sources,
        "unique_window_ts": unique_window,
        "unique_price": unique_price,
        "issues": issues,
    }


def validate_eqh_eql_band_candidates_v1_strict(
    candidates_raw: list[dict[str, Any]],
    *,
    require_present: bool,
) -> list[str]:
    """Повертає список issues для strict-гейта (3.2.5b EQH/EQL)."""

    v = validate_eqh_eql_band_candidates_v1(candidates_raw)
    issues = [str(x) for x in (v.get("issues") or [])]
    if require_present and int(v.get("count") or 0) == 0:
        issues.append("candidates відсутні (require_present=true)")
    return issues


def validate_session_high_low_candidates_v1(
    candidates_raw: list[dict[str, Any]],
    *,
    label_high: str,
    label_low: str,
) -> dict[str, Any]:
    """Валідація інваріантів 3.2.3 для одного session-пару (без монотонності).

    Очікування:
    - або кандидати відсутні (0), або рівно 2 (high+low);
    - `kind` = line;
    - `source` = SESSION;
    - `window_ts` присутній і однаковий для обох;
    - `window_ts` відповідає session_window(asof_ts) для цього пару.
    """

    issues: list[str] = []
    labels: list[str] = []
    kinds: list[str] = []
    sources: list[str] = []
    asof_values: list[float] = []
    window_pairs: list[tuple[int, int]] = []

    for c in candidates_raw or []:
        labels.append(str(c.get("label") or "").upper())
        kinds.append(str(c.get("kind") or "").lower())
        sources.append(str(c.get("source") or "").upper())

        a = c.get("asof_ts")
        if isinstance(a, (int, float)):
            asof_values.append(float(a))

        w = _as_pair_window_ts(c.get("window_ts"))
        if w is not None:
            window_pairs.append(w)

    count = len(candidates_raw or [])
    unique_labels = sorted(set([x for x in labels if x]))
    unique_kinds = sorted(set([x for x in kinds if x]))
    unique_sources = sorted(set([x for x in sources if x]))
    unique_window_ts = sorted(set(window_pairs))

    exp_high = str(label_high or "").upper()
    exp_low = str(label_low or "").upper()

    if count not in {0, 2}:
        issues.append(f"Невалідний count={count} (очікуємо 0 або 2)")

    if count == 2:
        if set(unique_labels) != {exp_high, exp_low}:
            issues.append(
                f"Невалідні labels={unique_labels} (очікуємо {exp_high}+{exp_low})"
            )
        if unique_kinds != ["line"]:
            issues.append(f"Невалідні kind={unique_kinds} (очікуємо line)")
        if unique_sources != ["SESSION"]:
            issues.append(f"Невалідні source={unique_sources} (очікуємо SESSION)")
        if len(unique_window_ts) != 1:
            issues.append(f"Невалідні window_ts={unique_window_ts} (очікуємо 1 пару)")

        if len(unique_window_ts) == 1:
            got_start, got_end = unique_window_ts[0]
            if int(got_end) - int(got_start) <= 0:
                issues.append(
                    "Невалідна довжина window_ts: "
                    f"got=({got_start},{got_end}) delta={int(got_end) - int(got_start)}"
                )

            if not asof_values:
                issues.append(
                    "Відсутній asof_ts у candidates (потрібно для перевірки session_window)"
                )
            else:
                asof_min = min(asof_values)
                asof_max = max(asof_values)
                if abs(asof_max - asof_min) > 1e-6:
                    issues.append(
                        f"asof_ts неузгоджений між pair: min={asof_min} max={asof_max}"
                    )

                try:
                    from config.config import SMC_SESSION_WINDOWS_UTC
                    from core.contracts import get_session_window_utc

                    # Визначаємо, який тег відповідає цьому пару.
                    labels_to_tag = {
                        "ASH": "ASIA",
                        "ASL": "ASIA",
                        "LSH": "LONDON",
                        "LSL": "LONDON",
                        "NYH": "NY",
                        "NYL": "NY",
                    }
                    tag = labels_to_tag.get(exp_high) or labels_to_tag.get(exp_low)
                    if tag and tag in dict(SMC_SESSION_WINDOWS_UTC):
                        start_h, end_h = dict(SMC_SESSION_WINDOWS_UTC)[tag]
                        exp_start, exp_end = get_session_window_utc(
                            float(asof_max),
                            session_start_hour_utc=int(start_h),
                            session_end_hour_utc=int(end_h),
                        )
                        exp_start_i = int(float(exp_start))
                        exp_end_i = int(float(exp_end))
                        if exp_start_i != int(got_start) or exp_end_i != int(got_end):
                            issues.append(
                                "window_ts не відповідає session_window(asof_ts): "
                                f"expected=({exp_start_i},{exp_end_i}) got=({int(got_start)},{int(got_end)}) "
                                f"tag={tag}"
                            )
                except Exception as e:
                    issues.append(
                        f"Не вдалося перевірити session_window(asof_ts): {type(e).__name__}: {e}"
                    )

    return {
        "count": count,
        "unique_labels": unique_labels,
        "unique_kinds": unique_kinds,
        "unique_sources": unique_sources,
        "unique_window_ts": unique_window_ts,
        "issues": issues,
    }


def validate_session_high_low_candidates_v1_strict(
    candidates_raw: list[dict[str, Any]],
    *,
    label_high: str,
    label_low: str,
    require_present: bool,
) -> list[str]:
    v = validate_session_high_low_candidates_v1(
        candidates_raw, label_high=label_high, label_low=label_low
    )
    issues = [str(x) for x in (v.get("issues") or [])]
    if require_present and int(v.get("count") or 0) == 0:
        issues.append("candidates відсутні (require_present=true)")
    return issues


def _as_pair_window_ts(w: Any) -> tuple[int, int] | None:
    if isinstance(w, (list, tuple)) and len(w) == 2:
        a, b = w[0], w[1]
        try:
            a_i = int(float(a))
            b_i = int(float(b))
        except (TypeError, ValueError):
            return None
        if a_i > 0 and b_i > 0 and a_i < b_i:
            return (a_i, b_i)
    return None


def validate_today_edh_edl_candidates_v1(
    candidates_raw: list[dict[str, Any]],
) -> dict[str, Any]:
    """Валідація інваріантів 3.2.2c для одного owner_tf (без монотонності)."""

    issues: list[str] = []
    labels: list[str] = []
    kinds: list[str] = []
    sources: list[str] = []
    asof_values: list[float] = []
    window_pairs: list[tuple[int, int]] = []

    for c in candidates_raw or []:
        labels.append(str(c.get("label") or "").upper())
        kinds.append(str(c.get("kind") or "").lower())
        sources.append(str(c.get("source") or "").upper())

        a = c.get("asof_ts")
        if isinstance(a, (int, float)):
            asof_values.append(float(a))

        w = _as_pair_window_ts(c.get("window_ts"))
        if w is not None:
            window_pairs.append(w)

    count = len(candidates_raw or [])
    unique_labels = sorted(set([x for x in labels if x]))
    unique_kinds = sorted(set([x for x in kinds if x]))
    unique_sources = sorted(set([x for x in sources if x]))
    unique_window_ts = sorted(set(window_pairs))

    if count not in {0, 2}:
        issues.append(f"Невалідний count={count} (очікуємо 0 або 2)")

    if count == 2:
        if set(unique_labels) != {"EDH", "EDL"}:
            issues.append(f"Невалідні labels={unique_labels} (очікуємо EDH+EDL)")
        if unique_kinds != ["line"]:
            issues.append(f"Невалідні kind={unique_kinds} (очікуємо line)")
        if unique_sources != ["DAILY"]:
            issues.append(f"Невалідні source={unique_sources} (очікуємо DAILY)")
        if len(unique_window_ts) != 1:
            issues.append(f"Невалідні window_ts={unique_window_ts} (очікуємо 1 пару)")

        # Перевірка, що window_ts == today_window(asof_ts) (з урахуванням day_start_hour_utc).
        # Це важливо: саме так ми ловимо «зсув дня» або помилкові межі вікна.
        if len(unique_window_ts) == 1:
            got_start, got_end = unique_window_ts[0]
            if int(got_end) - int(got_start) != 24 * 3600:
                issues.append(
                    f"Невалідна довжина window_ts: got=({got_start},{got_end}) delta={int(got_end) - int(got_start)} (очікуємо 86400)"
                )

            # asof_ts має бути присутнім і узгодженим між EDH/EDL.
            if not asof_values:
                issues.append(
                    "Відсутній asof_ts у candidates (потрібно для перевірки today_window)"
                )
            else:
                asof_min = min(asof_values)
                asof_max = max(asof_values)
                if abs(asof_max - asof_min) > 1e-6:
                    issues.append(
                        f"asof_ts неузгоджений між EDH/EDL: min={asof_min} max={asof_max}"
                    )

                try:
                    from config.config import SMC_DAILY_START_HOUR_UTC
                    from core.contracts import get_day_window_utc

                    exp_start, exp_end = get_day_window_utc(
                        float(asof_max),
                        day_start_hour_utc=int(SMC_DAILY_START_HOUR_UTC),
                    )
                    exp_start_i = int(float(exp_start))
                    exp_end_i = int(float(exp_end))
                    if exp_start_i != int(got_start) or exp_end_i != int(got_end):
                        issues.append(
                            "window_ts не відповідає today_window(asof_ts): "
                            f"expected=({exp_start_i},{exp_end_i}) got=({int(got_start)},{int(got_end)}) "
                            f"day_start_hour_utc={int(SMC_DAILY_START_HOUR_UTC)}"
                        )
                except Exception as e:
                    issues.append(
                        f"Не вдалося перевірити today_window(asof_ts): {type(e).__name__}: {e}"
                    )

    return {
        "count": count,
        "unique_labels": unique_labels,
        "unique_kinds": unique_kinds,
        "unique_sources": unique_sources,
        "unique_window_ts": unique_window_ts,
        "issues": issues,
    }


def validate_today_edh_edl_candidates_v1_strict(
    candidates_raw: list[dict[str, Any]],
    *,
    require_present: bool,
) -> list[str]:
    v = validate_today_edh_edl_candidates_v1(candidates_raw)
    issues = [str(x) for x in (v.get("issues") or [])]
    if require_present and int(v.get("count") or 0) == 0:
        issues.append("candidates відсутні (require_present=true)")
    return issues


def validate_today_edh_edl_monotonicity(
    *,
    samples: list[dict[str, Any]],
    tf: str,
    require_present: bool,
    eps: float = 1e-9,
) -> list[str]:
    """Перевіряє монотонність EDH/EDL по серії знімків в межах одного today window.

    Інваріанти:
    - EDH(t) не зменшується
    - EDL(t) не збільшується
    - при зміні window_ts (новий день) — reset дозволений
    """

    issues: list[str] = []
    last_window_key: str | None = None
    last_edh: float | None = None
    last_edl: float | None = None

    for s in samples:
        i = s.get("i")
        per_tf = (s.get("per_tf") or {}).get(tf) or {}
        cand = per_tf.get("candidates_v1") or {}
        raw = cand.get("raw") if isinstance(cand, dict) else None
        raw_list = raw if isinstance(raw, list) else []

        edh = None
        edl = None
        window_key = None

        for c in raw_list:
            if not isinstance(c, dict):
                continue
            lab = str(c.get("label") or "").upper()
            if lab not in {"EDH", "EDL"}:
                continue
            if str(c.get("source") or "").upper() != "DAILY":
                continue
            if str(c.get("kind") or "").lower() != "line":
                continue

            w = _as_pair_window_ts(c.get("window_ts"))
            if w is not None:
                window_key = f"{w[0]}..{w[1]}"

            p = c.get("price")
            if p is None:
                continue
            try:
                p_f = float(p)
            except (TypeError, ValueError):
                continue

            if lab == "EDH":
                edh = p_f
            else:
                edl = p_f

        if require_present and (edh is None or edl is None):
            issues.append(f"i={i} tf={tf}: EDH/EDL відсутні")
            continue
        if edh is None or edl is None or window_key is None:
            # Немає чого перевіряти (або не готові дані).
            continue

        if last_window_key is None or window_key != last_window_key:
            last_window_key = window_key
            last_edh = edh
            last_edl = edl
            continue

        if last_edh is not None and edh + eps < last_edh:
            issues.append(
                f"i={i} tf={tf}: EDH зменшився {edh} < {last_edh} (window={window_key})"
            )
        if last_edl is not None and edl - eps > last_edl:
            issues.append(
                f"i={i} tf={tf}: EDL збільшився {edl} > {last_edl} (window={window_key})"
            )
        last_edh = edh
        last_edl = edl

    return issues


def validate_session_high_low_monotonicity(
    *,
    samples: list[dict[str, Any]],
    tf: str,
    label_high: str,
    label_low: str,
    require_present: bool,
    eps: float = 1e-9,
) -> list[str]:
    """Перевіряє монотонність SESSION high/low по серії знімків.

    Інваріанти в межах одного window_ts:
    - HIGH(t) не зменшується
    - LOW(t) не збільшується
    - при зміні window_ts (нове вікно цієї сесії) — reset дозволений
    """

    issues: list[str] = []
    last_window_key: str | None = None
    last_high: float | None = None
    last_low: float | None = None

    exp_high = str(label_high or "").upper()
    exp_low = str(label_low or "").upper()

    for s in samples:
        i = s.get("i")
        per_tf = (s.get("per_tf") or {}).get(tf) or {}
        cand = per_tf.get("candidates_v1") or {}
        raw = cand.get("raw") if isinstance(cand, dict) else None
        raw_list = raw if isinstance(raw, list) else []

        hi = None
        lo = None
        window_key = None

        for c in raw_list:
            if not isinstance(c, dict):
                continue
            lab = str(c.get("label") or "").upper()
            if lab not in {exp_high, exp_low}:
                continue
            if str(c.get("source") or "").upper() != "SESSION":
                continue
            if str(c.get("kind") or "").lower() != "line":
                continue

            w = _as_pair_window_ts(c.get("window_ts"))
            if w is not None:
                window_key = f"{w[0]}..{w[1]}"

            p = c.get("price")
            if p is None:
                continue
            try:
                p_f = float(p)
            except (TypeError, ValueError):
                continue

            if lab == exp_high:
                hi = p_f
            else:
                lo = p_f

        # Presence (require_present) перевіряємо в per-snapshot strict-гейті.
        # Тут (монотонність) не дублюємо issues, щоб звіт був читабельним.
        if hi is None or lo is None or window_key is None:
            continue

        if last_window_key is None or window_key != last_window_key:
            last_window_key = window_key
            last_high = hi
            last_low = lo
            continue

        if last_high is not None and hi + eps < last_high:
            issues.append(
                f"i={i} tf={tf}: {exp_high} зменшився {hi} < {last_high} (window={window_key})"
            )
        if last_low is not None and lo - eps > last_low:
            issues.append(
                f"i={i} tf={tf}: {exp_low} збільшився {lo} > {last_low} (window={window_key})"
            )
        last_high = hi
        last_low = lo

    return issues


def candidates_geometry_hash(items: list[dict[str, Any]]) -> str:
    payload = [
        {
            "kind": str(it.get("kind") or ""),
            "label": str(it.get("label") or ""),
            "source": str(it.get("source") or ""),
            "price": (
                None
                if it.get("price") is None
                else round(float(it.get("price") or 0.0), 6)
            ),
            "bot": (
                None if it.get("bot") is None else round(float(it.get("bot") or 0.0), 6)
            ),
            "top": (
                None if it.get("top") is None else round(float(it.get("top") or 0.0), 6)
            ),
        }
        for it in (items or [])
    ]
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def selected_geometry_hash(items: list[dict[str, Any]]) -> str:
    payload = [
        {
            "kind": str(it.get("kind") or ""),
            "label": str(it.get("label") or ""),
            "source": str(it.get("source") or ""),
            "price": (
                None
                if it.get("price") is None
                else round(float(it.get("price") or 0.0), 6)
            ),
            "bot": (
                None if it.get("bot") is None else round(float(it.get("bot") or 0.0), 6)
            ),
            "top": (
                None if it.get("top") is None else round(float(it.get("top") or 0.0), 6)
            ),
            "rank": int(it.get("rank") or 0),
            "reason": [str(r) for r in (it.get("reason") or [])],
            "distance_at_select": (
                None
                if it.get("distance_at_select") is None
                else round(float(it.get("distance_at_select") or 0.0), 6)
            ),
            "selected_at_close_ts": (
                None
                if it.get("selected_at_close_ts") is None
                else round(float(it.get("selected_at_close_ts") or 0.0), 3)
            ),
        }
        for it in (items or [])
    ]
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_tf(tf: str) -> str:
    t = str(tf).strip().lower()
    # Приймаємо "60m" як "1h" лише для зручності.
    if t in {"60m", "60min", "60"}:
        return "1h"
    return t


def _fetch_viewer_state(base_url: str, symbol: str) -> dict[str, Any]:
    qs = urllib.parse.urlencode({"symbol": symbol.upper()})
    url = f"{base_url.rstrip('/')}/smc-viewer/snapshot?{qs}"
    data = _http_get_json(url)
    if not isinstance(data, dict):
        raise RuntimeError("Очікував dict як SmcViewerState")
    return data


def _fetch_ohlcv(
    base_url: str, symbol: str, tf: str, limit: int
) -> list[dict[str, Any]]:
    qs = urllib.parse.urlencode(
        {"symbol": symbol.lower(), "tf": tf, "limit": int(limit)}
    )
    url = f"{base_url.rstrip('/')}/smc-viewer/ohlcv?{qs}"
    data = _http_get_json(url)
    if not isinstance(data, list):
        return []
    return [b for b in data if isinstance(b, dict)]


def build_baseline_summary_md(
    *,
    symbol: str,
    base_url: str,
    tfs: list[str],
    samples: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append(f"# Levels baseline summary (as-is) — {symbol}")
    lines.append("")
    lines.append(f"- Зібрано: {len(samples)} знімків")
    lines.append(f"- Endpoint: {base_url.rstrip('/')}/smc-viewer/snapshot")
    lines.append(f"- TF: {', '.join(tfs)}")
    lines.append("")

    for tf in tfs:
        counts: list[int] = []
        hashes: list[str] = []
        title_counts: dict[str, int] = {}

        shadow_counts: list[int] = []
        shadow_hashes: list[str] = []
        shadow_title_counts: dict[str, int] = {}
        match_flags: list[bool] = []

        candidates_counts: list[int] = []
        candidates_hashes: list[str] = []
        candidates_label_counts: dict[str, int] = {}

        selected_counts: list[int] = []
        selected_hashes: list[str] = []

        # 3.2.2b інваріанти (PDH/PDL prev-day) — агреговані по знімках.
        pdh_pdl_issue_count = 0
        pdh_pdl_samples_with_candidates = 0
        pdh_pdl_window_ts_uniq: set[str] = set()
        pdh_pdl_source_counts: dict[str, int] = {}
        pdh_pdl_kind_counts: dict[str, int] = {}
        pdh_pdl_label_samples: dict[str, int] = {}

        # 3.2.5b інваріанти (EQH/EQL bands) — агреговані по знімках.
        eq_issue_count = 0
        eq_samples_with_candidates = 0

        for s in samples:
            per_tf = (s.get("per_tf") or {}).get(tf) or {}
            items = per_tf.get("items") or []
            if isinstance(items, list):
                counts.append(len(items))
                for it in items:
                    title = str((it or {}).get("title") or "")
                    if title:
                        title_counts[title] = int(title_counts.get(title, 0)) + 1
            h = per_tf.get("geometry_hash")
            if isinstance(h, str) and h:
                hashes.append(h)

            sh = per_tf.get("shadow") or {}
            if isinstance(sh, dict):
                sh_items = sh.get("items") or []
                if isinstance(sh_items, list):
                    shadow_counts.append(len(sh_items))
                    for it in sh_items:
                        title = str((it or {}).get("title") or "")
                        if title:
                            shadow_title_counts[title] = (
                                int(shadow_title_counts.get(title, 0)) + 1
                            )
                sh_hash = sh.get("geometry_hash")
                if isinstance(sh_hash, str) and sh_hash:
                    shadow_hashes.append(sh_hash)
                as_is_hash = per_tf.get("geometry_hash")
                if (
                    isinstance(as_is_hash, str)
                    and as_is_hash
                    and isinstance(sh_hash, str)
                ):
                    match_flags.append(as_is_hash == sh_hash)

            cand = per_tf.get("candidates_v1") or {}
            if isinstance(cand, dict):
                cand_items = cand.get("items") or []
                if isinstance(cand_items, list):
                    candidates_counts.append(len(cand_items))
                    for it in cand_items:
                        label = str((it or {}).get("label") or "")
                        if label:
                            candidates_label_counts[label] = (
                                int(candidates_label_counts.get(label, 0)) + 1
                            )
                cand_hash = cand.get("geometry_hash")
                if isinstance(cand_hash, str) and cand_hash:
                    candidates_hashes.append(cand_hash)

            sel = per_tf.get("selected_v1") or {}
            if isinstance(sel, dict):
                sel_items = sel.get("items") or []
                if isinstance(sel_items, list):
                    selected_counts.append(len(sel_items))
                sel_hash = sel.get("geometry_hash")
                if isinstance(sel_hash, str) and sel_hash:
                    selected_hashes.append(sel_hash)

                # Дотягуємо "сирі" candidates, щоб валідатор бачив window_ts/kind/source.
                raw_cands = cand.get("raw")
                if isinstance(raw_cands, list):
                    # ВАЖЛИВО: валідатор 3.2.2b застосовуємо лише до піднабору PDH/PDL,
                    # інакше він буде хибно фейлити після додавання SESSION/EDH/EDL/RANGE.
                    pdhpdl = [
                        c
                        for c in raw_cands
                        if isinstance(c, dict)
                        and str(c.get("label") or "").upper() in {"PDH", "PDL"}
                        and str(c.get("source") or "").upper() == "DAILY"
                        and str(c.get("kind") or "").lower() == "line"
                    ]

                    v = validate_prev_day_pdh_pdl_candidates_v1(pdhpdl)
                    issues = v.get("issues") or []
                    if issues:
                        pdh_pdl_issue_count += 1
                    if int(v.get("count") or 0) == 2:
                        pdh_pdl_samples_with_candidates += 1
                        for w in v.get("unique_window_ts") or []:
                            try:
                                a, b = w
                                pdh_pdl_window_ts_uniq.add(f"{int(a)}..{int(b)}")
                            except Exception:
                                continue
                        for s in v.get("unique_sources") or []:
                            pdh_pdl_source_counts[str(s)] = (
                                int(pdh_pdl_source_counts.get(str(s), 0)) + 1
                            )
                        for k in v.get("unique_kinds") or []:
                            pdh_pdl_kind_counts[str(k)] = (
                                int(pdh_pdl_kind_counts.get(str(k), 0)) + 1
                            )
                        for lab in v.get("unique_labels") or []:
                            pdh_pdl_label_samples[str(lab)] = (
                                int(pdh_pdl_label_samples.get(str(lab), 0)) + 1
                            )

                    # Інформаційно: 3.2.5b EQH/EQL bands (валідатор застосовуємо до піднабору EQH/EQL).
                    eq_raw = [
                        c
                        for c in raw_cands
                        if isinstance(c, dict)
                        and str(c.get("label") or "").upper() in {"EQH", "EQL"}
                        and str(c.get("source") or "").upper() == "POOL_DERIVED"
                        and str(c.get("kind") or "").lower() == "band"
                    ]
                    ev = validate_eqh_eql_band_candidates_v1(eq_raw)
                    if ev.get("issues"):
                        eq_issue_count += 1
                    if int(ev.get("count") or 0) == 2:
                        eq_samples_with_candidates += 1

        uniq_hashes = sorted(set(hashes))
        top_titles = sorted(title_counts.items(), key=lambda x: (-x[1], x[0]))[:12]

        uniq_shadow_hashes = sorted(set(shadow_hashes))
        top_shadow_titles = sorted(
            shadow_title_counts.items(), key=lambda x: (-x[1], x[0])
        )[:12]

        lines.append(f"## TF={tf}")
        if counts:
            lines.append(
                f"- count: min={min(counts)} max={max(counts)} avg={sum(counts)/len(counts):.2f}"
            )
        else:
            lines.append("- count: (немає даних)")
        lines.append(f"- geometry_hash: unique={len(uniq_hashes)}")
        if top_titles:
            lines.append("- top labels (title → freq):")
            for title, n in top_titles:
                lines.append(f"  - {title} → {n}")

        lines.append("")
        lines.append("### shadow: levels_shadow_v1")
        if shadow_counts:
            lines.append(
                f"- count: min={min(shadow_counts)} max={max(shadow_counts)} avg={sum(shadow_counts)/len(shadow_counts):.2f}"
            )
        else:
            lines.append("- count: (немає даних)")
        lines.append(f"- geometry_hash: unique={len(uniq_shadow_hashes)}")
        if top_shadow_titles:
            lines.append("- top labels (title → freq):")
            for title, n in top_shadow_titles:
                lines.append(f"  - {title} → {n}")
        if match_flags:
            lines.append(
                f"- match(as-is geometry_hash): {sum(match_flags)}/{len(match_flags)}"
            )

        lines.append("")
        lines.append("### candidates_v1: levels_candidates_v1")
        if candidates_counts:
            lines.append(
                f"- count: min={min(candidates_counts)} max={max(candidates_counts)} avg={sum(candidates_counts)/len(candidates_counts):.2f}"
            )
        else:
            lines.append("- count: (немає даних)")
        lines.append(f"- geometry_hash: unique={len(sorted(set(candidates_hashes)))}")
        top_cand_labels = sorted(
            candidates_label_counts.items(), key=lambda x: (-x[1], x[0])
        )[:12]
        if top_cand_labels:
            lines.append("- top labels (label → freq):")
            for label, n in top_cand_labels:
                lines.append(f"  - {label} → {n}")

        lines.append("")
        lines.append("### selected_v1: levels_selected_v1")
        if selected_counts:
            lines.append(
                f"- count: min={min(selected_counts)} max={max(selected_counts)} avg={sum(selected_counts)/len(selected_counts):.2f}"
            )
        else:
            lines.append("- count: (немає даних)")
        lines.append(f"- geometry_hash: unique={len(sorted(set(selected_hashes)))}")

        # 3.2.2b очікування (PDH/PDL prev-day) — лише інформаційний звіт.
        # Гейт/фейл робиться окремо в main() за прапорцем.
        if _normalize_tf(tf) in {"5m", "1h", "4h"}:
            lines.append("")
            lines.append("#### 3.2.2b (prev-day PDH/PDL) інваріанти")
            lines.append(
                f"- знімків з PDH/PDL present: {pdh_pdl_samples_with_candidates}/{len(samples)}"
            )
            lines.append(f"- знімків з issues: {pdh_pdl_issue_count}/{len(samples)}")
            if pdh_pdl_window_ts_uniq:
                lines.append(
                    f"- window_ts unique: {len(sorted(pdh_pdl_window_ts_uniq))}"
                )
            if pdh_pdl_source_counts:
                pairs = sorted(
                    pdh_pdl_source_counts.items(), key=lambda x: (-x[1], x[0])
                )
                lines.append(
                    "- sources (unique per-snapshot): "
                    + ", ".join([f"{k}→{v}" for k, v in pairs])
                )
            if pdh_pdl_kind_counts:
                pairs = sorted(pdh_pdl_kind_counts.items(), key=lambda x: (-x[1], x[0]))
                lines.append(
                    "- kinds (unique per-snapshot): "
                    + ", ".join([f"{k}→{v}" for k, v in pairs])
                )
            if pdh_pdl_label_samples:
                pairs = sorted(
                    pdh_pdl_label_samples.items(), key=lambda x: (-x[1], x[0])
                )
                lines.append(
                    "- labels (unique per-snapshot): "
                    + ", ".join([f"{k}→{v}" for k, v in pairs])
                )

            lines.append("")
            lines.append("#### 3.2.5b (EQH/EQL як band) інваріанти")
            lines.append(
                f"- знімків з EQH/EQL present: {eq_samples_with_candidates}/{len(samples)}"
            )
            lines.append(f"- знімків з issues: {eq_issue_count}/{len(samples)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Levels baseline harness (as-is): знімає viewer_state і емулює поточний UI-відбір levels "
            "через selectPoolsForRender(). Пише baseline.json + baseline_summary.md."
        )
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8080",
        help="Base URL для UI_v2 viewer_state server",
    )
    parser.add_argument("--symbol", required=True, help="Символ, напр. XAUUSD")
    parser.add_argument(
        "--tfs", nargs="+", default=["1m", "5m", "1h", "4h"], help="Список TF"
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=20,
        help="Кількість знімків (мін. 20 рекомендовано)",
    )
    parser.add_argument(
        "--interval-sec", type=float, default=0.7, help="Інтервал між знімками"
    )
    parser.add_argument(
        "--ohlcv-limit", type=int, default=220, help="Ліміт барів для /ohlcv"
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Куди писати артефакти (default: reports/levels_baseline/<timestamp>_<symbol>)",
    )

    parser.add_argument(
        "--strict-3-2-2b-pdhpdl",
        action="store_true",
        help=(
            "Увімкнути гейт 3.2.2b: для TF=5m/1h/4h в кожному знімку candidates_v1 мають бути "
            "рівно PDH+PDL (2 шт) з source=DAILY, kind=line і валідним window_ts."
        ),
    )

    parser.add_argument(
        "--strict-3-2-2b-pdhpdl-require-present",
        action="store_true",
        help=(
            "Разом зі --strict-3-2-2b-pdhpdl: вимагати, щоб candidates були присутні (тобто count=2) "
            "в кожному знімку для TF=5m/1h/4h. Корисно для offline replay, коли очікуємо готові дані."
        ),
    )

    parser.add_argument(
        "--strict-3-2-2c-edhedl",
        action="store_true",
        help=(
            "Увімкнути гейт 3.2.2c: для TF=5m/1h/4h EDH/EDL (today) мають бути або відсутні, або рівно 2 шт "
            "(labels EDH+EDL, source=DAILY, kind=line, валідний window_ts). Додатково перевіряється монотонність по серії знімків."
        ),
    )
    parser.add_argument(
        "--strict-3-2-2c-edhedl-require-present",
        action="store_true",
        help=(
            "Разом зі --strict-3-2-2c-edhedl: вимагати presence EDH/EDL у кожному знімку для TF=5m/1h/4h. "
            "Рекомендується для live replay, де today бари точно є."
        ),
    )

    parser.add_argument(
        "--strict-3-2-3-session",
        action="store_true",
        help=(
            "Увімкнути гейт 3.2.3: SESSION кандидати (ASH/ASL, LSH/LSL, NYH/NYL) мають бути "
            "або відсутні, або присутні попарно (2 шт) з source=SESSION, kind=line і валідним window_ts. "
            "Додатково перевіряється монотонність по серії знімків (в межах одного window_ts)."
        ),
    )
    parser.add_argument(
        "--strict-3-2-3-session-require-present",
        action="store_true",
        help=(
            "Разом зі --strict-3-2-3-session: вимагати presence кожного SESSION-пару у кожному знімку "
            "для TF=5m/1h/4h (корисно для offline replay, коли очікуємо готові дані)."
        ),
    )

    parser.add_argument(
        "--strict-3-2-4-range",
        action="store_true",
        help=(
            "Увімкнути гейт 3.2.4b1: RANGE кандидати (RANGE_H/RANGE_L) мають бути або відсутні, або присутні "
            "попарно (2 шт) з source=RANGE, kind=line та window_ts=None для TF=5m/1h/4h."
        ),
    )
    parser.add_argument(
        "--strict-3-2-4-range-require-present",
        action="store_true",
        help=(
            "Разом зі --strict-3-2-4-range: вимагати presence RANGE_H/RANGE_L у кожному знімку для TF=5m/1h/4h. "
            "Корисно для replay, коли очікуємо, що RANGE вже є у payload."
        ),
    )

    parser.add_argument(
        "--strict-3-2-5-eq",
        action="store_true",
        help=(
            "Увімкнути гейт 3.2.5b: EQ bands (EQH/EQL) мають бути або відсутні, або присутні "
            "попарно (2 шт) з source=POOL_DERIVED, kind=band, price=None, top>bot і window_ts=None для TF=5m/1h/4h."
        ),
    )
    parser.add_argument(
        "--strict-3-2-5-eq-require-present",
        action="store_true",
        help=(
            "Разом зі --strict-3-2-5-eq: вимагати presence EQH/EQL у кожному знімку для TF=5m/1h/4h. "
            "Корисно для replay, коли очікуємо, що EQ вже є у payload."
        ),
    )

    parser.add_argument(
        "--strict-3-3b-merge",
        action="store_true",
        help=(
            "Увімкнути гейт 3.3b: selected_v1 має бути 1:1 merge з candidates_v1 (ігноруємо selection meta, "
            "але перевіряємо rank=1..N та reason містить MERGE_FROM_CANDIDATE_V1)."
        ),
    )
    parser.add_argument(
        "--strict-3-3b-merge-require-present",
        action="store_true",
        help=(
            "Разом зі --strict-3-3b-merge: вимагати presence candidates і selected у кожному знімку. "
            "Корисно після 3.3b, коли очікуємо що selected завжди формується з candidates."
        ),
    )

    parser.add_argument(
        "--strict-3-3-selected-caps",
        action="store_true",
        help=(
            "Увімкнути гейт 3.3c: caps для selected_v1 (TF=5m lines<=3 bands<=2; TF=1h/4h lines<=6 bands<=2; TF=1m=0)."
        ),
    )

    parser.add_argument(
        "--strict-3-3d-freeze-on-close",
        action="store_true",
        help=(
            "Увімкнути гейт 3.3d: selected_v1 має бути freeze-on-close — якщо selected_at_close_ts не змінився, "
            "то selection (items) не має змінюватися між snapshot'ами для кожного TF."
        ),
    )

    parser.add_argument(
        "--strict-3-3f-selected-composition",
        action="store_true",
        help=(
            "Увімкнути гейт 3.3f (TF=5m): композиція selected_v1 по слотах: bands<=2, lines<=3; "
            "pinned active session pair (якщо є і в gate); pinned PDH/PDL (якщо в gate); "
            "та перевірка slot-specific reason[]."
        ),
    )

    args = parser.parse_args()

    base_url = str(args.base_url)
    symbol = str(args.symbol).upper().strip()
    if not symbol:
        raise SystemExit("Порожній symbol")

    tfs = [_normalize_tf(t) for t in (args.tfs or [])]
    if not tfs:
        raise SystemExit("Порожній список TF")

    samples_n = max(1, int(args.samples))
    interval_sec = max(0.0, float(args.interval_sec))
    ohlcv_limit = max(50, int(args.ohlcv_limit))

    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    root = (
        Path(args.out_dir)
        if args.out_dir
        else Path("reports") / "levels_baseline" / f"{run_id}_{symbol}"
    )
    _ensure_dir(root)

    collected: list[dict[str, Any]] = []
    strict_pdhpdl = bool(args.strict_3_2_2b_pdhpdl)
    strict_require_present = bool(args.strict_3_2_2b_pdhpdl_require_present)
    strict_edhedl = bool(args.strict_3_2_2c_edhedl)
    strict_edhedl_require_present = bool(args.strict_3_2_2c_edhedl_require_present)
    strict_session = bool(args.strict_3_2_3_session)
    strict_session_require_present = bool(args.strict_3_2_3_session_require_present)
    strict_range = bool(args.strict_3_2_4_range)
    strict_range_require_present = bool(args.strict_3_2_4_range_require_present)
    strict_eq = bool(args.strict_3_2_5_eq)
    strict_eq_require_present = bool(args.strict_3_2_5_eq_require_present)

    strict_merge = bool(args.strict_3_3b_merge)
    strict_merge_require_present = bool(args.strict_3_3b_merge_require_present)
    strict_selected_caps = bool(args.strict_3_3_selected_caps)
    strict_freeze_on_close = bool(args.strict_3_3d_freeze_on_close)
    strict_selected_composition = bool(args.strict_3_3f_selected_composition)
    strict_issues: list[str] = []

    # 3.3d: попередній signature для freeze-on-close (по TF).
    prev_selected_sig_by_tf: dict[str, tuple[float | None, str]] = {}

    for i in range(samples_n):
        fetched_at = _utc_iso()
        viewer_state = _fetch_viewer_state(base_url, symbol)

        price_raw = viewer_state.get("price")
        try:
            ref_price = float(price_raw) if price_raw is not None else None
        except (TypeError, ValueError):
            ref_price = None

        pools_any = (viewer_state.get("liquidity") or {}).get("pools") or []
        pools: list[dict[str, Any]] = (
            [p for p in pools_any if isinstance(p, dict)]
            if isinstance(pools_any, list)
            else []
        )

        per_tf: dict[str, Any] = {}
        for tf in tfs:
            ohlcv = _fetch_ohlcv(base_url, symbol, tf, limit=ohlcv_limit)
            sel = select_pools_for_render_as_levels(
                pools, ref_price=ref_price, ohlcv_bars=ohlcv
            )
            items = rendered_levels_items(sel)
            shadow_items = shadow_items_from_viewer_state(viewer_state, tf=tf)
            cand_items = candidates_items_from_viewer_state(viewer_state, owner_tf=tf)
            cand_raw = raw_candidates_from_viewer_state(viewer_state, owner_tf=tf)

            selected_items = selected_items_from_viewer_state(viewer_state, owner_tf=tf)
            per_tf[tf] = {
                "count": len(items),
                "items": items,
                "geometry_hash": geometry_hash(items),
                "selection_meta": {
                    "ref_price": sel.ref_price,
                    "price_window_abs": sel.price_window_abs,
                    "merge_tol_abs": sel.merge_tol_abs,
                },
                "shadow": {
                    "count": len(shadow_items),
                    "items": shadow_items,
                    "geometry_hash": geometry_hash(shadow_items),
                },
                "candidates_v1": {
                    "count": len(cand_items),
                    "items": cand_items,
                    "raw": cand_raw,
                    "geometry_hash": candidates_geometry_hash(cand_items),
                },
                "selected_v1": {
                    "count": len(selected_items),
                    "items": selected_items,
                    "geometry_hash": selected_geometry_hash(selected_items),
                },
            }

            if strict_merge and _normalize_tf(tf) in {"5m", "1h", "4h"}:
                merge_issues = validate_selected_merge_from_candidates_v1_strict(
                    candidates_items=cand_items,
                    selected_items=selected_items,
                    require_present=strict_merge_require_present,
                )
                if merge_issues:
                    strict_issues.append(
                        f"i={i} tf={tf}: 3.3b merge: "
                        + "; ".join([str(x) for x in merge_issues])
                    )

            if strict_pdhpdl and _normalize_tf(tf) in {"5m", "1h", "4h"}:
                pd_raw = [
                    c
                    for c in (cand_raw or [])
                    if isinstance(c, dict)
                    and str(c.get("label") or "").upper() in {"PDH", "PDL"}
                    and str(c.get("source") or "").upper() == "DAILY"
                    and str(c.get("kind") or "").lower() == "line"
                ]
                issues = validate_prev_day_pdh_pdl_candidates_v1_strict(
                    pd_raw, require_present=strict_require_present
                )
                if issues:
                    strict_issues.append(
                        f"i={i} tf={tf}: PDH/PDL: "
                        + "; ".join([str(x) for x in issues])
                    )

            if strict_edhedl and _normalize_tf(tf) in {"5m", "1h", "4h"}:
                # Фільтруємо лише EDH/EDL для цього TF.
                ed_raw = [
                    c
                    for c in (cand_raw or [])
                    if isinstance(c, dict)
                    and str(c.get("label") or "").upper() in {"EDH", "EDL"}
                    and str(c.get("source") or "").upper() == "DAILY"
                    and str(c.get("kind") or "").lower() == "line"
                ]
                issues = validate_today_edh_edl_candidates_v1_strict(
                    ed_raw, require_present=strict_edhedl_require_present
                )
                if issues:
                    strict_issues.append(
                        f"i={i} tf={tf}: EDH/EDL: "
                        + "; ".join([str(x) for x in issues])
                    )

            if strict_session and _normalize_tf(tf) in {"5m", "1h", "4h"}:
                pairs = [
                    ("ASH", "ASL"),
                    ("LSH", "LSL"),
                    ("NYH", "NYL"),
                ]
                for hi, lo in pairs:
                    pair_raw = [
                        c
                        for c in (cand_raw or [])
                        if isinstance(c, dict)
                        and str(c.get("label") or "").upper() in {hi, lo}
                        and str(c.get("source") or "").upper() == "SESSION"
                        and str(c.get("kind") or "").lower() == "line"
                    ]
                    issues = validate_session_high_low_candidates_v1_strict(
                        pair_raw,
                        label_high=hi,
                        label_low=lo,
                        require_present=strict_session_require_present,
                    )
                    if issues:
                        strict_issues.append(
                            f"i={i} tf={tf}: "
                            + f"{hi}/{lo}: "
                            + "; ".join([str(x) for x in issues])
                        )

            if strict_range and _normalize_tf(tf) in {"5m", "1h", "4h"}:
                range_raw = [
                    c
                    for c in (cand_raw or [])
                    if isinstance(c, dict)
                    and str(c.get("label") or "").upper() in {"RANGE_H", "RANGE_L"}
                    and str(c.get("source") or "").upper() == "RANGE"
                    and str(c.get("kind") or "").lower() == "line"
                ]
                issues = validate_range_high_low_candidates_v1_strict(
                    range_raw, require_present=strict_range_require_present
                )
                if issues:
                    strict_issues.append(
                        f"i={i} tf={tf}: RANGE_H/RANGE_L: "
                        + "; ".join([str(x) for x in issues])
                    )

            if strict_eq and _normalize_tf(tf) in {"5m", "1h", "4h"}:
                eq_raw = [
                    c
                    for c in (cand_raw or [])
                    if isinstance(c, dict)
                    and str(c.get("label") or "").upper() in {"EQH", "EQL"}
                    and str(c.get("source") or "").upper() == "POOL_DERIVED"
                    and str(c.get("kind") or "").lower() == "band"
                ]
                issues = validate_eqh_eql_band_candidates_v1_strict(
                    eq_raw, require_present=strict_eq_require_present
                )
                if issues:
                    strict_issues.append(
                        f"i={i} tf={tf}: EQH/EQL: "
                        + "; ".join([str(x) for x in issues])
                    )

        if strict_selected_caps:
            caps_issues = validate_selected_caps_v1(viewer_state)
            if caps_issues:
                strict_issues.append(
                    f"i={i}: 3.3c caps: " + "; ".join([str(x) for x in caps_issues])
                )

        if strict_selected_composition:
            tf = "5m"
            tf_pack = per_tf.get(tf) or {}
            cand_raw_5m = (tf_pack.get("candidates_v1") or {}).get("items")
            selected_items_5m = (tf_pack.get("selected_v1") or {}).get("items")
            if isinstance(cand_raw_5m, list) and isinstance(selected_items_5m, list):
                comp_issues = validate_selected_composition_5m_v1(
                    base_url=base_url,
                    symbol=symbol,
                    viewer_state=viewer_state,
                    selected_items_5m=selected_items_5m,
                    candidates_raw_5m=cand_raw_5m,
                    ohlcv_limit=ohlcv_limit,
                )
                if comp_issues:
                    strict_issues.append(
                        f"i={i}: 3.3f composition(5m): "
                        + "; ".join([str(x) for x in comp_issues])
                    )

        if strict_freeze_on_close:
            for tf in tfs:
                tf_norm = _normalize_tf(tf)
                if tf_norm not in {"5m", "1h", "4h", "1m"}:
                    continue
                selected_items = ((per_tf.get(tf) or {}).get("selected_v1") or {}).get(
                    "items"
                )
                if not isinstance(selected_items, list) or not selected_items:
                    continue

                close_ts_values: set[float] = set()
                for it in selected_items:
                    if not isinstance(it, dict):
                        continue
                    v = it.get("selected_at_close_ts")
                    try:
                        x = float(v) if v is not None else None
                    except (TypeError, ValueError):
                        x = None
                    if x is not None and x == x:
                        close_ts_values.add(float(x))

                if len(close_ts_values) != 1:
                    strict_issues.append(
                        f"i={i} tf={tf}: 3.3d freeze: очікуємо один selected_at_close_ts, але отримали {sorted(list(close_ts_values))}"
                    )
                    continue

                close_ts = next(iter(close_ts_values))
                sig = json.dumps(selected_items, ensure_ascii=False, sort_keys=True)
                prev = prev_selected_sig_by_tf.get(tf_norm)
                if prev is not None and prev[0] == close_ts and prev[1] != sig:
                    strict_issues.append(
                        f"i={i} tf={tf}: 3.3d freeze: selection змінився при тому самому selected_at_close_ts={close_ts}"
                    )
                prev_selected_sig_by_tf[tf_norm] = (close_ts, sig)

        collected.append(
            {
                "i": i,
                "fetched_at": fetched_at,
                "symbol": symbol,
                "payload_ts": viewer_state.get("payload_ts"),
                "payload_seq": viewer_state.get("payload_seq"),
                "price": ref_price,
                "per_tf": per_tf,
            }
        )

        if interval_sec > 0 and i < samples_n - 1:
            time.sleep(interval_sec)

    baseline = {
        "schema": "levels_baseline_v1",
        "captured_at": _utc_iso(),
        "symbol": symbol,
        "base_url": base_url,
        "tfs": tfs,
        "samples": samples_n,
        "interval_sec": interval_sec,
        "ohlcv_limit": ohlcv_limit,
        "notes": {
            "meaning": "AS-IS baseline: емулюємо поточний UI_v2 selectPoolsForRender() як 'levels'.",
        },
        "snapshots": collected,
    }

    # Cross-snapshot гейти (монотонність) для 3.2.2c.
    if strict_edhedl:
        for tf in tfs:
            if _normalize_tf(tf) not in {"5m", "1h", "4h"}:
                continue
            mono_issues = validate_today_edh_edl_monotonicity(
                samples=collected,
                tf=tf,
                require_present=strict_edhedl_require_present,
            )
            for msg in mono_issues:
                strict_issues.append(str(msg))

    # Cross-snapshot гейти (монотонність) для 3.2.3 (SESSION).
    if strict_session:
        pairs = [
            ("ASH", "ASL"),
            ("LSH", "LSL"),
            ("NYH", "NYL"),
        ]
        for tf in tfs:
            if _normalize_tf(tf) not in {"5m", "1h", "4h"}:
                continue
            for hi, lo in pairs:
                mono_issues = validate_session_high_low_monotonicity(
                    samples=collected,
                    tf=tf,
                    label_high=hi,
                    label_low=lo,
                    require_present=False,
                )
                for msg in mono_issues:
                    strict_issues.append(str(msg))

    if strict_issues:
        baseline["validation"] = {
            "strict_3_2_2b_pdhpdl": bool(strict_pdhpdl),
            "strict_3_2_2c_edhedl": bool(strict_edhedl),
            "strict_3_2_3_session": bool(strict_session),
            "strict_3_2_4_range": bool(strict_range),
            "strict_3_2_5_eq": bool(strict_eq),
            "issues": strict_issues,
        }

    _write_json(root / "baseline.json", baseline)
    _write_text(
        root / "baseline_summary.md",
        build_baseline_summary_md(
            symbol=symbol, base_url=base_url, tfs=tfs, samples=collected
        ),
    )

    if strict_issues:
        _write_text(
            root / "validation_issues.md",
            "# Strict issues\n\n" + "\n".join([f"- {x}" for x in strict_issues]) + "\n",
        )
        print(
            f"FAIL: знайдено issues={len(strict_issues)} (див. {root / 'validation_issues.md'})"
        )
        return 2

    print(f"OK: baseline збережено в {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
