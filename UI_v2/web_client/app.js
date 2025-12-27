// Базове HTTP/WS налаштування.
// Публічний режим: працюємо в same-origin (через reverse-proxy), без жорстких 127.0.0.1:8080/8081.
function buildHttpBaseUrl() {
    try {
        // Якщо відкрили фронтенд як file://, origin буде "null" — підставляємо локальний бекенд.
        if (window.location.protocol === "file:") {
            return "http://127.0.0.1:8080";
        }

        // Дозволяємо явний override для dev/діагностики.
        const params = new URLSearchParams(window.location.search || "");
        const override = (params.get("http_base") || "").trim();
        if (override) {
            return override;
        }
        return window.location.origin;
    } catch (_e) {
        // Fallback для нестандартних середовищ; у браузері сюди не маємо потрапляти.
        return "http://127.0.0.1:8080";
    }
}

function buildWsBaseUrl() {
    try {
        if (window.location.protocol === "file:") {
            return "ws://127.0.0.1:8081";
        }

        // Дозволяємо явний override для dev/діагностики.
        const params = new URLSearchParams(window.location.search || "");
        const override = (params.get("ws_base") || "").trim();
        if (override) {
            return override;
        }

        const proto = window.location.protocol === "https:" ? "wss:" : "ws:";

        // Локальний dev-стек: HTTP статика на :8080, WS стрім на :8081.
        const hostname = window.location.hostname;
        const port = String(window.location.port || "").trim();
        if ((isLocalHostname(hostname) || isPrivateLanIp(hostname)) && (port === "" || port === "8080")) {
            return `${proto}//${hostname}:8081`;
        }

        // Публічний режим: same-origin (reverse-proxy на один домен/порт).
        return `${proto}//${window.location.host}`;
    } catch (_e) {
        return "ws://127.0.0.1:8081";
    }
}

function isLocalHostname(hostname) {
    return hostname === "localhost" || hostname === "127.0.0.1";
}

function isPrivateLanIp(hostname) {
    // Дозволяємо локальний доступ з телефону/іншого ПК у LAN.
    // RFC1918: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
    const h = String(hostname || "").trim();
    if (!h) return false;
    const parts = h.split(".");
    if (parts.length !== 4) return false;
    const nums = parts.map((p) => Number(p));
    if (nums.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) return false;

    if (nums[0] === 10) return true;
    if (nums[0] === 192 && nums[1] === 168) return true;
    if (nums[0] === 172 && nums[1] >= 16 && nums[1] <= 31) return true;
    return false;
}

// Прод-домени: тут за замовчуванням вмикаємо FXCM live по same-origin.
const isProdDomain = ["aione-smc.com", "www.aione-smc.com"].includes(
    String(window.location.hostname || "").trim().toLowerCase(),
);

function parseOptionalBool(raw) {
    if (raw === null || raw === undefined) {
        return null;
    }
    const s = String(raw).trim().toLowerCase();
    if (s === "") {
        return null;
    }
    if (s === "1" || s === "true" || s === "yes" || s === "on") {
        return true;
    }
    if (s === "0" || s === "false" || s === "no" || s === "off") {
        return false;
    }
    return null;
}

function isFxcmWsEnabled() {
    // FXCM live у проді працює через same-origin шлях /fxcm/... (nginx/Cloudflare).
    // Прямий порт :8082 використовуємо лише локально (dev).
    try {
        const params = new URLSearchParams(window.location.search || "");

        // Ручний override: fxcm_ws=0 вимикає live навіть у проді.
        const fxcmWsRaw = params.get("fxcm_ws");
        const fxcmWs = parseOptionalBool(fxcmWsRaw);
        if (fxcmWs !== null) {
            return fxcmWs;
        }

        // Прод-дефолт: якщо параметр не заданий — вважаємо fxcm_ws увімкненим.
        if (isProdDomain && fxcmWsRaw === null) {
            return true;
        }

        // Якщо явно задано same-origin флаг — теж вважаємо, що live потрібен.
        const sameOriginRaw = params.get("fxcm_ws_same_origin");
        const sameOrigin = parseOptionalBool(sameOriginRaw);
        if (sameOrigin === true) {
            return true;
        }

        const host = window.location.hostname;
        return isLocalHostname(host) || isPrivateLanIp(host);
    } catch (_e) {
        return false;
    }
}

function isTfHealthEnabled() {
    // Stage0/1 діагностика TF-правди — це контрактний сигнал, а не "дебаг-приблуда".
    // Дефолт: показуємо завжди. Override: ?tf_health=0|1
    try {
        const params = new URLSearchParams(window.location.search || "");
        const raw = params.get("tf_health");
        const parsed = parseOptionalBool(raw);
        if (parsed !== null) {
            return parsed;
        }
        return true;
    } catch (_e) {
        return true;
    }
}

function isFxcmWsSameOrigin() {
    // Якщо FXCM WS міст прокситься у same-origin (nginx/Cloudflare tunnel),
    // підключаємось до wss://<домен>/fxcm/... замість прямого :8082.
    try {
        const params = new URLSearchParams(window.location.search || "");
        const raw = params.get("fxcm_ws_same_origin");
        const parsed = parseOptionalBool(raw);
        if (parsed !== null) {
            return parsed;
        }

        // Прод-дефолт: якщо параметр не заданий — вважаємо same-origin увімкненим.
        if (isProdDomain && raw === null) {
            return true;
        }
        return false;
    } catch (_e) {
        return false;
    }
}

function isFxcmApplyCompleteEnabled() {
    // Керує тим, чи треба одразу "фіналізувати" live-свічку при complete=true:
    // - true: при приході complete=true для поточного open_time прибираємо live overlay (і tick overlay),
    //   щоб на графіку лишилася тільки фінальна свічка.
    // - false: live overlay зникне природно при переході на наступний open_time (менше "підстрибує").
    try {
        const params = new URLSearchParams(window.location.search || "");
        const raw = (params.get("fxcm_apply_complete") || "").trim().toLowerCase();
        if (!raw) {
            return true;
        }
        return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
    } catch (_e) {
        return true;
    }
}

function isFxcmTickCountVolumeEnabled() {
    // Керує тим, чи дозволено підміняти/доповнювати volume через tick_count.
    // Дефолт: увімкнено (для FX це ближче до TradingView-поведінки: tick volume).
    // Override: ?fxcm_tickcount_volume=0|1
    try {
        const params = new URLSearchParams(window.location.search || "");
        const raw = params.get("fxcm_tickcount_volume");
        const parsed = parseOptionalBool(raw);
        if (parsed !== null) {
            return parsed;
        }
        return true;
    } catch (_e) {
        return true;
    }
}

function buildFxcmWsBaseUrl() {
    // Явний override для dev/public-режимів, коли FXCM WS піднятий на іншому домені/тунелі.
    // Приклад: ?fxcm_ws=1&fxcm_ws_base=wss://<your-tunnel>.trycloudflare.com
    try {
        const params = new URLSearchParams(window.location.search || "");
        const override = (params.get("fxcm_ws_base") || "").trim();
        if (override) {
            return override;
        }
    } catch (_e) {
        // ignore
    }

    if (isFxcmWsSameOrigin()) {
        // Прод: працюємо строго через same-origin reverse-proxy (/fxcm/*).
        // Використовуємо базовий WS для viewer_state (той самий origin).
        return WS_BASE_URL;
    }

    // Dev: прямий доступ до FXCM WS моста на :8082.
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.hostname}:8082`;
}

function buildFxcmStatusWsBaseUrl() {
    // Для “A по даних” (session high/low) нам потрібен лише fxcm:status.
    // На відміну від OHLCV/ticks WS, status у public хочемо підключати за замовчуванням через same-origin.
    try {
        const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
        if (window.location.protocol === "file:") {
            return "ws://127.0.0.1:8082";
        }

        // Явний override для dev/public-режимів, коли FXCM WS піднятий на іншому домені/тунелі.
        // (status теж ведемо через нього, щоб не роз'їжджались джерела даних).
        const params = new URLSearchParams(window.location.search || "");
        const override = (params.get("fxcm_ws_base") || "").trim();
        if (override) {
            return override;
        }

        // Якщо явно ввімкнули same-origin проксі для FXCM — використовуємо його.
        if (isFxcmWsSameOrigin()) {
            return `${proto}//${window.location.host}`;
        }

        // Локальний dev-стек: UI_v2 HTTP на :8080, FXCM WS міст на :8082.
        const hostname = window.location.hostname;
        const port = String(window.location.port || "").trim();
        if ((isLocalHostname(hostname) || isPrivateLanIp(hostname)) && (port === "" || port === "8080")) {
            return `${proto}//${hostname}:8082`;
        }

        // Public режим: очікуємо reverse-proxy location /fxcm/ на той самий origin.
        return `${proto}//${window.location.host}`;
    } catch (_e) {
        return null;
    }
}

const HTTP_BASE_URL = buildHttpBaseUrl();
const WS_BASE_URL = buildWsBaseUrl();
const FXCM_WS_ENABLED = isFxcmWsEnabled();
const FXCM_OHLCV_WS_BASE_URL = FXCM_WS_ENABLED ? buildFxcmWsBaseUrl() : null;
const FXCM_TICKS_WS_BASE_URL = FXCM_OHLCV_WS_BASE_URL;
const FXCM_STATUS_WS_BASE_URL = buildFxcmStatusWsBaseUrl();
const FXCM_APPLY_COMPLETE_ENABLED = isFxcmApplyCompleteEnabled();
const FXCM_TICKCOUNT_VOLUME_ENABLED = isFxcmTickCountVolumeEnabled();
const TF_HEALTH_ENABLED = isTfHealthEnabled();
const DEFAULT_SYMBOL = "xauusd";
const OHLCV_DEFAULT_TF = "1m";
const OHLCV_DEFAULT_LIMIT = 500;
const AVAILABLE_TIMEFRAMES = ["1m", "5m", "1h", "4h"];
const DEFAULT_ZONE_LIMIT_MODE = "near2";
const CHART_HEIGHT_NORMAL = 700;
const CHART_HEIGHT_LARGE = 900;
const CHART_HEIGHT_DEFAULT = CHART_HEIGHT_NORMAL;
const STORAGE_KEYS = {
    symbol: "smc_viewer_selected_symbol",
    timeframe: "smc_viewer_selected_tf",
    chartHeight: "smc_viewer_chart_height",
    summaryCollapsed: "smc_viewer_summary_collapsed",
    stage6Collapsed: "smc_viewer_stage6_collapsed",
    layersVisibility: "smc_viewer_layers_visibility",
    zoneLimitMode: "smc_viewer_zone_limit_mode",
};

let lastOhlcvResponse = null;
let cachedStorage = null;
let storageUnavailable = false;

function formatNumber(value, digits = 2) {
    if (value === null || value === undefined) return "-";
    const num = Number(value);
    if (!Number.isFinite(num)) return "-";
    return num.toFixed(digits);
}

function formatPoolStrength(pool) {
    const strength = pool.strength ?? pool.strength_score ?? null;
    const touches = pool.touch_count ?? pool.touches ?? null;

    if (strength !== null && touches !== null) {
        return `S=${formatNumber(strength, 2)} / T=${formatNumber(touches, 0)}`;
    }
    if (strength !== null) {
        return `S=${formatNumber(strength, 2)}`;
    }
    if (touches !== null) {
        return `T=${formatNumber(touches, 0)}`;
    }
    return "-";
}

function mapEventsFromViewerState(state) {
    const events = state?.structure?.events || [];
    return events.map((evt) => ({
        time: safeUnixSeconds(evt.ts || evt.time || evt.timestamp),
        type: evt.type || evt.event_type,
        direction: evt.direction || evt.dir,
        price: evt.price ?? evt.level ?? evt.value,
    }));
}

function mapExecutionEventsFromViewerState(state) {
    const raw = state?.execution?.execution_events || [];
    return (Array.isArray(raw) ? raw : []).map((evt) => ({
        time: safeUnixSeconds(evt.time || evt.ts || evt.timestamp),
        type: evt.event_type || evt.type,
        direction: evt.direction || evt.dir,
        price: evt.price,
        level: evt.level,
        ref: evt.ref,
        poi_zone_id: evt.poi_zone_id,
        meta: evt.meta,
    }));
}

function mapPoolsFromViewerState(state) {
    const pools = state?.liquidity?.pools || [];
    const mappedPools = pools.map((pool) => ({
        price: pool.price,
        role: pool.role,
        type: pool.type,
        strength: pool.strength ?? pool.strength_score ?? null,
        touches: pool.touch_count ?? pool.touches ?? null,
    }));

    // HTF targets (якщо є): використовуємо для Delivery/Target у тултіпі.
    // Важливо: не малюємо горизонтальну лінію по всьому графіку, лише бейдж на шкалі.
    const targets = state?.liquidity?.targets || [];
    const mappedTargets = (Array.isArray(targets) ? targets : []).map((t) => ({
        price: t.price,
        role: "TARGET",
        type: t.type,
        tf: t.tf,
        strength: t.strength ?? null,
        touches: null,
        _isTarget: true,
        _axisLabel: true,
        _lineVisible: false,
    }));

    return [...mappedPools, ...mappedTargets];
}

function mapLevelsSelectedV1FromViewerState(state) {
    const toFiniteOrNull = (value) => {
        if (value === null || value === undefined) return null;
        if (typeof value === "number") {
            return Number.isFinite(value) ? value : null;
        }
        if (typeof value === "string" && !value.trim()) {
            return null;
        }
        const num = Number(value);
        return Number.isFinite(num) ? num : null;
    };

    const raw = state?.levels_selected_v1 || [];
    return (Array.isArray(raw) ? raw : [])
        .map((lvl) => {
            const kind = String(lvl?.kind || "").toLowerCase();
            const out = {
                ...lvl,
                kind,
                owner_tf: String(lvl?.owner_tf || ""),
                label: String(lvl?.label || ""),
                price: toFiniteOrNull(lvl?.price),
                top: toFiniteOrNull(lvl?.top),
                bot: toFiniteOrNull(lvl?.bot),
                rank: toFiniteOrNull(lvl?.rank),
            };
            return out;
        })
        .filter((lvl) => {
            if (lvl.kind === "band") {
                return lvl.top !== null && lvl.bot !== null;
            }
            return lvl.price !== null;
        });
}

function pickSelectedLevelsRenderTf(viewTf) {
    const tf = normalizeTimeframe(viewTf);
    // Дизайн Levels-V1: 1m selected у нас вимкнений, тому на 1m рендеримо selected від 5m.
    if (tf === "1m") {
        return "5m";
    }
    return tf;
}

function mapRangesFromViewerState(state) {
    const ranges = state?.structure?.ranges || [];
    return ranges.map((range) => ({
        min: range.price_min ?? range.min,
        max: range.price_max ?? range.max,
        start_time: safeUnixSeconds(range.start_ts || range.from || range.time_start),
        end_time: safeUnixSeconds(range.end_ts || range.to || range.time_end),
    }));
}

function mapOteZonesFromViewerState(state) {
    const zones = state?.structure?.ote_zones || [];
    return zones.map((zone) => ({
        min: zone.ote_min ?? zone.price_min,
        max: zone.ote_max ?? zone.price_max,
        direction: (zone.direction || "").toUpperCase(),
        role: zone.role,
        label: `${zone.direction || ""} OTE`.trim(),
    }));
}

