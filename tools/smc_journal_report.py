"""QA утиліта: зведений звіт по SMC Lifecycle Journal (JSONL).

Призначення:
- читати JSONL події з `reports/smc_journal/YYYY-MM-DD/{symbol}.jsonl`;
- будувати прості агрегати для аудиту шуму/флікеру:
  - created_per_hour
  - touch_rate
  - evicted_then_touched_rate
    - evicted_then_touched_rate_by_reason_sub (zone vs pool)
    - short_lifetime_share_by_type (lifetime_bars<=1/<=2)
    - flicker_short_lived_by_type
  - wide_zone_rate(span_atr)
    - span_atr_vs_outcomes(touched/mitigated)
    - preview_vs_close_delta (frame-based: preview vs close по primary_close_ms)
    - lifetime_histogram_by_type (по removed/lifetime_bars)
    - active_count_distribution (frame-based: скільки об'єктів одночасно active)

Це офлайн-інструмент: він не залежить від Redis чи UI.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class _Row:
    dt: datetime
    symbol: str
    tf: str
    entity: str
    event: str
    id: str
    type: str | None
    direction: str | None
    role: str | None
    price_min: float | None
    price_max: float | None
    level: float | None
    ctx: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _Frame:
    dt: datetime
    symbol: str
    tf: str
    kind: str
    primary_close_ms: int
    bar_complete: bool
    active_ids: dict[str, set[str]]
    zone_overlap_n_active: int
    zone_overlap_total_pairs: int
    zone_overlap_pairs_iou_ge: dict[str, int]


@dataclass(frozen=True, slots=True)
class _ZoneBounds:
    price_min: float
    price_max: float
    type: str | None
    direction: str | None
    role: str | None
    created_dt: datetime
    created_primary_close_ms: int | None


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


def _parse_dt(ev: dict[str, Any]) -> datetime:
    ts = ev.get("ts")
    if isinstance(ts, str) and ts and ts != "-":
        try:
            # ts формується як ISO з tz=UTC
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except Exception:
            pass
    # fallback
    return datetime.fromtimestamp(0, tz=UTC)


def _parse_dt_from_frame(fr: dict[str, Any]) -> datetime:
    ts = fr.get("ts")
    if isinstance(ts, str) and ts and ts != "-":
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except Exception:
            pass
    return datetime.fromtimestamp(0, tz=UTC)


def _iter_jsonl_files(base_dir: Path) -> list[Path]:
    if not base_dir.exists():
        return []
    if base_dir.is_file() and base_dir.suffix.lower() == ".jsonl":
        return [base_dir]
    out: list[Path] = []
    for p in sorted(base_dir.rglob("*.jsonl")):
        if p.is_file():
            out.append(p)
    return out


def _load_rows(*, base_dir: Path, symbol_filter: str | None) -> list[_Row]:
    rows: list[_Row] = []
    for path in _iter_jsonl_files(base_dir):
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(ev, dict):
                        continue

                    symbol = str(ev.get("symbol") or "").upper()
                    if symbol_filter and symbol != symbol_filter:
                        continue

                    ctx = ev.get("ctx")
                    if not isinstance(ctx, dict):
                        ctx = {}

                    rows.append(
                        _Row(
                            dt=_parse_dt(ev),
                            symbol=symbol,
                            tf=str(ev.get("tf") or ""),
                            entity=str(ev.get("entity") or ""),
                            event=str(ev.get("event") or ""),
                            id=str(ev.get("id") or ""),
                            type=(
                                str(ev.get("type"))
                                if ev.get("type") is not None
                                else None
                            ),
                            direction=(
                                str(ev.get("direction"))
                                if ev.get("direction") is not None
                                else None
                            ),
                            role=(
                                str(ev.get("role"))
                                if ev.get("role") is not None
                                else None
                            ),
                            price_min=_safe_float(ev.get("price_min")),
                            price_max=_safe_float(ev.get("price_max")),
                            level=_safe_float(ev.get("level")),
                            ctx=ctx,
                        )
                    )
        except OSError:
            continue
    return rows


def _load_frames(*, frames_dir: Path, symbol_filter: str | None) -> list[_Frame]:
    frames: list[_Frame] = []
    for path in _iter_jsonl_files(frames_dir):
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        fr = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(fr, dict):
                        continue

                    symbol = str(fr.get("symbol") or "").upper()
                    if symbol_filter and symbol != symbol_filter:
                        continue

                    kind = str(fr.get("kind") or "")
                    if kind not in {"preview", "close"}:
                        continue

                    tf = str(fr.get("tf") or "")
                    primary_close_raw = fr.get("primary_close_ms")
                    if primary_close_raw is None:
                        continue
                    try:
                        primary_close_ms = int(primary_close_raw)
                    except Exception:
                        continue

                    active_ids_raw = fr.get("active_ids")
                    active_ids: dict[str, set[str]] = {}
                    if isinstance(active_ids_raw, dict):
                        for k, v in active_ids_raw.items():
                            if not isinstance(k, str):
                                continue
                            if isinstance(v, list):
                                active_ids[k] = {str(x) for x in v if str(x)}

                    overlap_n_active = 0
                    overlap_total_pairs = 0
                    overlap_pairs_iou_ge: dict[str, int] = {
                        "0.2": 0,
                        "0.4": 0,
                        "0.6": 0,
                    }
                    overlap_raw = fr.get("zone_overlap_active")
                    if isinstance(overlap_raw, dict):
                        try:
                            overlap_n_active = int(overlap_raw.get("n_active") or 0)
                        except Exception:
                            overlap_n_active = 0
                        try:
                            overlap_total_pairs = int(
                                overlap_raw.get("total_pairs") or 0
                            )
                        except Exception:
                            overlap_total_pairs = 0
                        pairs_raw = overlap_raw.get("pairs_iou_ge")
                        if isinstance(pairs_raw, dict):
                            for k, v in pairs_raw.items():
                                if not isinstance(k, str):
                                    continue
                                try:
                                    overlap_pairs_iou_ge[str(k)] = (
                                        int(v) if v is not None else 0
                                    )
                                except Exception:
                                    overlap_pairs_iou_ge[str(k)] = 0

                    frames.append(
                        _Frame(
                            dt=_parse_dt_from_frame(fr),
                            symbol=symbol,
                            tf=tf,
                            kind=kind,
                            primary_close_ms=primary_close_ms,
                            bar_complete=bool(fr.get("bar_complete", True)),
                            active_ids=active_ids,
                            zone_overlap_n_active=int(overlap_n_active),
                            zone_overlap_total_pairs=int(overlap_total_pairs),
                            zone_overlap_pairs_iou_ge=overlap_pairs_iou_ge,
                        )
                    )
        except OSError:
            continue

    return frames


def _collect_zone_bounds(rows: list[_Row]) -> dict[str, _ZoneBounds]:
    bounds_by_id: dict[str, _ZoneBounds] = {}
    for r in rows:
        if r.entity != "zone" or r.event != "created":
            continue
        if r.price_min is None or r.price_max is None:
            continue
        lo = float(min(r.price_min, r.price_max))
        hi = float(max(r.price_min, r.price_max))
        if not (hi > lo):
            continue

        primary_close_ms: int | None = None
        raw_ms = r.ctx.get("primary_close_ms")
        if raw_ms is not None:
            try:
                primary_close_ms = int(raw_ms)
            except Exception:
                primary_close_ms = None

        bounds_by_id[r.id] = _ZoneBounds(
            price_min=lo,
            price_max=hi,
            type=r.type,
            direction=r.direction,
            role=r.role,
            created_dt=r.dt,
            created_primary_close_ms=primary_close_ms,
        )

    return bounds_by_id


def _interval_iou(a_min: float, a_max: float, b_min: float, b_max: float) -> float:
    inter = min(a_max, b_max) - max(a_min, b_min)
    if inter <= 0.0:
        return 0.0
    union = (a_max - a_min) + (b_max - b_min) - inter
    if union <= 0.0:
        return 0.0
    return float(inter / union)


def _compute_zone_overlap_for_frames(
    *,
    frames: list[_Frame],
    rows: list[_Row],
    thresholds: tuple[str, ...] = ("0.2", "0.4", "0.6"),
    examples_max: int = 200,
) -> tuple[list[_Frame], list[dict[str, Any]]]:
    """Офлайн-обчислення overlap між активними зонами у frames.

    Важливо: це не змінює логіку детектора. Це лише репортинг.
    """

    zone_bounds = _collect_zone_bounds(rows)
    thr_f = [float(x) for x in thresholds]

    out_frames: list[_Frame] = []
    examples: list[dict[str, Any]] = []

    for fr in frames:
        active_zone_ids = sorted(fr.active_ids.get("zone", set()))
        # Якщо frames взагалі не мають активних зон — нічого рахувати.
        if not active_zone_ids:
            out_frames.append(fr)
            continue

        intervals: list[tuple[float, float, str]] = []
        missing_bounds = 0
        for zid in active_zone_ids:
            zb = zone_bounds.get(zid)
            if zb is None:
                missing_bounds += 1
                continue
            intervals.append((zb.price_min, zb.price_max, zid))

        # Якщо не змогли знайти межі — залишаємо як є.
        if len(intervals) < 2:
            if fr.zone_overlap_n_active == 0 and fr.zone_overlap_total_pairs == 0:
                # Мінімально заповнюємо n_active, щоб було видно, що active є, але меж нема.
                out_frames.append(
                    _Frame(
                        dt=fr.dt,
                        symbol=fr.symbol,
                        tf=fr.tf,
                        kind=fr.kind,
                        primary_close_ms=fr.primary_close_ms,
                        bar_complete=fr.bar_complete,
                        active_ids=fr.active_ids,
                        zone_overlap_n_active=int(len(intervals)),
                        zone_overlap_total_pairs=0,
                        zone_overlap_pairs_iou_ge={k: 0 for k in thresholds},
                    )
                )
            else:
                out_frames.append(fr)
            continue

        intervals.sort(key=lambda x: (x[0], x[1], x[2]))
        counts = {k: 0 for k in thresholds}
        max_iou = 0.0
        max_pair: tuple[str, str] | None = None

        # Sweep line: порівнюємо лише ті інтервали, які потенційно перетинаються.
        for i in range(len(intervals)):
            a_min, a_max, a_id = intervals[i]
            j = i + 1
            while j < len(intervals):
                b_min, b_max, b_id = intervals[j]
                if b_min >= a_max:
                    break
                iou = _interval_iou(a_min, a_max, b_min, b_max)
                if iou > 0.0:
                    if iou > max_iou:
                        max_iou = iou
                        max_pair = (a_id, b_id)
                    for thr_s, thr in zip(thresholds, thr_f, strict=True):
                        if iou >= thr:
                            counts[thr_s] = int(counts.get(thr_s, 0)) + 1
                j += 1

        n_active = int(len(intervals))
        total_pairs = int(n_active * (n_active - 1) // 2)

        # Якщо в frames вже є overlap-дані (в майбутньому), не перетираємо їх.
        should_fill = fr.zone_overlap_n_active == 0 and fr.zone_overlap_total_pairs == 0
        if should_fill:
            out_frames.append(
                _Frame(
                    dt=fr.dt,
                    symbol=fr.symbol,
                    tf=fr.tf,
                    kind=fr.kind,
                    primary_close_ms=fr.primary_close_ms,
                    bar_complete=fr.bar_complete,
                    active_ids=fr.active_ids,
                    zone_overlap_n_active=n_active,
                    zone_overlap_total_pairs=total_pairs,
                    zone_overlap_pairs_iou_ge=counts,
                )
            )
        else:
            out_frames.append(fr)

        if len(examples) < int(examples_max):
            ex = {
                "dt_utc": fr.dt.isoformat().replace("+00:00", "Z"),
                "symbol": fr.symbol,
                "tf": fr.tf,
                "kind": fr.kind,
                "primary_close_ms": str(fr.primary_close_ms),
                "n_active": str(n_active),
                "total_pairs": str(total_pairs),
                "max_iou": f"{max_iou:.4f}",
                "max_pair_a": max_pair[0] if max_pair else "-",
                "max_pair_b": max_pair[1] if max_pair else "-",
                "missing_bounds": str(missing_bounds),
            }
            for k in thresholds:
                ex[f"pairs_iou_ge_{k}"] = str(int(counts.get(k, 0)))
            examples.append(ex)

    return out_frames, examples


def _md_table(headers: list[str], data: list[list[str]]) -> str:
    if not data:
        return "(нема даних)"
    out: list[str] = []
    out.append("| " + " | ".join(headers) + " |")
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in data:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def _write_csv(path: Path, headers: list[str], data: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for row in data:
            w.writerow(row)


def _fmt_pct(num: int, den: int) -> str:
    if den <= 0:
        return "-"
    return f"{(100.0 * num / den):.1f}%"


def _fmt_float(x: float | None, *, nd: int = 3) -> str:
    if x is None:
        return "-"
    try:
        return f"{float(x):.{int(nd)}f}"
    except Exception:
        return "-"


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    qn = float(q)
    if qn <= 0.0:
        return float(min(values))
    if qn >= 1.0:
        return float(max(values))
    v = sorted(float(x) for x in values)
    n = len(v)
    idx = int((n - 1) * qn)
    if idx < 0:
        idx = 0
    if idx >= n:
        idx = n - 1
    return float(v[idx])


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return float(sum(values) / float(len(values)))


def _report_created_per_hour(rows: list[_Row]) -> tuple[list[str], list[list[str]]]:
    buckets: dict[tuple[str, str], int] = defaultdict(int)
    for r in rows:
        if r.event != "created":
            continue
        hour = (
            r.dt.replace(minute=0, second=0, microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        buckets[(hour, r.entity)] += 1

    headers = ["hour_utc", "entity", "created"]
    data: list[list[str]] = []
    for (hour, entity), n in sorted(buckets.items()):
        data.append([hour, entity, str(n)])
    return headers, data


def _report_touch_rate(rows: list[_Row]) -> tuple[list[str], list[list[str]]]:
    created: dict[str, int] = defaultdict(int)
    touched: dict[str, int] = defaultdict(int)
    touched_late: dict[str, int] = defaultdict(int)
    removed: dict[str, int] = defaultdict(int)

    for r in rows:
        if r.event == "created":
            created[r.entity] += 1
        elif r.event == "removed":
            removed[r.entity] += 1
        elif r.event == "touched":
            late = bool(r.ctx.get("late"))
            if late:
                touched_late[r.entity] += 1
            else:
                touched[r.entity] += 1

    entities = sorted(set(created) | set(touched) | set(touched_late) | set(removed))
    headers = [
        "entity",
        "created",
        "touched",
        "touch_rate",
        "removed",
        "touched_late",
        "late_touch_rate_vs_removed",
    ]
    data: list[list[str]] = []
    for e in entities:
        c = created.get(e, 0)
        t = touched.get(e, 0)
        rl = removed.get(e, 0)
        tl = touched_late.get(e, 0)
        data.append(
            [
                e,
                str(c),
                str(t),
                _fmt_pct(t, c),
                str(rl),
                str(tl),
                _fmt_pct(tl, rl),
            ]
        )
    return headers, data


def _report_evicted_then_touched(rows: list[_Row]) -> tuple[list[str], list[list[str]]]:
    removed_by_reason: dict[str, int] = defaultdict(int)
    late_touch_by_reason: dict[str, int] = defaultdict(int)

    for r in rows:
        if r.event == "removed":
            reason = str(r.ctx.get("reason") or "-")
            removed_by_reason[reason] += 1
        elif r.event == "touched" and bool(r.ctx.get("late")):
            reason = str(r.ctx.get("removed_reason") or "-")
            late_touch_by_reason[reason] += 1

    reasons = sorted(set(removed_by_reason) | set(late_touch_by_reason))
    headers = ["removed_reason", "removed", "touched_late", "rate"]
    data: list[list[str]] = []
    for reason in reasons:
        rem = removed_by_reason.get(reason, 0)
        lt = late_touch_by_reason.get(reason, 0)
        data.append([reason, str(rem), str(lt), _fmt_pct(lt, rem)])
    return headers, data


def _report_evicted_then_touched_by_reason_sub(
    rows: list[_Row], *, entities: set[str] | None = None
) -> tuple[list[str], list[list[str]]]:
    """Evicted-then-touched (touched_late) по reason_sub.

    Ціль (випадок B): ловити технічні "remove" (rebucket/context_flip/flicker)
    які потім проявляються як touched_late.

    Рахуємо rate = touched_late / removed для ключа:
      (entity, removed_reason, removed_reason_sub)

    За замовчуванням обмежуємося {zone,pool}, бо це те, що видно в UI.
    """

    use_entities = entities if entities is not None else {"zone", "pool"}

    removed: dict[tuple[str, str, str], int] = defaultdict(int)
    late: dict[tuple[str, str, str], int] = defaultdict(int)

    for r in rows:
        if r.entity not in use_entities:
            continue
        if r.event == "removed":
            reason = str(r.ctx.get("reason") or "-")
            reason_sub = str(r.ctx.get("reason_sub") or "-")
            removed[(r.entity, reason, reason_sub)] += 1
        elif r.event == "touched" and bool(r.ctx.get("late")):
            reason = str(r.ctx.get("removed_reason") or "-")
            reason_sub = str(r.ctx.get("removed_reason_sub") or "-")
            late[(r.entity, reason, reason_sub)] += 1

    keys = set(removed) | set(late)

    headers = [
        "entity",
        "removed_reason",
        "removed_reason_sub",
        "removed",
        "touched_late",
        "rate",
    ]
    data: list[list[str]] = []
    for entity, reason, reason_sub in sorted(
        keys,
        key=lambda k: (
            -(late.get(k, 0) / float(removed.get(k, 1) or 1)),
            -removed.get(k, 0),
            k[0],
            k[1],
            k[2],
        ),
    ):
        rem = int(removed.get((entity, reason, reason_sub), 0))
        lt = int(late.get((entity, reason, reason_sub), 0))
        data.append([entity, reason, reason_sub, str(rem), str(lt), _fmt_pct(lt, rem)])

    return headers, data


def _report_wide_zone_rate(rows: list[_Row]) -> tuple[list[str], list[list[str]]]:
    spans: list[float] = []
    for r in rows:
        if r.entity != "zone" or r.event != "created":
            continue
        atr = _safe_float(r.ctx.get("atr_last"))
        if atr is None or atr <= 0:
            continue
        if r.price_min is None or r.price_max is None:
            continue
        span_atr = abs(r.price_max - r.price_min) / atr
        spans.append(float(span_atr))

    if not spans:
        return ["metric", "value"], []

    n = len(spans)
    thr = [1.0, 2.0, 3.0]
    headers = ["metric", "value"]
    data: list[list[str]] = []
    data.append(["zones_with_atr", str(n)])
    for t in thr:
        k = sum(1 for x in spans if x >= t)
        data.append([f"span_atr>= {t:.1f}", f"{k} ({_fmt_pct(k, n)})"])
    data.append(["span_atr_avg", f"{(sum(spans) / n):.3f}"])
    return headers, data


def _pearson_corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    n = len(xs)
    mx = sum(xs) / float(n)
    my = sum(ys) / float(n)
    num = 0.0
    dx2 = 0.0
    dy2 = 0.0
    for x, y in zip(xs, ys, strict=False):
        dx = float(x) - mx
        dy = float(y) - my
        num += dx * dy
        dx2 += dx * dx
        dy2 += dy * dy
    den = (dx2 * dy2) ** 0.5
    if den <= 0:
        return None
    return num / den


def _report_span_atr_vs_outcomes(
    rows: list[_Row],
) -> tuple[list[str], list[list[str]]]:
    """Кореляція span_atr з touched/mitigated для zone.

    Обчислюємо span_atr лише там, де є atr_last у ctx та price_min/price_max.
    Далі для кожного zone_id рахуємо бінінг:
    - created
    - touched_rate (touched/created)
    - mitigated_rate (mitigated/created)

    Додатково даємо Pearson corr(span_atr, outcome) для touched/mitigated.
    """

    spans_by_id: dict[str, float] = {}
    for r in rows:
        if r.entity != "zone" or r.event != "created":
            continue
        atr = _safe_float(r.ctx.get("atr_last"))
        if atr is None or atr <= 0:
            continue
        if r.price_min is None or r.price_max is None:
            continue
        span_atr = abs(r.price_max - r.price_min) / float(atr)
        spans_by_id[str(r.id)] = float(span_atr)

    if not spans_by_id:
        return ["metric", "value"], []

    touched_ids: set[str] = set()
    mitigated_ids: set[str] = set()
    for r in rows:
        if r.entity != "zone":
            continue
        if r.event == "touched":
            touched_ids.add(str(r.id))
        elif r.event == "mitigated":
            mitigated_ids.add(str(r.id))

    # Бінінг по порогах, які ми вже використовуємо для wide_zone_rate.
    edges = [0.0, 0.5, 1.0, 2.0, 3.0, float("inf")]

    def _bin_label(lo: float, hi: float) -> str:
        if hi == float("inf"):
            return f"[{lo:.1f}, +inf)"
        return f"[{lo:.1f}, {hi:.1f})"

    headers = [
        "span_atr_bin",
        "created",
        "touched",
        "touched_rate",
        "mitigated",
        "mitigated_rate",
    ]
    data: list[list[str]] = []

    items = list(spans_by_id.items())
    for lo, hi in zip(edges[:-1], edges[1:], strict=False):
        ids_in: list[str] = [
            zid for zid, s in items if float(s) >= float(lo) and float(s) < float(hi)
        ]
        if not ids_in:
            continue
        created = len(ids_in)
        touched = sum(1 for zid in ids_in if zid in touched_ids)
        mitigated = sum(1 for zid in ids_in if zid in mitigated_ids)
        data.append(
            [
                _bin_label(lo, hi),
                str(created),
                str(touched),
                _fmt_pct(touched, created),
                str(mitigated),
                _fmt_pct(mitigated, created),
            ]
        )

    # Кореляції (point-biserial = Pearson з 0/1 outcome)
    xs = [float(s) for _, s in items]
    ys_touched = [1.0 if zid in touched_ids else 0.0 for zid, _ in items]
    ys_mitigated = [1.0 if zid in mitigated_ids else 0.0 for zid, _ in items]

    corr_t = _pearson_corr(xs, ys_touched)
    corr_m = _pearson_corr(xs, ys_mitigated)

    data.append(
        [
            "corr(span_atr,touched)",
            "-",
            "-",
            f"{corr_t:.4f}" if corr_t is not None else "-",
            "-",
            "-",
        ]
    )
    data.append(
        [
            "corr(span_atr,mitigated)",
            "-",
            "-",
            "-",
            "-",
            f"{corr_m:.4f}" if corr_m is not None else "-",
        ]
    )
    return headers, data


def _report_preview_vs_close_delta(
    frames: list[_Frame],
) -> tuple[list[str], list[list[str]]]:
    """Frame-based preview vs close стабільність по primary_close_ms.

    Для кожного primary_close_ms очікуємо (best-effort):
    - 1 frame kind=preview (bar_complete=False)
    - 1 frame kind=close   (bar_complete=True)

    Рахуємо для entity∈{zone,pool,magnet} та для all:
    - stable = |preview ∩ close|
    - preview_only = |preview - close|
    - close_only = |close - preview|
    - jaccard = |∩| / |∪| (якщо обидва порожні => 1.0)
    """

    # (tf, primary_close_ms) -> {kind: frame}
    grouped: dict[tuple[str, int], dict[str, _Frame]] = defaultdict(dict)
    for fr in frames:
        grouped[(fr.tf, int(fr.primary_close_ms))][fr.kind] = fr

    headers = [
        "primary_close_utc",
        "tf",
        "entity",
        "preview_n",
        "close_n",
        "stable",
        "preview_only",
        "close_only",
        "jaccard",
    ]

    def _fmt_dt_from_ms(ms: int) -> str:
        try:
            return (
                datetime.fromtimestamp(int(ms) / 1000.0, tz=UTC)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except Exception:
            return "-"

    def _stats_for_sets(
        pre: set[str], clo: set[str]
    ) -> tuple[int, int, int, int, int, float]:
        inter = pre & clo
        uni = pre | clo
        stable = len(inter)
        pre_only = len(pre - clo)
        clo_only = len(clo - pre)
        if not uni:
            jacc = 1.0
        else:
            jacc = stable / float(len(uni))
        return len(pre), len(clo), stable, pre_only, clo_only, float(jacc)

    data: list[list[str]] = []
    for (tf, pcm), mp in sorted(grouped.items(), key=lambda x: (x[0][1], x[0][0])):
        fr_pre = mp.get("preview")
        fr_clo = mp.get("close")
        if fr_pre is None or fr_clo is None:
            continue

        entities = [
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
        for ent in entities:
            pre = set(fr_pre.active_ids.get(ent) or set())
            clo = set(fr_clo.active_ids.get(ent) or set())
            pre_n, clo_n, stable, pre_only, clo_only, jacc = _stats_for_sets(pre, clo)
            data.append(
                [
                    _fmt_dt_from_ms(pcm),
                    str(tf),
                    ent,
                    str(pre_n),
                    str(clo_n),
                    str(stable),
                    str(pre_only),
                    str(clo_only),
                    f"{jacc:.3f}",
                ]
            )

        pre_all = set().union(
            *(set(fr_pre.active_ids.get(ent) or set()) for ent in entities)
        )
        clo_all = set().union(
            *(set(fr_clo.active_ids.get(ent) or set()) for ent in entities)
        )
        pre_n, clo_n, stable, pre_only, clo_only, jacc = _stats_for_sets(
            pre_all, clo_all
        )
        data.append(
            [
                _fmt_dt_from_ms(pcm),
                str(tf),
                "all",
                str(pre_n),
                str(clo_n),
                str(stable),
                str(pre_only),
                str(clo_only),
                f"{jacc:.3f}",
            ]
        )

    return headers, data


def _report_preview_vs_close_summary(
    frames: list[_Frame],
) -> tuple[list[str], list[list[str]]]:
    """Зведення preview-vs-close без переліку кожного primary_close_ms.

    Для кожного entity порахуємо:
    - pairs: скільки пар preview/close знайдено
    - jaccard_mean/p50/p90/p99
    - preview_only_mean, close_only_mean
    """

    # (tf, primary_close_ms) -> {kind: frame}
    grouped: dict[tuple[str, int], dict[str, _Frame]] = defaultdict(dict)
    for fr in frames:
        grouped[(fr.tf, int(fr.primary_close_ms))][fr.kind] = fr

    entities = [
        "zone",
        "pool",
        "magnet",
        "structure_event",
        "range_state",
        "active_range",
        "ote",
        "amd_phase",
        "wick_cluster",
        "all",
    ]

    # ent -> stats lists
    jacc_by_ent: dict[str, list[float]] = defaultdict(list)
    pre_only_by_ent: dict[str, list[float]] = defaultdict(list)
    clo_only_by_ent: dict[str, list[float]] = defaultdict(list)

    for (_tf, _pcm), mp in grouped.items():
        fr_pre = mp.get("preview")
        fr_clo = mp.get("close")
        if fr_pre is None or fr_clo is None:
            continue

        sets_pre: dict[str, set[str]] = {}
        sets_clo: dict[str, set[str]] = {}
        for ent in entities:
            if ent == "all":
                ents2 = [e for e in entities if e != "all"]
                sets_pre[ent] = set().union(
                    *(set(fr_pre.active_ids.get(e) or set()) for e in ents2)
                )
                sets_clo[ent] = set().union(
                    *(set(fr_clo.active_ids.get(e) or set()) for e in ents2)
                )
            else:
                sets_pre[ent] = set(fr_pre.active_ids.get(ent) or set())
                sets_clo[ent] = set(fr_clo.active_ids.get(ent) or set())

        for ent in entities:
            pre = sets_pre[ent]
            clo = sets_clo[ent]
            inter = pre & clo
            uni = pre | clo
            stable = len(inter)
            pre_only = len(pre - clo)
            clo_only = len(clo - pre)
            if not uni:
                jacc = 1.0
            else:
                jacc = stable / float(len(uni))
            jacc_by_ent[ent].append(float(jacc))
            pre_only_by_ent[ent].append(float(pre_only))
            clo_only_by_ent[ent].append(float(clo_only))

    headers = [
        "entity",
        "pairs",
        "jaccard_mean",
        "jaccard_p50",
        "jaccard_p90",
        "jaccard_p99",
        "preview_only_mean",
        "close_only_mean",
    ]
    data: list[list[str]] = []
    for ent in entities:
        j = jacc_by_ent.get(ent) or []
        data.append(
            [
                ent,
                str(len(j)),
                _fmt_float(_mean(j), nd=3),
                _fmt_float(_percentile(j, 0.50), nd=3),
                _fmt_float(_percentile(j, 0.90), nd=3),
                _fmt_float(_percentile(j, 0.99), nd=3),
                _fmt_float(_mean(pre_only_by_ent.get(ent) or []), nd=2),
                _fmt_float(_mean(clo_only_by_ent.get(ent) or []), nd=2),
            ]
        )
    return headers, data


def _report_removed_reason_sub(rows: list[_Row]) -> tuple[list[str], list[list[str]]]:
    """Розріз removed по reason / reason_sub.

    Дає базовий зріз для QA: де саме сидить шум (context_flip vs price_invalidated тощо).
    """

    buckets: dict[tuple[str, str, str, str, str, str], int] = defaultdict(int)
    for r in rows:
        if r.event != "removed":
            continue
        reason = str(r.ctx.get("reason") or "-")
        reason_sub = str(r.ctx.get("reason_sub") or "-")
        compute_kind = str(r.ctx.get("compute_kind") or "-")
        bias = str(r.ctx.get("bias") or "-")
        buckets[
            (r.entity, compute_kind, bias, reason, reason_sub, str(r.type or "-"))
        ] += 1

    headers = [
        "entity",
        "compute_kind",
        "bias",
        "reason",
        "reason_sub",
        "type",
        "removed",
    ]
    data: list[list[str]] = []
    for (entity, compute_kind, bias, reason, reason_sub, typ), n in sorted(
        buckets.items(), key=lambda x: (-x[1], x[0])
    ):
        data.append([entity, compute_kind, bias, reason, reason_sub, typ, str(n)])
    return headers, data


def _report_merge_rate(rows: list[_Row]) -> tuple[list[str], list[list[str]]]:
    """merge_rate на основі removed_reason=replaced_by_merge.

    Це практичний proxy для Case E: «дві зони як одна» => одна зникає,
    бо її поглинули/замістили.
    """

    removed_total: dict[tuple[str, str, str], int] = defaultdict(int)
    removed_merged: dict[tuple[str, str, str], int] = defaultdict(int)

    for r in rows:
        if r.event != "removed":
            continue
        ck = str(r.ctx.get("compute_kind") or "-")
        typ = str(r.type or "-")
        key = (str(r.entity), ck, typ)
        removed_total[key] += 1
        reason = str(r.ctx.get("reason") or "-")
        if reason == "replaced_by_merge":
            removed_merged[key] += 1

    headers = [
        "entity",
        "compute_kind",
        "type",
        "removed_total",
        "removed_replaced_by_merge",
        "merge_rate",
    ]

    data: list[list[str]] = []
    for (entity, ck, typ), total in sorted(
        removed_total.items(),
        key=lambda x: (-removed_merged.get(x[0], 0), -x[1], x[0]),
    ):
        merged = int(removed_merged.get((entity, ck, typ), 0))
        data.append(
            [
                entity,
                ck,
                typ,
                str(int(total)),
                str(merged),
                _fmt_pct(merged, int(total)),
            ]
        )

    return headers, data


def _report_zone_overlap_matrix_active(
    frames: list[_Frame],
    *,
    thresholds: tuple[str, ...] = ("0.2", "0.4", "0.6"),
) -> tuple[list[str], list[list[str]]]:
    """Overlap-матриця активних зон по frames (Case E).

    Вхід: frames JSONL містить zone_overlap_active, який рахується у
    smc_core.lifecycle_journal.build_frame_record().
    """

    thr = tuple(str(t) for t in thresholds if str(t))
    if not thr:
        thr = ("0.2", "0.4", "0.6")

    # (tf, kind) -> агрегати
    agg_n: dict[tuple[str, str], int] = defaultdict(int)
    sum_active: dict[tuple[str, str], int] = defaultdict(int)
    sum_pairs: dict[tuple[str, str], int] = defaultdict(int)
    sum_pairs_ge: dict[tuple[str, str, str], int] = defaultdict(int)
    any_ge: dict[tuple[str, str, str], int] = defaultdict(int)

    for fr in frames:
        key = (str(fr.tf), str(fr.kind))
        agg_n[key] += 1
        sum_active[key] += int(fr.zone_overlap_n_active)
        sum_pairs[key] += int(fr.zone_overlap_total_pairs)
        for t in thr:
            v = int(fr.zone_overlap_pairs_iou_ge.get(t, 0) or 0)
            sum_pairs_ge[(key[0], key[1], t)] += v
            if v > 0:
                any_ge[(key[0], key[1], t)] += 1

    headers = [
        "tf",
        "kind",
        "frames",
        "avg_active_zones",
        "avg_pairs_total",
    ]
    for t in thr:
        headers.extend([f"avg_pairs_iou_ge_{t}", f"share_frames_with_pairs_ge_{t}"])

    data: list[list[str]] = []
    for (tf, kind), n in sorted(agg_n.items(), key=lambda x: (x[0][0], x[0][1])):
        n_i = int(n)
        if n_i <= 0:
            continue
        row: list[str] = [
            tf,
            kind,
            str(n_i),
            f"{(sum_active[(tf, kind)] / float(n_i)):.2f}",
            f"{(sum_pairs[(tf, kind)] / float(n_i)):.2f}",
        ]
        for t in thr:
            avg_pairs = sum_pairs_ge[(tf, kind, t)] / float(n_i)
            share_any = 100.0 * any_ge[(tf, kind, t)] / float(n_i)
            row.extend([f"{avg_pairs:.2f}", f"{share_any:.1f}%"])
        data.append(row)

    return headers, data


def _dt_to_ms(dt: datetime) -> int:
    try:
        return int(dt.timestamp() * 1000.0)
    except Exception:
        return 0


def _tf_to_ms(tf: str) -> int | None:
    s = str(tf or "").strip().lower()
    if not s:
        return None
    n_s = ""
    unit = ""
    for ch in s:
        if ch.isdigit():
            if unit:
                return None
            n_s += ch
        else:
            unit += ch
    if not n_s or not unit:
        return None
    try:
        n = int(n_s)
    except Exception:
        return None
    if n <= 0:
        return None

    unit = unit.strip()
    if unit == "m":
        return int(n * 60_000)
    if unit == "h":
        return int(n * 3_600_000)
    if unit == "d":
        return int(n * 86_400_000)
    return None


def _fmt_dt_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _maybe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return None


def _load_ohlcv_bars(
    path: Path,
) -> tuple[list[int], list[float], list[float], list[float]]:
    """Завантажує OHLCV snapshot jsonl (datastore/*_bars_*_snapshot.jsonl).

    Повертає три паралельні масиви:
    - close_time_ms (sorted)
    - low
    - high
    - close
    """

    close_ms: list[int] = []
    lows: list[float] = []
    highs: list[float] = []
    closes: list[float] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            ct = obj.get("close_time")
            lo = obj.get("low")
            hi = obj.get("high")
            cl = obj.get("close")
            if ct is None or lo is None or hi is None or cl is None:
                continue
            try:
                close_ms.append(int(float(ct)))
                lows.append(float(lo))
                highs.append(float(hi))
                closes.append(float(cl))
            except Exception:
                continue

    # Гарантуємо сортування (jsonl може бути обрізаний/зшитий).
    if not close_ms:
        return [], [], [], []
    order = sorted(range(len(close_ms)), key=lambda i: close_ms[i])
    close_ms = [close_ms[i] for i in order]
    lows = [lows[i] for i in order]
    highs = [highs[i] for i in order]
    closes = [closes[i] for i in order]
    return close_ms, lows, highs, closes


def _report_touch_outcome_by_direction(
    rows: list[_Row],
    *,
    close_ms: list[int],
    lows: list[float],
    highs: list[float],
    closes: list[float],
    x_atr: float,
    max_k: int,
) -> tuple[list[str], list[list[str]]]:
    """Випадок H: outcome label для touched (LONG vs SHORT).

    Для кожного touched по zone дивимося forward K=1..N барів і перевіряємо:
    - reversal: рух у «очікуваному» напрямку (по zone.direction) >= X*ATR
    - continuation: рух проти «очікуваного» напряму >= X*ATR

    Проксі-референс для touch: close ціни touch-бару.
    """

    if not close_ms:
        return ["note"], [["Нема OHLCV барів для outcome-аудиту."]]

    x = max(0.0, float(x_atr or 0.0))
    kmax = max(1, int(max_k))

    # key = (compute_kind, type, role, direction, k)
    total: dict[tuple[str, str, str, str, int], int] = defaultdict(int)
    missing_atr: dict[tuple[str, str, str, str, int], int] = defaultdict(int)
    hit_reversal: dict[tuple[str, str, str, str, int], int] = defaultdict(int)
    hit_cont: dict[tuple[str, str, str, str, int], int] = defaultdict(int)
    hit_both: dict[tuple[str, str, str, str, int], int] = defaultdict(int)
    out_of_range: dict[tuple[str, str, str, str, int], int] = defaultdict(int)

    for r in rows:
        if r.entity != "zone" or r.event != "touched":
            continue

        direction = str(r.direction or "-").upper()
        if direction not in {"LONG", "SHORT"}:
            continue
        typ = str(r.type or "-")
        role = str(r.role or "-")
        ck = str(r.ctx.get("compute_kind") or "-")

        atr_last = None
        atr_last_raw = r.ctx.get("atr_last")
        if atr_last_raw is not None:
            try:
                atr_last = float(atr_last_raw)
            except Exception:
                atr_last = None

        touch_ms = _dt_to_ms(r.dt)

        # Знаходимо індекс touch-бару: якщо timestamp не співпадає точно — беремо попередній.
        j = bisect.bisect_left(close_ms, int(touch_ms))
        if j >= len(close_ms):
            j = len(close_ms) - 1
        if j > 0 and close_ms[j] > int(touch_ms):
            j -= 1

        ref = None
        try:
            ref = float(closes[j])
        except Exception:
            ref = None

        for k in range(1, kmax + 1):
            key = (ck, typ, role, direction, k)
            total[key] += 1

            if atr_last is None or atr_last <= 0 or ref is None:
                missing_atr[key] += 1
                continue

            end = min(len(close_ms), j + 1 + k)
            if end <= j + 1:
                out_of_range[key] += 1
                continue

            # forward bars: (j+1 .. end-1)
            max_high = max(highs[j + 1 : end])
            min_low = min(lows[j + 1 : end])

            thr = x * float(atr_last)
            if direction == "LONG":
                favorable = float(max_high) - float(ref)
                adverse = float(ref) - float(min_low)
            else:
                favorable = float(ref) - float(min_low)
                adverse = float(max_high) - float(ref)

            ok_rev = favorable >= thr
            ok_con = adverse >= thr
            if ok_rev:
                hit_reversal[key] += 1
            if ok_con:
                hit_cont[key] += 1
            if ok_rev and ok_con:
                hit_both[key] += 1

    headers = [
        "compute_kind",
        "type",
        "role",
        "direction",
        "k",
        "x_atr",
        "touched_total",
        "touched_with_atr",
        "reversal_hits",
        "reversal_rate",
        "continuation_hits",
        "continuation_rate",
        "both_hits",
        "both_rate",
        "out_of_range",
    ]

    data: list[list[str]] = []
    keys_sorted = sorted(total.keys(), key=lambda t: (t[0], t[1], t[2], t[3], t[4]))
    for key in keys_sorted:
        ck, typ, role, direction, k = key
        n = int(total.get(key, 0))
        miss = int(missing_atr.get(key, 0))
        with_atr = max(0, n - miss)
        rev = int(hit_reversal.get(key, 0))
        con = int(hit_cont.get(key, 0))
        both = int(hit_both.get(key, 0))
        oor = int(out_of_range.get(key, 0))
        data.append(
            [
                ck,
                typ,
                role,
                direction,
                str(k),
                _fmt_float(x, nd=4),
                str(n),
                str(with_atr),
                str(rev),
                _fmt_pct(rev, with_atr),
                str(con),
                _fmt_pct(con, with_atr),
                str(both),
                _fmt_pct(both, with_atr),
                str(oor),
            ]
        )

    return headers, data


def _report_missed_touch_rate(
    rows: list[_Row],
    *,
    close_ms: list[int],
    lows: list[float],
    highs: list[float],
) -> tuple[list[str], list[list[str]]]:
    """Випадок F: missed_touch_rate (FN) через офлайн перевірку по OHLCV.

    Для кожного "життя" зони (created->removed) визначаємо:
    - should_touch_eps: чи був перетин high/low з [min-eps, max+eps]
    - has_journal_touch: чи є touched (late/nonlate) у журналі в цьому вікні

    FN = should_touch_eps=1 і has_journal_touch=0.
    Додатково рахуємо fn_eps_only: коли touch з'являється тільки якщо eps>0.
    """

    if not close_ms:
        return ["note"], [["Нема OHLCV барів для аудиту."]]

    # Витягуємо eps із ctx (SSOT: zones.meta.touch_epsilon -> ctx.touch_epsilon).
    eps = 0.0
    for r in rows:
        v = r.ctx.get("touch_epsilon")
        if v is None:
            continue
        try:
            eps = float(v)
            break
        except Exception:
            continue
    eps = max(0.0, float(eps or 0.0))

    # id -> список touched_ms (для бінарного пошуку)
    touched_ms_by_id: dict[str, list[int]] = defaultdict(list)

    # id -> поточний created (ms) та bounds
    open_created: dict[str, int] = {}
    open_bounds: dict[str, tuple[float, float]] = {}
    open_ck: dict[str, str] = {}

    # compute_kind -> агрегати
    total: dict[str, int] = defaultdict(int)
    with_bars: dict[str, int] = defaultdict(int)
    should_eps: dict[str, int] = defaultdict(int)
    should_eps0: dict[str, int] = defaultdict(int)
    journal_touch: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn_eps_only: dict[str, int] = defaultdict(int)
    out_of_range: dict[str, int] = defaultdict(int)

    # Спочатку зберемо touched.
    for r in rows:
        if r.entity != "zone" or r.event != "touched":
            continue
        touched_ms_by_id[str(r.id)].append(_dt_to_ms(r.dt))
    for _zid, ts in touched_ms_by_id.items():
        ts.sort()

    # Обробляємо події у часі.
    for r in rows:
        if r.entity != "zone":
            continue

        ck = str(r.ctx.get("compute_kind") or "-")

        if r.event == "created":
            pmin = r.price_min
            pmax = r.price_max
            if pmin is None or pmax is None:
                continue
            lo = min(float(pmin), float(pmax))
            hi = max(float(pmin), float(pmax))
            open_created[str(r.id)] = _dt_to_ms(r.dt)
            open_bounds[str(r.id)] = (lo, hi)
            open_ck[str(r.id)] = ck
            continue

        if r.event != "removed":
            continue

        zid = str(r.id)
        created = open_created.pop(zid, None)
        bounds = open_bounds.pop(zid, None)
        ck2 = open_ck.pop(zid, None) or ck
        if created is None or bounds is None:
            continue
        removed = _dt_to_ms(r.dt)
        if removed <= created:
            continue

        total[ck2] += 1

        # Знайдемо bars у (created, removed]
        left = bisect.bisect_right(close_ms, int(created))
        right = bisect.bisect_right(close_ms, int(removed))
        if right <= left:
            out_of_range[ck2] += 1
            continue
        with_bars[ck2] += 1

        lo, hi = bounds
        lo0, hi0 = lo, hi
        loe, hie = (lo - eps), (hi + eps)

        touched0 = False
        touche = False
        for i in range(left, right):
            if lows[i] <= hi0 and highs[i] >= lo0:
                touched0 = True
            if lows[i] <= hie and highs[i] >= loe:
                touche = True
            if touched0 and touche:
                break

        if touched0:
            should_eps0[ck2] += 1
        if touche:
            should_eps[ck2] += 1

        # Journal touched у (created, removed]
        ts = touched_ms_by_id.get(zid) or []
        has_touch = False
        if ts:
            i0 = bisect.bisect_right(ts, int(created))
            if i0 < len(ts) and int(ts[i0]) <= int(removed):
                has_touch = True
        if has_touch:
            journal_touch[ck2] += 1

        if touche and not has_touch:
            fn[ck2] += 1
            if (not touched0) and eps > 0:
                fn_eps_only[ck2] += 1
        if (not touche) and has_touch:
            fp[ck2] += 1

    headers = [
        "compute_kind",
        "zone_instances",
        "instances_with_bars",
        "should_touch_eps",
        "journal_touched",
        "missed_touch_fn",
        "missed_touch_rate_vs_should",
        "fn_eps_only",
        "journal_touch_but_no_ohlcv_touch_fp",
        "instances_out_of_range",
        "touch_epsilon",
    ]

    data: list[list[str]] = []
    keys = sorted(
        set(total) | set(with_bars) | set(should_eps) | set(journal_touch) | set(fn)
    )
    for ck in keys:
        inst = int(total.get(ck, 0))
        wb = int(with_bars.get(ck, 0))
        sh = int(should_eps.get(ck, 0))
        jt = int(journal_touch.get(ck, 0))
        fn_n = int(fn.get(ck, 0))
        fp_n = int(fp.get(ck, 0))
        eps_only = int(fn_eps_only.get(ck, 0))
        oor = int(out_of_range.get(ck, 0))
        data.append(
            [
                ck,
                str(inst),
                str(wb),
                str(sh),
                str(jt),
                str(fn_n),
                _fmt_pct(fn_n, sh),
                str(eps_only),
                str(fp_n),
                str(oor),
                _fmt_float(eps, nd=6),
            ]
        )

    return headers, data


def _collect_case_b_removed_then_late_touch_examples(
    rows: list[_Row], *, max_items: int = 200
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for r in rows:
        if r.event != "touched" or not bool(r.ctx.get("late")):
            continue

        removed_ms = _maybe_int(r.ctx.get("removed_ms"))
        if removed_ms is None:
            continue
        dt_remove = datetime.fromtimestamp(float(removed_ms) / 1000.0, tz=UTC)

        touch_primary_close_ms = _maybe_int(r.ctx.get("primary_close_ms"))
        tf_ms = _tf_to_ms(r.tf)
        bars_to_touch: int | None = None
        if touch_primary_close_ms is not None and tf_ms:
            try:
                bars_to_touch = max(
                    0, int((touch_primary_close_ms - removed_ms) // tf_ms)
                )
            except Exception:
                bars_to_touch = None

        items.append(
            {
                "entity": r.entity,
                "id": r.id,
                "symbol": r.symbol,
                "tf": r.tf,
                "dt_remove_utc": _fmt_dt_utc(dt_remove),
                "dt_touch_utc": _fmt_dt_utc(r.dt),
                "primary_close_ms": (
                    str(touch_primary_close_ms)
                    if touch_primary_close_ms is not None
                    else "-"
                ),
                "bars_to_touch": (
                    str(bars_to_touch) if bars_to_touch is not None else "-"
                ),
                "removed_reason": str(r.ctx.get("removed_reason") or "-"),
                "removed_reason_sub": str(r.ctx.get("removed_reason_sub") or "-"),
                "touch_type": str(r.ctx.get("touch_type") or "-"),
            }
        )

    def _key(x: dict[str, Any]) -> tuple[float, str]:
        b_raw = x.get("bars_to_touch")
        b_s = str(b_raw) if b_raw is not None else "-1"
        try:
            bb = float(b_s)
        except Exception:
            bb = -1.0
        # Більше bars_to_touch — вище; tie-break: dt_touch desc
        return (bb, str(x.get("dt_touch_utc") or ""))

    items = sorted(items, key=_key, reverse=True)
    return items[: int(max_items)]


def _collect_case_c_short_lifetime_examples(
    rows: list[_Row], *, lifetime_le: int = 1, max_items: int = 200
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        if r.event != "removed":
            continue
        lb = r.ctx.get("lifetime_bars")
        try:
            life = int(lb) if lb is not None else None
        except Exception:
            continue
        if life is None or life > int(lifetime_le):
            continue

        pc_ms = _maybe_int(r.ctx.get("primary_close_ms"))
        out.append(
            {
                "entity": r.entity,
                "id": r.id,
                "symbol": r.symbol,
                "tf": r.tf,
                "dt_removed_utc": _fmt_dt_utc(r.dt),
                "primary_close_ms": str(pc_ms) if pc_ms is not None else "-",
                "type": str(r.type or "-"),
                "lifetime_bars": str(life),
                "reason": str(r.ctx.get("reason") or "-"),
                "reason_sub": str(r.ctx.get("reason_sub") or "-"),
                "compute_kind": str(r.ctx.get("compute_kind") or "-"),
            }
        )

    out = sorted(
        out,
        key=lambda x: (
            int(x.get("lifetime_bars") or 999999),
            str(x.get("dt_removed_utc") or ""),
        ),
    )
    return out[: int(max_items)]


def _collect_case_d_widest_zone_examples(
    rows: list[_Row], *, max_items: int = 200
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        if r.entity != "zone" or r.event != "created":
            continue
        if r.price_min is None or r.price_max is None:
            continue
        atr = _safe_float(r.ctx.get("atr_last"))
        if atr is None or atr <= 0.0:
            continue
        span = float(abs(float(r.price_max) - float(r.price_min)))
        span_atr = float(span / float(atr))
        pc_ms = _maybe_int(r.ctx.get("primary_close_ms"))
        out.append(
            {
                "id": r.id,
                "symbol": r.symbol,
                "tf": r.tf,
                "dt_created_utc": _fmt_dt_utc(r.dt),
                "primary_close_ms": str(pc_ms) if pc_ms is not None else "-",
                "type": str(r.type or "-"),
                "direction": str(r.direction or "-"),
                "role": str(r.role or "-"),
                "price_min": _fmt_float(r.price_min, nd=3),
                "price_max": _fmt_float(r.price_max, nd=3),
                "atr_last": _fmt_float(atr, nd=4),
                "span_atr": f"{span_atr:.3f}",
                "compute_kind": str(r.ctx.get("compute_kind") or "-"),
            }
        )

    out = sorted(out, key=lambda x: -float(x.get("span_atr") or 0.0))
    return out[: int(max_items)]


def _collect_case_f_missed_touch_examples(
    rows: list[_Row],
    *,
    close_ms: list[int],
    lows: list[float],
    highs: list[float],
    max_items: int = 200,
) -> list[dict[str, Any]]:
    if not close_ms:
        return []

    # eps з ctx
    eps = 0.0
    for r in rows:
        v = r.ctx.get("touch_epsilon")
        if v is None:
            continue
        try:
            eps = float(v)
            break
        except Exception:
            continue
    eps = max(0.0, float(eps or 0.0))

    touched_ms_by_id: dict[str, list[int]] = defaultdict(list)
    open_created: dict[str, int] = {}
    open_bounds: dict[str, tuple[float, float]] = {}
    open_meta: dict[str, dict[str, Any]] = {}

    for r in rows:
        if r.entity != "zone" or r.event != "touched":
            continue
        touched_ms_by_id[str(r.id)].append(_dt_to_ms(r.dt))
    for _zid, ts in touched_ms_by_id.items():
        ts.sort()

    out: list[dict[str, Any]] = []
    for r in rows:
        if r.entity != "zone":
            continue

        if r.event == "created":
            if r.price_min is None or r.price_max is None:
                continue
            lo = min(float(r.price_min), float(r.price_max))
            hi = max(float(r.price_min), float(r.price_max))
            open_created[str(r.id)] = _dt_to_ms(r.dt)
            open_bounds[str(r.id)] = (lo, hi)
            open_meta[str(r.id)] = {
                "symbol": r.symbol,
                "tf": r.tf,
                "type": str(r.type or "-"),
                "direction": str(r.direction or "-"),
                "role": str(r.role or "-"),
                "compute_kind": str(r.ctx.get("compute_kind") or "-"),
            }
            continue

        if r.event != "removed":
            continue

        zid = str(r.id)
        created = open_created.pop(zid, None)
        bounds = open_bounds.pop(zid, None)
        meta = open_meta.pop(zid, None) or {}
        if created is None or bounds is None:
            continue
        removed = _dt_to_ms(r.dt)
        if removed <= created:
            continue

        # Вікно барів
        left = bisect.bisect_right(close_ms, int(created))
        right = bisect.bisect_right(close_ms, int(removed))
        if right <= left:
            continue

        lo, hi = bounds
        loe, hie = (lo - eps), (hi + eps)

        should_touch = False
        first_touch_ms: int | None = None
        for i in range(left, right):
            if lows[i] <= hie and highs[i] >= loe:
                should_touch = True
                first_touch_ms = int(close_ms[i])
                break
        if not should_touch:
            continue

        # Journal touch у (created, removed]
        ts = touched_ms_by_id.get(zid) or []
        has_touch = False
        if ts:
            i0 = bisect.bisect_right(ts, int(created))
            if i0 < len(ts) and int(ts[i0]) <= int(removed):
                has_touch = True

        if has_touch:
            continue

        out.append(
            {
                "id": zid,
                "symbol": str(meta.get("symbol") or "-"),
                "tf": str(meta.get("tf") or "-"),
                "type": str(meta.get("type") or "-"),
                "direction": str(meta.get("direction") or "-"),
                "role": str(meta.get("role") or "-"),
                "compute_kind": str(meta.get("compute_kind") or "-"),
                "dt_created_utc": _fmt_dt_utc(
                    datetime.fromtimestamp(created / 1000.0, tz=UTC)
                ),
                "dt_removed_utc": _fmt_dt_utc(r.dt),
                "first_touch_close_ms": (
                    str(first_touch_ms) if first_touch_ms is not None else "-"
                ),
                "price_min": _fmt_float(lo, nd=3),
                "price_max": _fmt_float(hi, nd=3),
                "touch_epsilon": _fmt_float(eps, nd=6),
            }
        )

    out = sorted(
        out,
        key=lambda x: (
            str(x.get("tf") or ""),
            str(x.get("first_touch_close_ms") or ""),
        ),
    )
    return out[: int(max_items)]


def _write_audit_todo_md(path: Path, items: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "case",
        "dt_utc",
        "symbol",
        "tf",
        "primary_close_ms",
        "entity",
        "id",
        "note",
    ]
    rows_md = [[str(it.get(h, "-")) for h in headers] for it in items]
    out = ["# audit_todo\n", _md_table(headers, rows_md), ""]
    path.write_text("\n".join(out), encoding="utf-8")


def _report_quality_matrix(rows: list[_Row]) -> tuple[list[str], list[list[str]]]:
    """Quality matrix для lifecycle.

    Групування максимально інформативне (але все ще читається):
    entity × type × direction × role × compute_kind × bias.

    Метрики:
    - created/removed/touched (late/nonlate)/merged/mitigated
    - touch_rate, removed_rate, late_touch_rate_vs_removed
    - lifetime_bars p50/p90/p99 (по removed)
    """

    @dataclass(slots=True)
    class _Agg:
        created: int = 0
        removed: int = 0
        touched: int = 0
        touched_late: int = 0
        merged: int = 0
        mitigated: int = 0
        lifetimes: list[float] = None  # type: ignore[assignment]

        def __post_init__(self) -> None:
            if self.lifetimes is None:
                self.lifetimes = []

    buckets: dict[tuple[str, str, str, str, str, str], _Agg] = {}

    def _key(r: _Row) -> tuple[str, str, str, str, str, str]:
        compute_kind = str(r.ctx.get("compute_kind") or "-")
        bias = str(r.ctx.get("bias") or "-")
        return (
            str(r.entity or "-"),
            str(r.type or "-"),
            str(r.direction or "-"),
            str(r.role or "-"),
            compute_kind,
            bias,
        )

    for r in rows:
        k = _key(r)
        agg = buckets.get(k)
        if agg is None:
            agg = _Agg()
            buckets[k] = agg
        if r.event == "created":
            agg.created += 1
        elif r.event == "removed":
            agg.removed += 1
            lb = r.ctx.get("lifetime_bars")
            try:
                if lb is not None:
                    agg.lifetimes.append(float(lb))
            except Exception:
                pass
        elif r.event == "touched":
            if bool(r.ctx.get("late")):
                agg.touched_late += 1
            else:
                agg.touched += 1
        elif r.event == "merged":
            agg.merged += 1
        elif r.event == "mitigated":
            agg.mitigated += 1

    headers = [
        "entity",
        "type",
        "direction",
        "role",
        "compute_kind",
        "bias",
        "created",
        "removed",
        "removed_rate",
        "touched",
        "touch_rate",
        "touched_late",
        "late_touch_rate_vs_removed",
        "merged",
        "mitigated",
        "lifetime_p50_bars",
        "lifetime_p90_bars",
        "lifetime_p99_bars",
    ]

    data: list[list[str]] = []
    for (entity, typ, direction, role, compute_kind, bias), agg in sorted(
        buckets.items(), key=lambda x: (-x[1].removed, -x[1].created, x[0])
    ):
        p50 = _percentile(agg.lifetimes, 0.50)
        p90 = _percentile(agg.lifetimes, 0.90)
        p99 = _percentile(agg.lifetimes, 0.99)
        data.append(
            [
                entity,
                typ,
                direction,
                role,
                compute_kind,
                bias,
                str(agg.created),
                str(agg.removed),
                _fmt_pct(agg.removed, agg.created),
                str(agg.touched),
                _fmt_pct(agg.touched, agg.created),
                str(agg.touched_late),
                _fmt_pct(agg.touched_late, agg.removed),
                str(agg.merged),
                str(agg.mitigated),
                _fmt_float(p50, nd=1),
                _fmt_float(p90, nd=1),
                _fmt_float(p99, nd=1),
            ]
        )

    return headers, data


def _report_pool_wickcluster_reason_sub_top(
    rows: list[_Row], *, top_k: int = 15
) -> tuple[list[str], list[list[str]]]:
    """Топ reason_sub для pool/WICK_CLUSTER (preview vs close окремо).

    Ціль: швидко побачити, що саме домінує у churn:
    - flicker_short_lived vs rebucket_time_window vs context_flip тощо.
    """

    top_k_n = max(1, int(top_k))

    # Деномінатори: скільки removed всього в pool/WICK_CLUSTER для кожного compute_kind.
    denom: dict[str, int] = defaultdict(int)
    buckets: dict[tuple[str, str], int] = defaultdict(
        int
    )  # (compute_kind, reason_sub) -> n

    for r in rows:
        if r.event != "removed":
            continue
        if str(r.entity) != "pool":
            continue
        if str(r.type or "") != "WICK_CLUSTER":
            continue

        compute_kind = str(r.ctx.get("compute_kind") or "-")
        reason_sub = str(r.ctx.get("reason_sub") or "-")
        denom[compute_kind] += 1
        buckets[(compute_kind, reason_sub)] += 1

    headers = [
        "compute_kind",
        "reason_sub",
        "removed",
        "share_of_pool_wickcluster_removed",
    ]

    data: list[list[str]] = []
    for compute_kind in sorted(denom.keys()):
        d = int(denom.get(compute_kind) or 0)
        items = [
            (reason_sub, n)
            for (ck, reason_sub), n in buckets.items()
            if ck == compute_kind
        ]
        items.sort(key=lambda x: (-x[1], x[0]))
        for reason_sub, n in items[:top_k_n]:
            data.append([compute_kind, reason_sub, str(n), _fmt_pct(int(n), d)])

    return headers, data


def _report_short_lifetime_share_by_type(
    rows: list[_Row], *, thresholds: tuple[int, ...] = (1, 2)
) -> tuple[list[str], list[list[str]]]:
    """Частка короткого lifetime для removed подій по type.

    Випадок C: "зона сформувалась і відразу знята".

    Рахуємо по ключу (entity, compute_kind, type):
      - removed_total
      - removed_with_lifetime
      - share(lifetime_bars<=t) для t∈{1,2}
    """

    thr = tuple(sorted({int(t) for t in thresholds if int(t) >= 0}))
    if not thr:
        thr = (1, 2)

    removed_total: dict[tuple[str, str, str], int] = defaultdict(int)
    removed_with_life: dict[tuple[str, str, str], int] = defaultdict(int)
    le_counts: dict[tuple[str, str, str, int], int] = defaultdict(int)

    for r in rows:
        if r.event != "removed":
            continue
        compute_kind = str(r.ctx.get("compute_kind") or "-")
        typ = str(r.type or "-")
        key = (str(r.entity), compute_kind, typ)
        removed_total[key] += 1

        lb_any = r.ctx.get("lifetime_bars")
        if lb_any is None:
            continue
        try:
            lb = int(lb_any)
        except Exception:
            continue
        removed_with_life[key] += 1
        for t in thr:
            if lb <= t:
                le_counts[(key[0], key[1], key[2], int(t))] += 1

    headers = [
        "entity",
        "compute_kind",
        "type",
        "removed_total",
        "removed_with_lifetime",
    ]
    for t in thr:
        headers.extend([f"lifetime_le_{t}", f"share_le_{t}"])

    data: list[list[str]] = []
    for entity, compute_kind, typ in sorted(
        removed_total.keys(),
        key=lambda k: (
            -removed_total.get(k, 0),
            k[0],
            k[1],
            k[2],
        ),
    ):
        total = int(removed_total.get((entity, compute_kind, typ), 0))
        with_life = int(removed_with_life.get((entity, compute_kind, typ), 0))
        row: list[str] = [entity, compute_kind, typ, str(total), str(with_life)]
        for t in thr:
            le = int(le_counts.get((entity, compute_kind, typ, int(t)), 0))
            row.extend([str(le), _fmt_pct(le, with_life)])
        data.append(row)

    return headers, data


def _report_lifetime_histogram_by_type(
    rows: list[_Row],
    *,
    thresholds: tuple[int, ...] = (1, 2),
) -> tuple[list[str], list[list[str]]]:
    """Гістограма lifetime_bars для removed подій по type.

    Прохід 1: базова валідація шуму.
    Особливо цікавить частка lifetime<=1/<=2 бари, але також корисно мати грубі
    бін-и, щоб бачити, де зосереджений "churn".

    Ключ: (entity, compute_kind, type).
    Джерело: removed події з ctx.lifetime_bars.
    """

    thr = tuple(sorted({int(t) for t in thresholds if int(t) >= 0}))
    if not thr:
        thr = (1, 2)

    # Бін-и (включно):
    # 0, 1, 2, 3-5, 6-10, 11-20, 21-50, 51+
    bin_defs: list[tuple[str, int | None, int | None]] = [
        ("0", 0, 0),
        ("1", 1, 1),
        ("2", 2, 2),
        ("3_5", 3, 5),
        ("6_10", 6, 10),
        ("11_20", 11, 20),
        ("21_50", 21, 50),
        ("51_plus", 51, None),
    ]

    def _bin_name(lb: int) -> str:
        for name, lo, hi in bin_defs:
            if lo is None:
                continue
            if hi is None:
                if lb >= lo:
                    return name
            else:
                if lo <= lb <= hi:
                    return name
        return "51_plus"

    removed_with_life: dict[tuple[str, str, str], int] = defaultdict(int)
    le_counts: dict[tuple[str, str, str, int], int] = defaultdict(int)
    bins: dict[tuple[str, str, str, str], int] = defaultdict(int)
    lifetimes: dict[tuple[str, str, str], list[float]] = defaultdict(list)

    for r in rows:
        if r.event != "removed":
            continue
        lb_any = r.ctx.get("lifetime_bars")
        if lb_any is None:
            continue
        try:
            lb = int(lb_any)
        except Exception:
            continue
        if lb < 0:
            continue

        compute_kind = str(r.ctx.get("compute_kind") or "-")
        typ = str(r.type or "-")
        key = (str(r.entity), compute_kind, typ)

        removed_with_life[key] += 1
        lifetimes[key].append(float(lb))

        for t in thr:
            if lb <= t:
                le_counts[(key[0], key[1], key[2], int(t))] += 1

        bins[(key[0], key[1], key[2], _bin_name(lb))] += 1

    headers = [
        "entity",
        "compute_kind",
        "type",
        "removed_with_lifetime",
    ]
    for t in thr:
        headers.extend([f"lifetime_le_{t}", f"share_le_{t}"])
    headers.extend(
        [
            "lifetime_p50_bars",
            "lifetime_p90_bars",
            "lifetime_p99_bars",
        ]
    )
    for name, _, _ in bin_defs:
        headers.extend([f"bin_{name}", f"share_{name}"])

    data: list[list[str]] = []
    for entity, compute_kind, typ in sorted(
        removed_with_life.keys(),
        key=lambda k: (-removed_with_life.get(k, 0), k[0], k[1], k[2]),
    ):
        den = int(removed_with_life.get((entity, compute_kind, typ), 0))
        p50 = _percentile(lifetimes.get((entity, compute_kind, typ), []), 0.50)
        p90 = _percentile(lifetimes.get((entity, compute_kind, typ), []), 0.90)
        p99 = _percentile(lifetimes.get((entity, compute_kind, typ), []), 0.99)

        row: list[str] = [entity, compute_kind, typ, str(den)]
        for t in thr:
            le = int(le_counts.get((entity, compute_kind, typ, int(t)), 0))
            row.extend([str(le), _fmt_pct(le, den)])
        row.extend(
            [
                _fmt_float(p50, nd=1),
                _fmt_float(p90, nd=1),
                _fmt_float(p99, nd=1),
            ]
        )

        for name, _, _ in bin_defs:
            n = int(bins.get((entity, compute_kind, typ, name), 0))
            row.extend([str(n), _fmt_pct(n, den)])

        data.append(row)

    return headers, data


def _report_active_count_distribution(
    frames: list[_Frame],
) -> tuple[list[str], list[list[str]]]:
    """Розподіл кількості active об'єктів у кадрі (одночасно).

    Прохід 1: базова валідація шуму.

    Рахуємо по ключу (kind, entity):
    - n_frames
    - mean/p50/p90/p99/max для active_count
    """

    # key=(kind, entity)
    series: dict[tuple[str, str], list[float]] = defaultdict(list)

    for fr in frames:
        kind = str(fr.kind or "-")
        for entity, ids in (fr.active_ids or {}).items():
            try:
                series[(kind, str(entity))].append(float(len(ids)))
            except Exception:
                continue

    headers = [
        "kind",
        "entity",
        "n_frames",
        "active_mean",
        "active_p50",
        "active_p90",
        "active_p99",
        "active_max",
    ]

    data: list[list[str]] = []
    for (kind, entity), vals in sorted(
        series.items(),
        key=lambda x: (
            -max(x[1]) if x[1] else 0.0,
            -len(x[1]),
            x[0][0],
            x[0][1],
        ),
    ):
        if not vals:
            continue
        p50 = _percentile(vals, 0.50)
        p90 = _percentile(vals, 0.90)
        p99 = _percentile(vals, 0.99)
        mean = _mean(vals)
        mx = float(max(vals))
        data.append(
            [
                kind,
                entity,
                str(len(vals)),
                _fmt_float(mean, nd=2),
                _fmt_float(p50, nd=1),
                _fmt_float(p90, nd=1),
                _fmt_float(p99, nd=1),
                _fmt_float(mx, nd=1),
            ]
        )

    return headers, data


def _report_flicker_short_lived_by_type(
    rows: list[_Row], *, reason_sub: str = "flicker_short_lived"
) -> tuple[list[str], list[list[str]]]:
    """removed_reason_sub=flicker_short_lived по типах.

    Випадок C: зрозуміти, які типи найбільше страждають від flicker.
    """

    rs = str(reason_sub or "flicker_short_lived")
    denom: dict[tuple[str, str, str], int] = defaultdict(int)  # (entity, ck, type)
    num: dict[tuple[str, str, str], int] = defaultdict(int)

    for r in rows:
        if r.event != "removed":
            continue
        compute_kind = str(r.ctx.get("compute_kind") or "-")
        typ = str(r.type or "-")
        key = (str(r.entity), compute_kind, typ)
        denom[key] += 1
        if str(r.ctx.get("reason_sub") or "-") == rs:
            num[key] += 1

    headers = [
        "entity",
        "compute_kind",
        "type",
        "removed_total",
        "removed_flicker_short_lived",
        "share_of_removed_for_type",
    ]
    data: list[list[str]] = []
    for (entity, compute_kind, typ), total in sorted(
        denom.items(),
        key=lambda x: (
            -(num.get(x[0], 0) / float(x[1] or 1)),
            -num.get(x[0], 0),
            -x[1],
            x[0],
        ),
    ):
        n = int(num.get((entity, compute_kind, typ), 0))
        data.append(
            [
                entity,
                compute_kind,
                typ,
                str(int(total)),
                str(n),
                _fmt_pct(n, int(total)),
            ]
        )

    return headers, data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dir",
        required=True,
        help=(
            "Папка base (наприклад reports/smc_journal) або конкретний *.jsonl файл."
        ),
    )
    ap.add_argument(
        "--frames-dir",
        default="",
        help=(
            "(Опційно) Папка з frames JSONL. Якщо не задано — використовуємо <dir>/frames. "
            "Приклад: reports/smc_journal/frames"
        ),
    )
    ap.add_argument(
        "--ohlcv-path",
        default="",
        help=(
            "(Опційно) Шлях до OHLCV snapshot *.jsonl (datastore/*_bars_*_snapshot.jsonl). "
            "Якщо задано — увімкнуться офлайн аудити missed_touch_rate (Case F) і touch_outcomes (Case H)."
        ),
    )
    ap.add_argument(
        "--outcome-x-atr",
        default="1.0",
        help=(
            "(Опційно) Поріг для outcome-аудиту (Case H): reversal/continuation >= X*ATR. "
            "Приклад: 0.8 або 1.2"
        ),
    )
    ap.add_argument(
        "--outcome-max-k",
        default="12",
        help=(
            "(Опційно) Максимальний горизонт K (у барах) для outcome-аудиту (Case H). "
            "Рахуємо K=1..N"
        ),
    )
    ap.add_argument("--symbol", default="", help="Фільтр по symbol (наприклад XAUUSD)")
    ap.add_argument(
        "--run-dir",
        default="",
        help=(
            "(Опційно) Папка run-а (наприклад reports/smc_journal_p0_run4). "
            "Якщо задано — звіт буде записано в report_<SYMBOL>.md у цю папку, "
            "а CSV (якщо не задано --csv-dir) теж будуть збережені туди."
        ),
    )
    ap.add_argument(
        "--csv-dir",
        default="",
        help="(Опційно) Папка, куди зберегти CSV-таблиці.",
    )
    args = ap.parse_args()

    base_dir = Path(str(args.dir).strip())
    symbol_filter = str(args.symbol or "").strip().upper() or None
    run_dir = (
        Path(str(getattr(args, "run_dir", "") or "").strip())
        if str(getattr(args, "run_dir", "") or "").strip()
        else None
    )
    if run_dir is not None:
        run_dir.mkdir(parents=True, exist_ok=True)

    csv_dir = (
        Path(str(args.csv_dir).strip()) if str(args.csv_dir or "").strip() else None
    )
    if csv_dir is None and run_dir is not None:
        csv_dir = run_dir

    frames_dir_raw = str(args.frames_dir or "").strip()
    if frames_dir_raw:
        frames_dir = Path(frames_dir_raw)
    else:
        if base_dir.is_dir() and base_dir.name.lower() == "frames":
            frames_dir = base_dir
        else:
            frames_dir = base_dir / "frames"

    rows = _load_rows(base_dir=base_dir, symbol_filter=symbol_filter)
    if not rows:
        print("Немає подій для звіту (перевір шлях/фільтри).")
        return 2

    rows.sort(key=lambda r: (r.dt, r.entity, r.event, r.id))

    parts: list[str] = []
    audit_items: list[dict[str, str]] = []
    parts.append("# SMC Journal Report\n")
    parts.append(f"Подій: {len(rows)}")
    if symbol_filter:
        parts.append(f"Symbol: {symbol_filter}")

    # created_per_hour
    h, d = _report_created_per_hour(rows)
    parts.append("\n## created_per_hour\n")
    parts.append(_md_table(h, d))
    if csv_dir is not None:
        _write_csv(csv_dir / "created_per_hour.csv", h, d)

    # touch_rate
    h, d = _report_touch_rate(rows)
    parts.append("\n## touch_rate\n")
    parts.append(_md_table(h, d))
    if csv_dir is not None:
        _write_csv(csv_dir / "touch_rate.csv", h, d)

    # evicted_then_touched_rate
    h, d = _report_evicted_then_touched(rows)
    parts.append("\n## evicted_then_touched_rate\n")
    parts.append(_md_table(h, d))
    if csv_dir is not None:
        _write_csv(csv_dir / "evicted_then_touched_rate.csv", h, d)

    # evicted_then_touched_rate_by_reason_sub (case B)
    h, d = _report_evicted_then_touched_by_reason_sub(rows)
    parts.append("\n## evicted_then_touched_rate_by_reason_sub\n")
    if d:
        # Markdown: показуємо топ-200 рядків за rate/removed, повний дамп у CSV.
        parts.append(_md_table(h, d[:200]))
        if len(d) > 200:
            parts.append(f"\n(показано 200/{len(d)} рядків; повний розріз у CSV)\n")
    else:
        parts.append("(нема даних)\n")
    if csv_dir is not None:
        _write_csv(csv_dir / "evicted_then_touched_rate_by_reason_sub.csv", h, d)

    # Case B: top-N прикладів removed -> late_touch
    case_b = _collect_case_b_removed_then_late_touch_examples(rows, max_items=200)
    parts.append("\n## case_B_removed_then_late_touch_examples\n")
    if case_b:
        headers = [
            "dt_touch_utc",
            "symbol",
            "tf",
            "entity",
            "id",
            "primary_close_ms",
            "dt_remove_utc",
            "bars_to_touch",
            "removed_reason_sub",
            "removed_reason",
            "touch_type",
        ]
        parts.append(
            _md_table(
                headers, [[str(x.get(hh, "-")) for hh in headers] for x in case_b[:50]]
            )
        )
        if csv_dir is not None:
            _write_csv(
                csv_dir / "case_b_removed_then_late_touch_examples.csv",
                headers,
                [[str(x.get(hh, "-")) for hh in headers] for x in case_b],
            )
        for x in case_b[:30]:
            audit_items.append(
                {
                    "case": "B",
                    "dt_utc": str(x.get("dt_touch_utc") or "-"),
                    "symbol": str(x.get("symbol") or "-"),
                    "tf": str(x.get("tf") or "-"),
                    "primary_close_ms": str(x.get("primary_close_ms") or "-"),
                    "entity": str(x.get("entity") or "-"),
                    "id": str(x.get("id") or "-"),
                    "note": (
                        f"late_touch; bars_to_touch={x.get('bars_to_touch','-')}; "
                        f"removed_reason_sub={x.get('removed_reason_sub','-')}"
                    ),
                }
            )
    else:
        parts.append("(нема late_touch із removed_ms у ctx)\n")

    # wide_zone_rate(span_atr)
    h, d = _report_wide_zone_rate(rows)
    parts.append("\n## wide_zone_rate(span_atr)\n")
    parts.append(_md_table(h, d))
    if csv_dir is not None:
        _write_csv(csv_dir / "wide_zone_rate.csv", h, d)

    # Case D: top-N найширших зон (span_atr)
    case_d = _collect_case_d_widest_zone_examples(rows, max_items=200)
    parts.append("\n## case_D_widest_zone_examples\n")
    if case_d:
        headers = [
            "dt_created_utc",
            "symbol",
            "tf",
            "id",
            "primary_close_ms",
            "type",
            "direction",
            "role",
            "price_min",
            "price_max",
            "atr_last",
            "span_atr",
            "compute_kind",
        ]
        parts.append(
            _md_table(
                headers, [[str(x.get(hh, "-")) for hh in headers] for x in case_d[:50]]
            )
        )
        if csv_dir is not None:
            _write_csv(
                csv_dir / "case_d_widest_zone_examples.csv",
                headers,
                [[str(x.get(hh, "-")) for hh in headers] for x in case_d],
            )
        for x in case_d[:30]:
            audit_items.append(
                {
                    "case": "D",
                    "dt_utc": str(x.get("dt_created_utc") or "-"),
                    "symbol": str(x.get("symbol") or "-"),
                    "tf": str(x.get("tf") or "-"),
                    "primary_close_ms": str(x.get("primary_close_ms") or "-"),
                    "entity": "zone",
                    "id": str(x.get("id") or "-"),
                    "note": f"widest_zone; span_atr={x.get('span_atr','-')}",
                }
            )
    else:
        parts.append("(нема zone.created з price_min/price_max та atr_last)\n")

    # span_atr_vs_outcomes (touched/mitigated)
    h, d = _report_span_atr_vs_outcomes(rows)
    parts.append("\n## span_atr_vs_outcomes(touched/mitigated)\n")
    parts.append(_md_table(h, d))
    if csv_dir is not None:
        _write_csv(csv_dir / "span_atr_vs_outcomes.csv", h, d)

    # preview_vs_close_delta (frame-based)
    frames = _load_frames(frames_dir=frames_dir, symbol_filter=symbol_filter)

    # case E: overlap між активними зонами (офлайн з journal+frames)
    # (не змінює логіку детектора; лише зацементовує метрику для аудиту)
    frames, zone_overlap_examples = _compute_zone_overlap_for_frames(
        frames=frames,
        rows=rows,
        thresholds=("0.2", "0.4", "0.6"),
    )

    # active_count_distribution (frame-based)
    h, d = _report_active_count_distribution(frames)
    parts.append("\n## active_count_distribution\n")
    if d:
        parts.append(_md_table(h, d))
    else:
        parts.append(
            "(нема frames або active_ids; переконайся, що replay пише frames у <journal_dir>/frames)"
        )
    if csv_dir is not None:
        _write_csv(csv_dir / "active_count_distribution.csv", h, d)

    # zone_overlap_matrix_active (case E, frame-based)
    h, d = _report_zone_overlap_matrix_active(frames)
    parts.append("\n## zone_overlap_matrix_active\n")
    if d:
        parts.append(_md_table(h, d))
    else:
        parts.append(
            "(нема даних у frames; переконайся, що replay пише frames у <journal_dir>/frames)"
        )
    if csv_dir is not None:
        _write_csv(csv_dir / "zone_overlap_matrix_active.csv", h, d)

    # zone_overlap_examples (top frames)
    parts.append("\n## zone_overlap_examples\n")
    if zone_overlap_examples:
        # Показуємо найінформативніші фрейми: max_iou desc, потім pairs_iou_ge_0.4 desc.
        zone_overlap_examples_sorted = sorted(
            zone_overlap_examples,
            key=lambda x: (
                -float(x.get("max_iou") or 0.0),
                -int(x.get("pairs_iou_ge_0.4") or 0),
                -int(x.get("total_pairs") or 0),
                str(x.get("dt_utc") or ""),
            ),
        )
        headers = [
            "dt_utc",
            "symbol",
            "tf",
            "kind",
            "primary_close_ms",
            "n_active",
            "total_pairs",
            "pairs_iou_ge_0.4",
            "max_iou",
            "max_pair_a",
            "max_pair_b",
            "missing_bounds",
        ]
        data = [
            [str(x.get(hh, "-")) for hh in headers]
            for x in zone_overlap_examples_sorted[:50]
        ]
        parts.append(_md_table(headers, data))
        if csv_dir is not None:
            _write_csv(
                csv_dir / "zone_overlap_examples.csv",
                headers,
                [
                    [str(x.get(hh, "-")) for hh in headers]
                    for x in zone_overlap_examples_sorted
                ],
            )

        for x in zone_overlap_examples_sorted[:30]:
            audit_items.append(
                {
                    "case": "E",
                    "dt_utc": str(x.get("dt_utc") or "-"),
                    "symbol": str(x.get("symbol") or "-"),
                    "tf": str(x.get("tf") or "-"),
                    "primary_close_ms": str(x.get("primary_close_ms") or "-"),
                    "entity": "zone_pair",
                    "id": f"{x.get('max_pair_a','-')} | {x.get('max_pair_b','-')}",
                    "note": f"overlap; max_iou={x.get('max_iou','-')}; pairs_iou_ge_0.4={x.get('pairs_iou_ge_0.4','-')}",
                }
            )
    else:
        parts.append("(нема frames або не вдалося зіставити межі зон з journal)")

    # preview_vs_close_summary
    h, d = _report_preview_vs_close_summary(frames)
    parts.append("\n## preview_vs_close_summary\n")
    if d:
        parts.append(_md_table(h, d))
    else:
        parts.append(
            "(нема даних у frames; переконайся, що replay запущено з --journal-dir ... --with-preview)"
        )
    if csv_dir is not None:
        _write_csv(csv_dir / "preview_vs_close_summary.csv", h, d)

    h, d = _report_preview_vs_close_delta(frames)
    parts.append("\n## preview_vs_close_delta\n")
    if d:
        parts.append(_md_table(h, d))
    else:
        parts.append(
            "(нема пар preview/close у frames; переконайся, що replay запущено з --journal-dir ... --with-preview і що frames пишуться у <journal_dir>/frames)"
        )
    if csv_dir is not None:
        _write_csv(csv_dir / "preview_vs_close_delta.csv", h, d)

    # removed_reason_sub
    h, d = _report_removed_reason_sub(rows)
    parts.append("\n## removed_reason_sub\n")
    if d:
        # Для Markdown не роздуваємо безмежно: показуємо топ-200, повний дамп у CSV.
        parts.append(_md_table(h, d[:200]))
        if len(d) > 200:
            parts.append(f"\n(показано 200/{len(d)} рядків; повний розріз у CSV)\n")
    else:
        parts.append("(нема removed або відсутні reason_sub у ctx)\n")
    if csv_dir is not None:
        _write_csv(csv_dir / "removed_reason_sub.csv", h, d)

    # merge_rate (case E)
    h, d = _report_merge_rate(rows)
    parts.append("\n## merge_rate\n")
    parts.append(_md_table(h, d))
    if csv_dir is not None:
        _write_csv(csv_dir / "merge_rate.csv", h, d)

    # missed_touch_rate (case F, офлайн, потребує OHLCV)
    ohlcv_path_raw = str(getattr(args, "ohlcv_path", "") or "").strip()
    if ohlcv_path_raw:
        try:
            close_ms, lows, highs, closes = _load_ohlcv_bars(Path(ohlcv_path_raw))
            h, d = _report_missed_touch_rate(
                rows, close_ms=close_ms, lows=lows, highs=highs
            )
            parts.append("\n## missed_touch_rate(offline)\n")
            parts.append(_md_table(h, d))
            if csv_dir is not None:
                _write_csv(csv_dir / "missed_touch_rate_offline.csv", h, d)

            # Case F: top-N конкретних FN missed_touch (offline)
            case_f = _collect_case_f_missed_touch_examples(
                rows,
                close_ms=close_ms,
                lows=lows,
                highs=highs,
                max_items=200,
            )
            parts.append("\n## case_F_missed_touch_examples(offline)\n")
            if case_f:
                headers = [
                    "symbol",
                    "tf",
                    "id",
                    "type",
                    "direction",
                    "role",
                    "dt_created_utc",
                    "dt_removed_utc",
                    "first_touch_close_ms",
                    "price_min",
                    "price_max",
                    "touch_epsilon",
                    "compute_kind",
                ]
                parts.append(
                    _md_table(
                        headers,
                        [[str(x.get(hh, "-")) for hh in headers] for x in case_f[:50]],
                    )
                )
                if csv_dir is not None:
                    _write_csv(
                        csv_dir / "case_f_missed_touch_examples_offline.csv",
                        headers,
                        [[str(x.get(hh, "-")) for hh in headers] for x in case_f],
                    )
                for x in case_f[:30]:
                    ft = str(x.get("first_touch_close_ms") or "-")
                    dt_utc = "-"
                    try:
                        if ft != "-":
                            dt_utc = _fmt_dt_utc(
                                datetime.fromtimestamp(int(ft) / 1000.0, tz=UTC)
                            )
                    except Exception:
                        dt_utc = "-"
                    audit_items.append(
                        {
                            "case": "F",
                            "dt_utc": dt_utc,
                            "symbol": str(x.get("symbol") or "-"),
                            "tf": str(x.get("tf") or "-"),
                            "primary_close_ms": ft,
                            "entity": "zone",
                            "id": str(x.get("id") or "-"),
                            "note": "missed_touch_offline (should_touch але в journal touch нема)",
                        }
                    )
            else:
                parts.append(
                    "(нема FN прикладів за поточними правилами offline-аудиту)\n"
                )

            # Case H: outcomes для touched (LONG vs SHORT)
            try:
                x_atr = float(getattr(args, "outcome_x_atr", 1.0))
            except Exception:
                x_atr = 1.0
            try:
                max_k = int(getattr(args, "outcome_max_k", 12))
            except Exception:
                max_k = 12

            h, d = _report_touch_outcome_by_direction(
                rows,
                close_ms=close_ms,
                lows=lows,
                highs=highs,
                closes=closes,
                x_atr=x_atr,
                max_k=max_k,
            )
            parts.append("\n## touch_outcomes_after_touch(offline)\n")
            parts.append(_md_table(h, d))
            if csv_dir is not None:
                _write_csv(csv_dir / "touch_outcomes_after_touch_offline.csv", h, d)
        except Exception:
            parts.append("\n## missed_touch_rate(offline)\n")
            parts.append("(не вдалося завантажити OHLCV; перевір --ohlcv-path)\n")

    # quality_matrix
    h, d = _report_quality_matrix(rows)
    parts.append("\n## quality_matrix\n")
    if d:
        parts.append(_md_table(h, d[:200]))
        if len(d) > 200:
            parts.append(f"\n(показано 200/{len(d)} рядків; повний розріз у CSV)\n")
    else:
        parts.append("(нема даних)\n")
    if csv_dir is not None:
        _write_csv(csv_dir / "quality_matrix.csv", h, d)

    # lifetime_histogram_by_type (removed lifetime)
    h, d = _report_lifetime_histogram_by_type(rows, thresholds=(1, 2))
    parts.append("\n## lifetime_histogram_by_type\n")
    if d:
        parts.append(_md_table(h, d[:200]))
        if len(d) > 200:
            parts.append(f"\n(показано 200/{len(d)} рядків; повний розріз у CSV)\n")
    else:
        parts.append("(нема removed з lifetime_bars у ctx)\n")
    if csv_dir is not None:
        _write_csv(csv_dir / "lifetime_histogram_by_type.csv", h, d)

    # pool_wickcluster_reason_sub_top
    h, d = _report_pool_wickcluster_reason_sub_top(rows, top_k=15)
    parts.append("\n## pool_wickcluster_reason_sub_top\n")
    if d:
        parts.append(_md_table(h, d))
    else:
        parts.append("(нема removed для pool/WICK_CLUSTER або відсутні reason_sub)\n")
    if csv_dir is not None:
        _write_csv(csv_dir / "pool_wickcluster_reason_sub_top.csv", h, d)

    # short_lifetime_share_by_type (case C)
    h, d = _report_short_lifetime_share_by_type(rows, thresholds=(1, 2))
    parts.append("\n## short_lifetime_share_by_type\n")
    if d:
        parts.append(_md_table(h, d[:200]))
        if len(d) > 200:
            parts.append(f"\n(показано 200/{len(d)} рядків; повний розріз у CSV)\n")
    else:
        parts.append("(нема даних)\n")
    if csv_dir is not None:
        _write_csv(csv_dir / "short_lifetime_share_by_type.csv", h, d)

    # Case C: top-N конкретних removed з lifetime_bars<=1
    case_c = _collect_case_c_short_lifetime_examples(rows, lifetime_le=1, max_items=200)
    parts.append("\n## case_C_short_lifetime_examples(lifetime_bars<=1)\n")
    if case_c:
        headers = [
            "dt_removed_utc",
            "symbol",
            "tf",
            "entity",
            "id",
            "primary_close_ms",
            "type",
            "lifetime_bars",
            "reason_sub",
            "reason",
            "compute_kind",
        ]
        parts.append(
            _md_table(
                headers, [[str(x.get(hh, "-")) for hh in headers] for x in case_c[:50]]
            )
        )
        if csv_dir is not None:
            _write_csv(
                csv_dir / "case_c_short_lifetime_examples.csv",
                headers,
                [[str(x.get(hh, "-")) for hh in headers] for x in case_c],
            )
        for x in case_c[:30]:
            audit_items.append(
                {
                    "case": "C",
                    "dt_utc": str(x.get("dt_removed_utc") or "-"),
                    "symbol": str(x.get("symbol") or "-"),
                    "tf": str(x.get("tf") or "-"),
                    "primary_close_ms": str(x.get("primary_close_ms") or "-"),
                    "entity": str(x.get("entity") or "-"),
                    "id": str(x.get("id") or "-"),
                    "note": f"short_lifetime<=1; reason_sub={x.get('reason_sub','-')}",
                }
            )
    else:
        parts.append("(нема removed з lifetime_bars<=1)\n")

    # flicker_short_lived_by_type (case C)
    h, d = _report_flicker_short_lived_by_type(rows)
    parts.append("\n## flicker_short_lived_by_type\n")
    if d:
        parts.append(_md_table(h, d[:200]))
        if len(d) > 200:
            parts.append(f"\n(показано 200/{len(d)} рядків; повний розріз у CSV)\n")
    else:
        parts.append("(нема даних)\n")
    if csv_dir is not None:
        _write_csv(csv_dir / "flicker_short_lived_by_type.csv", h, d)

    out = "\n".join(parts) + "\n"
    sys.stdout.write(out)
    if run_dir is not None:
        report_name = f"report_{symbol_filter or 'ALL'}.md"
        (run_dir / report_name).write_text(out, encoding="utf-8")

    # audit_todo.md: конкретні приклади для replay (symbol/tf/primary_close_ms + id)
    todo_dir = run_dir or csv_dir
    if todo_dir is not None and audit_items:
        try:
            audit_items_sorted = sorted(
                audit_items,
                key=lambda x: (
                    str(x.get("case") or ""),
                    str(x.get("symbol") or ""),
                    str(x.get("tf") or ""),
                    str(x.get("dt_utc") or ""),
                ),
            )
            _write_audit_todo_md(todo_dir / "audit_todo.md", audit_items_sorted)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
