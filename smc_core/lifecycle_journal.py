"""SMC Lifecycle Journal (zones/pools/magnets).

Ціль: отримати текстовий (JSONL) журнал подій, щоб аудіювати шум/флікер та
поведінку SMC-обʼєктів без привʼязки до UI.

Ключова ідея:
- беремо стабільний `SmcHintPlain` (dict),
- на кожному 5m snapshot робимо diff prev→cur,
- генеруємо події created/removed/touched/mitigated/merged,
- (опційно) інкрементуємо Prometheus метрики.

Увага: це best-effort QA інструмент. Частина причин removed не присутня в
`SmcHintPlain`, тому reason класифікується евристично з використанням meta/порогів.
"""

from __future__ import annotations

import importlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

JournalEntity = Literal["zone", "pool", "magnet"]
JournalEvent = Literal[
    "created",
    "removed",
    "touched",
    "mitigated",
    "merged",
]

RemovedReason = Literal[
    "expired_ttl",
    "evicted_cap",
    "dropped_distance",
    "invalidated_rule",
    "replaced_by_merge",
]

TouchType = Literal["wick", "body", "close", "level"]

ComputeKind = Literal["preview", "close"]


class MetricsSink(Protocol):
    def inc_created(self, *, entity: JournalEntity) -> None: ...

    def inc_removed(self, *, entity: JournalEntity, reason: RemovedReason) -> None: ...

    def inc_touched(self, *, entity: JournalEntity, late: bool) -> None: ...

    def inc_merged(self, *, entity: JournalEntity) -> None: ...

    def observe_lifetime_bars(
        self, *, entity: JournalEntity, lifetime_bars: int
    ) -> None: ...


try:  # pragma: no cover - опціонально
    _PromCounter = importlib.import_module("prometheus_client").Counter
    _PromHistogram = importlib.import_module("prometheus_client").Histogram
except Exception:  # pragma: no cover - бібліотека може бути відсутня
    _PromCounter = None
    _PromHistogram = None


class _NoopCounter:
    def labels(self, *args: Any, **kwargs: Any) -> _NoopCounter:  # noqa: D401
        return self

    def inc(self, value: float = 1.0) -> None:  # noqa: D401
        return None


class _NoopHistogram:
    def labels(self, *args: Any, **kwargs: Any) -> _NoopHistogram:  # noqa: D401
        return self

    def observe(self, value: float) -> None:  # noqa: D401
        return None


def _build_counter(
    name: str, description: str, *, labelnames: tuple[str, ...] = ()
) -> Any:
    if _PromCounter is None:
        return _NoopCounter()
    try:
        return _PromCounter(name, description, labelnames=labelnames)
    except Exception:  # pragma: no cover - уже зареєстровано в процесі
        return _NoopCounter()


def _build_histogram(
    name: str,
    description: str,
    *,
    labelnames: tuple[str, ...] = (),
    buckets: tuple[float, ...] | None = None,
) -> Any:
    if _PromHistogram is None:
        return _NoopHistogram()
    try:
        if buckets is None:
            return _PromHistogram(name, description, labelnames=labelnames)
        return _PromHistogram(name, description, labelnames=labelnames, buckets=buckets)
    except Exception:  # pragma: no cover - уже зареєстровано в процесі
        return _NoopHistogram()


SMC_LIFECYCLE_CREATED_TOTAL = _build_counter(
    "ai_one_smc_lifecycle_created_total",
    "Кількість створених SMC-обʼєктів (journal).",
    labelnames=("entity",),
)
SMC_LIFECYCLE_REMOVED_TOTAL = _build_counter(
    "ai_one_smc_lifecycle_removed_total",
    "Кількість видалених SMC-обʼєктів (journal).",
    labelnames=("entity", "reason"),
)
SMC_LIFECYCLE_TOUCHED_TOTAL = _build_counter(
    "ai_one_smc_lifecycle_touched_total",
    "Кількість торкань SMC-обʼєктів (journal).",
    labelnames=("entity", "late"),
)
SMC_LIFECYCLE_MERGED_TOTAL = _build_counter(
    "ai_one_smc_lifecycle_merged_total",
    "Кількість merge-подій SMC-обʼєктів (journal).",
    labelnames=("entity",),
)
SMC_LIFECYCLE_LIFETIME_BARS = _build_histogram(
    "ai_one_smc_lifecycle_lifetime_bars",
    "Тривалість життя SMC-обʼєктів у барах (від created до removed).",
    labelnames=("entity",),
    buckets=(1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377),
)


@dataclass(slots=True)
class PrometheusMetricsSink:
    """Prometheus-реалізація MetricsSink (лічильники/гістограма).

    Важливо: експорт /metrics залежить від процесу, який хостить endpoint.
    Тут ми тільки інкрементуємо метрики.
    """

    def inc_created(self, *, entity: JournalEntity) -> None:
        SMC_LIFECYCLE_CREATED_TOTAL.labels(entity=str(entity)).inc()

    def inc_removed(self, *, entity: JournalEntity, reason: RemovedReason) -> None:
        SMC_LIFECYCLE_REMOVED_TOTAL.labels(entity=str(entity), reason=str(reason)).inc()

    def inc_touched(self, *, entity: JournalEntity, late: bool) -> None:
        SMC_LIFECYCLE_TOUCHED_TOTAL.labels(
            entity=str(entity), late=str(bool(late))
        ).inc()

    def inc_merged(self, *, entity: JournalEntity) -> None:
        SMC_LIFECYCLE_MERGED_TOTAL.labels(entity=str(entity)).inc()

    def observe_lifetime_bars(
        self, *, entity: JournalEntity, lifetime_bars: int
    ) -> None:
        SMC_LIFECYCLE_LIFETIME_BARS.labels(entity=str(entity)).observe(
            float(lifetime_bars)
        )


@dataclass(slots=True)
class _NoopMetrics:
    def inc_created(self, *, entity: JournalEntity) -> None:
        return

    def inc_removed(self, *, entity: JournalEntity, reason: RemovedReason) -> None:
        return

    def inc_touched(self, *, entity: JournalEntity, late: bool) -> None:
        return

    def inc_merged(self, *, entity: JournalEntity) -> None:
        return

    def observe_lifetime_bars(
        self, *, entity: JournalEntity, lifetime_bars: int
    ) -> None:
        return


@dataclass(frozen=True, slots=True)
class BarSnapshot:
    """Мінімальний опис бару для touched/mitigated."""

    open: float
    high: float
    low: float
    close: float
    close_time_ms: int
    complete: bool = True