function mapZonesFromViewerState(state) {
    const raw = state?.zones?.raw || {};

    const atrLast = Number(
        state?.structure?.meta?.atr_last ??
        state?.structure?.meta?.atr_median ??
        state?.structure?.atr_last ??
        null,
    );

    // UX-правило: дефолт — тільки "active" (малий список для трейдингу).
    // Архів/повний список зон показуємо лише в debug-режимі.
    let debugZones = false;
    try {
        const params = new URLSearchParams(window.location.search || "");
        const parsed = parseOptionalBool(params.get("debug_zones"));
        debugZones = parsed === true;
    } catch (_e) {
        debugZones = false;
    }

    const zones = debugZones ? raw.zones || [] : raw.active_zones || raw.poi_zones || [];

    const mapped = (Array.isArray(zones) ? zones : []).map((zone) => {
        const meta = zone?.meta && typeof zone.meta === "object" ? zone.meta : {};
        const kind = String(zone?.type ?? zone?.zone_type ?? meta?.poi_type ?? zone?.role ?? "ZONE");
        const dir = String(zone?.direction ?? meta?.direction ?? "").toUpperCase();
        const role = zone?.role ?? meta?.role ?? null;

        const timeframe = zone?.timeframe ?? zone?.tf ?? meta?.timeframe ?? meta?.tf ?? null;

        const whyRaw =
            zone?.why ??
            meta?.why ??
            meta?.reasons ??
            meta?.explain ??
            meta?.explanation ??
            null;
        const why = Array.isArray(whyRaw)
            ? whyRaw.map((v) => String(v)).filter((v) => v)
            : typeof whyRaw === "string"
                ? [whyRaw]
                : [];

        const originRaw = zone?.origin_time ?? zone?.origin_ts ?? zone?.ts ?? zone?.timestamp ?? null;
        const originSec = safeUnixSeconds(originRaw);

        const invalidatedRaw =
            zone?.invalidated_time ??
            zone?.invalidated_ts ??
            zone?.end_time ??
            zone?.end_ts ??
            meta?.invalidated_time ??
            meta?.invalidated_ts ??
            null;
        const invalidatedSec = safeUnixSeconds(invalidatedRaw);

        const rawScore = zone?.score ?? meta?.score ?? null;
        const scoreNum = Number(rawScore);
        const score = Number.isFinite(scoreNum) && scoreNum > 0 ? scoreNum : null;

        const rawFilled = zone?.filled_pct ?? meta?.filled_pct ?? null;
        const filledNum = Number(rawFilled);
        let filledPct = null;
        if (Number.isFinite(filledNum)) {
            // Канон у бекенді: 0..1. UI показує 0..100.
            filledPct = filledNum <= 1.0 ? filledNum * 100.0 : filledNum;
        }

        const rawState = zone?.state ?? meta?.state ?? null;
        const distanceAtrRaw = zone?.distance_atr ?? meta?.distance_atr ?? null;
        const distanceAtr = Number(distanceAtrRaw);

        let stateTag = rawState ? String(rawState).toUpperCase() : "";
        if (!stateTag) {
            if (invalidatedSec !== undefined && invalidatedSec !== null) {
                stateTag = "INVALIDATED";
            } else if (Number.isFinite(filledPct) && filledPct >= 99.9) {
                stateTag = "FILLED";
            } else if (Number.isFinite(filledPct) && filledPct > 0) {
                stateTag = "TOUCHED";
            } else {
                stateTag = "FRESH";
            }
        }

        return {
            min: zone?.price_min ?? zone?.min,
            max: zone?.price_max ?? zone?.max,
            label: kind,
            type: zone?.type ?? zone?.zone_type ?? null,
            direction: dir,
            role,
            poi_type: zone?.poi_type ?? meta?.poi_type ?? null,
            score,
            filled_pct: filledPct,
            timeframe,
            strength: zone?.strength ?? meta?.strength ?? null,
            confidence: zone?.confidence ?? meta?.confidence ?? null,
            why,
            origin_time: originSec,
            invalidated_time: invalidatedSec,
            zone_id: zone?.zone_id ?? null,
            state: stateTag,
            distance_atr: Number.isFinite(distanceAtr) ? distanceAtr : null,
        };
    });

    // Merge stacked зон у COMPOSITE (мінімізує шум і робить кластер POI читабельним).
    // Правило: overlap >= 0.35 або gap <= 0.2 * ATR.
    function bounds(z) {
        const a = Number(z?.min);
        const b = Number(z?.max);
        if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
        return { lo: Math.min(a, b), hi: Math.max(a, b) };
    }

    function overlapRatio(a, b) {
        const aa = bounds(a);
        const bb = bounds(b);
        if (!aa || !bb) return null;
        const inter = Math.max(0, Math.min(aa.hi, bb.hi) - Math.max(aa.lo, bb.lo));
        const uni = Math.max(aa.hi, bb.hi) - Math.min(aa.lo, bb.lo);
        if (!(uni > 0)) return null;
        return inter / uni;
    }

    function gapAbs(a, b) {
        const aa = bounds(a);
        const bb = bounds(b);
        if (!aa || !bb) return null;
        if (aa.hi < bb.lo) return bb.lo - aa.hi;
        if (bb.hi < aa.lo) return aa.lo - bb.hi;
        return 0;
    }

    function shortTypeLabel(z) {
        const raw = String(z?.poi_type || z?.type || z?.label || "ZONE").toUpperCase();
        if (raw.startsWith("COMPOSITE")) return raw;
        if (raw.includes("ORDER") && raw.includes("BLOCK")) return "OB";
        if (raw.includes("OB")) return "OB";
        if (raw.includes("BREAKER")) return "BREAKER";
        if (raw.includes("FVG") || raw.includes("IMBALANCE")) return "FVG";
        return raw.replace(/\s+/g, " ").trim() || "ZONE";
    }

    function compositeLabel(types) {
        const counts = new Map();
        types.forEach((t) => counts.set(t, (counts.get(t) || 0) + 1));
        const parts = Array.from(counts.entries())
            .sort((a, b) => String(a[0]).localeCompare(String(b[0])))
            .map(([t, n]) => (n > 1 ? `${t}×${n}` : String(t)));
        return `COMPOSITE(${parts.join("+")})`;
    }

    function shouldMerge(a, b) {
        const o = overlapRatio(a, b);
        if (o !== null && o >= 0.35) return true;
        const g = gapAbs(a, b);
        if (g === null) return false;
        if (Number.isFinite(atrLast) && atrLast > 0) {
            return g <= 0.2 * atrLast;
        }
        return false;
    }

    function mergeGroup(group) {
        const mins = group.map((z) => Number(z.min)).filter((v) => Number.isFinite(v));
        const maxs = group.map((z) => Number(z.max)).filter((v) => Number.isFinite(v));
        if (!mins.length || !maxs.length) {
            return group[0];
        }
        const lo = Math.min(...mins);
        const hi = Math.max(...maxs);

        const types = group.map(shortTypeLabel);
        const label = compositeLabel(types);

        const scores = group.map((z) => Number(z.score)).filter((v) => Number.isFinite(v));
        const baseScore = scores.length ? Math.max(...scores) : null;
        const boost = scores.length > 1 ? (scores.length - 1) * 3.0 : 0.0;
        const score = baseScore !== null ? baseScore + boost : null;

        const whys = [];
        group.forEach((z) => {
            const w = Array.isArray(z.why) ? z.why : [];
            w.forEach((x) => {
                const s = String(x);
                if (s && !whys.includes(s)) whys.push(s);
            });
        });
        whys.unshift("confluence:overlap");

        const filleds = group.map((z) => Number(z.filled_pct)).filter((v) => Number.isFinite(v));
        const filled_pct = filleds.length ? Math.max(...filleds) : null;

        const dist = group.map((z) => Number(z.distance_atr)).filter((v) => Number.isFinite(v));
        const distance_atr = dist.length ? Math.min(...dist) : null;

        const state = group.some((z) => String(z.state || "").toUpperCase() === "TOUCHED") ? "TOUCHED" : "FRESH";

        return {
            ...group[0],
            min: lo,
            max: hi,
            poi_type: label,
            label,
            score,
            filled_pct,
            why: whys,
            state,
            distance_atr,
            zone_id: `composite:${group.map((z) => z.zone_id || "-").join("|")}`,
            composite_of: group.map((z) => z.zone_id).filter(Boolean),
        };
    }

    function mergeStacked(zonesIn) {
        const byKey = new Map();
        zonesIn.forEach((z) => {
            const dir = String(z.direction || "").toUpperCase();
            const tf = String(z.timeframe || "");
            const key = `${dir}|${tf}`;
            if (!byKey.has(key)) byKey.set(key, []);
            byKey.get(key).push(z);
        });

        const out = [];
        for (const group of byKey.values()) {
            const sorted = group
                .slice()
                .sort((a, b) => Number(a.min) - Number(b.min));
            let bucket = [];
            for (const z of sorted) {
                if (!bucket.length) {
                    bucket = [z];
                    continue;
                }
                const last = bucket[bucket.length - 1];
                if (shouldMerge(last, z)) {
                    bucket.push(z);
                } else {
                    out.push(bucket.length > 1 ? mergeGroup(bucket) : bucket[0]);
                    bucket = [z];
                }
            }
            if (bucket.length) {
                out.push(bucket.length > 1 ? mergeGroup(bucket) : bucket[0]);
            }
        }

        // Зберігаємо порядок «важливості» приблизно як було (score desc).
        return out.sort((a, b) => (Number(b.score) || 0) - (Number(a.score) || 0));
    }

    // ВАЖЛИВО (SMC UX): компоненти POI-кластера мають лишатись доступними для tooltip.
    // Кластеризацію/антишум робимо в chart_adapter (рендер + hit-test), а не тут.
    return mapped;
}

function isDebugUiEnabled() {
    try {
        const params = new URLSearchParams(window.location.search || "");
        const parsed = parseOptionalBool(params.get("debug_ui"));
        return parsed === true;
    } catch (_e) {
        return false;
    }
}

function formatZoneTypeShort(zone) {
    const raw = String(zone?.poi_type || zone?.type || zone?.label || "ZONE").toUpperCase();
    if (raw.includes("ORDER") && raw.includes("BLOCK")) return "OB";
    if (raw.includes("OB")) return "OB";
    if (raw.includes("BREAKER")) return "BREAKER";
    if (raw.includes("FVG") || raw.includes("IMBALANCE")) return "FVG";
    return raw.replace(/\s+/g, " ").trim() || "ZONE";
}

function formatZoneSide(zone) {
    const dir = String(zone?.direction || "").toUpperCase();
    if (dir === "SHORT") return "SELL";
    if (dir === "LONG") return "BUY";
    return "-";
}

function formatZoneHeadline(zone, index = null) {
    const side = formatZoneSide(zone);
    const type = formatZoneTypeShort(zone);
    const tf = zone?.timeframe ? String(zone.timeframe) : "";
    const score = Number(zone?.score);
    const scorePart = Number.isFinite(score) ? ` score=${formatNumber(score, 2)}` : "";
    const filled = Number(zone?.filled_pct);
    const filledPart = type === "FVG" && Number.isFinite(filled) ? ` filled=${formatNumber(filled, 0)}%` : "";
    return `${side} ${type}${tf ? " " + tf : ""}${scorePart}${filledPart}`.trim();
}

function formatZoneWhyShort(zone) {
    const why = Array.isArray(zone?.why) ? zone.why : [];
    if (!why.length) return "-";
    const head = why.slice(0, 3).join("; ");
    return head || "-";
}

function safeUnixSeconds(value) {
    if (value === null || value === undefined) {
        return undefined;
    }

    // 1) Числа та "числові рядки" (sec або ms).
    const direct = Number(value);
    if (Number.isFinite(direct)) {
        const abs = Math.abs(direct);
        // Евристика: sec ~ 1e9, ms ~ 1e12, us ~ 1e15.
        if (abs > 1e14) {
            return Math.floor(direct / 1e6);
        }
        if (abs > 1e12) {
            return Math.floor(direct / 1e3);
        }
        return Math.floor(direct);
    }

    // 2) ISO-рядки часу (наприклад "2025-12-16T12:34:56Z").
    if (typeof value === "string") {
        const parsedMs = Date.parse(value);
        if (Number.isFinite(parsedMs)) {
            return Math.floor(parsedMs / 1000);
        }
    }
    return undefined;
}

function normalizeOhlcvBar(bar) {
    if (!bar) {
        return null;
    }
    // ВАЖЛИВО для lightweight-charts: time має бути open time бакету.
    // Якщо використати close/end time, UI може сприймати це як "новий бар" замість update().
    const timeCandidate =
        bar.open_time ??
        bar.start_ts ??
        bar.start_time ??
        bar.open_ts ??
        bar.time ??
        bar.ts ??
        bar.timestamp ??
        bar.end_ts;
    const openCandidate = bar.open ?? bar.o;
    const highCandidate = bar.high ?? bar.h;
    const lowCandidate = bar.low ?? bar.l;
    const closeCandidate = bar.close ?? bar.c;
    if (
        timeCandidate === undefined ||
        openCandidate === undefined ||
        highCandidate === undefined ||
        lowCandidate === undefined ||
        closeCandidate === undefined
    ) {
        return null;
    }
    const timeSec = safeUnixSeconds(timeCandidate);
    if (timeSec === undefined) {
        return null;
    }
    const normalized = {
        time: timeSec,
        open: Number(openCandidate),
        high: Number(highCandidate),
        low: Number(lowCandidate),
        close: Number(closeCandidate),
    };
    if (
        !Number.isFinite(normalized.open) ||
        !Number.isFinite(normalized.high) ||
        !Number.isFinite(normalized.low) ||
        !Number.isFinite(normalized.close)
    ) {
        return null;
    }
    normalized.volume = Number(bar.volume ?? bar.vol ?? 0);
    // Якщо бекенд віддає `complete=false` (поточний незакритий бар) — збережемо для UI інваріантів.
    // За відсутності поля вважаємо бар закритим (історія).
    normalized.complete = bar.complete !== false;
    return normalized;
}

function pickFirstPositiveNumber(...candidates) {
    for (const value of candidates) {
        const num = Number(value);
        if (Number.isFinite(num) && num > 0) {
            return num;
        }
    }
    return 0;
}

function pickVolumeFromFxcmBar(bar) {
    if (!bar) {
        return 0;
    }
    // "Чистий" volume (без проксі-інтенсивності).
    // Nullish-coalescing (??) тут не підходить — нам треба «перше додатне».
    if (!FXCM_TICKCOUNT_VOLUME_ENABLED) {
        return pickFirstPositiveNumber(bar.volume, bar.vol, bar.v);
    }

    // Під флагом дозволяємо fallback, якщо FXCM інколи передає `volume=0`, але `tick_count>0`.
    return pickFirstPositiveNumber(
        bar.volume,
        bar.vol,
        bar.v,
        bar.tick_count,
        bar.tickCount,
        bar.ticks,
        bar.volume_intensity,
        bar.intensity,
    );
}

function pickVolumeSourceFromFxcmBar(bar) {
    if (!bar) {
        return "-";
    }
    // Повертаємо коротку мітку джерела, щоб миттєво бачити, від чого *зараз*
    // залежить висота volume-гістограми.
    // Важливо: для стабільності UX не показуємо "-" лише через те, що значення 0
    // (наприклад, у свіжому інструменті або при пустій історії). Якщо позитивного
    // значення немає — показуємо найімовірніше/наявне поле відповідно до режиму.

    const volValue = Number(bar.volume ?? bar.vol ?? bar.v);
    const tcValue = Number(bar.tick_count ?? bar.tickCount);
    const ticksValue = Number(bar.ticks);
    const intensityValue = Number(bar.volume_intensity ?? bar.intensity);

    const hasVol = bar.volume != null || bar.vol != null || bar.v != null;
    const hasTc = bar.tick_count != null || bar.tickCount != null;
    const hasTicks = bar.ticks != null;
    const hasIntensity = bar.volume_intensity != null || bar.intensity != null;

    // 1) Якщо є позитивний "чистий" volume — він завжди пріоритетний.
    if (Number.isFinite(volValue) && volValue > 0) {
        return "volume";
    }

    // 2) Під флагом дозволяємо проксі-джерела (tick_count/ticks/intensity).
    if (FXCM_TICKCOUNT_VOLUME_ENABLED) {
        if (Number.isFinite(tcValue) && tcValue > 0) {
            return "tick_count";
        }
        if (Number.isFinite(ticksValue) && ticksValue > 0) {
            return "ticks";
        }
        if (Number.isFinite(intensityValue) && intensityValue > 0) {
            return "intensity";
        }

        // 3) Немає позитивних значень: показуємо найкраще доступне поле (не "-").
        if (hasTc) return "tick_count";
        if (hasTicks) return "ticks";
        if (hasIntensity) return "intensity";
        if (hasVol) return "volume";
        return "-";
    }

    // 2b) Без флагу: завжди чесно показуємо, що працюємо тільки з volume.
    return hasVol ? "volume" : "-";
}

function extractLastBarFromViewerState(state) {
    if (!state) {
        return null;
    }
    const candidate =
        state.ohlcv_last_bar ||
        state.last_bar ||
        state.latest_bar ||
        state.structure?.last_bar ||
        state.ohlcv?.last_bar ||
        state.ohlcv?.last;
    return normalizeOhlcvBar(candidate);
}

function extractReplayCursorMs(state) {
    try {
        const raw = state?.meta?.replay_cursor_ms;
        if (raw === null || raw === undefined) return null;
        const n = Number(raw);
        return Number.isFinite(n) ? Math.floor(n) : null;
    } catch (_e) {
        return null;
    }
}

function isReplayViewerState(state) {
    try {
        const mode = String(state?.meta?.replay_mode || "").trim().toLowerCase();
        if (mode) return true;
        const fxcmState = String(state?.meta?.fxcm?.process_state || "").trim().toLowerCase();
        return fxcmState.includes("replay");
    } catch (_e) {
        return false;
    }
}

function getOverlaySeqKey(state) {
    if (!state) {
        return null;
    }
    const seqValue =
        state.payload_seq ??
        state.meta?.payload_seq ??
        state.meta?.seq ??
        state.meta?.ts ??
        state.payload_ts ??
        null;
    return seqValue == null ? null : String(seqValue);
}

const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000];
const OHLCV_RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000];
const TICK_RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000];

const FXCM_LIVE_STALE_MS = 5000;
const FXCM_OHLCV_RECONNECT_MAX_ATTEMPTS = 12;
const FXCM_TICK_RECONNECT_MAX_ATTEMPTS = 12;
const STATUS_RECONNECT_MAX_ATTEMPTS = 12;

const appState = {
    snapshot: {},
    latestStates: {},
    currentSymbol: null,
    currentTimeframe: OHLCV_DEFAULT_TF,
    preferredSymbol: null,
    ws: null,
    reconnectAttempt: 0,
    reconnectTimer: null,
    ohlcvWs: null,
    ohlcvReconnectAttempt: 0,
    ohlcvReconnectTimer: null,
    ohlcvLiveOpenTimeSec: null,
    ohlcvLiveCandle: null,
    tickWs: null,
    tickReconnectAttempt: 0,
    tickReconnectTimer: null,
    statusWs: null,
    statusReconnectAttempt: 0,
    statusReconnectTimer: null,
    lastFxcmStatus: null,
    tickLiveOpenTimeSec: null,
    tickLiveCandle: null,
    tickLiveVolumeCount: 0,
    tickLastEmitMs: 0,
    lastTickMid: null,
    lastTickSymbol: null,
    lastTickTsMs: 0,
    lastOhlcvVolSrc: "-",
    replay: {
        lastCursorMs: null,
        lastOhlcvCursorMs: null,
        lastOhlcvRefetchAtMs: 0,
        ohlcvRefetchMinIntervalMs: 200,
    },
    prevCompleteCandle: null,
    lastCompleteCandle: null,
    lastPayloadTs: null,
    lastLagSeconds: null,
    fxcmLiveLastSeenMs: null,
    fxcmLiveOffTimer: null,
    uiStatusState: null,
    uiStatusDetail: "",
    chart: null,
    chartState: {
        overlaySeqBySymbol: Object.create(null),
        layersVisibility: {
            events: true,
            pools: true,
            ranges: true,
            ote: true,
            zones: true,
            sessions: true,
        },
        zoneLimitMode: DEFAULT_ZONE_LIMIT_MODE,
    },
    chartUi: {
        heightPx: CHART_HEIGHT_DEFAULT,
        fullscreen: false,
    },

    ui: {
        view: "overview",
        filtersOpen: false,
        summaryCollapsed: false,
        stage6Collapsed: false,
    },
};

const elements = {};

function resetOverlaySeqCache(symbol = null) {
    try {
        if (!appState?.chartState?.overlaySeqBySymbol) {
            return;
        }
        if (symbol) {
            const key = String(symbol).toUpperCase();
            delete appState.chartState.overlaySeqBySymbol[key];
            return;
        }
        appState.chartState.overlaySeqBySymbol = Object.create(null);
    } catch (_e) {
        // noop
    }
}

loadPersistedPreferences();

document.addEventListener("DOMContentLoaded", () => {
    cacheElements();
    initUiViews();
    bindUi();
    initChartController();

    // Debug UI: вмикає видимість діагностичних блоків (таблиці під чартом) та технічні індикатори.
    document.body.classList.toggle("debug-ui", isDebugUiEnabled());

    bootstrap().catch((err) => console.error("[UI] Помилка старту:", err));
});

window.addEventListener("beforeunload", () => {
    cleanupSocket();
    cleanupOhlcvSocket();
    cleanupTickSocket();
    cleanupStatusSocket();
});

function cacheElements() {
    elements.symbolSelect = document.getElementById("symbol-select");
    elements.refreshBtn = document.getElementById("refresh-btn");
    elements.reconnectBtn = document.getElementById("reconnect-btn");
    elements.wsStatus = document.getElementById("ws-status");
    elements.marketStatus = document.getElementById("market-status");
    elements.payloadTs = document.getElementById("payload-ts");
    elements.payloadLag = document.getElementById("payload-lag");
    elements.activeZones = document.getElementById("active-zones");
    elements.execStatus = document.getElementById("exec-status");
    elements.tfHealth = document.getElementById("tf-health");
    elements.timeframeSelect = document.getElementById("timeframe-select");

    if (elements.tfHealth && !TF_HEALTH_ENABLED) {
        elements.tfHealth.hidden = true;
    }

    elements.summaryToggleBtn = document.getElementById("summary-toggle-btn");
    elements.mobileSummaryToggle = {
        overview: document.getElementById("m-overview-summary-toggle"),
        chart: document.getElementById("m-chart-summary-toggle"),
    };

    elements.summary = {
        symbol: document.getElementById("summary-symbol"),
        price: document.getElementById("summary-price"),
        session: document.getElementById("summary-session"),
        trend: document.getElementById("summary-trend"),
        bias: document.getElementById("summary-bias"),
        range: document.getElementById("summary-range"),
        amd: document.getElementById("summary-amd"),
        market: document.getElementById("summary-market"),
        process: document.getElementById("summary-process"),
        lag: document.getElementById("summary-lag"),
        volSrc: document.getElementById("summary-vol-src"),
    };

    elements.stage6 = {
        panel: document.getElementById("stage6-panel"),
        toggleBtn: document.getElementById("stage6-toggle-btn"),
        grid: document.getElementById("stage6-grid"),
        noData: document.getElementById("stage6-nodata"),
        mode: document.getElementById("stage6-mode"),
        stableConf: document.getElementById("stage6-stable-conf"),
        stableConfBar: document.getElementById("stage6-stable-conf-bar"),
        rawLine: document.getElementById("stage6-raw-line"),
        pendingLine: document.getElementById("stage6-pending-line"),
        why: document.getElementById("stage6-why"),
        htfDr: document.getElementById("stage6-htf-dr"),
        htfPd: document.getElementById("stage6-htf-pd"),
        htfAtr: document.getElementById("stage6-htf-atr"),
        sweep: document.getElementById("stage6-sweep"),
        hold: document.getElementById("stage6-hold"),
        failedHold: document.getElementById("stage6-failed-hold"),
        targets: document.getElementById("stage6-targets"),
        poi: document.getElementById("stage6-poi"),
        antiflipTtl: document.getElementById("stage6-antiflip-ttl"),
        antiflipBlocked: document.getElementById("stage6-antiflip-blocked"),
        antiflipOverride: document.getElementById("stage6-antiflip-override"),
    };

    elements.tables = {
        events: document.getElementById("events-body"),
        ote: document.getElementById("ote-body"),
        pools: document.getElementById("pools-body"),
        zones: document.getElementById("zones-body"),
    };
    elements.chartContainer = document.getElementById("chart-container");
    elements.chartCard = document.querySelector(".card-chart");
    elements.chartSizeToggleBtn = document.getElementById("chart-size-toggle-btn");
    elements.chartFullscreenBtn = document.getElementById("chart-fullscreen-btn");
    elements.chartLayerMenuBtn = document.getElementById("chart-layer-menu-btn");
    elements.chartLayerMenu = document.getElementById("chart-layer-menu");
    elements.zonesLimitSelect = document.getElementById("zones-limit-select");
    elements.layerToggles = {
        events: document.getElementById("layer-toggle-events"),
        pools: document.getElementById("layer-toggle-pools"),
        ote: document.getElementById("layer-toggle-ote"),
        zones: document.getElementById("layer-toggle-zones"),
        sessions: document.getElementById("layer-toggle-sessions"),
    };

    elements.views = {
        overview: document.getElementById("view-overview"),
        chart: document.getElementById("view-chart"),
    };
    elements.chartSlots = {
        overview: document.getElementById("overview-chart-slot"),
        chart: document.getElementById("chart-slot"),
    };
    elements.overviewEventsList = document.getElementById("overview-events-list");

    elements.bottomNav = document.getElementById("bottom-nav");
    elements.drawer = {
        root: document.getElementById("filters-drawer"),
        closeBtn: document.getElementById("drawer-close"),
        timeframeSelect: document.getElementById("timeframe-select-mobile"),
        layers: {
            events: document.getElementById("drawer-layer-events"),
            pools: document.getElementById("drawer-layer-pools"),
            ote: document.getElementById("drawer-layer-ote"),
            zones: document.getElementById("drawer-layer-zones"),
            sessions: document.getElementById("drawer-layer-sessions"),
        },
        zonesLimitSelect: document.getElementById("zones-limit-select-mobile"),
    };
    elements.mobile = {
        headerOverview: document.getElementById("mobile-header-overview"),
        headerChart: document.getElementById("mobile-header-chart"),
        overviewSymbol: document.getElementById("m-overview-symbol"),
        overviewPrice: document.getElementById("m-overview-price"),
        overviewDelta: document.getElementById("m-overview-delta"),
        overviewWs: document.getElementById("m-overview-ws"),
        chartSymbol: document.getElementById("m-chart-symbol"),
        chartPrice: document.getElementById("m-chart-price"),
        backBtn: document.getElementById("m-chart-back"),
        menuBtn: document.getElementById("m-chart-menu"),
    };
}

function normalizeZoneLimitMode(value) {
    const v = String(value || "").trim().toLowerCase();
    if (v === "near1" || v === "near2" || v === "all") {
        return v;
    }
    return DEFAULT_ZONE_LIMIT_MODE;
}

function persistZoneLimitMode(value) {
    const storage = getStorage();
    if (!storage) {
        return;
    }
    try {
        storage.setItem(STORAGE_KEYS.zoneLimitMode, normalizeZoneLimitMode(value));
    } catch (err) {
        console.warn("[UI] Не вдалося зберегти ліміт зон у localStorage", err);
    }
}

function applyZoneLimitModeToChart() {
    if (!appState.chart || typeof appState.chart.setZoneLimitMode !== "function") {
        return;
    }
    appState.chart.setZoneLimitMode(appState.chartState.zoneLimitMode);
}

function bindZonesLimitControl() {
    const desktop = elements.zonesLimitSelect;
    const mobile = elements.drawer?.zonesLimitSelect;

    const applyValue = (value, options = {}) => {
        const next = normalizeZoneLimitMode(value);
        appState.chartState.zoneLimitMode = next;
        if (desktop) desktop.value = next;
        if (mobile) mobile.value = next;
        if (options.persist !== false) {
            persistZoneLimitMode(next);
        }
        applyZoneLimitModeToChart();

        const symbol = appState.currentSymbol;
        const state = symbol ? appState.latestStates[symbol] : null;
        if (state) {
            updateChartFromViewerState(state, {
                force: true,
                symbolOverride: symbol,
            });
        }
    };

    // Початкове значення.
    applyValue(appState.chartState.zoneLimitMode, { persist: false });

    if (desktop) {
        desktop.addEventListener("change", (event) => {
            applyValue(event.target.value);
        });
    }
    if (mobile) {
        mobile.addEventListener("change", (event) => {
            applyValue(event.target.value);
        });
    }
}

function initUiViews() {
    const initial = getViewFromUrl() || "overview";
    setView(initial, { pushHistory: false });

    window.addEventListener("popstate", () => {
        const view = getViewFromUrl() || "overview";
        setView(view, { pushHistory: false });
    });

    bindMobileUi();
    scheduleMobileChartHeight();
    window.addEventListener("resize", scheduleMobileChartHeight);
    window.addEventListener("orientationchange", scheduleMobileChartHeight);
    if (window.visualViewport && typeof window.visualViewport.addEventListener === "function") {
        window.visualViewport.addEventListener("resize", scheduleMobileChartHeight);
        window.visualViewport.addEventListener("scroll", scheduleMobileChartHeight);
    }
}

function getViewFromUrl() {
    try {
        const url = new URL(window.location.href);
        const view = String(url.searchParams.get("view") || "").toLowerCase();
        if (view === "chart") return "chart";
        if (view === "overview") return "overview";
        return null;
    } catch {
        return null;
    }
}

function setView(view, { pushHistory } = { pushHistory: true }) {
    const isMobile =
        typeof window !== "undefined" &&
        typeof window.matchMedia === "function" &&
        window.matchMedia("(max-width: 768px)").matches;

    const next = isMobile && view === "overview" ? "overview" : "chart";
    appState.ui.view = next;

    document.body.classList.remove("ui-view-overview", "ui-view-chart");
    document.body.classList.add(next === "chart" ? "ui-view-chart" : "ui-view-overview");

    updateBottomNavSelection(next);
    moveChartCardToActiveSlot(next);
    scheduleChartResize();
    scheduleMobileChartHeight();

    if (pushHistory) {
        try {
            const url = new URL(window.location.href);
            url.searchParams.set("view", next);
            window.history.pushState({ view: next }, "", url);
        } catch {
            // ignore
        }
    }
}

function updateBottomNavSelection(view) {
    if (!elements.bottomNav) {
        return;
    }
    const buttons = elements.bottomNav.querySelectorAll("button[data-view]");
    buttons.forEach((btn) => {
        const v = String(btn.getAttribute("data-view") || "").toLowerCase();
        if (v === view) {
            btn.setAttribute("aria-current", "page");
        } else {
            btn.removeAttribute("aria-current");
        }
    });
}

function moveChartCardToActiveSlot(view) {
    if (!elements.chartCard) {
        return;
    }
    const target = view === "chart" ? elements.chartSlots.chart : elements.chartSlots.overview;
    if (!target) {
        return;
    }
    if (elements.chartCard.parentElement !== target) {
        target.appendChild(elements.chartCard);
    }
}

function bindMobileUi() {
    if (elements.bottomNav) {
        elements.bottomNav.addEventListener("click", (evt) => {
            const btn = evt.target.closest("button");
            if (!btn) {
                return;
            }
            const view = btn.getAttribute("data-view");
            const action = btn.getAttribute("data-action");
            if (view === "overview" || view === "chart") {
                setView(view, { pushHistory: true });
                return;
            }
            if (action === "filters") {
                setDrawerOpen(!appState.ui.filtersOpen);
            }
        });
    }

    if (elements.mobile?.backBtn) {
        elements.mobile.backBtn.addEventListener("click", () => {
            setView("overview", { pushHistory: true });
        });
    }
    if (elements.mobile?.menuBtn) {
        elements.mobile.menuBtn.addEventListener("click", () => {
            setDrawerOpen(true);
        });
    }

    if (elements.drawer?.closeBtn) {
        elements.drawer.closeBtn.addEventListener("click", () => {
            setDrawerOpen(false);
        });
    }

    if (elements.drawer?.timeframeSelect) {
        elements.drawer.timeframeSelect.value = normalizeTimeframe(appState.currentTimeframe);
        elements.drawer.timeframeSelect.addEventListener("change", (event) => {
            const tf = normalizeTimeframe(event.target.value);
            if (elements.timeframeSelect) {
                elements.timeframeSelect.value = tf;
            }
            handleTimeframeChange(tf);
        });
    }

    Object.entries(elements.drawer?.layers || {}).forEach(([key, el]) => {
        if (!el) return;
        el.addEventListener("change", () => {
            const checked = Boolean(el.checked);
            const desktopToggle = elements.layerToggles?.[key];
            if (desktopToggle) {
                desktopToggle.checked = checked;
            }
            appState.chartState.layersVisibility[key] = checked;
            persistLayersVisibility();

            if (key === "sessions" && appState.chart && typeof appState.chart.setSessionsEnabled === "function") {
                appState.chart.setSessionsEnabled(checked);
                applySessionRangeBoxFromFxcmStatus();
            }

            rehydrateOverlays({ force: true });
        });
    });

    syncDrawerFromDesktopControls();
}

function syncDrawerFromDesktopControls() {
    if (elements.drawer?.timeframeSelect) {
        elements.drawer.timeframeSelect.value = normalizeTimeframe(appState.currentTimeframe);
    }
    Object.entries(elements.drawer?.layers || {}).forEach(([key, el]) => {
        if (!el) return;
        const desktopToggle = elements.layerToggles?.[key];
        if (desktopToggle) {
            el.checked = Boolean(desktopToggle.checked);
        }
    });
}

function isMobileViewport() {
    try {
        return (
            typeof window !== "undefined" &&
            typeof window.matchMedia === "function" &&
            window.matchMedia("(max-width: 768px)").matches
        );
    } catch (_e) {
        return false;
    }
}

function setDrawerOpen(isOpen) {
    appState.ui.filtersOpen = Boolean(isOpen);
    if (!elements.drawer?.root) {
        return;
    }
    if (appState.ui.filtersOpen) {
        elements.drawer.root.classList.add("is-open");
    } else {
        elements.drawer.root.classList.remove("is-open");
    }
}

let mobileChartHeightRaf = null;
function scheduleMobileChartHeight() {
    if (typeof window === "undefined") {
        return;
    }
    if (!isMobileViewport()) {
        return;
    }
    const raf = window.requestAnimationFrame || window.setTimeout;
    if (mobileChartHeightRaf) {
        return;
    }
    mobileChartHeightRaf = raf(() => {
        mobileChartHeightRaf = null;
        updateMobileChartHeightVar();
    });
}

function updateMobileChartHeightVar() {
    if (!isMobileViewport()) {
        return;
    }
    const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
    const vh = Number(viewportHeight || 0);
    if (!Number.isFinite(vh) || vh <= 0) {
        return;
    }

    // Стабілізуємо CSS-висоту екрана під мобільні браузери (Android address bar / iOS toolbar).
    document.documentElement.style.setProperty("--app-vh", `${Math.round(vh)}px`);

    const headerEl =
        appState.ui.view === "chart"
            ? elements.mobile?.headerChart
            : elements.mobile?.headerOverview;
    const headerH = headerEl?.offsetHeight || 0;
    const bottomH = elements.bottomNav?.offsetHeight || 0;

    // Мобільний Chart: чарт має займати максимум доступного простору.
    // Мобільний Overview: лишаємо запас під summary/події.
    const reserve = appState.ui.view === "chart" ? 0 : 420;
    const raw = vh - headerH - bottomH - reserve;
    const target = Math.max(220, Math.min(1200, raw));

    document.documentElement.style.setProperty(
        "--mobile-chart-height",
        `${Math.round(target)}px`,
    );

    if (appState.ui.view === "chart") {
        scheduleChartResize();
    }
}

function initChartController() {
    if (!elements.chartContainer) {
        return;
    }
    if (typeof window.createChartController !== "function") {
        console.warn("[UI] chart_adapter не завантажено — чарт недоступний");
        return;
    }
    try {
        appState.chart = window.createChartController(elements.chartContainer);

        if (appState.chart && typeof appState.chart.setViewTimeframe === "function") {
            appState.chart.setViewTimeframe(appState.currentTimeframe);
        }

        applyZoneLimitModeToChart();

        if (appState.chart && typeof appState.chart.setSessionsEnabled === "function") {
            const enabled = appState.chartState?.layersVisibility?.sessions !== false;
            appState.chart.setSessionsEnabled(enabled);
        }

        applySessionRangeBoxFromFxcmStatus();

        scheduleChartResize();
    } catch (error) {
        console.error("[UI] Не вдалося створити chartController", error);
        appState.chart = null;
    }
}

function bindUi() {
    elements.symbolSelect.addEventListener("change", (event) => {
        const nextSymbol = String(event.target.value || "").toUpperCase();
        if (!nextSymbol) return;
        handleSymbolChange(nextSymbol);
    });

    if (elements.timeframeSelect) {
        elements.timeframeSelect.value = appState.currentTimeframe;
        elements.timeframeSelect.addEventListener("change", (event) => {
            handleTimeframeChange(event.target.value);
        });
    }

    if (elements.refreshBtn) {
        elements.refreshBtn.addEventListener("click", async () => {
            try {
                await reloadSnapshot(true);
                if (appState.currentSymbol) {
                    await fetchOhlcv(
                        appState.currentSymbol,
                        appState.currentTimeframe,
                    );
                }
            } catch (err) {
                console.error("[UI] Snapshot error:", err);
            }
        });
    }

    if (elements.reconnectBtn) {
        elements.reconnectBtn.addEventListener("click", () => {
            if (appState.currentSymbol) {
                openViewerSocket(appState.currentSymbol);
                openOhlcvSocket(appState.currentSymbol, appState.currentTimeframe);
                openTickSocket(appState.currentSymbol);
                openStatusSocket();
            }
        });
    }

    bindSummaryToggle();
    bindStage6Toggle();

    bindLayerToggles();
    bindChartLayerMenu();
    bindZonesLimitControl();
    initChartLayoutControls();
    syncDrawerFromDesktopControls();
}

function bindSummaryToggle() {
    const buttons = [
        elements.summaryToggleBtn,
        elements.mobileSummaryToggle?.overview,
        elements.mobileSummaryToggle?.chart,
    ].filter(Boolean);

    if (buttons.length === 0) {
        return;
    }

    buttons.forEach((btn) => {
        btn.addEventListener("click", () => {
            applySummaryCollapsed(!appState.ui.summaryCollapsed);
        });
    });

    applySummaryCollapsed(appState.ui.summaryCollapsed, { persist: false });
}

function bindStage6Toggle() {
    const btn = elements.stage6?.toggleBtn;
    if (!btn) {
        return;
    }
    btn.addEventListener("click", () => {
        applyStage6Collapsed(!appState.ui.stage6Collapsed);
    });
    applyStage6Collapsed(appState.ui.stage6Collapsed, { persist: false });
}