def extract_active_ids_from_hint(hint: dict[str, Any]) -> dict[str, set[str]]:
    """Повертає активні id для frame-логів (preview/close) із SmcHintPlain.

    Мета: стабільні ID для preview_vs_close_delta, без "дельт" через мікро-зсув рівнів.

    Дефолтний набір сутностей (для звіту):
    - zone/pool/magnet (як і раніше)
    - structure_event (BOS/CHOCH)
    - range_state (скаляр)
    - active_range (обʼєкт, без end_time)
    - ote (OTE zones)
    - amd_phase (скаляр)
    - wick_cluster
    """

    tick = _resolve_tick(hint)

    out: dict[str, set[str]] = {
        "zone": set(),
        "pool": set(),
        "magnet": set(),
        "structure_event": set(),
        "range_state": set(),
        "active_range": set(),
        "ote": set(),
        "amd_phase": set(),
        "wick_cluster": set(),
    }

    zones_obj = hint.get("zones")
    if isinstance(zones_obj, dict):
        zones = zones_obj.get("zones")
        if isinstance(zones, list):
            for z in zones:
                if not isinstance(z, dict):
                    continue
                zid = _zone_id(z)
                if zid:
                    out["zone"].add(zid)
                    continue
                fid = _zone_fallback_id(z, tick=tick)
                if fid:
                    out["zone"].add(fid)

    liq_obj = hint.get("liquidity")
    if isinstance(liq_obj, dict):
        pools = liq_obj.get("pools")
        if isinstance(pools, list):
            for p in pools:
                if not isinstance(p, dict):
                    continue
                out["pool"].add(_pool_id_quantized(p, tick=tick))

        magnets = liq_obj.get("magnets")
        if isinstance(magnets, list):
            for m in magnets:
                if not isinstance(m, dict):
                    continue
                out["magnet"].add(_magnet_id_quantized(m, tick=tick))

        amd_phase = liq_obj.get("amd_phase")
        if amd_phase is not None:
            phase_s = str(amd_phase).strip()
            if phase_s:
                out["amd_phase"].add(f"amd:{phase_s}")

        liq_meta = liq_obj.get("meta")
        if isinstance(liq_meta, dict):
            wick_clusters = liq_meta.get("wick_clusters")
            if isinstance(wick_clusters, list):
                for wc in wick_clusters:
                    if not isinstance(wc, dict):
                        continue
                    cid = wc.get("cluster_id")
                    if isinstance(cid, str) and cid:
                        out["wick_cluster"].add(f"wcluster:{cid}")
                        continue

                    side = str(wc.get("side") or wc.get("direction") or "?").strip()
                    level = _safe_float(wc.get("level"))
                    if level is None:
                        continue
                    level_q = _q_price(level, tick=tick)
                    out["wick_cluster"].add(f"wcluster:{side}:{level_q}")

            # (майбутнє) sfp_events
            sfp_events = liq_meta.get("sfp_events")
            if isinstance(sfp_events, list):
                # Не включаємо в дефолтний набір ключів, але IDs додаємо в same bucket "wick_cluster"
                # лише якщо сутність реально потрібна. Поки що пропускаємо, щоб не ламати порядок.
                pass

    struct_obj = hint.get("structure")
    if isinstance(struct_obj, dict):
        # 1) structure.events (BOS/CHOCH)
        events = struct_obj.get("events")
        if isinstance(events, list):
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                ev_type = str(
                    ev.get("event_type") or ev.get("type") or ev.get("kind") or "?"
                ).strip()
                direction = str(ev.get("direction") or ev.get("side") or "?").strip()
                time_s = _time_s(
                    ev.get("time_s") or ev.get("time") or ev.get("timestamp")
                )

                source_leg = ev.get("source_leg")
                from_idx = (
                    _nested_int(source_leg, "from_swing", "index")
                    if isinstance(source_leg, dict)
                    else None
                )
                to_idx = (
                    _nested_int(source_leg, "to_swing", "index")
                    if isinstance(source_leg, dict)
                    else None
                )
                if from_idx is None:
                    from_idx = _nested_int(ev, "from_swing", "index")
                if to_idx is None:
                    to_idx = _nested_int(ev, "to_swing", "index")

                from_s = str(from_idx) if from_idx is not None else "?"
                to_s = str(to_idx) if to_idx is not None else "?"
                t_s = str(time_s) if time_s is not None else "?"
                out["structure_event"].add(
                    f"{ev_type}:{direction}:{t_s}:{from_s}->{to_s}"
                )

        # 2) range_state (скаляр)
        range_state = struct_obj.get("range_state")
        if range_state is not None:
            rs = str(range_state).strip()
            if rs:
                out["range_state"].add(f"range_state:{rs}")

        # 2) active_range (обʼєкт) — без end_time
        active_range = struct_obj.get("active_range")
        if isinstance(active_range, dict):
            start_time_s = _time_s(
                active_range.get("start_time_s")
                or active_range.get("start_time")
                or active_range.get("start")
                or active_range.get("timestamp")
            )
            low = _safe_float(active_range.get("low") or active_range.get("price_low"))
            high = _safe_float(
                active_range.get("high") or active_range.get("price_high")
            )
            if start_time_s is not None and low is not None and high is not None:
                low_q = _q_price(low, tick=tick)
                high_q = _q_price(high, tick=tick)
                out["active_range"].add(f"range:{int(start_time_s)}:{low_q}:{high_q}")

        # 3) structure.ote_zones
        ote_zones = struct_obj.get("ote_zones")
        if isinstance(ote_zones, list):
            for oz in ote_zones:
                if not isinstance(oz, dict):
                    continue
                direction = str(oz.get("direction") or oz.get("side") or "?").strip()
                role = str(oz.get("role") or "?").strip()
                from_idx = _nested_int(oz, "from_swing", "index")
                to_idx = _nested_int(oz, "to_swing", "index")
                if from_idx is None:
                    from_idx = _safe_int(oz.get("from_idx") or oz.get("from_index"))
                if to_idx is None:
                    to_idx = _safe_int(oz.get("to_idx") or oz.get("to_index"))

                pmin = _safe_float(
                    oz.get("price_min") or oz.get("min") or oz.get("low")
                )
                pmax = _safe_float(
                    oz.get("price_max") or oz.get("max") or oz.get("high")
                )
                if pmin is None or pmax is None:
                    continue
                min_q = _q_price(pmin, tick=tick)
                max_q = _q_price(pmax, tick=tick)
                from_s = str(from_idx) if from_idx is not None else "?"
                to_s = str(to_idx) if to_idx is not None else "?"
                out["ote"].add(
                    f"ote:{direction}:{role}:{from_s}->{to_s}:{min_q}:{max_q}"
                )

    return out