function bindChartLayerMenu() {
    const btn = elements.chartLayerMenuBtn;
    const menu = elements.chartLayerMenu;
    if (!btn || !menu) {
        return;
    }

    const setOpen = (open) => {
        if (open) {
            menu.removeAttribute("hidden");
            btn.setAttribute("aria-expanded", "true");
        } else {
            menu.setAttribute("hidden", "");
            btn.setAttribute("aria-expanded", "false");
        }
    };

    const toggle = () => {
        const isOpen = !menu.hasAttribute("hidden");
        setOpen(!isOpen);
    };

    btn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        toggle();
    });

    document.addEventListener("click", (event) => {
        if (menu.hasAttribute("hidden")) {
            return;
        }
        if (menu.contains(event.target) || btn.contains(event.target)) {
            return;
        }
        setOpen(false);
    });

    document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") {
            return;
        }
        if (!menu.hasAttribute("hidden")) {
            setOpen(false);
        }
    });
}

function bindLayerToggles() {
    if (!elements.layerToggles) {
        return;
    }
    Object.entries(elements.layerToggles).forEach(([layerKey, checkbox]) => {
        if (!checkbox) {
            return;
        }
        const defaultValue = appState.chartState.layersVisibility[layerKey];
        checkbox.checked = defaultValue !== false;
        checkbox.addEventListener("change", () => {
            appState.chartState.layersVisibility[layerKey] = checkbox.checked;
            persistLayersVisibility();

            // Сесії рахуються від OHLCV і не залежать від viewer_state,
            // тому вмикаємо/вимикаємо їх одразу.
            if (layerKey === "sessions" && appState.chart && typeof appState.chart.setSessionsEnabled === "function") {
                appState.chart.setSessionsEnabled(checkbox.checked);
            }

            const drawerToggle = elements.drawer?.layers?.[layerKey];
            if (drawerToggle) {
                drawerToggle.checked = checkbox.checked;
            }
            const symbol = appState.currentSymbol;
            const state = symbol ? appState.latestStates[symbol] : null;
            if (state) {
                updateChartFromViewerState(state, {
                    force: true,
                    symbolOverride: symbol,
                });
            } else {
                clearChartLayer(layerKey);
            }
        });
    });
}

function clearChartLayer(layerKey) {
    if (!appState.chart) {
        return;
    }
    switch (layerKey) {
        case "events":
            appState.chart.setEvents([]);
            break;
        case "pools":
            appState.chart.setLiquidityPools([]);
            break;
        case "ranges":
            appState.chart.setRanges([]);
            break;
        case "ote":
            appState.chart.setOteZones([]);
            break;
        case "zones":
            appState.chart.setZones([]);
            break;
        case "sessions":
            if (typeof appState.chart.setSessionsEnabled === "function") {
                appState.chart.setSessionsEnabled(false);
            }
            break;
        default:
            break;
    }
}

async function bootstrap() {
    setStatus("loading", "Очікуємо snapshot");
    await reloadSnapshot(false);
    const initialSymbol = pickInitialSymbol(appState.snapshot);
    if (!initialSymbol) {
        setStatus("error", "Snapshot порожній");
        renderEmptyState("Немає даних (ще не надійшов snapshot)");
        return;
    }
    setCurrentSymbol(initialSymbol);
    renderFromCache(initialSymbol);
    await fetchOhlcv(initialSymbol, appState.currentTimeframe);
    openViewerSocket(initialSymbol);
    openOhlcvSocket(initialSymbol, appState.currentTimeframe);
    openTickSocket(initialSymbol);
    openStatusSocket();
}

async function reloadSnapshot(manual) {
    const snapshot = await fetchSnapshot();
    const normalized = {};
    Object.entries(snapshot || {}).forEach(([key, value]) => {
        normalized[key.toUpperCase()] = value;
    });
    appState.snapshot = normalized;
    appState.latestStates = { ...normalized };
    populateSymbolSelect(Object.keys(normalized));
    if (manual && appState.currentSymbol) {
        renderFromCache(appState.currentSymbol);
    }
}

async function fetchSnapshot() {
    const response = await fetch(`${HTTP_BASE_URL}/smc-viewer/snapshot`, {
        method: "GET",
        headers: { "Content-Type": "application/json" },
    });
    if (!response.ok) {
        throw new Error(`Snapshot HTTP ${response.status}`);
    }
    return response.json();
}

async function fetchOhlcv(symbol, timeframe = appState.currentTimeframe || OHLCV_DEFAULT_TF) {
    if (!symbol) {
        lastOhlcvResponse = null;
        renderOhlcvSummary(null);
        return;
    }

    const normalizedTf = normalizeTimeframe(timeframe);

    const lowerSymbol = symbol.toLowerCase();
    // Replay cursor (опційно): якщо йде offline replay, бари повинні "доростати"
    // разом з курсором, без lookahead.
    const replayCursorRaw = appState.replay?.lastCursorMs;
    const replayCursorMs = (typeof replayCursorRaw === "number" && Number.isFinite(replayCursorRaw))
        ? Math.floor(replayCursorRaw)
        : null;
    const cursorSuffix = replayCursorMs !== null
        ? `&to_ms=${encodeURIComponent(String(replayCursorMs))}`
        : "";

    const url = `${HTTP_BASE_URL}/smc-viewer/ohlcv` +
        `?symbol=${encodeURIComponent(lowerSymbol)}` +
        `&tf=${encodeURIComponent(normalizedTf)}` +
        `&limit=${OHLCV_DEFAULT_LIMIT}` +
        cursorSuffix;

    try {
        const response = await fetch(url, {
            method: "GET",
            headers: { Accept: "application/json" },
        });
        if (!response.ok) {
            console.warn("[UI] OHLCV request failed", response.status);
            lastOhlcvResponse = null;
            renderOhlcvSummary(null);
            if (appState.chart) {
                appState.chart.clearAll();
                resetOverlaySeqCache(appState.currentSymbol);
                if (typeof appState.chart.clearLiveBar === "function") {
                    appState.chart.clearLiveBar();
                }
            }
            resetTickLiveState();
            return;
        }
        const data = await response.json();
        lastOhlcvResponse = data;
        renderOhlcvSummary(data);
        pushBarsToChart(data);
        openOhlcvSocket(symbol, normalizedTf);
    } catch (err) {
        console.error("[UI] OHLCV request error", err);
        lastOhlcvResponse = null;
        renderOhlcvSummary(null);
        if (appState.chart) {
            appState.chart.clearAll();
            resetOverlaySeqCache(appState.currentSymbol);
            if (typeof appState.chart.clearLiveBar === "function") {
                appState.chart.clearLiveBar();
            }
        }
        resetTickLiveState();
    }
}

function pickInitialSymbol(snapshot) {
    if (!snapshot || Object.keys(snapshot).length === 0) {
        return null;
    }
    const preferred = (appState.preferredSymbol || DEFAULT_SYMBOL).toUpperCase();
    if (snapshot[preferred]) {
        return preferred;
    }
    const defaultKey = DEFAULT_SYMBOL.toUpperCase();
    if (snapshot[defaultKey]) {
        return defaultKey;
    }
    return Object.keys(snapshot)[0];
}

function populateSymbolSelect(symbols) {
    const select = elements.symbolSelect;
    if (!select) {
        return;
    }
    select.innerHTML = "";
    const sorted = symbols.sort();
    sorted.forEach((symbol) => {
        const option = document.createElement("option");
        option.value = symbol;
        option.textContent = symbol;
        select.appendChild(option);
    });
    const targetSymbol = appState.currentSymbol || appState.preferredSymbol;
    if (targetSymbol && symbols.includes(targetSymbol)) {
        select.value = targetSymbol;
    }
}

function setCurrentSymbol(symbol, options = {}) {
    if (!symbol) {
        return;
    }
    const normalized = String(symbol).toUpperCase();
    appState.currentSymbol = normalized;
    appState.preferredSymbol = normalized;
    if (elements.symbolSelect) {
        elements.symbolSelect.value = normalized;
    }
    if (options.persist !== false) {
        persistSymbol(normalized);
    }
}

function handleSymbolChange(symbol) {
    setCurrentSymbol(symbol);
    renderFromCache(symbol);
    fetchOhlcv(symbol, appState.currentTimeframe);
    openViewerSocket(symbol);
    openOhlcvSocket(symbol, appState.currentTimeframe);
    openTickSocket(symbol);
    applySessionRangeBoxFromFxcmStatus();
}

function handleTimeframeChange(nextTf) {
    const normalized = normalizeTimeframe(nextTf);
    if (normalized === appState.currentTimeframe) {
        return;
    }
    appState.currentTimeframe = normalized;

    if (appState.chart && typeof appState.chart.setViewTimeframe === "function") {
        appState.chart.setViewTimeframe(normalized);
    }

    syncTimeframeSelect(normalized);
    if (elements.drawer?.timeframeSelect) {
        elements.drawer.timeframeSelect.value = normalized;
    }
    persistTimeframe(normalized);
    resetTickLiveState();
    if (appState.currentSymbol) {
        fetchOhlcv(appState.currentSymbol, normalized);
        openOhlcvSocket(appState.currentSymbol, normalized);
    }
    applySessionRangeBoxFromFxcmStatus();
}

function parseUtcIsoToSeconds(value) {
    const text = String(value || "").trim();
    if (!text) return null;
    const ms = Date.parse(text);
    if (!Number.isFinite(ms)) return null;
    return Math.floor(ms / 1000);
}

function utcDayStartSec(timeSec) {
    const d = new Date(Number(timeSec) * 1000);
    return Math.floor(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) / 1000);
}

function pickReferenceTimeSecForSessions() {
    try {
        const bars = Array.isArray(lastOhlcvResponse?.bars) ? lastOhlcvResponse.bars : [];
        if (bars.length) {
            const last = bars[bars.length - 1];
            const t = safeUnixSeconds(last?.time ?? last?.t ?? last?.ts ?? last?.timestamp);
            if (Number.isFinite(t)) {
                return t;
            }
        }
    } catch (_e) {
        // ignore
    }
    return Math.floor(Date.now() / 1000);
}

function computeActiveSessionWindowUtc(timeSec) {
    const nowSec = Number(timeSec);
    if (!Number.isFinite(nowSec)) return null;

    const dayStart = utcDayStartSec(nowSec);
    const minOfDay = Math.floor((nowSec - dayStart) / 60);

    // Має збігатися з розкладом у chart_adapter.js (UTC).
    const ASIA = { startMin: 0 * 60, endMin: 9 * 60 };
    const LONDON = { startMin: 9 * 60, endMin: 17 * 60 };
    const NEW_YORK = { startMin: 17 * 60, endMin: 22 * 60 };

    const pick = (window, tag) => ({
        from: dayStart + window.startMin * 60,
        to: dayStart + window.endMin * 60,
        session: tag,
    });

    // Нотація для UI: Asia = Tokyo.
    if (minOfDay >= ASIA.startMin && minOfDay < ASIA.endMin) return pick(ASIA, "tokyo");
    if (minOfDay >= LONDON.startMin && minOfDay < LONDON.endMin) return pick(LONDON, "london");
    if (minOfDay >= NEW_YORK.startMin && minOfDay < NEW_YORK.endMin) return pick(NEW_YORK, "new_york");
    return null;
}

function applySessionRangeBoxFromFxcmStatus() {
    if (!appState.chart || typeof appState.chart.setSessionRangeBox !== "function") {
        return;
    }
    const enabled = appState.chartState?.layersVisibility?.sessions !== false;
    if (!enabled) {
        appState.chart.setSessionRangeBox(null);
        return;
    }

    const payload = appState.lastFxcmStatus;
    const session = payload?.session;
    const sessionState = String(session?.state || "").toLowerCase();
    if (sessionState && sessionState !== "open") {
        appState.chart.setSessionRangeBox(null);
        return;
    }
    const symbols = Array.isArray(session?.symbols) ? session.symbols : [];

    // ВАЖЛИВО: часові межі сесій малюємо як і раніше (фіксований UTC-розклад),
    // не беремо market-open/close із fxcm:status.
    const window = computeActiveSessionWindowUtc(pickReferenceTimeSecForSessions());
    const from = Number(window?.from);
    const to = Number(window?.to);
    const sessionTag = String(window?.session || "").trim().toLowerCase();
    const symbol = String(appState.currentSymbol || "").toUpperCase();
    const tf = normalizeTimeframe(appState.currentTimeframe);

    const row = symbols.find((s) => {
        const sSym = String(s?.symbol || "").toUpperCase();
        const sTf = normalizeTimeframe(s?.tf);
        return sSym === symbol && sTf === tf;
    });
    const low = Number(row?.low);
    const high = Number(row?.high);

    if (!Number.isFinite(from) || !Number.isFinite(to) || !Number.isFinite(low) || !Number.isFinite(high)) {
        appState.chart.setSessionRangeBox(null);
        return;
    }

    appState.chart.setSessionRangeBox({ from, to, low, high, session: sessionTag });
}

function renderFromCache(symbol) {
    const state = appState.latestStates[symbol];
    if (state) {
        renderAll(state, { symbolOverride: symbol });
        return;
    }
    renderEmptyState("Немає даних по символу");
}

function openViewerSocket(symbol) {
    cleanupSocket();
    setStatus("connecting", `до ${symbol}`);
    const wsUrl = `${WS_BASE_URL}/smc-viewer/stream?symbol=${encodeURIComponent(symbol)}`;
    const ws = new WebSocket(wsUrl);
    appState.ws = ws;

    ws.onopen = () => {
        appState.reconnectAttempt = 0;
        clearReconnectTimer();
        setStatus("connected", `${symbol}`);
        console.info(`[WS] Connected to ${symbol}`);
    };

    ws.onmessage = (event) => {
        try {
            const payload = JSON.parse(event.data);
            if (!payload || !payload.viewer_state) {
                return;
            }
            const normalizedSymbol = String(payload.symbol || symbol).toUpperCase();
            appState.latestStates[normalizedSymbol] = payload.viewer_state;
            if (normalizedSymbol === appState.currentSymbol) {
                renderAll(payload.viewer_state, {
                    skipChartUpdate: true,
                    symbolOverride: normalizedSymbol,
                });
                maybeUpdateChartFromWs(normalizedSymbol, payload.viewer_state);
            }
        } catch (err) {
            console.warn("[WS] Не вдалося розпарсити повідомлення", err);
        }
    };

    ws.onclose = (event) => {
        setStatus("stale", `${symbol}: стрім втрачено`);
        const code = event?.code;
        const reason = event?.reason;
        const wasClean = event?.wasClean;
        console.warn(
            `[WS] Disconnected from ${symbol} (code=${code}, clean=${wasClean}, reason=${reason || ""})`,
        );
        scheduleReconnect();
    };

    ws.onerror = (err) => {
        console.error(
            "[WS] Помилка",
            err,
            `readyState=${ws.readyState}`,
            `url=${wsUrl}`,
        );
        setStatus("error", `${symbol}: WS помилка`);
        try {
            ws.close();
        } catch (_e) {
            // ignore
        }
    };
}

function cleanupSocket() {
    if (appState.ws) {
        appState.ws.onopen = null;
        appState.ws.onmessage = null;
        appState.ws.onclose = null;
        appState.ws.onerror = null;
        appState.ws.close();
        appState.ws = null;
    }
    clearReconnectTimer();
}

function scheduleReconnect() {
    if (!appState.currentSymbol) {
        return;
    }
    const attempt = Math.min(appState.reconnectAttempt, RECONNECT_DELAYS_MS.length - 1);
    const delay = RECONNECT_DELAYS_MS[attempt];
    appState.reconnectAttempt += 1;
    setStatus("reconnecting", `через ${delay / 1000}s`);
    console.info(`[WS] Reconnecting in ${delay} ms`);
    appState.reconnectTimer = setTimeout(() => {
        openViewerSocket(appState.currentSymbol || DEFAULT_SYMBOL.toUpperCase());
    }, delay);
}

function clearReconnectTimer() {
    if (appState.reconnectTimer) {
        clearTimeout(appState.reconnectTimer);
        appState.reconnectTimer = null;
    }
}

function setStatus(state, detail = "") {
    appState.uiStatusState = state;
    appState.uiStatusDetail = detail;

    const liveSuffix = FXCM_WS_ENABLED ? ` • LIVE: ${isFxcmLiveOn() ? "ON" : "OFF"}` : "";
    const label = buildStatusLabel(state, detail) + liveSuffix;

    applyStatusToPill(elements.wsStatus, state, label);
    applyStatusToPill(elements.mobile?.overviewWs, state, label);
}

function buildStatusLabel(state, detail) {
    switch (state) {
        case "connected":
            return "WS: Підключено";
        case "connecting":
            return detail ? `WS: Підключення (${detail})` : "WS: Підключення";
        case "reconnecting":
            return detail ? `WS: Перепідключення (${detail})` : "WS: Перепідключення";
        case "stale":
            return detail ? `WS: Без стріму (${detail})` : "WS: Без стріму";
        case "error":
            return detail ? `WS: Помилка (${detail})` : "WS: Помилка";
        case "loading":
            return detail ? `WS: ${detail}` : "WS: Завантаження...";
        default:
            return detail ? `WS: Відключено (${detail})` : "WS: Відключено";
    }
}

function applyStatusToPill(pill, state, text) {
    if (!pill) {
        return;
    }
    pill.classList.remove(
        "status-connected",
        "status-disconnected",
        "status-reconnecting",
        "status-stale",
    );
    if (state === "connected") {
        pill.classList.add("status-connected");
    } else if (state === "stale") {
        pill.classList.add("status-stale");
    } else if (state === "connecting" || state === "reconnecting" || state === "loading") {
        pill.classList.add("status-reconnecting");
    } else {
        pill.classList.add("status-disconnected");
    }
    pill.textContent = text;
}

function applyMarketStatus(fxcm) {
    const pill = elements.marketStatus;
    if (!pill) {
        return;
    }

    pill.classList.remove(
        "status-connected",
        "status-disconnected",
        "status-reconnecting",
        "status-stale",
    );

    const raw = String(fxcm?.market_state || "-");
    const normalized = raw.trim().toUpperCase();
    if (normalized.includes("OPEN")) {
        pill.classList.add("status-connected");
        pill.textContent = "FX: OPEN";
        return;
    }
    if (normalized.includes("CLOSED")) {
        pill.classList.add("status-stale");
        pill.textContent = "FX: CLOSED";
        return;
    }
    pill.classList.add("status-stale");
    pill.textContent = normalized && normalized !== "-" ? `FX: ${raw}` : "FX: -";
}

function isFxcmLiveOn() {
    const lastSeen = appState.fxcmLiveLastSeenMs;
    if (!Number.isFinite(lastSeen) || lastSeen <= 0) {
        return false;
    }
    return Date.now() - lastSeen <= FXCM_LIVE_STALE_MS;
}

function noteFxcmLiveSeen() {
    appState.fxcmLiveLastSeenMs = Date.now();

    if (appState.fxcmLiveOffTimer) {
        clearTimeout(appState.fxcmLiveOffTimer);
        appState.fxcmLiveOffTimer = null;
    }

    // Один таймер лише для вимкнення індикатора (не рендер-таймер графіка).
    appState.fxcmLiveOffTimer = setTimeout(() => {
        appState.fxcmLiveOffTimer = null;
        if (appState.uiStatusState) {
            setStatus(appState.uiStatusState, appState.uiStatusDetail);
        }
    }, FXCM_LIVE_STALE_MS + 50);

    if (appState.uiStatusState) {
        setStatus(appState.uiStatusState, appState.uiStatusDetail);
    }
}

function effectiveLagSeconds(serverLagSeconds) {
    // Якщо маємо live-стрім (FXCM WS), показуємо "свіжість" live подій,
    // а не server-side lag_seconds (який може відображати delayed complete-бари).
    if (FXCM_WS_ENABLED && isFxcmLiveOn()) {
        const lastSeen = Number(appState.fxcmLiveLastSeenMs);
        if (Number.isFinite(lastSeen) && lastSeen > 0) {
            return (Date.now() - lastSeen) / 1000;
        }
    }
    return serverLagSeconds;
}

function renderAll(state, options = {}) {
    const {
        skipChartUpdate = false,
        forceChartUpdate = true,
        symbolOverride = null,
    } = options;
    if (!state) {
        renderEmptyState("Немає даних");
        return;
    }
    updatePayloadMeta(state);
    renderSummary(state);
    renderEvents(state.structure?.events || []);
    renderOverviewEvents(state.structure?.events || []);
    renderOteZones(state.structure?.ote_zones || []);
    renderPools(state.liquidity?.pools || []);
    renderZones(mapZonesFromViewerState(state));
    if (!skipChartUpdate) {
        updateChartFromViewerState(state, {
            force: forceChartUpdate,
            symbolOverride,
        });
    }
}

function updatePayloadMeta(state) {
    const payloadTs = state.payload_ts || state.meta?.ts || null;
    const lagSecondsRaw = state.meta?.fxcm?.lag_seconds ?? state.fxcm?.lag_seconds ?? null;
    const lagSeconds = effectiveLagSeconds(lagSecondsRaw);

    appState.lastPayloadTs = payloadTs;
    appState.lastLagSeconds = lagSeconds;

    if (elements.payloadTs) {
        elements.payloadTs.textContent = payloadTs
            ? `ts: ${formatLocalDateTime(payloadTs)}`
            : "ts: -";
    }
    if (elements.payloadLag) {
        elements.payloadLag.textContent =
            lagSeconds !== null && lagSeconds !== undefined
                ? `${formatNumber(lagSeconds, 2)} s`
                : "-";
    }

    const debugUi = isDebugUiEnabled();

    if (elements.activeZones) {
        if (!debugUi) {
            elements.activeZones.hidden = true;
        } else {
            elements.activeZones.hidden = false;
            const active = Array.isArray(state?.zones?.raw?.active_zones)
                ? state.zones.raw.active_zones
                : [];
            elements.activeZones.textContent = `active_zones: ${active.length}/6`;
        }
    }

    if (elements.execStatus) {
        if (!debugUi) {
            elements.execStatus.hidden = true;
        } else {
            elements.execStatus.hidden = false;
            elements.execStatus.textContent = formatExecutionMetaLine(state || null);
        }
    }

    if (elements.tfHealth) {
        if (!TF_HEALTH_ENABLED || !debugUi) {
            elements.tfHealth.hidden = true;
        } else {
            elements.tfHealth.hidden = false;
            elements.tfHealth.textContent = formatTfMetaLine(state || null);
        }
    }
}

function formatExecutionMetaLine(state) {
    if (!state || typeof state !== "object") {
        return "EXEC: -";
    }

    const exec = state.execution && typeof state.execution === "object" ? state.execution : null;
    if (!exec) {
        return "EXEC: -";
    }

    const meta = exec.meta && typeof exec.meta === "object" ? exec.meta : null;
    const execEnabled = meta && "exec_enabled" in meta ? meta.exec_enabled === true : null;
    if (execEnabled === false) {
        return "EXEC: вимкнено";
    }

    const inPlay =
        (meta && meta.in_play === true) ||
        ("in_play" in exec && exec.in_play === true);

    const refObj = meta && meta.in_play_ref && typeof meta.in_play_ref === "object" ? meta.in_play_ref : null;
    const ref = refObj && refObj.ref ? String(refObj.ref) : null;

    const refDetail = (() => {
        if (!refObj || !ref) return "";
        const u = String(ref).toUpperCase();
        if (u === "TARGET") {
            const lvl = Number.isFinite(refObj.level) ? `@${formatNumber(refObj.level, 2)}` : "";
            return lvl;
        }
        if (u === "POI") {
            const zid = refObj.poi_zone_id ? `#${String(refObj.poi_zone_id)}` : "";
            return zid;
        }
        return "";
    })();

    const rawEvents = Array.isArray(exec.execution_events) ? exec.execution_events : [];
    const last = rawEvents.slice(-2);

    const shortType = (t) => {
        const u = String(t || "").toUpperCase();
        if (u === "RETEST_OK") return "RETEST";
        if (u === "MICRO_BOS") return "μBOS";
        if (u === "MICRO_CHOCH") return "μCHOCH";
        return u || "?";
    };

    const fmtEvt = (e) => {
        if (!e || typeof e !== "object") return "?";
        const t = shortType(e.event_type);
        const d = String(e.direction || "").toUpperCase();
        const arrow = d === "LONG" ? "↑" : d === "SHORT" ? "↓" : "";
        const lvl = Number.isFinite(e.level) ? `@${formatNumber(e.level, 2)}` : "";
        return `${t}${arrow}${lvl}`;
    };

    const eventsPart = last.length ? last.map(fmtEvt).join(" ") : null;
    const statePart = inPlay ? "в грі" : "idle";
    const refPart = ref ? `(${ref}${refDetail ? ":" + refDetail : ""})` : "";

    return eventsPart ? `EXEC: ${statePart}${refPart} | ${eventsPart}` : `EXEC: ${statePart}${refPart}`;
}

function formatTfMetaLine(state) {
    if (!state || typeof state !== "object") {
        return "TF: -";
    }

    const plan = state.tf_plan && typeof state.tf_plan === "object" ? state.tf_plan : null;
    const effective = Array.isArray(state.tf_effective) ? state.tf_effective : [];
    const gates = Array.isArray(state.gates) ? state.gates : [];
    const targets = Array.isArray(state?.liquidity?.targets) ? state.liquidity.targets : [];

    const planPart = formatTfPlanCompact(plan);
    const effPart = effective.length ? `ефф: ${effective.slice(0, 4).join(",")}${effective.length > 4 ? "…" : ""}` : "ефф: -";
    const gatesPart = formatGatesCompact(gates);

    const bars5m = Number.isFinite(state.bars_5m) ? Number(state.bars_5m) : null;
    const lagMs = Number.isFinite(state.lag_ms) ? Number(state.lag_ms) : null;
    const barsPart = bars5m !== null ? `5m:${bars5m}` : null;
    const lagPart = lagMs !== null ? `лаг:${formatNumber(lagMs / 1000.0, 1)}s` : null;

    const healthPart = formatTfHealthCompact(state.tf_health || null);
    const ltPart = formatLiquidityTargetsCompact(targets);

    const extras = [barsPart, lagPart, healthPart, ltPart].filter((v) => v);
    return `TF: ${planPart} | ${effPart} | ${gatesPart}${extras.length ? " | " + extras.join(" ") : ""}`;
}

function formatTfPlanCompact(plan) {
    if (!plan) {
        return "план:-";
    }
    const execTf = plan.tf_exec ? String(plan.tf_exec) : "-";
    const structTf = plan.tf_structure ? String(plan.tf_structure) : "-";
    const ctx = Array.isArray(plan.tf_context) ? plan.tf_context.map(String).join(",") : "-";
    return `план:${execTf}/${structTf}/${ctx}`;
}

function formatGatesCompact(gates) {
    if (!gates.length) {
        return "gates: OK";
    }
    const codes = gates
        .map((g) => (g && typeof g === "object" ? (g.code || g.id || null) : null))
        .filter((v) => v)
        .map(String);
    if (!codes.length) {
        return `gates: ${gates.length}`;
    }
    const uniq = [...new Set(codes)].slice(0, 3);
    return `gates: ${uniq.join(",")}${codes.length > uniq.length ? "…" : ""}`;
}

function formatLiquidityTargetsCompact(targets) {
    if (!targets.length) {
        return null;
    }
    const pick = (role) => targets.find((t) => t && typeof t === "object" && t.role === role);
    const i = pick("internal");
    const e = pick("external");

    const fmt = (t) => {
        if (!t) return null;
        const side = String(t.side || "").toLowerCase();
        const arrow = side === "above" ? "↑" : side === "below" ? "↓" : "";
        const price = Number.isFinite(t.price) ? formatNumber(t.price, 2) : "-";
        return `${String(t.role || "-").slice(0, 1)}${arrow}${price}`;
    };

    const parts = [fmt(i), fmt(e)].filter((v) => v);
    if (!parts.length) {
        return null;
    }
    return `LT:${parts.join(" ")}`;
}

function formatTfHealthCompact(tfHealth) {
    if (!tfHealth || typeof tfHealth !== "object") {
        return null;
    }

    const order = ["1m", "5m", "1h", "4h"];
    const parts = [];

    order.forEach((tf) => {
        const info = tfHealth[tf];
        if (!info || typeof info !== "object") {
            parts.push(`${tf}:-`);
            return;
        }

        const hasData = info.has_data === true;
        const bars = Number.isFinite(info.bars) ? Number(info.bars) : null;
        const lagMs = Number.isFinite(info.lag_ms) ? Number(info.lag_ms) : null;

        const status = hasData ? "ok" : "no";
        const barsPart = bars !== null ? `|${bars}` : "";
        const lagPart = lagMs !== null ? `|${formatNumber(lagMs / 1000.0, 1)}s` : "";
        parts.push(`${tf}:${status}${barsPart}${lagPart}`);
    });

    return `health:${parts.join(" ")}`;
}

function renderEmptyState(message = "Немає даних") {
    updatePayloadMeta({ payload_ts: null, meta: { fxcm: { lag_seconds: null } } });
    setText(elements.summary.symbol, "-");
    setText(elements.summary.price, "");
    setText(elements.summary.session, "-");
    setBadgeText("summary-trend", null);
    setBadgeText("summary-bias", null);
    setBadgeText("summary-range", null);
    setBadgeText("summary-amd", null);
    setText(elements.summary.market, "-");
    setText(elements.summary.process, "-");
    setText(elements.summary.lag, "-");

    applyMarketStatus(null);

    setText(elements.mobile?.overviewSymbol, "-");
    setText(elements.mobile?.overviewPrice, "");
    setText(elements.mobile?.overviewDelta, "-");
    setText(elements.mobile?.chartSymbol, "-");
    setText(elements.mobile?.chartPrice, "");

    if (elements.overviewEventsList) {
        elements.overviewEventsList.innerHTML = `<div class="overview-event"><div class="overview-event__time">-</div><div class="overview-event__title">${message}</div><div class="overview-event__price">-</div></div>`;
    }

    [
        { el: elements.tables.events, cols: 4 },
        { el: elements.tables.ote, cols: 4 },
        { el: elements.tables.pools, cols: 4 },
        { el: elements.tables.zones, cols: 4 },
    ].forEach(({ el, cols }) => {
        if (!el) return;
        el.innerHTML = `<tr class="empty-row"><td colspan="${cols}">${message}</td></tr>`;
    });
}

function renderOverviewEvents(events) {
    if (!elements.overviewEventsList) {
        return;
    }

    const filtered = (events || [])
        .filter((evt) => {
            const kind = (evt?.type || evt?.event_type || "").toUpperCase();
            return kind.includes("BOS") || kind.includes("CHOCH");
        })
        .slice(-5)
        .reverse();

    if (!filtered.length) {
        elements.overviewEventsList.innerHTML =
            '<div class="overview-event"><div class="overview-event__time">-</div><div class="overview-event__title">Немає подій</div><div class="overview-event__price">-</div></div>';
        return;
    }

    elements.overviewEventsList.innerHTML = filtered
        .map((evt) => {
            const direction = normalizeDirection(evt.direction || evt.dir);
            const title = `${evt.type || evt.event_type || "-"} ${direction}`.trim();
            return `<div class="overview-event">
    <div class="overview-event__time">${formatTime(evt.ts || evt.time || evt.timestamp)}</div>
    <div class="overview-event__title">${title}</div>
    <div class="overview-event__price">${formatNumber(evt.price ?? evt.level ?? evt.value)}</div>
  </div>`;
        })
        .join("");
}

function pushBarsToChart(ohlcvResponse) {
    if (!appState.chart || !ohlcvResponse || !Array.isArray(ohlcvResponse.bars)) {
        return;
    }
    // Діагностика: при HTTP-історії у нас немає OHLCV WS-івента, тож
    // ініціалізуємо VOL src одразу (і не чекаємо live-барів).
    const rawLast = ohlcvResponse.bars[ohlcvResponse.bars.length - 1] || null;
    if (rawLast) {
        appState.lastOhlcvVolSrc = pickVolumeSourceFromFxcmBar(rawLast) || "-";
        if (elements.summary?.volSrc) {
            setText(elements.summary.volSrc, appState.lastOhlcvVolSrc || "-");
        }
    }
    const bars = ohlcvResponse.bars.map(normalizeOhlcvBar).filter(Boolean);
    if (!bars.length) {
        return;
    }
    appState.chart.setBars(bars);
    rehydrateOverlays();
}

function maybeUpdateChartFromWs(symbol, viewerState) {
    if (!appState.chart || !viewerState) {
        return;
    }

    // Replay: якщо змінився cursor — перевантажуємо OHLCV, щоб свічки реально додавались.
    // Без цього графік вантажиться один раз і не "доростає".
    if (isReplayViewerState(viewerState)) {
        const cursorMs = extractReplayCursorMs(viewerState);
        if (cursorMs !== null) {
            appState.replay.lastCursorMs = cursorMs;
            const now = Date.now();
            const lastCursor = appState.replay.lastOhlcvCursorMs;
            const lastAt = Number(appState.replay.lastOhlcvRefetchAtMs || 0);
            const minInterval = Number(appState.replay.ohlcvRefetchMinIntervalMs || 200);
            const shouldRefetch =
                (lastCursor === null || lastCursor === undefined || cursorMs !== lastCursor) &&
                (now - lastAt >= minInterval);

            if (shouldRefetch && symbol === appState.currentSymbol) {
                appState.replay.lastOhlcvCursorMs = cursorMs;
                appState.replay.lastOhlcvRefetchAtMs = now;
                // Не await: щоб не блокувати WS onmessage.
                fetchOhlcv(symbol, appState.currentTimeframe);
            }
        }
    }

    const lastBar = extractLastBarFromViewerState(viewerState);
    if (lastBar) {
        appState.chart.updateLastBar(lastBar);
    }
    updateChartFromViewerState(viewerState, {
        symbolOverride: symbol,
        force: false,
    });
}

function openOhlcvSocket(symbol, timeframe) {
    if (!FXCM_WS_ENABLED || !FXCM_OHLCV_WS_BASE_URL) {
        cleanupOhlcvSocket();
        return;
    }
    if (!symbol) {
        cleanupOhlcvSocket();
        return;
    }

    const symUpper = String(symbol || "").toUpperCase();
    const tf = normalizeTimeframe(timeframe);
    const key = `${symUpper}:${tf}`;

    // Якщо WS вже відкритий для того ж key — не чіпаємо.
    if (
        appState.ohlcvWs &&
        appState.ohlcvWs.readyState === WebSocket.OPEN &&
        appState.ohlcvWs.__key === key
    ) {
        return;
    }

    cleanupOhlcvSocket();
    const wsUrl = `${FXCM_OHLCV_WS_BASE_URL}/fxcm/ohlcv?symbol=${encodeURIComponent(symUpper)}&tf=${encodeURIComponent(tf)}`;

    try {
        const ws = new WebSocket(wsUrl);
        ws.__key = key;
        appState.ohlcvWs = ws;

        ws.onopen = () => {
            appState.ohlcvReconnectAttempt = 0;
            clearOhlcvReconnectTimer();
            console.info(`[OHLCV_WS] Connected ${key}`);
        };

        ws.onmessage = (event) => {
            try {
                const payload = JSON.parse(event.data);
                handleOhlcvWsPayload(payload);
            } catch (err) {
                console.warn("[OHLCV_WS] Не вдалося розпарсити повідомлення", err);
            }
        };

        ws.onclose = () => {
            console.warn(`[OHLCV_WS] Disconnected ${key}`);
            scheduleOhlcvReconnect(symUpper, tf);
        };

        ws.onerror = (err) => {
            console.warn("[OHLCV_WS] Помилка", err);
            try {
                ws.close();
            } catch (_e) {
                // ignore
            }
        };
    } catch (err) {
        console.warn("[OHLCV_WS] Не вдалося створити WebSocket", err);
    }
}

function cleanupOhlcvSocket() {
    if (appState.ohlcvWs) {
        appState.ohlcvWs.onopen = null;
        appState.ohlcvWs.onmessage = null;
        appState.ohlcvWs.onclose = null;
        appState.ohlcvWs.onerror = null;
        try {
            appState.ohlcvWs.close();
        } catch (_e) {
            // ignore
        }
        appState.ohlcvWs = null;
    }
    appState.ohlcvLiveOpenTimeSec = null;
    appState.ohlcvLiveCandle = null;
    clearOhlcvReconnectTimer();
}