def build_frame_record(
    *,
    symbol: str,
    tf: str,
    now_ms: int,
    kind: ComputeKind,
    primary_close_ms: int,
    bar_complete: bool,
    hint: dict[str, Any],
) -> dict[str, Any]:
    """Будує мінімальний frame marker запис для preview_vs_close_delta.

    Це окремий запис, який пишеться *на кожний snapshot*, навіть якщо diff-подій немає.
    """

    ids = extract_active_ids_from_hint(hint)

    # --- Випадок E: overlap-матриця активних зон (IoU) ---
    # Рахуємо перекриття для active_zones, щоб мати офлайн-метрику конкуренції
    # "дві зони як одна" по кроках (без залежності від UI).
    zone_overlap_active: dict[str, Any] = {
        "n_active": 0,
        "total_pairs": 0,
        "pairs_iou_ge": {"0.2": 0, "0.4": 0, "0.6": 0},
    }
    try:
        zones_obj = hint.get("zones")
        active = zones_obj.get("active_zones") if isinstance(zones_obj, dict) else None
        if isinstance(active, list):
            bounds: list[tuple[float, float]] = []
            for z in active:
                if not isinstance(z, dict):
                    continue
                pmin = _safe_float(z.get("price_min"))
                pmax = _safe_float(z.get("price_max"))
                if pmin is None or pmax is None:
                    continue
                lo = min(float(pmin), float(pmax))
                hi = max(float(pmin), float(pmax))
                if hi <= lo:
                    continue
                bounds.append((lo, hi))

            n = len(bounds)
            zone_overlap_active["n_active"] = int(n)
            total_pairs = (n * (n - 1)) // 2
            zone_overlap_active["total_pairs"] = int(total_pairs)
            if n >= 2:
                c02 = c04 = c06 = 0
                for i in range(n):
                    a_lo, a_hi = bounds[i]
                    for j in range(i + 1, n):
                        b_lo, b_hi = bounds[j]
                        inter = max(0.0, min(a_hi, b_hi) - max(a_lo, b_lo))
                        union = max(a_hi, b_hi) - min(a_lo, b_lo)
                        if union <= 0:
                            continue
                        iou = inter / union
                        if iou >= 0.2:
                            c02 += 1
                        if iou >= 0.4:
                            c04 += 1
                        if iou >= 0.6:
                            c06 += 1
                zone_overlap_active["pairs_iou_ge"] = {
                    "0.2": int(c02),
                    "0.4": int(c04),
                    "0.6": int(c06),
                }
    except Exception:
        pass
    keys = [
        "zone",
        "pool",
        "magnet",
        "structure_event",
        "range_state",
        "active_range",
        "ote",
        "amd_phase",
        "wick_cluster",
    ]

    active_ids: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    for k in keys:
        items = sorted(ids.get(k) or set())
        active_ids[k] = items
        counts[k] = len(items)

    return {
        "ts": _utc_iso_from_ms(now_ms),
        "symbol": str(symbol).upper(),
        "tf": str(tf),
        "kind": str(kind),
        "primary_close_ms": int(primary_close_ms),
        "bar_complete": bool(bar_complete),
        "counts": counts,
        "active_ids": active_ids,
        "zone_overlap_active": zone_overlap_active,
    }


def _safe_int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def _nested_int(obj: Any, key1: str, key2: str) -> int | None:
    if not isinstance(obj, dict):
        return None
    inner = obj.get(key1)
    if not isinstance(inner, dict):
        return None
    return _safe_int(inner.get(key2))