function timeframeToSeconds(tf) {
    const normalized = normalizeTimeframe(tf);
    switch (normalized) {
        case "5m":
            return 300;
        case "1m":
        default:
            return 60;
    }
}

function normalizeTickTimestampToSeconds(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) {
        return null;
    }

    const abs = Math.abs(num);
    if (abs > 1e14) {
        return num / 1e6;
    }
    if (abs > 1e12) {
        return num / 1e3;
    }
    return num;
}

function resetTickLiveState() {
    appState.tickLiveOpenTimeSec = null;
    appState.tickLiveCandle = null;
    appState.tickLiveVolumeCount = 0;
    appState.tickLastEmitMs = 0;
    if (appState.chart && typeof appState.chart.clearLiveBar === "function") {
        appState.chart.clearLiveBar();
    }
}

function openTickSocket(symbol) {
    if (!FXCM_WS_ENABLED || !FXCM_TICKS_WS_BASE_URL) {
        cleanupTickSocket();
        return;
    }
    if (!symbol) {
        cleanupTickSocket();
        return;
    }

    const symUpper = String(symbol || "").toUpperCase();
    const key = `${symUpper}`;

    if (
        appState.tickWs &&
        appState.tickWs.readyState === WebSocket.OPEN &&
        appState.tickWs.__key === key
    ) {
        return;
    }

    cleanupTickSocket();
    const wsUrl = `${FXCM_TICKS_WS_BASE_URL}/fxcm/ticks?symbol=${encodeURIComponent(symUpper)}`;

    try {
        const ws = new WebSocket(wsUrl);
        ws.__key = key;
        appState.tickWs = ws;

        ws.onopen = () => {
            appState.tickReconnectAttempt = 0;
            clearTickReconnectTimer();
            console.info(`[TICK_WS] Connected ${key}`);
        };

        ws.onmessage = (event) => {
            try {
                const payload = JSON.parse(event.data);
                handleTickWsPayload(payload);
            } catch (err) {
                console.warn("[TICK_WS] Не вдалося розпарсити повідомлення", err);
            }
        };

        ws.onclose = () => {
            console.warn(`[TICK_WS] Disconnected ${key}`);
            scheduleTickReconnect(symUpper);
        };

        ws.onerror = (err) => {
            console.warn("[TICK_WS] Помилка", err);
            try {
                ws.close();
            } catch (_e) {
                // ignore
            }
        };
    } catch (err) {
        console.warn("[TICK_WS] Не вдалося створити WebSocket", err);
    }
}

function openStatusSocket() {
    if (!FXCM_STATUS_WS_BASE_URL) {
        cleanupStatusSocket();
        return;
    }
    const key = "fxcm:status";
    if (
        appState.statusWs &&
        appState.statusWs.readyState === WebSocket.OPEN &&
        appState.statusWs.__key === key
    ) {
        return;
    }

    cleanupStatusSocket();
    const wsUrl = `${FXCM_STATUS_WS_BASE_URL}/fxcm/status`;
    try {
        const ws = new WebSocket(wsUrl);
        ws.__key = key;
        appState.statusWs = ws;

        ws.onopen = () => {
            appState.statusReconnectAttempt = 0;
            clearStatusReconnectTimer();
            console.info(`[STATUS_WS] Connected ${key}`);
        };

        ws.onmessage = (event) => {
            try {
                const payload = JSON.parse(event.data);
                handleStatusWsPayload(payload);
            } catch (err) {
                console.warn("[STATUS_WS] Не вдалося розпарсити повідомлення", err);
            }
        };

        ws.onclose = () => {
            console.warn(`[STATUS_WS] Disconnected ${key}`);
            scheduleStatusReconnect();
        };

        ws.onerror = (err) => {
            console.warn("[STATUS_WS] Помилка", err);
            try {
                ws.close();
            } catch (_e) {
                // ignore
            }
        };
    } catch (err) {
        console.warn("[STATUS_WS] Не вдалося створити WebSocket", err);
    }
}

function cleanupStatusSocket() {
    if (appState.statusWs) {
        appState.statusWs.onopen = null;
        appState.statusWs.onmessage = null;
        appState.statusWs.onclose = null;
        appState.statusWs.onerror = null;
        try {
            appState.statusWs.close();
        } catch (_e) {
            // ignore
        }
        appState.statusWs = null;
    }
    clearStatusReconnectTimer();
}

function scheduleStatusReconnect() {
    if (!FXCM_STATUS_WS_BASE_URL) {
        return;
    }

    if (appState.statusReconnectAttempt >= STATUS_RECONNECT_MAX_ATTEMPTS) {
        // Не спамимо нескінченними reconnect'ами у public, якщо /fxcm/status недоступний.
        return;
    }
    const attempt = Math.min(
        appState.statusReconnectAttempt,
        RECONNECT_DELAYS_MS.length - 1,
    );
    const delay = RECONNECT_DELAYS_MS[attempt];
    appState.statusReconnectAttempt += 1;
    clearStatusReconnectTimer();
    appState.statusReconnectTimer = setTimeout(() => {
        openStatusSocket();
    }, delay);
}

function clearStatusReconnectTimer() {
    if (appState.statusReconnectTimer) {
        clearTimeout(appState.statusReconnectTimer);
        appState.statusReconnectTimer = null;
    }
}

function handleStatusWsPayload(payload) {
    if (!payload) {
        return;
    }
    noteFxcmLiveSeen();
    appState.lastFxcmStatus = payload;
    applySessionRangeBoxFromFxcmStatus();
}

function cleanupTickSocket() {
    if (appState.tickWs) {
        appState.tickWs.onopen = null;
        appState.tickWs.onmessage = null;
        appState.tickWs.onclose = null;
        appState.tickWs.onerror = null;
        try {
            appState.tickWs.close();
        } catch (_e) {
            // ignore
        }
        appState.tickWs = null;
    }
    clearTickReconnectTimer();
}

function scheduleTickReconnect(symbolUpper) {
    if (!FXCM_WS_ENABLED) {
        return;
    }

    if (appState.tickReconnectAttempt >= FXCM_TICK_RECONNECT_MAX_ATTEMPTS) {
        // У проді не молотимо нескінченно, якщо проксі/конектор недоступні.
        return;
    }
    const currentSymbol = (appState.currentSymbol || "").toUpperCase();
    if (!symbolUpper || currentSymbol !== symbolUpper) {
        return;
    }
    const attempt = Math.min(
        appState.tickReconnectAttempt,
        TICK_RECONNECT_DELAYS_MS.length - 1,
    );
    const delay = TICK_RECONNECT_DELAYS_MS[attempt];
    appState.tickReconnectAttempt += 1;
    clearTickReconnectTimer();
    appState.tickReconnectTimer = setTimeout(() => {
        openTickSocket(symbolUpper);
    }, delay);
}

function clearTickReconnectTimer() {
    if (appState.tickReconnectTimer) {
        clearTimeout(appState.tickReconnectTimer);
        appState.tickReconnectTimer = null;
    }
}

function handleTickWsPayload(payload) {
    if (!payload) {
        return;
    }
    const symbol = String(payload.symbol || "").toUpperCase();
    const currentSymbol = (appState.currentSymbol || "").toUpperCase();
    if (!symbol || symbol !== currentSymbol) {
        return;
    }
    if (!appState.chart || typeof appState.chart.setLiveBar !== "function") {
        return;
    }

    const mid = Number(payload.mid);
    if (!Number.isFinite(mid)) {
        return;
    }

    // Тримаємо «живу» ціну для summary/мобільного UI між viewer_state снапшотами.
    appState.lastTickMid = mid;
    appState.lastTickSymbol = symbol;
    appState.lastTickTsMs = Date.now();

    // Оновлюємо UI-поля ціни одразу від тика, не чекаючи закриття свічки.
    // (renderSummary може перезаписати — тому він теж враховує lastTickMid якщо він свіжий)
    const midText = formatNumber(mid);
    setText(elements.summary?.price, midText);
    setText(elements.mobile?.overviewPrice, midText);
    setText(elements.mobile?.chartPrice, midText);

    // Тиковий стрім теж вважаємо "live" (для індикатора і лагу).
    noteFxcmLiveSeen();

    const tsSec =
        normalizeTickTimestampToSeconds(payload.tick_ts) ??
        normalizeTickTimestampToSeconds(payload.snap_ts) ??
        null;
    if (tsSec === null) {
        // Без timestamp не будуємо свічку з wall-clock (це породжує whitespace/"дірки").
        return;
    }
    const tfSec = timeframeToSeconds(appState.currentTimeframe);
    const candleStart = Math.floor(tsSec / tfSec) * tfSec;

    // ВАЖЛИВО: volume малюємо лише при закритті свічки (complete=true) і лише з "чистого" volume.
    // Тиковий стрім використовується тільки для live-ціни (OHLC), без підміни volume.
    if (appState.tickLiveOpenTimeSec !== candleStart) {
        appState.tickLiveVolumeCount = 0;
    }

    // throttle, щоб не "забивати" UI при частих тиках
    const nowMs = Date.now();
    const shouldRender =
        !appState.tickLastEmitMs || nowMs - appState.tickLastEmitMs >= 200;
    if (shouldRender) {
        appState.tickLastEmitMs = nowMs;
    }

    let candle = appState.tickLiveCandle;
    if (!candle || appState.tickLiveOpenTimeSec !== candleStart) {
        const baseFromOhlcv =
            appState.ohlcvLiveOpenTimeSec === candleStart && appState.ohlcvLiveCandle
                ? appState.ohlcvLiveCandle
                : null;
        const baseOpen =
            baseFromOhlcv?.open ??
            appState.lastCompleteCandle?.close ??
            mid;

        candle = {
            time: candleStart,
            open: Number(baseOpen),
            high: Math.max(Number(baseOpen), mid),
            low: Math.min(Number(baseOpen), mid),
            close: mid,
            volume: 0,
        };
        appState.tickLiveCandle = candle;
        appState.tickLiveOpenTimeSec = candleStart;
    } else {
        candle = {
            time: candle.time,
            open: candle.open,
            high: Math.max(candle.high, mid),
            low: Math.min(candle.low, mid),
            close: mid,
            volume: 0,
        };
        appState.tickLiveCandle = candle;
    }

    // Рендеримо live-бар; якщо throttle активний — пропустимо кадр.
    if (shouldRender) {
        appState.chart.setLiveBar(candle);
    }
}

function scheduleOhlcvReconnect(symbolUpper, tf) {
    if (!FXCM_WS_ENABLED) {
        return;
    }

    if (appState.ohlcvReconnectAttempt >= FXCM_OHLCV_RECONNECT_MAX_ATTEMPTS) {
        // У проді не молотимо нескінченно, якщо проксі/конектор недоступні.
        return;
    }
    const currentSymbol = (appState.currentSymbol || "").toUpperCase();
    const currentTf = normalizeTimeframe(appState.currentTimeframe);
    if (!symbolUpper || !tf || currentSymbol !== symbolUpper || currentTf !== tf) {
        return;
    }

    const attempt = Math.min(
        appState.ohlcvReconnectAttempt,
        OHLCV_RECONNECT_DELAYS_MS.length - 1,
    );
    const delay = OHLCV_RECONNECT_DELAYS_MS[attempt];
    appState.ohlcvReconnectAttempt += 1;

    clearOhlcvReconnectTimer();
    appState.ohlcvReconnectTimer = setTimeout(() => {
        openOhlcvSocket(symbolUpper, tf);
    }, delay);
}

function clearOhlcvReconnectTimer() {
    if (appState.ohlcvReconnectTimer) {
        clearTimeout(appState.ohlcvReconnectTimer);
        appState.ohlcvReconnectTimer = null;
    }
}

function handleOhlcvWsPayload(payload) {
    if (!payload || !Array.isArray(payload.bars)) {
        return;
    }
    const symbol = String(payload.symbol || "").toUpperCase();
    const tf = String(payload.tf || payload.timeframe || "").toLowerCase();
    const currentSymbol = (appState.currentSymbol || "").toUpperCase();
    const currentTf = normalizeTimeframe(appState.currentTimeframe);
    if (!symbol || !tf || symbol !== currentSymbol || tf !== currentTf) {
        return;
    }
    if (!appState.chart) {
        return;
    }

    for (const bar of payload.bars) {
        if (!bar) {
            continue;
        }
        const openTimeMs = Number(bar.open_time);
        if (!Number.isFinite(openTimeMs) || openTimeMs <= 0) {
            continue;
        }

        const candle = {
            time: Math.floor(openTimeMs / 1000),
            open: Number(bar.open),
            high: Number(bar.high),
            low: Number(bar.low),
            close: Number(bar.close),
            // Для UI це одна шкала під гістограму (volume або проксі-інтенсивність).
            volume: 0,
        };

        const isComplete = bar.complete !== false;
        // Діагностика: показуємо, звідки береться volume для *закритих* свічок.
        // Для live-свічок volume навмисно 0 (щоб не малювати "псевдо-обсяг"),
        // тому НЕ перезаписуємо VOL src на "-" — інакше він буде скакати при кожному тіку.
        if (isComplete) {
            appState.lastOhlcvVolSrc = pickVolumeSourceFromFxcmBar(bar) || "-";
        }
        if (
            !Number.isFinite(candle.open) ||
            !Number.isFinite(candle.high) ||
            !Number.isFinite(candle.low) ||
            !Number.isFinite(candle.close)
        ) {
            continue;
        }

        if (!isComplete) {
            noteFxcmLiveSeen();
            if (typeof appState.chart.setLiveBar === "function") {
                candle.volume = 0;
                appState.chart.setLiveBar(candle);
                appState.ohlcvLiveOpenTimeSec = candle.time;
                appState.ohlcvLiveCandle = candle;
            }
            continue;
        }

        candle.volume = pickVolumeFromFxcmBar(bar);

        appState.chart.updateLastBar(candle);
        appState.prevCompleteCandle = appState.lastCompleteCandle;
        appState.lastCompleteCandle = candle;
        renderMobileDelta();

        // Під флагом: одразу прибираємо live overlay, щоб лишилась фінальна свічка.
        // Якщо вимкнено — live overlay зникне природно при наступному open_time.
        if (FXCM_APPLY_COMPLETE_ENABLED) {
            if (
                appState.ohlcvLiveOpenTimeSec !== null &&
                appState.ohlcvLiveOpenTimeSec === candle.time &&
                typeof appState.chart.clearLiveBar === "function"
            ) {
                appState.chart.clearLiveBar();
                appState.ohlcvLiveOpenTimeSec = null;
                appState.ohlcvLiveCandle = null;
            }

            if (
                appState.tickLiveOpenTimeSec !== null &&
                appState.tickLiveOpenTimeSec === candle.time
            ) {
                appState.tickLiveOpenTimeSec = null;
                appState.tickLiveCandle = null;
            }
        }
    }
}

function updateChartFromViewerState(state, options = {}) {
    if (!appState.chart || !state) {
        return;
    }
    const { force = false, symbolOverride = null } = options;
    const symbol = (symbolOverride || state.symbol || appState.currentSymbol || DEFAULT_SYMBOL).toUpperCase();
    const seqKey = getOverlaySeqKey(state);
    if (!force && seqKey !== null) {
        const previousSeq = appState.chartState.overlaySeqBySymbol[symbol];
        if (previousSeq !== undefined && previousSeq === String(seqKey)) {
            return;
        }
    }

    const events = mapEventsFromViewerState(state);
    const execEvents = mapExecutionEventsFromViewerState(state);
    const levelsSelectedV1 = mapLevelsSelectedV1FromViewerState(state);
    const ranges = mapRangesFromViewerState(state);
    const oteZones = mapOteZonesFromViewerState(state);
    const zones = mapZonesFromViewerState(state);
    const layersVisibility = {
        events: true,
        pools: true,
        ranges: true,
        ote: true,
        zones: true,
        sessions: true,
        ...(appState.chartState.layersVisibility || {}),
    };

    appState.chart.setEvents(layersVisibility.events ? events : []);
    if (typeof appState.chart.setExecutionEvents === "function") {
        appState.chart.setExecutionEvents(layersVisibility.events ? execEvents : []);
    }

    const selectedRenderTf = pickSelectedLevelsRenderTf(appState.currentTimeframe);

    // Крок 4.1: one-layer truth у UI. Legacy-рівні з pools більше НЕ рендеримо.
    // Якщо selected тимчасово порожній — показуємо 0 рівнів (але без «подвійної правди»).
    appState.chart.setLiquidityPools([]);
    if (typeof appState.chart.setLevelsSelectedV1 === "function") {
        appState.chart.setLevelsSelectedV1(
            layersVisibility.pools ? levelsSelectedV1 : [],
            selectedRenderTf,
        );
    }
    appState.chart.setRanges(layersVisibility.ranges ? ranges : []);
    appState.chart.setOteZones(layersVisibility.ote ? oteZones : []);
    appState.chart.setZones(layersVisibility.zones ? zones : []);
    if (typeof appState.chart.setSessionsEnabled === "function") {
        appState.chart.setSessionsEnabled(layersVisibility.sessions !== false);
    }

    if (seqKey !== null) {
        appState.chartState.overlaySeqBySymbol[symbol] = String(seqKey);
    } else if (force) {
        appState.chartState.overlaySeqBySymbol[symbol] = `force-${Date.now()}`;
    }
}

function renderSummary(state) {
    const struct = state.structure || {};
    const fxcm = state.meta?.fxcm || state.fxcm || {};
    const scenario = state.scenario || {};

    const scenarioNameByCode = {
        "4_2": "Продовження вниз",
        "4_3": "Інвалідація шорта → вгору",
        "UNCLEAR": "Невизначено",
    };

    const scenarioArrow = (direction) => {
        const d = String(direction || "").toUpperCase();
        if (d === "LONG") return "↑";
        if (d === "SHORT") return "↓";
        return "·";
    };

    const formatScenarioHuman = (id) => {
        const code = String(id || "").toUpperCase();
        if (scenarioNameByCode[code]) {
            return scenarioNameByCode[code];
        }
        return code ? `Невідомий режим (${code})` : "-";
    };

    const formatScenarioHumanWithConfidence = (id, conf) => {
        const name = formatScenarioHuman(id);
        const c = Number(conf);
        if (Number.isFinite(c) && c > 0) {
            return `${name} · ${formatNumber(c, 2)}`;
        }
        return name;
    };

    const nowMs = Date.now();
    const tickIsFresh =
        appState.lastTickTsMs && nowMs - appState.lastTickTsMs <= FXCM_LIVE_STALE_MS;
    const tickSymbolOk =
        (appState.lastTickSymbol || "") === String(state.symbol || "").toUpperCase();

    const rawPrice = tickIsFresh && tickSymbolOk ? Number(appState.lastTickMid) : Number(state.price);
    const priceText = Number.isFinite(rawPrice) ? formatNumber(rawPrice) : "";

    setText(elements.summary.symbol, state.symbol || "-");
    setText(elements.summary.price, priceText);
    setText(elements.summary.session, state.session || "-");
    setBadgeText("summary-trend", struct.trend || "UNKNOWN");
    setBadgeText("summary-bias", struct.bias || "UNKNOWN");

    renderStage6Panel(scenario, {
        formatScenarioHuman,
        scenarioArrow,
        formatScenarioHumanWithConfidence,
    });

    // У згорнутому режимі кнопка показує компактний "маркер режиму" (стрілка + confidence).
    const btn = elements.stage6?.toggleBtn;
    if (btn) {
        if (appState.ui.stage6Collapsed) {
            const arrow = scenarioArrow(scenario.direction);
            const c = Number(scenario.confidence);
            const conf = Number.isFinite(c) && c > 0 ? formatNumber(c, 2) : "";
            const compact = `${arrow}${conf}`.trim();
            btn.textContent = compact || "S6";
            const code = String(scenario.scenario_id || "").toUpperCase();
            const name = formatScenarioHuman(code);
            btn.title = code ? `SMC Контекст: ${name} (код: ${code})` : `SMC Контекст: ${name}`;
        } else {
            btn.textContent = "S6";
            btn.title = "Згорнути SMC контекст";
        }
    }

    setBadgeText("summary-range", struct.range_state || "UNKNOWN");
    setBadgeText("summary-amd", state.liquidity?.amd_phase || "UNKNOWN");
    setText(elements.summary.market, fxcm.market_state || "-");
    setText(elements.summary.process, fxcm.process_state || fxcm.price_state || "-");
    const lagSeconds = effectiveLagSeconds(fxcm.lag_seconds ?? null);
    setText(elements.summary.lag, formatNumber(lagSeconds, 2));
    setText(elements.summary.volSrc, appState.lastOhlcvVolSrc || "-");

    applyMarketStatus(fxcm);

    const symbolText = state.symbol || appState.currentSymbol || "-";
    setText(elements.mobile?.overviewSymbol, symbolText);
    setText(elements.mobile?.chartSymbol, symbolText);
    setText(elements.mobile?.overviewPrice, priceText);
    setText(elements.mobile?.chartPrice, priceText);
    renderMobileDelta();
}

function renderStage6Panel(scenario, helpers) {
    const el = elements.stage6;
    if (!el) {
        return;
    }

    const emptyText = "немає даних";
    const setValue = (node, value, opts = {}) => {
        if (!node) return;
        const v = value == null ? "" : String(value);
        const text = v.trim() ? v : (opts.emptyText || emptyText);
        node.textContent = text;
        if (opts.title !== undefined) {
            node.title = String(opts.title || "");
        }
    };

    const minutesFromTf = (tf) => {
        const s = String(tf || "").toLowerCase();
        if (!s) return null;
        if (s.endsWith("m")) {
            const n = Number(s.slice(0, -1));
            return Number.isFinite(n) ? n : null;
        }
        if (s.endsWith("h")) {
            const n = Number(s.slice(0, -1));
            return Number.isFinite(n) ? n * 60 : null;
        }
        return null;
    };

    const formatDurationSec = (sec) => {
        const s = Number(sec);
        if (!Number.isFinite(s) || s <= 0) return "0s";
        const total = Math.floor(s);
        const m = Math.floor(total / 60);
        const r = total % 60;
        if (m <= 0) return `${r}s`;
        return `${m}m ${r}s`;
    };

    const formatLevel = (v) => {
        const x = Number(v);
        if (!Number.isFinite(x)) return null;
        return formatNumber(x, 2);
    };

    const formatAtr = (v) => {
        const x = Number(v);
        if (!Number.isFinite(x)) return null;
        return formatNumber(x, 1);
    };

    const whyList = Array.isArray(scenario?.why) ? scenario.why.map((v) => String(v)) : [];
    const rawGates = Array.isArray(scenario?.raw_gates) ? scenario.raw_gates.map((v) => String(v)) : [];
    const reason =
        scenario?.unclear_reason || scenario?.raw_unclear_reason || rawGates.join(", ") || "";

    const rawId = String(scenario?.raw_scenario_id || "").toUpperCase();
    const stableId = String(scenario?.scenario_id || "").toUpperCase();
    const hasSmc = Boolean(scenario?.raw_key_levels?.smc || scenario?.key_levels?.smc);
    const isNoContext =
        !scenario ||
        (!stableId && !rawId) ||
        (!hasSmc && (stableId === "UNCLEAR" || rawId === "UNCLEAR" || rawGates.length > 0 || reason));

    if (el.noData && el.grid) {
        if (isNoContext) {
            const gateText = rawGates.length ? rawGates.join(", ") : (reason ? String(reason) : "немає даних");
            el.noData.textContent = `Немає контексту: ${gateText}`;
            el.noData.removeAttribute("hidden");
            el.grid.setAttribute("hidden", "");
        } else {
            el.noData.setAttribute("hidden", "");
            el.grid.removeAttribute("hidden");
        }
    }

    // Режим (stable) + код у tooltip.
    const stableName = helpers?.formatScenarioHuman ? helpers.formatScenarioHuman(stableId) : (stableId || emptyText);
    const stableConf = Number(scenario?.confidence);
    const stableConfText = Number.isFinite(stableConf) ? formatNumber(stableConf, 2) : "";
    const stableDirArrow = helpers?.scenarioArrow ? helpers.scenarioArrow(scenario?.direction) : "·";
    const modeText = stableId ? `${stableName}` : emptyText;
    setValue(el.mode, modeText, { title: stableId ? `код: ${stableId}` : "" });

    // stable_conf + прогрес.
    setValue(el.stableConf, Number.isFinite(stableConf) ? stableConfText : "", { emptyText });
    if (el.stableConfBar) {
        const pct = Number.isFinite(stableConf) ? Math.max(0, Math.min(1, stableConf)) * 100 : 0;
        el.stableConfBar.style.width = `${pct}%`;
        el.stableConfBar.title = Number.isFinite(stableConf) ? `stable_conf=${stableConfText}` : "";
    }

    // RAW line: raw label + conf + unclear_reason (якщо є).
    const rawName = helpers?.formatScenarioHuman ? helpers.formatScenarioHuman(rawId) : (rawId || emptyText);
    const rawConf = Number(scenario?.raw_confidence);
    const rawConfText = Number.isFinite(rawConf) ? formatNumber(rawConf, 2) : "";
    const rawReason = scenario?.raw_unclear_reason || scenario?.unclear_reason || "";
    const rawParts = [];
    if (rawId) rawParts.push(`${rawName}`);
    if (rawConfText) rawParts.push(`conf ${rawConfText}`);
    if (rawReason) rawParts.push(String(rawReason));
    setValue(el.rawLine, rawParts.join(" · "), { title: rawId ? `код: ${rawId}` : "" });

    // Pending: показуємо прогрес confirm (2/3).
    const pendingId = scenario?.pending_id;
    const pendingCount = Number(scenario?.pending_count);
    const anti = scenario?.anti_flip || {};
    const evalBlock = scenario?.last_eval || {};
    const confirmReq = Number(anti?.confirm_required ?? evalBlock?.confirm_required);
    if (pendingId && Number.isFinite(pendingCount) && pendingCount > 0 && Number.isFinite(confirmReq) && confirmReq > 0) {
        const pCode = String(pendingId || "").toUpperCase();
        const pName = helpers?.formatScenarioHuman ? helpers.formatScenarioHuman(pCode) : pCode;
        setValue(el.pendingLine, `${pName} (${pendingCount}/${confirmReq})`, { title: pCode ? `код: ${pCode}` : "" });
    } else {
        setValue(el.pendingLine, "", { emptyText: "—" });
    }

    // Чому: why[] (коротко) + tooltip повністю.
    const whyShort = whyList.filter((v) => v).slice(0, 4).join(" · ");
    setValue(el.why, whyShort, {
        emptyText: reason ? `Немає контексту: ${String(reason)}` : emptyText,
        title: whyList.filter((v) => v).slice(0, 12).join("\n"),
    });

    // SMC dict.
    const smc = scenario?.raw_key_levels?.smc || scenario?.key_levels?.smc || null;
    const htf = smc?.htf || {};
    const facts = smc?.facts || {};
    const sweep = facts?.sweep || null;
    const hold = facts?.hold || {};
    const failedHold = facts?.failed_hold || {};

    // HTF DR/PD/ATR.
    const drLow = formatLevel(htf?.dr_low);
    const drHigh = formatLevel(htf?.dr_high);
    const drMid = formatLevel(htf?.dr_mid);
    const drTf = String(htf?.dr_tf || "");
    const drText = drLow && drHigh ? `${drLow}–${drHigh}${drMid ? ` (mid ${drMid})` : ""}` : "";
    setValue(el.htfDr, drText, {
        emptyText: rawGates.length ? `немає даних (${rawGates.join(", ")})` : emptyText,
        title: drTf ? `DR TF: ${drTf}` : "",
    });
    setValue(el.htfPd, htf?.pd ? String(htf.pd) : "", { emptyText, title: "" });
    const atrText = formatAtr(htf?.atr14);
    const atrTf = String(htf?.atr_tf || "");
    setValue(el.htfAtr, atrText ? atrText : "", { emptyText, title: atrTf ? `ATR TF: ${atrTf}` : "" });

    // Sweep.
    const tfMin = minutesFromTf(appState.currentTimeframe);
    if (sweep && sweep.level != null) {
        const lvl = formatLevel(sweep.level) || "?";
        const side = String(sweep.side || "").toUpperCase();
        const sideArrow = side === "DOWN" ? "↓" : side === "UP" ? "↑" : "·";
        const typ = String(sweep.pool_type || sweep.type || "?");
        const ageBars = Number(sweep.age_bars);
        let ageText = "";
        if (Number.isFinite(ageBars) && ageBars >= 0) {
            ageText = `age ${ageBars} bars`;
            if (Number.isFinite(tfMin)) {
                ageText += ` (~${ageBars * tfMin}m)`;
            }
        }
        const text = `sweep: ${typ} ${sideArrow} @ ${lvl}${ageText ? ` (${ageText})` : ""}`;
        setValue(el.sweep, text, { title: "" });
    } else {
        setValue(el.sweep, "", { emptyText: "sweep: —" });
    }

    // Hold / Failed hold.
    const holdK = hold?.k;
    const holdOk = hold?.ok;
    const holdLvl = formatLevel(hold?.level_up);
    if (holdOk === true || holdOk === false) {
        const yesNo = holdOk ? "YES" : "NO";
        const kText = holdK != null ? `k=${holdK}` : "k=?";
        const lvlText = holdLvl ? ` @ ${holdLvl}` : "";
        setValue(el.hold, `hold_above(range_high,${kText}): ${yesNo}${lvlText}`);
    } else {
        setValue(el.hold, "", { emptyText: "hold_above(range_high): немає даних" });
    }

    const failedOk = failedHold?.ok;
    const failedLvl = formatLevel(failedHold?.level_up);
    if (failedOk === true || failedOk === false) {
        const yesNo = failedOk ? "YES" : "NO";
        const lvlText = failedLvl ? ` @ ${failedLvl}` : "";
        setValue(el.failedHold, `failed_hold: ${yesNo}${lvlText}`);
    } else {
        setValue(el.failedHold, "", { emptyText: "failed_hold: немає даних" });
    }

    // Targets / POI.
    const targets = Array.isArray(smc?.targets_near) ? smc.targets_near : [];
    const poi = Array.isArray(smc?.poi_active) ? smc.poi_active : [];
    const briefTargets = targets
        .slice(0, 2)
        .map((x) => {
            const k = x?.type ?? x?.kind ?? "?";
            const lvl = formatLevel(x?.level);
            return lvl ? `${String(k)}@${lvl}` : String(k);
        })
        .join(", ");
    setValue(el.targets, `targets: ${targets.length}${briefTargets ? ` (${briefTargets})` : ""}`, {
        emptyText: "targets: немає даних",
        title: targets
            .slice(0, 6)
            .map((x) => {
                const k = x?.type ?? x?.kind ?? "?";
                const lvl = formatLevel(x?.level);
                const d = Number(x?.dist_atr);
                const dText = Number.isFinite(d) ? `dist ${formatNumber(d, 2)} ATR` : "";
                return `${String(k)}@${lvl || "?"}${dText ? ` · ${dText}` : ""}`;
            })
            .join("\n"),
    });

    const poiBrief = poi
        .slice(0, 1)
        .map((x) => {
            const k = x?.type ?? x?.kind ?? "?";
            const score = Number(x?.score ?? x?.poi_score);
            const d = Number(x?.dist_atr);
            const scoreText = Number.isFinite(score) ? `score ${formatNumber(score, 2)}` : "";
            const dText = Number.isFinite(d) ? `dist ${formatNumber(d, 2)} ATR` : "";
            return [String(k), scoreText, dText].filter(Boolean).join(", ");
        })
        .join(" ");
    setValue(el.poi, `POI: ${poi.length}${poiBrief ? ` (${poiBrief})` : ""}`, {
        emptyText: "POI: немає даних",
        title: poi
            .slice(0, 6)
            .map((x) => {
                const k = x?.type ?? x?.kind ?? "?";
                const score = Number(x?.score ?? x?.poi_score);
                const d = Number(x?.dist_atr);
                const scoreText = Number.isFinite(score) ? `score ${formatNumber(score, 2)}` : "";
                const dText = Number.isFinite(d) ? `dist ${formatNumber(d, 2)} ATR` : "";
                return [String(k), scoreText, dText].filter(Boolean).join(" · ");
            })
            .join("\n"),
    });

    // Anti-flip.
    const ttlLeft = Number(anti?.ttl_left_sec ?? evalBlock?.ttl_left_sec);
    const ttlHuman = Number.isFinite(ttlLeft) && ttlLeft > 0 ? `TTL: ${formatDurationSec(ttlLeft)}` : "TTL: ок";
    const reasonText = String(anti?.reason || "").trim();
    setValue(el.antiflipTtl, reasonText ? `${ttlHuman} · ${reasonText}` : ttlHuman, { emptyText });

    const blocked = Array.isArray(anti?.blocked) ? anti.blocked.map((v) => String(v)) : [];
    const reqConf = Number(anti?.required_confidence ?? evalBlock?.required_confidence);
    const reqConfText = Number.isFinite(reqConf) ? `need_conf=${formatNumber(reqConf, 2)}` : "";
    const delta = Number(evalBlock?.switch_delta ?? evalBlock?.delta ?? evalBlock?.delta_score);
    const deltaText = Number.isFinite(delta) ? `delta=${formatNumber(delta, 2)}` : "";
    const blockedParts = [blocked.length ? `reason=${blocked.join("|")}` : "", reqConfText, deltaText].filter(Boolean);
    setValue(el.antiflipBlocked, blockedParts.length ? `blocked: ${blockedParts.join(" | ")}` : "blocked: —", { emptyText: "blocked: немає даних" });

    const override =
        anti?.strong_override ||
        anti?.override ||
        anti?.override_reason ||
        evalBlock?.strong_override ||
        "";
    setValue(el.antiflipOverride, override ? `override: ${String(override)}` : "override: —", { emptyText: "override: немає даних" });
}

function renderMobileDelta() {
    const prev = Number(appState.prevCompleteCandle?.close);
    const curr = Number(appState.lastCompleteCandle?.close);
    if (!elements.mobile?.overviewDelta) {
        return;
    }
    if (!Number.isFinite(prev) || !Number.isFinite(curr) || prev === 0) {
        elements.mobile.overviewDelta.textContent = "-";
        return;
    }
    const deltaPct = ((curr - prev) / prev) * 100;
    const sign = deltaPct > 0 ? "+" : "";
    elements.mobile.overviewDelta.textContent = `${sign}${formatNumber(deltaPct, 2)}%`;
}

function syncTimeframeSelect(tf) {
    if (elements.timeframeSelect) {
        elements.timeframeSelect.value = tf;
    }
}

function normalizeTimeframe(tf) {
    const value = String(tf || OHLCV_DEFAULT_TF).toLowerCase();
    return AVAILABLE_TIMEFRAMES.includes(value) ? value : OHLCV_DEFAULT_TF;
}

function persistSymbol(symbol) {
    const storage = getStorage();
    if (!storage || !symbol) {
        return;
    }
    try {
        storage.setItem(STORAGE_KEYS.symbol, String(symbol).toUpperCase());
    } catch (err) {
        console.warn("[UI] Не вдалося зберегти символ у localStorage", err);
    }
}

function persistTimeframe(tf) {
    const storage = getStorage();
    if (!storage || !tf) {
        return;
    }
    try {
        storage.setItem(STORAGE_KEYS.timeframe, normalizeTimeframe(tf));
    } catch (err) {
        console.warn("[UI] Не вдалося зберегти таймфрейм у localStorage", err);
    }
}