def _time_s(v: Any) -> int | None:
    """Повертає timestamp у секундах (int) із ms/seconds/ISO (best-effort)."""

    if v is None:
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        if x <= 0:
            return None
        # якщо схоже на ms
        if x >= 10_000_000_000:
            return int(x // 1000.0)
        return int(x)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        # numeric string
        try:
            return _time_s(float(s))
        except Exception:
            pass
        # ISO
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return int(dt.timestamp())
        except Exception:
            return None
    return None


def _resolve_tick(hint: dict[str, Any]) -> float | None:
    """Best-effort tick для квантизації цін у frames.

    Пріоритет:
    - явний tick у hint (tick або meta.tick або context.tick)
    - fallback: atr_last * 0.01 (1% ATR) якщо atr доступний
    """

    # 1) explicit tick
    candidates: list[Any] = [
        hint.get("tick"),
        (
            (hint.get("meta") or {}).get("tick")
            if isinstance(hint.get("meta"), dict)
            else None
        ),
        (
            (hint.get("context") or {}).get("tick")
            if isinstance(hint.get("context"), dict)
            else None
        ),
    ]
    for c in candidates:
        t = _safe_float(c)
        if t is not None and t > 0:
            return float(t)

    # 2) atr_last fallback
    atr_candidates: list[Any] = []
    meta = hint.get("meta")
    if isinstance(meta, dict):
        atr_candidates.append(meta.get("atr_last"))

    liq = hint.get("liquidity")
    if isinstance(liq, dict):
        atr_candidates.append(liq.get("atr_last"))
        liq_meta = liq.get("meta")
        if isinstance(liq_meta, dict):
            atr_candidates.append(liq_meta.get("atr_last"))

    zones = hint.get("zones")
    if isinstance(zones, dict):
        atr_candidates.append(zones.get("atr_last"))
        zmeta = zones.get("meta")
        if isinstance(zmeta, dict):
            atr_candidates.append(zmeta.get("atr_last"))

    struct = hint.get("structure")
    if isinstance(struct, dict):
        atr_candidates.append(struct.get("atr_last"))
        smeta = struct.get("meta")
        if isinstance(smeta, dict):
            atr_candidates.append(smeta.get("atr_last"))

    for c in atr_candidates:
        atr = _safe_float(c)
        if atr is not None and atr > 0:
            tick = float(atr) * 0.01
            # захист від занадто малих/дивних значень
            if tick > 0:
                return tick

    return None


def _q_price(price: float, *, tick: float | None) -> str:
    """Квантизує ціну (string) для стабільних ID."""

    if tick is None or tick <= 0:
        return f"{float(price):.6f}"

    try:
        q = round(float(price) / float(tick)) * float(tick)
    except Exception:
        return f"{float(price):.6f}"

    # підбираємо кількість знаків після коми від tick
    try:
        dec = max(0, min(10, int(math.ceil(-math.log10(float(tick)))) + 1))
    except Exception:
        dec = 6
    return f"{q:.{dec}f}"


def _zone_fallback_id(zone: dict[str, Any], *, tick: float | None) -> str | None:
    """Fallback ID для зони, якщо zone_id відсутній.

    Формат (best-effort):
    zone:{zone_type}:{timeframe}:{origin_time_s}:{direction}:{role}:{min_q}:{max_q}
    """

    zone_type = str(zone.get("zone_type") or zone.get("type") or "?").strip()
    timeframe = str(zone.get("timeframe") or zone.get("tf") or "?").strip()
    direction = str(zone.get("direction") or "?").strip()
    role = str(zone.get("role") or "?").strip()

    origin_time_s = _time_s(
        zone.get("origin_time_s")
        or zone.get("origin_time")
        or zone.get("created_time")
        or zone.get("timestamp")
    )
    pmin = _safe_float(zone.get("price_min"))
    pmax = _safe_float(zone.get("price_max"))
    if pmin is None or pmax is None:
        return None
    min_q = _q_price(pmin, tick=tick)
    max_q = _q_price(pmax, tick=tick)
    t_s = str(int(origin_time_s)) if origin_time_s is not None else "?"
    return f"zone:{zone_type}:{timeframe}:{t_s}:{direction}:{role}:{min_q}:{max_q}"


def _pool_id_quantized(pool: dict[str, Any], *, tick: float | None) -> str:
    liq_type = str(pool.get("liq_type") or "?")
    role = str(pool.get("role") or "")
    meta_any = pool.get("meta")
    meta: dict[str, Any] = meta_any if isinstance(meta_any, dict) else {}
    if str(liq_type).upper() == "WICK_CLUSTER":
        cid = meta.get("cluster_id")
        if isinstance(cid, str) and cid:
            cid_s = cid.replace(":", "_")
            return f"pool:{liq_type}:{role}:cid_{cid_s}:-:-".strip()

    first = str(pool.get("first_time") or "")
    last = str(pool.get("last_time") or "")
    level = _safe_float(pool.get("level"))
    level_q = _q_price(level, tick=tick) if isinstance(level, float) else "?"
    return f"pool:{liq_type}:{role}:{level_q}:{first}:{last}".strip()


def _magnet_id_quantized(magnet: dict[str, Any], *, tick: float | None) -> str:
    liq_type = str(magnet.get("liq_type") or "?")
    role = str(magnet.get("role") or "")
    center = _safe_float(magnet.get("center"))
    pmin = _safe_float(magnet.get("price_min"))
    pmax = _safe_float(magnet.get("price_max"))
    center_q = _q_price(center, tick=tick) if isinstance(center, float) else "?"
    pmin_q = _q_price(pmin, tick=tick) if isinstance(pmin, float) else "?"
    pmax_q = _q_price(pmax, tick=tick) if isinstance(pmax, float) else "?"
    return f"magnet:{liq_type}:{role}:{center_q}:{pmin_q}:{pmax_q}".strip()


def _utc_iso_from_ms(ms: int) -> str:
    try:
        dt = datetime.fromtimestamp(int(ms) / 1000.0, tz=UTC)
        return dt.isoformat()
    except Exception:
        return "-"


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except Exception:
        return None


def _zone_id(zone: dict[str, Any]) -> str | None:
    zid = zone.get("zone_id")
    if isinstance(zid, str) and zid.strip():
        return zid.strip()
    return None


def _pool_id(pool: dict[str, Any]) -> str:
    # У SmcLiquidityPool немає id — робимо детермінований ключ.
    liq_type = str(pool.get("liq_type") or "?")
    meta_any = pool.get("meta")
    meta: dict[str, Any] = meta_any if isinstance(meta_any, dict) else {}
    if str(liq_type).upper() == "WICK_CLUSTER":
        cid = meta.get("cluster_id")
        if isinstance(cid, str) and cid:
            cid_s = cid.replace(":", "_")
            role = str(pool.get("role") or "")
            return f"pool:{liq_type}:{role}:cid_{cid_s}:-:-".strip()
    level = _safe_float(pool.get("level"))
    first = str(pool.get("first_time") or "")
    last = str(pool.get("last_time") or "")
    role = str(pool.get("role") or "")
    level_s = f"{level:.6f}" if isinstance(level, float) else "?"
    return f"pool:{liq_type}:{role}:{level_s}:{first}:{last}".strip()


def _parse_pool_id(pool_id: str) -> dict[str, str]:
    """Best-effort парсер нашого детермінованого pool-id.

    Формат: pool:{liq_type}:{role}:{level_s}:{first}:{last}
    Повертає ключі liq_type/role/level_s/first/last (якщо вдалося).
    """

    s = str(pool_id or "")
    parts = s.split(":")
    if len(parts) < 6 or parts[0] != "pool":
        return {}
    return {
        "liq_type": str(parts[1]),
        "role": str(parts[2]),
        "level_s": str(parts[3]),
        "first": str(parts[4]),
        "last": str(parts[5]),
    }


def _magnet_id(magnet: dict[str, Any]) -> str:
    liq_type = str(magnet.get("liq_type") or "?")
    pmin = _safe_float(magnet.get("price_min"))
    pmax = _safe_float(magnet.get("price_max"))
    center = _safe_float(magnet.get("center"))
    role = str(magnet.get("role") or "")
    pmin_s = f"{pmin:.6f}" if isinstance(pmin, float) else "?"
    pmax_s = f"{pmax:.6f}" if isinstance(pmax, float) else "?"
    center_s = f"{center:.6f}" if isinstance(center, float) else "?"
    return f"magnet:{liq_type}:{role}:{center_s}:{pmin_s}:{pmax_s}".strip()


def _bar_intersects_zone(
    bar: BarSnapshot, *, price_min: float, price_max: float, eps: float = 0.0
) -> TouchType | None:
    # Детермінований touch (Випадок F): перетин [low,high] з [min-eps, max+eps].
    # eps у абсолютних одиницях ціни.
    e = max(0.0, float(eps or 0.0))
    pmin = float(price_min) - e
    pmax = float(price_max) + e
    # 1) Wick
    if bar.low <= pmax and bar.high >= pmin:
        # 2) Body
        body_low = min(bar.open, bar.close)
        body_high = max(bar.open, bar.close)
        if body_low <= pmax and body_high >= pmin:
            # 3) Close inside
            if pmin <= bar.close <= pmax:
                return "close"
            return "body"
        return "wick"
    return None


def _bar_touches_level(bar: BarSnapshot, level: float) -> bool:
    return bar.low <= level <= bar.high


def _tf_minutes(tf: str) -> int:
    tf_norm = str(tf or "").strip().lower()
    if tf_norm.endswith("m"):
        try:
            return int(tf_norm[:-1])
        except Exception:
            return 0
    if tf_norm.endswith("h"):
        try:
            return int(tf_norm[:-1]) * 60
        except Exception:
            return 0
    return 0


@dataclass(slots=True)
class _EntityState:
    entity: JournalEntity
    id: str
    type: str
    direction: str
    role: str
    price_min: float | None = None
    price_max: float | None = None
    level: float | None = None
    created_at_ms: int = 0
    created_step: int = 0
    last_seen_step: int = 0
    was_touched: bool = False
    last_touch_ms: int | None = None


@dataclass(slots=True)
class SmcLifecycleJournal:
    """Stateful diff+journal генератор для одного symbol/tf."""

    symbol: str
    tf: str
    metrics: MetricsSink = field(default_factory=_NoopMetrics)

    # Preview ≠ truth: removed на preview не фіналимо. На close можна ввімкнути
    # "grace" (підтвердження) — removed лише якщо об'єкт відсутній N close-кроків.
    removed_confirm_close_steps: int = 1

    _step: int = 0
    _prev: dict[tuple[JournalEntity, str], _EntityState] = field(default_factory=dict)
    _removed_cache: dict[
        tuple[JournalEntity, str], tuple[_EntityState, RemovedReason, str, int]
    ] = field(default_factory=dict)

    # Для евристики evicted_cap по POI
    _prev_poi_dropped_due_cap: int = 0

    # Для grace: ключі, які тимчасово зникли на close.
    _missing_close_steps: dict[tuple[JournalEntity, str], int] = field(
        default_factory=dict
    )

    # Для reason_sub: ловимо context flip між снапшотами.
    _prev_bias: str | None = None
    _prev_range_state: str | None = None

    def process_snapshot(
        self,
        *,
        hint: dict[str, Any],
        now_ms: int,
        bar: BarSnapshot | None = None,
        compute_kind: ComputeKind | None = None,
        primary_close_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """Обробляє один SmcHintPlain і повертає список подій (для JSONL).

        now_ms: курсор часу (close_time бару, на якому порахований snapshot).
        bar: (опційно) останній бар compute_tf для touched/mitigated.
        """

        self._step += 1

        cur, ctx = self._extract_entities(
            hint=hint,
            now_ms=now_ms,
            bar=bar,
            compute_kind=compute_kind,
            primary_close_ms=primary_close_ms,
        )

        # Збагачуємо контекст для reason_sub.
        ctx["prev_bias"] = self._prev_bias
        ctx["prev_range_state"] = self._prev_range_state
        events: list[dict[str, Any]] = []

        prev_keys = set(self._prev.keys())
        cur_keys = set(cur.keys())

        # Preview ≠ truth: removed на preview не фіналимо.
        if compute_kind == "preview":
            removed_keys: list[tuple[JournalEntity, str]] = []
        else:
            removed_keys = sorted(prev_keys - cur_keys)

        # 1) created
        for key in sorted(cur_keys - prev_keys):
            st = cur[key]
            st.created_at_ms = int(now_ms)
            st.created_step = int(self._step)
            st.last_seen_step = int(self._step)
            self._prev[key] = st
            events.append(self._event_dict(st, event="created", now_ms=now_ms, ctx=ctx))
            self.metrics.inc_created(entity=st.entity)

            # Магніти зазвичай агрегують пули — це природний merged.
            if st.entity == "magnet":
                merged_from = self._extract_magnet_pool_ids(hint, magnet_id=st.id)
                if merged_from:
                    events.append(
                        self._event_dict(
                            st,
                            event="merged",
                            now_ms=now_ms,
                            ctx={**ctx, "merged_from": merged_from},
                        )
                    )
                    self.metrics.inc_merged(entity=st.entity)

        # 2) removed
        confirm_steps = max(1, int(self.removed_confirm_close_steps or 1))
        # Якщо об'єкт з'явився знову — скидаємо missing counter.
        for key in list(self._missing_close_steps.keys()):
            if key in cur_keys:
                self._missing_close_steps.pop(key, None)

        for key in removed_keys:
            st_prev = self._prev.get(key)
            if st_prev is None:
                continue

            # Grace only для close: якщо confirm_steps>1 — не видаляємо одразу.
            if confirm_steps > 1 and compute_kind != "preview":
                missing = int(self._missing_close_steps.get(key, 0)) + 1
                self._missing_close_steps[key] = missing
                if missing < confirm_steps:
                    continue
                # Підтвердили removed.
                self._missing_close_steps.pop(key, None)

            reason = self._classify_removed_reason(
                hint_prev_state=st_prev,
                hint_cur=hint,
                ctx=ctx,
            )
            reason_sub = self._classify_removed_reason_sub(
                hint_prev_state=st_prev,
                hint_cur=hint,
                ctx=ctx,
                reason=reason,
            )
            lifetime_bars = max(0, int(self._step - st_prev.created_step))
            self.metrics.observe_lifetime_bars(
                entity=st_prev.entity, lifetime_bars=lifetime_bars
            )
            self.metrics.inc_removed(entity=st_prev.entity, reason=reason)

            # Кешуємо видалені, щоб ловити touched_late.
            self._removed_cache[key] = (st_prev, reason, reason_sub, int(now_ms))

            events.append(
                self._event_dict(
                    st_prev,
                    event="removed",
                    now_ms=now_ms,
                    ctx={
                        **ctx,
                        "reason": reason,
                        "reason_sub": reason_sub,
                        "lifetime_bars": lifetime_bars,
                    },
                )
            )
            self._prev.pop(key, None)

        # 3) update common + touched
        for key in sorted(cur_keys & prev_keys):
            st_prev = self._prev.get(key)
            st_cur = cur.get(key)
            if st_prev is None or st_cur is None:
                continue
            st_prev.last_seen_step = int(self._step)

            touched_event = self._maybe_touch(
                st_prev,
                bar=bar,
                now_ms=now_ms,
                touch_epsilon=_safe_float(ctx.get("touch_epsilon")) or 0.0,
            )
            if touched_event is not None:
                events.append(
                    {
                        **touched_event,
                        "ctx": {**(touched_event.get("ctx") or {}), **ctx},
                    }
                )

        # 4) late touch (після removed)
        if bar is not None and self._removed_cache:
            late_events = self._check_late_touches(
                bar=bar,
                now_ms=now_ms,
                ctx=ctx,
                touch_epsilon=_safe_float(ctx.get("touch_epsilon")) or 0.0,
            )
            events.extend(late_events)

        # 5) evicted_cap counter tracking
        self._prev_poi_dropped_due_cap = int(ctx.get("poi_dropped_due_cap") or 0)

        # 6) контекст для наступного снапшоту
        try:
            self._prev_bias = (
                str(ctx.get("bias")) if ctx.get("bias") is not None else None
            )
        except Exception:
            self._prev_bias = None
        try:
            self._prev_range_state = (
                str(ctx.get("range_state"))
                if ctx.get("range_state") is not None
                else None
            )
        except Exception:
            self._prev_range_state = None

        return events

    def _check_late_touches(
        self,
        *,
        bar: BarSnapshot,
        now_ms: int,
        ctx: dict[str, Any],
        touch_epsilon: float,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        # Не чистимо кеш одразу: для аудиту корисно бачити touched_late.
        for key, (st, reason, reason_sub, removed_ms) in list(
            self._removed_cache.items()
        ):
            late = False
            touch_type: TouchType | None = None
            if (
                st.entity in ("zone", "magnet")
                and st.price_min is not None
                and st.price_max is not None
            ):
                touch_type = _bar_intersects_zone(
                    bar,
                    price_min=st.price_min,
                    price_max=st.price_max,
                    eps=float(touch_epsilon or 0.0),
                )
                late = touch_type is not None
            elif st.entity == "pool" and st.level is not None:
                if _bar_touches_level(bar, st.level):
                    touch_type = "level"
                    late = True

            if not late or touch_type is None:
                continue

            # Створюємо touched з маркером late.
            ev = self._event_dict(
                st,
                event="touched",
                now_ms=now_ms,
                ctx={
                    **ctx,
                    "touch_type": touch_type,
                    "late": True,
                    "removed_ms": int(removed_ms),
                    "removed_reason": reason,
                    "removed_reason_sub": reason_sub,
                },
            )
            events.append(ev)
            self.metrics.inc_touched(entity=st.entity, late=True)

            # Для "evicted_then_touched" аудиту: залишаємо у кеші, але більше не дублюємо.
            self._removed_cache.pop(key, None)

        return events

    def _classify_removed_reason_sub(
        self,
        *,
        hint_prev_state: _EntityState,
        hint_cur: dict[str, Any],
        ctx: dict[str, Any],
        reason: RemovedReason,
    ) -> str:
        """Більш детальна причина removed (reason_sub) для аудиту.

        Ціль: розшити invalidated_rule хоча б на базові підпричини.
        Це QA-евристика, не продакшн сигнал.
        """

        if reason == "replaced_by_merge":
            return "merged"
        if reason == "expired_ttl":
            return "ttl_expired"
        if reason == "evicted_cap":
            return "cap_evicted"
        if reason == "dropped_distance":
            return "distance_drop"

        # invalidated_rule
        prev_bias = ctx.get("prev_bias")
        cur_bias = ctx.get("bias")
        prev_rs = ctx.get("prev_range_state")
        cur_rs = ctx.get("range_state")
        if (
            prev_bias is not None and cur_bias is not None and prev_bias != cur_bias
        ) or (prev_rs is not None and cur_rs is not None and prev_rs != cur_rs):
            return "context_flip"

        # POOL: детальні підпричини для churn/флікеру.
        if hint_prev_state.entity == "pool":
            life_bars = max(0, int(self._step - hint_prev_state.created_step))
            typ_u = str(hint_prev_state.type or "").upper()

            # Дуже короткоживучий WICK_CLUSTER майже завжди шум.
            if "WICK_CLUSTER" in typ_u and life_bars <= 2:
                return "flicker_short_lived"

            last_price = _safe_float(ctx.get("last_price"))
            atr_last = _safe_float(ctx.get("atr_last"))
            tick = _resolve_tick(hint_cur)
            tol = None
            if tick is not None and tick > 0:
                tol = float(tick) * 2.0
            elif atr_last is not None and atr_last > 0:
                tol = float(atr_last) * 0.002

            if (
                tol is not None
                and last_price is not None
                and hint_prev_state.level is not None
                and abs(float(last_price) - float(hint_prev_state.level)) <= float(tol)
            ):
                return "price_near_level_at_remove"

            prev_parts = _parse_pool_id(hint_prev_state.id)
            liq = hint_cur.get("liquidity")
            pools_cur = liq.get("pools") if isinstance(liq, dict) else None

            lvl_tol = None
            if tick is not None and tick > 0:
                lvl_tol = float(tick) * 3.0
            elif atr_last is not None and atr_last > 0:
                lvl_tol = float(atr_last) * 0.002

            if (
                isinstance(pools_cur, list)
                and hint_prev_state.level is not None
                and lvl_tol is not None
            ):
                found_same_kind = False
                for p in pools_cur:
                    if not isinstance(p, dict):
                        continue

                    liq_type = str(p.get("liq_type") or "")
                    role = str(p.get("role") or "")
                    if prev_parts:
                        if liq_type != prev_parts.get("liq_type"):
                            continue
                        if role != prev_parts.get("role"):
                            continue
                    else:
                        if liq_type != str(hint_prev_state.type or ""):
                            continue
                        if role != str(hint_prev_state.role or ""):
                            continue

                    found_same_kind = True
                    lvl = _safe_float(p.get("level"))
                    if lvl is None:
                        continue
                    if abs(float(lvl) - float(hint_prev_state.level)) > float(lvl_tol):
                        continue

                    first = str(p.get("first_time") or "")
                    last = str(p.get("last_time") or "")
                    if prev_parts:
                        if first == prev_parts.get("first") and last != prev_parts.get(
                            "last"
                        ):
                            return "rollover_last_time"
                        if first != prev_parts.get("first") and last == prev_parts.get(
                            "last"
                        ):
                            return "rollover_first_time"
                        if first != prev_parts.get("first") or last != prev_parts.get(
                            "last"
                        ):
                            return "rebucket_time_window"

                    return "level_shift"

                if found_same_kind:
                    return "vanished_same_type_role"

            return "unknown"

        last_price = _safe_float(ctx.get("last_price"))
        if (
            hint_prev_state.entity in ("zone", "magnet")
            and last_price is not None
            and hint_prev_state.price_min is not None
            and hint_prev_state.price_max is not None
        ):
            # Якщо ціна ще всередині — це найчастіше не "price_invalidated", а
            # скоріше логічний/контекстний дроп. Але для аудиту позначимо окремо.
            if hint_prev_state.price_min <= last_price <= hint_prev_state.price_max:
                return "price_inside_at_remove"

            # Просте правило "price invalidated" для зон: LONG => нижче min; SHORT => вище max.
            dir_u = str(hint_prev_state.direction or "").upper()
            if "LONG" in dir_u and last_price < float(hint_prev_state.price_min):
                return "price_invalidated"
            if "SHORT" in dir_u and last_price > float(hint_prev_state.price_max):
                return "price_invalidated"

        return "unknown"

    def _maybe_touch(
        self,
        st: _EntityState,
        *,
        bar: BarSnapshot | None,
        now_ms: int,
        touch_epsilon: float,
    ) -> dict[str, Any] | None:
        if bar is None:
            return None

        touch_type: TouchType | None = None
        if (
            st.entity in ("zone", "magnet")
            and st.price_min is not None
            and st.price_max is not None
        ):
            touch_type = _bar_intersects_zone(
                bar,
                price_min=st.price_min,
                price_max=st.price_max,
                eps=float(touch_epsilon or 0.0),
            )
        elif st.entity == "pool" and st.level is not None:
            if _bar_touches_level(bar, st.level):
                touch_type = "level"

        if touch_type is None:
            return None

        # touched тільки один раз на життя, щоб не шуміло.
        if st.was_touched:
            return None

        st.was_touched = True
        st.last_touch_ms = int(now_ms)
        self.metrics.inc_touched(entity=st.entity, late=False)

        return self._event_dict(
            st,
            event="touched",
            now_ms=now_ms,
            ctx={"touch_type": touch_type, "late": False},
        )

    def _extract_magnet_pool_ids(
        self, hint: dict[str, Any], *, magnet_id: str
    ) -> list[str]:
        liq = hint.get("liquidity")
        if not isinstance(liq, dict):
            return []
        magnets = liq.get("magnets")
        if not isinstance(magnets, list):
            return []
        for m in magnets:
            if not isinstance(m, dict):
                continue
            if _magnet_id(m) != magnet_id:
                continue
            pools = m.get("pools")
            if not isinstance(pools, list):
                return []
            out: list[str] = []
            for p in pools:
                if not isinstance(p, dict):
                    continue
                out.append(_pool_id(p))
            return out
        return []

    def _extract_entities(
        self,
        *,
        hint: dict[str, Any],
        now_ms: int,
        bar: BarSnapshot | None,
        compute_kind: ComputeKind | None,
        primary_close_ms: int | None,
    ) -> tuple[dict[tuple[JournalEntity, str], _EntityState], dict[str, Any]]:
        # Контекст для журналу: максимум корисного, але без ламання контрактів.
        ctx: dict[str, Any] = {
            "bar_complete": bool(bar.complete) if bar is not None else True,
            "compute_kind": str(compute_kind) if compute_kind is not None else None,
            "primary_close_ms": (
                int(primary_close_ms) if primary_close_ms is not None else None
            ),
            "atr_last": None,
            "range_state": None,
            "bias": None,
            "poi_dropped_due_cap": 0,
            "active_zone_distance_threshold_atr": None,
            "touch_epsilon": 0.0,
            "last_price": float(bar.close) if bar is not None else None,
        }

        try:
            structure = hint.get("structure")
            if isinstance(structure, dict):
                ctx["bias"] = structure.get("bias")
                ctx["range_state"] = structure.get("range_state")
                meta = structure.get("meta")
                if isinstance(meta, dict):
                    ctx["atr_last"] = meta.get("atr_last")
        except Exception:
            pass

        try:
            zones_obj = hint.get("zones")
            if isinstance(zones_obj, dict):
                zmeta = zones_obj.get("meta")
                if isinstance(zmeta, dict):
                    ctx["active_zone_distance_threshold_atr"] = zmeta.get(
                        "active_zone_distance_threshold_atr"
                    )
                    try:
                        ctx["touch_epsilon"] = float(zmeta.get("touch_epsilon") or 0.0)
                    except Exception:
                        ctx["touch_epsilon"] = 0.0
                    poi = zmeta.get("poi")
                    if isinstance(poi, dict):
                        ctx["poi_dropped_due_cap"] = int(
                            poi.get("poi_dropped_due_cap") or 0
                        )
        except Exception:
            pass

        out: dict[tuple[JournalEntity, str], _EntityState] = {}

        zones_obj = hint.get("zones")
        if isinstance(zones_obj, dict):
            zones = zones_obj.get("zones")
            if isinstance(zones, list):
                for z in zones:
                    if not isinstance(z, dict):
                        continue
                    zid = _zone_id(z)
                    if not zid:
                        continue
                    pmin = _safe_float(z.get("price_min"))
                    pmax = _safe_float(z.get("price_max"))
                    out[("zone", zid)] = _EntityState(
                        entity="zone",
                        id=zid,
                        type=str(z.get("zone_type") or "UNKNOWN"),
                        direction=str(z.get("direction") or "UNKNOWN"),
                        role=str(z.get("role") or "UNKNOWN"),
                        price_min=pmin,
                        price_max=pmax,
                        last_seen_step=int(self._step),
                    )

        liq_obj = hint.get("liquidity")
        if isinstance(liq_obj, dict):
            pools = liq_obj.get("pools")
            if isinstance(pools, list):
                for p in pools:
                    if not isinstance(p, dict):
                        continue
                    pid = _pool_id(p)
                    lvl = _safe_float(p.get("level"))
                    p_dir = p.get("direction")
                    if p_dir is None:
                        p_dir = p.get("side")
                    out[("pool", pid)] = _EntityState(
                        entity="pool",
                        id=pid,
                        type=str(p.get("liq_type") or "UNKNOWN"),
                        direction=str(p_dir or "UNKNOWN"),
                        role=str(p.get("role") or "UNKNOWN"),
                        level=lvl,
                        last_seen_step=int(self._step),
                    )

            magnets = liq_obj.get("magnets")
            if isinstance(magnets, list):
                for m in magnets:
                    if not isinstance(m, dict):
                        continue
                    mid = _magnet_id(m)
                    pmin = _safe_float(m.get("price_min"))
                    pmax = _safe_float(m.get("price_max"))
                    m_dir = m.get("direction")
                    if m_dir is None:
                        m_dir = m.get("side")
                    out[("magnet", mid)] = _EntityState(
                        entity="magnet",
                        id=mid,
                        type=str(m.get("liq_type") or "UNKNOWN"),
                        direction=str(m_dir or "UNKNOWN"),
                        role=str(m.get("role") or "UNKNOWN"),
                        price_min=pmin,
                        price_max=pmax,
                        last_seen_step=int(self._step),
                    )

        return out, ctx

    def _classify_removed_reason(
        self,
        *,
        hint_prev_state: _EntityState,
        hint_cur: dict[str, Any],
        ctx: dict[str, Any],
    ) -> RemovedReason:
        # 1) replaced_by_merge: якщо хтось явно посилається на цей id.
        merged_from = self._collect_merged_from_ids(hint_cur)
        if hint_prev_state.id in merged_from:
            return "replaced_by_merge"

        # 2) expired_ttl (для зон): беремо max_age_minutes з zones.meta.*_params.
        if hint_prev_state.entity == "zone":
            max_age_min = self._max_age_minutes_for_zone(
                hint_cur, zone_type=hint_prev_state.type
            )
            if max_age_min is not None:
                # Якщо не маємо age_min — використовуємо "вік" у барах як слабкий сигнал.
                # Переводимо в хвилини з tf.
                tf_min = _tf_minutes(self.tf)
                age_by_bars_min = int(
                    max(0, self._step - hint_prev_state.created_step) * tf_min
                )
                if age_by_bars_min >= int(max_age_min):
                    return "expired_ttl"

        # 3) evicted_cap: якщо в POI meta зріс лічильник dropped_due_cap.
        try:
            poi_dropped = int(ctx.get("poi_dropped_due_cap") or 0)
            if poi_dropped > int(self._prev_poi_dropped_due_cap or 0):
                return "evicted_cap"
        except Exception:
            pass

        # 4) dropped_distance: якщо обʼєкт далеко від поточної ціни в ATR.
        atr_last = _safe_float(ctx.get("atr_last"))
        last_price = _safe_float(ctx.get("last_price"))
        if (
            atr_last is not None
            and atr_last > 0
            and last_price is not None
            and hint_prev_state.price_min is not None
            and hint_prev_state.price_max is not None
        ):
            center = 0.5 * (hint_prev_state.price_min + hint_prev_state.price_max)
            dist_atr = abs(center - last_price) / atr_last
            # Беремо дефолтний поріг 15 ATR (узгоджений з zones.meta.active_zone_distance_threshold_atr).
            try:
                thr = float(ctx.get("active_zone_distance_threshold_atr") or 15.0)
            except Exception:
                thr = 15.0
            if dist_atr > thr:
                return "dropped_distance"

        return "invalidated_rule"

    def _collect_merged_from_ids(self, hint: dict[str, Any]) -> set[str]:
        # Підтримуємо майбутній ключ meta.merged_from у зоні/магніті.
        out: set[str] = set()
        try:
            zones_obj = hint.get("zones")
            if isinstance(zones_obj, dict):
                for z in zones_obj.get("zones") or []:
                    if not isinstance(z, dict):
                        continue
                    meta = z.get("meta")
                    if isinstance(meta, dict):
                        mf = meta.get("merged_from")
                        if isinstance(mf, list):
                            for x in mf:
                                if isinstance(x, str) and x.strip():
                                    out.add(x.strip())
        except Exception:
            pass
        try:
            liq_obj = hint.get("liquidity")
            if isinstance(liq_obj, dict):
                for m in liq_obj.get("magnets") or []:
                    if not isinstance(m, dict):
                        continue
                    meta = m.get("meta")
                    if isinstance(meta, dict):
                        mf = meta.get("merged_from")
                        if isinstance(mf, list):
                            for x in mf:
                                if isinstance(x, str) and x.strip():
                                    out.add(x.strip())
        except Exception:
            pass
        return out

    def _max_age_minutes_for_zone(
        self, hint: dict[str, Any], *, zone_type: str
    ) -> int | None:
        zones_obj = hint.get("zones")
        if not isinstance(zones_obj, dict):
            return None
        meta = zones_obj.get("meta")
        if not isinstance(meta, dict):
            return None

        zt = str(zone_type or "").upper()
        if "FVG" in zt or "FAIR_VALUE_GAP" in zt:
            params = meta.get("fvg_params")
            if isinstance(params, dict):
                v = params.get("fvg_max_age_minutes")
                try:
                    return int(v) if v is not None else None
                except Exception:
                    return None
        if "BREAKER" in zt:
            params = meta.get("breaker_params")
            if isinstance(params, dict):
                v = params.get("breaker_max_ob_age_minutes")
                try:
                    return int(v) if v is not None else None
                except Exception:
                    return None
        if "ORDER_BLOCK" in zt or "ORDERBLOCK" in zt:
            # У meta немає прямого TTL для OB, тому не визначаємо.
            return None

        return None

    def _event_dict(
        self,
        st: _EntityState,
        *,
        event: JournalEvent,
        now_ms: int,
        ctx: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base: dict[str, Any] = {
            "ts": _utc_iso_from_ms(now_ms),
            "symbol": self.symbol,
            "tf": self.tf,
            "entity": st.entity,
            "event": event,
            "id": st.id,
            "type": st.type,
            "direction": st.direction,
            "role": st.role,
        }
        if st.price_min is not None:
            base["price_min"] = float(st.price_min)
        if st.price_max is not None:
            base["price_max"] = float(st.price_max)
        if st.level is not None:
            base["level"] = float(st.level)
        if ctx:
            base["ctx"] = ctx
        return base


@dataclass(slots=True)
class JsonlJournalWriter:
    """Пише події journal у JSONL (один рядок = одна подія)."""

    base_dir: Path

    def append_events(
        self, *, symbol: str, day_utc: str, events: list[dict[str, Any]]
    ) -> Path | None:
        if not events:
            return None
        out_dir = self.base_dir / day_utc
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{symbol.lower()}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        return path


@dataclass(slots=True)
class JsonlFramesWriter:
    """Пише frame marker записи у JSONL.

    Рекомендовано зберігати окремо від подій, щоб не змішувати схеми.
    """

    base_dir: Path

    def append_frame(self, *, symbol: str, day_utc: str, frame: dict[str, Any]) -> Path:
        out_dir = self.base_dir / day_utc
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{symbol.lower()}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(frame, ensure_ascii=False) + "\n")
        return path