function loadPersistedPreferences() {
    const storage = getStorage();
    if (!storage) {
        return;
    }
    try {
        const storedTf = storage.getItem(STORAGE_KEYS.timeframe);
        if (storedTf) {
            const normalizedTf = normalizeTimeframe(storedTf);
            appState.currentTimeframe = normalizedTf;
            if (normalizedTf !== storedTf) {
                storage.setItem(STORAGE_KEYS.timeframe, normalizedTf);
            }
        }
        const storedSymbol = storage.getItem(STORAGE_KEYS.symbol);
        if (storedSymbol) {
            const normalizedSymbol = String(storedSymbol).toUpperCase();
            appState.preferredSymbol = normalizedSymbol;
            if (normalizedSymbol !== storedSymbol) {
                storage.setItem(STORAGE_KEYS.symbol, normalizedSymbol);
            }
        }
        const storedHeight = storage.getItem(STORAGE_KEYS.chartHeight);
        if (storedHeight) {
            const numericHeight = Number(storedHeight);
            if (Number.isFinite(numericHeight)) {
                appState.chartUi.heightPx = clampChartHeight(numericHeight);
            }
        }

        const storedCollapsed = storage.getItem(STORAGE_KEYS.summaryCollapsed);
        if (storedCollapsed != null) {
            const raw = String(storedCollapsed).trim().toLowerCase();
            appState.ui.summaryCollapsed = raw === "1" || raw === "true" || raw === "yes" || raw === "on";
        }

        const storedStage6Collapsed = storage.getItem(STORAGE_KEYS.stage6Collapsed);
        if (storedStage6Collapsed != null) {
            const raw = String(storedStage6Collapsed).trim().toLowerCase();
            appState.ui.stage6Collapsed = raw === "1" || raw === "true" || raw === "yes" || raw === "on";
        }

        const storedLayers = storage.getItem(STORAGE_KEYS.layersVisibility);
        if (storedLayers) {
            try {
                const parsed = JSON.parse(storedLayers);
                if (parsed && typeof parsed === "object") {
                    const allowedKeys = ["events", "pools", "ranges", "ote", "zones", "sessions"];
                    const next = { ...(appState.chartState.layersVisibility || {}) };
                    allowedKeys.forEach((k) => {
                        if (Object.prototype.hasOwnProperty.call(parsed, k)) {
                            next[k] = Boolean(parsed[k]);
                        }
                    });
                    appState.chartState.layersVisibility = next;
                }
            } catch (_e) {
                // Якщо JSON пошкоджений — ігноруємо.
            }
        }

        const storedZoneLimitMode = storage.getItem(STORAGE_KEYS.zoneLimitMode);
        if (storedZoneLimitMode) {
            appState.chartState.zoneLimitMode = normalizeZoneLimitMode(storedZoneLimitMode);
            if (appState.chartState.zoneLimitMode !== storedZoneLimitMode) {
                storage.setItem(STORAGE_KEYS.zoneLimitMode, appState.chartState.zoneLimitMode);
            }
        }
    } catch (err) {
        console.warn("[UI] Не вдалося зчитати налаштування з localStorage", err);
    }
}

function persistLayersVisibility() {
    const storage = getStorage();
    if (!storage) {
        return;
    }
    try {
        const value = appState.chartState?.layersVisibility || {};
        storage.setItem(STORAGE_KEYS.layersVisibility, JSON.stringify(value));
    } catch (err) {
        console.warn("[UI] Не вдалося зберегти шари у localStorage", err);
    }
}

function persistSummaryCollapsed(value) {
    const storage = getStorage();
    if (!storage) {
        return;
    }
    try {
        storage.setItem(STORAGE_KEYS.summaryCollapsed, value ? "1" : "0");
    } catch (err) {
        console.warn("[UI] Не вдалося зберегти стан підсумку", err);
    }
}

function persistStage6Collapsed(value) {
    const storage = getStorage();
    if (!storage) {
        return;
    }
    try {
        storage.setItem(STORAGE_KEYS.stage6Collapsed, value ? "1" : "0");
    } catch (err) {
        console.warn("[UI] Не вдалося зберегти стан Stage6", err);
    }
}

function applySummaryCollapsed(collapsed, options = {}) {
    const next = Boolean(collapsed);
    appState.ui.summaryCollapsed = next;
    document.body.classList.toggle("summary-collapsed", next);

    const label = next ? "Показати підсумок" : "Згорнути підсумок";
    const title = next ? "Показати підсумок" : "Згорнути підсумок";

    const buttons = [
        elements.summaryToggleBtn,
        elements.mobileSummaryToggle?.overview,
        elements.mobileSummaryToggle?.chart,
    ].filter(Boolean);

    buttons.forEach((btn) => {
        btn.setAttribute("aria-pressed", next ? "true" : "false");
        btn.setAttribute("aria-label", label);
        btn.setAttribute("title", title);
    });

    if (options.persist !== false) {
        persistSummaryCollapsed(next);
    }
}

function applyStage6Collapsed(collapsed, options = {}) {
    const next = Boolean(collapsed);
    appState.ui.stage6Collapsed = next;

    const panel = elements.stage6?.panel;
    if (panel) {
        panel.classList.toggle("stage6-panel--collapsed", next);
        if (next) {
            panel.setAttribute("hidden", "");
        } else {
            panel.removeAttribute("hidden");
        }
    }

    const btn = elements.stage6?.toggleBtn;
    if (btn) {
        const label = next ? "Показати SMC контекст" : "Згорнути SMC контекст";
        btn.setAttribute("aria-pressed", next ? "true" : "false");
        btn.setAttribute("aria-label", label);
        btn.setAttribute("title", label);
    }

    if (options.persist !== false) {
        persistStage6Collapsed(next);
    }
}

function getStorage() {
    if (storageUnavailable) {
        return null;
    }
    if (cachedStorage) {
        return cachedStorage;
    }
    try {
        if (typeof window !== "undefined" && window.localStorage) {
            cachedStorage = window.localStorage;
            return cachedStorage;
        }
    } catch (err) {
        storageUnavailable = true;
        console.warn("[UI] localStorage недоступний", err);
    }
    storageUnavailable = true;
    return null;
}

function renderOhlcvSummary(ohlcv) {
    const tfEl = document.getElementById("ohlcv-tf");
    const countEl = document.getElementById("ohlcv-count");
    const lastTimeEl = document.getElementById("ohlcv-last-time");
    const lastCloseEl = document.getElementById("ohlcv-last-close");
    if (!tfEl || !countEl || !lastTimeEl || !lastCloseEl) {
        return;
    }

    if (!ohlcv || !Array.isArray(ohlcv.bars) || ohlcv.bars.length === 0) {
        tfEl.textContent = "-";
        countEl.textContent = "0";
        lastTimeEl.textContent = "-";
        lastCloseEl.textContent = "-";
        return;
    }

    const bars = ohlcv.bars;
    const lastBar = bars[bars.length - 1];
    tfEl.textContent = ohlcv.timeframe || "-";
    countEl.textContent = String(bars.length);
    lastTimeEl.textContent = formatIsoDateTime(lastBar.time);
    lastCloseEl.textContent = formatNumber(lastBar.close, 2);
}

function rehydrateOverlays() {
    if (!appState.chart || !appState.currentSymbol) {
        return;
    }
    const state = appState.latestStates[appState.currentSymbol];
    if (state) {
        updateChartFromViewerState(state, {
            force: true,
            symbolOverride: appState.currentSymbol,
        });
    }
}

function initChartLayoutControls() {
    if (elements.chartSizeToggleBtn) {
        elements.chartSizeToggleBtn.addEventListener("click", () => {
            const nextValue =
                appState.chartUi.heightPx >= CHART_HEIGHT_LARGE
                    ? CHART_HEIGHT_NORMAL
                    : CHART_HEIGHT_LARGE;
            applyChartHeight(nextValue);
        });
    }
    applyChartHeight(appState.chartUi.heightPx, { persist: false });
    updateFullscreenButtonState();
    if (elements.chartFullscreenBtn) {
        elements.chartFullscreenBtn.addEventListener("click", toggleChartFullscreen);
    }
    document.addEventListener("keydown", handleFullscreenKeydown);
    setHeightControlEnabled(true);
}

function applyChartHeight(value, options = {}) {
    const nextValue = clampChartHeight(value);
    appState.chartUi.heightPx = nextValue;
    document.documentElement.style.setProperty("--chart-height", `${nextValue}px`);
    scheduleChartResize();
    updateChartSizeToggleState(nextValue);
    if (options.persist !== false) {
        persistChartHeight(nextValue);
    }
}

function setHeightControlEnabled(enabled) {
    if (elements.chartSizeToggleBtn) {
        elements.chartSizeToggleBtn.disabled = !enabled;
        elements.chartSizeToggleBtn.setAttribute("aria-disabled", enabled ? "false" : "true");
    }
}

function clampChartHeight(value) {
    if (!Number.isFinite(value)) {
        return CHART_HEIGHT_DEFAULT;
    }
    // Міграція зі старого слайдера: мапимо довільне число до найближчого з двох режимів.
    const midpoint = (CHART_HEIGHT_NORMAL + CHART_HEIGHT_LARGE) / 2;
    return value >= midpoint ? CHART_HEIGHT_LARGE : CHART_HEIGHT_NORMAL;
}

function updateChartSizeToggleState(heightPx) {
    if (!elements.chartSizeToggleBtn) {
        return;
    }
    const isLarge = heightPx >= CHART_HEIGHT_LARGE;
    elements.chartSizeToggleBtn.setAttribute("aria-pressed", isLarge ? "true" : "false");
    if (isLarge) {
        elements.chartSizeToggleBtn.setAttribute("aria-label", "Зменшити графік");
        elements.chartSizeToggleBtn.setAttribute("title", `Зменшити до ${CHART_HEIGHT_NORMAL}`);
    } else {
        elements.chartSizeToggleBtn.setAttribute("aria-label", "Збільшити графік");
        elements.chartSizeToggleBtn.setAttribute("title", `Збільшити до ${CHART_HEIGHT_LARGE}`);
    }
}

function persistChartHeight(value) {
    const storage = getStorage();
    if (!storage) {
        return;
    }
    try {
        storage.setItem(STORAGE_KEYS.chartHeight, String(value));
    } catch (err) {
        console.warn("[UI] Не вдалося зберегти висоту чарта", err);
    }
}

function toggleChartFullscreen() {
    if (appState.chartUi.fullscreen) {
        exitChartFullscreen();
    } else {
        enterChartFullscreen();
    }
}

function enterChartFullscreen() {
    if (!elements.chartCard) {
        return;
    }
    elements.chartCard.classList.add("card-chart--fullscreen");
    document.body.classList.add("chart-fullscreen-lock");
    document.documentElement.classList.add("chart-fullscreen-lock");
    appState.chartUi.fullscreen = true;
    updateFullscreenButtonState();
    setHeightControlEnabled(false);
    scheduleChartResize();
}

function exitChartFullscreen() {
    if (!elements.chartCard) {
        return;
    }
    elements.chartCard.classList.remove("card-chart--fullscreen");
    document.body.classList.remove("chart-fullscreen-lock");
    document.documentElement.classList.remove("chart-fullscreen-lock");
    appState.chartUi.fullscreen = false;
    updateFullscreenButtonState();
    setHeightControlEnabled(true);
    scheduleChartResize();
}

function updateFullscreenButtonState() {
    if (!elements.chartFullscreenBtn) {
        return;
    }
    if (appState.chartUi.fullscreen) {
        elements.chartFullscreenBtn.setAttribute("aria-pressed", "true");
        elements.chartFullscreenBtn.setAttribute("aria-label", "Вийти з повного екрана");
        elements.chartFullscreenBtn.setAttribute("title", "Вийти з повного екрана");
        elements.chartFullscreenBtn.classList.add("chart-fullscreen-btn--active");
    } else {
        elements.chartFullscreenBtn.setAttribute("aria-pressed", "false");
        elements.chartFullscreenBtn.setAttribute("aria-label", "Повний екран");
        elements.chartFullscreenBtn.setAttribute("title", "Повний екран");
        elements.chartFullscreenBtn.classList.remove("chart-fullscreen-btn--active");
    }
}

function handleFullscreenKeydown(event) {
    if (event.key === "Escape" && appState.chartUi.fullscreen) {
        exitChartFullscreen();
    }
}

let chartResizeRaf = null;
function scheduleChartResize() {
    if (typeof window === "undefined") {
        return;
    }
    if (!appState.chart || typeof appState.chart.resizeToContainer !== "function") {
        return;
    }
    const raf = window.requestAnimationFrame || window.setTimeout;
    if (chartResizeRaf) {
        return;
    }
    chartResizeRaf = raf(() => {
        chartResizeRaf = null;
        try {
            appState.chart.resizeToContainer();
        } catch (err) {
            console.warn("[UI] chart resize failed", err);
        }
    });
}

function setBadgeText(elementId, value) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const textValue = value || "-";
    el.textContent = textValue;
    el.className = "summary-badge";
    if (!value) {
        return;
    }
    const normalized = String(value).toLowerCase().replace(/\s+/g, "_");
    el.classList.add(`summary-badge--${normalized}`);
}

function findLastIndexes(events) {
    let lastBos = -1;
    let lastChoch = -1;
    for (let i = 0; i < events.length; i += 1) {
        const kind = (events[i]?.type || events[i]?.event_type || "").toUpperCase();
        if (lastBos === -1 && kind.includes("BOS")) {
            lastBos = i;
        }
        if (lastChoch === -1 && kind.includes("CHOCH")) {
            lastChoch = i;
        }
        if (lastBos !== -1 && lastChoch !== -1) {
            break;
        }
    }
    return { lastBos, lastChoch };
}

function renderEvents(events) {
    const filtered = (events || [])
        .filter((evt) => {
            const kind = (evt?.type || evt?.event_type || "").toUpperCase();
            return kind.includes("BOS") || kind.includes("CHOCH");
        })
        .slice(-20)
        .reverse();

    const { lastBos, lastChoch } = findLastIndexes(filtered);

    renderRows(
        elements.tables.events,
        filtered,
        (evt, idx) => {
            const direction = normalizeDirection(evt.direction || evt.dir);
            const classes = [directionClass(direction)];
            if (idx === lastBos) {
                classes.push("event-row-last-bos");
            }
            if (idx === lastChoch) {
                classes.push("event-row-last-choch");
            }
            return `<tr class="${classes.filter(Boolean).join(" ")}">
        <td>${formatTime(evt.ts || evt.time || evt.timestamp)}</td>
        <td>${evt.type || evt.event_type || "-"}</td>
        <td>${direction}</td>
        <td class="numeric">${formatNumber(evt.price ?? evt.level ?? evt.value)}</td>
      </tr>`;
        },
        4
    );
}

function renderOteZones(zones) {
    const rows = (zones || []).slice(0, 8);
    renderRows(
        elements.tables.ote,
        rows,
        (zone) => {
            const direction = normalizeDirection(zone.direction);
            const rowClass = directionClass(direction);
            return `<tr class="${rowClass}">
        <td>${direction}</td>
        <td>${(zone.role || "-").toUpperCase()}</td>
        <td class="numeric">${formatNumber(zone.ote_min)}</td>
        <td class="numeric">${formatNumber(zone.ote_max)}</td>
      </tr>`;
        },
        4
    );
}

function renderPools(pools) {
    const rows = (pools || []).slice(0, 8);
    renderRows(
        elements.tables.pools,
        rows,
        (pool) => {
            const direction = normalizeDirection(pool.direction);
            const role = (pool.role || "-").toUpperCase();
            const classes = [directionClass(direction)];
            if (role === "PRIMARY") {
                classes.push("role-primary");
            }
            const strengthLabel = formatPoolStrength(pool);
            return `<tr class="${classes.filter(Boolean).join(" ")}">
                <td>${pool.type || "-"}</td>
                <td>${role}</td>
                <td class="numeric">${formatNumber(pool.price)}</td>
                <td class="numeric pool-strength-cell">${strengthLabel}</td>
            </tr>`;
        },
        4
    );
}

function renderZones(zones) {
    const rows = Array.isArray(zones) ? zones.slice(0, 6) : [];
    renderRows(
        elements.tables.zones,
        rows,
        (zone, idx) => {
            const role = (zone.role || "-").toUpperCase();
            const headline = formatZoneHeadline(zone, idx);
            return `<tr>
        <td title="${escapeHtml(headline)}">${escapeHtml(headline)}</td>
        <td>${role}</td>
        <td class="numeric">${formatNumber(zone.min ?? zone.price_min)}</td>
        <td class="numeric">${formatNumber(zone.max ?? zone.price_max)}</td>
      </tr>`;
        },
        4
    );
}

function escapeHtml(text) {
    const s = String(text ?? "");
    return s
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function renderRows(tbody, rows, renderer, columnsCount) {
    if (!rows || rows.length === 0) {
        tbody.innerHTML = `<tr class="empty-row"><td colspan="${columnsCount}">Немає даних</td></tr>`;
        return;
    }
    tbody.innerHTML = rows.map((row, idx) => renderer(row, idx)).join("");
}

function setText(node, value) {
    if (!node) {
        return;
    }
    node.textContent = value == null || value === "" ? "-" : String(value);
}

function normalizeDirection(value) {
    const dir = (value || "-").toString().toUpperCase();
    if (dir === "LONG" || dir === "SHORT") {
        return dir;
    }
    return dir;
}

function directionClass(direction) {
    if (direction === "LONG") return "dir-long";
    if (direction === "SHORT") return "dir-short";
    return "";
}

function formatTime(value) {
    if (!value) return "-";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return String(value);
    }
    return date.toISOString().substring(11, 19); // HH:MM:SS
}

function formatIsoDateTime(value) {
    if (value === null || value === undefined) {
        return "-";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return "-";
    }
    return date.toISOString().replace(".000Z", "Z");
}

function toDateSmart(value) {
    if (value === null || value === undefined) {
        return null;
    }

    if (value instanceof Date) {
        return Number.isNaN(value.getTime()) ? null : value;
    }

    if (typeof value === "number") {
        if (!Number.isFinite(value)) return null;
        const ms = value > 1e12 ? value : value * 1000;
        const date = new Date(ms);
        return Number.isNaN(date.getTime()) ? null : date;
    }

    const asString = String(value).trim();
    if (!asString) {
        return null;
    }

    const numeric = Number(asString);
    if (Number.isFinite(numeric)) {
        const ms = numeric > 1e12 ? numeric : numeric * 1000;
        const date = new Date(ms);
        return Number.isNaN(date.getTime()) ? null : date;
    }

    const date = new Date(asString);
    return Number.isNaN(date.getTime()) ? null : date;
}

function formatLocalDateTime(value) {
    const date = toDateSmart(value);
    if (!date) {
        return "-";
    }

    const pad2 = (n) => String(n).padStart(2, "0");
    const dd = pad2(date.getDate());
    const mm = pad2(date.getMonth() + 1);
    const hh = pad2(date.getHours());
    const mi = pad2(date.getMinutes());
    const ss = pad2(date.getSeconds());

    // Компактно і зрозуміло: DD.MM HH:MM:SS (локальний час)
    return `${dd}.${mm} ${hh}:${mi}:${ss}`;
}
