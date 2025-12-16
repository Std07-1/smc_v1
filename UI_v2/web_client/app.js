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

function isFxcmWsEnabled() {
    // FXCM WS міст (8082) — dev інтерфейс; у публічному режимі вимкнений, щоб не було reconnect-циклів.
    try {
        const params = new URLSearchParams(window.location.search || "");
        const flag = (params.get("fxcm_ws") || "").trim().toLowerCase();
        if (flag === "1" || flag === "true") {
            return true;
        }
        const host = window.location.hostname;
        return isLocalHostname(host) || isPrivateLanIp(host);
    } catch (_e) {
        return false;
    }
}

function isFxcmWsSameOrigin() {
    // Якщо FXCM WS міст прокситься у same-origin (nginx/Cloudflare tunnel),
    // підключаємось до wss://<домен>/fxcm/... замість прямого :8082.
    try {
        const params = new URLSearchParams(window.location.search || "");
        const flag = (params.get("fxcm_ws_same_origin") || "").trim().toLowerCase();
        return flag === "1" || flag === "true";
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

function buildFxcmWsBaseUrl() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    if (isFxcmWsSameOrigin()) {
        return `${proto}//${window.location.host}`;
    }
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
const DEFAULT_SYMBOL = "xauusd";
const OHLCV_DEFAULT_TF = "1m";
const OHLCV_DEFAULT_LIMIT = 500;
const AVAILABLE_TIMEFRAMES = ["1m", "5m"];
const CHART_HEIGHT_NORMAL = 700;
const CHART_HEIGHT_LARGE = 900;
const CHART_HEIGHT_DEFAULT = CHART_HEIGHT_NORMAL;
const STORAGE_KEYS = {
    symbol: "smc_viewer_selected_symbol",
    timeframe: "smc_viewer_selected_tf",
    chartHeight: "smc_viewer_chart_height",
    summaryCollapsed: "smc_viewer_summary_collapsed",
    layersVisibility: "smc_viewer_layers_visibility",
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

function mapPoolsFromViewerState(state) {
    const pools = state?.liquidity?.pools || [];
    return pools.map((pool) => ({
        price: pool.price,
        role: pool.role,
        type: pool.type,
    }));
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
    const zones = state?.zones?.raw?.zones || [];
    return zones.map((zone) => ({
        min: zone.price_min ?? zone.min,
        max: zone.price_max ?? zone.max,
        label: zone.type || zone.role,
    }));
}

function safeUnixSeconds(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) {
        return undefined;
    }
    return Math.floor(num / (num > 1e12 ? 1000 : 1));
}

function normalizeOhlcvBar(bar) {
    if (!bar) {
        return null;
    }
    const timeCandidate = bar.time ?? bar.ts ?? bar.timestamp ?? bar.end_ts;
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
    const timeNumeric = Number(timeCandidate);
    if (!Number.isFinite(timeNumeric)) {
        return null;
    }
    const divisor = timeNumeric > 1e12 ? 1000 : 1;
    const normalized = {
        time: Math.floor(timeNumeric / divisor),
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
    // Важливо: FXCM інколи передає `volume=0`, але `tick_count>0`.
    // Nullish-coalescing (??) тут не підходить — нам треба «перше додатне».
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
    // Повертаємо лише коротку мітку джерела — для тимчасового діагностичного поля у шапці.
    const vol = Number(bar.volume ?? bar.vol ?? bar.v);
    if (Number.isFinite(vol) && vol > 0) {
        return "volume";
    }
    const tc = Number(bar.tick_count ?? bar.tickCount);
    if (Number.isFinite(tc) && tc > 0) {
        return "tick_count";
    }
    const ticks = Number(bar.ticks);
    if (Number.isFinite(ticks) && ticks > 0) {
        return "ticks";
    }
    const intensity = Number(bar.volume_intensity ?? bar.intensity);
    if (Number.isFinite(intensity) && intensity > 0) {
        return "intensity";
    }
    return "-";
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
    },
    chartUi: {
        heightPx: CHART_HEIGHT_DEFAULT,
        fullscreen: false,
    },

    ui: {
        view: "overview",
        filtersOpen: false,
        summaryCollapsed: false,
    },
};

const elements = {};

loadPersistedPreferences();

document.addEventListener("DOMContentLoaded", () => {
    cacheElements();
    initUiViews();
    bindUi();
    initChartController();
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
    elements.timeframeSelect = document.getElementById("timeframe-select");

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

    bindLayerToggles();
    bindChartLayerMenu();
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
    const url = `${HTTP_BASE_URL}/smc-viewer/ohlcv` +
        `?symbol=${encodeURIComponent(lowerSymbol)}` +
        `&tf=${encodeURIComponent(normalizedTf)}` +
        `&limit=${OHLCV_DEFAULT_LIMIT}`;

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
    renderZones(state.zones?.raw?.zones || []);
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
    return num > 1e12 ? num / 1000 : num;
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
        Date.now() / 1000;
    const tfSec = timeframeToSeconds(appState.currentTimeframe);
    const candleStart = Math.floor(tsSec / tfSec) * tfSec;

    // Локальний tick_count (інтенсивність) для гістограми volume.
    // Стабільний fallback: якщо FXCM шле `volume=0` або на 5m не надходить tick_count.
    if (appState.tickLiveOpenTimeSec !== candleStart) {
        appState.tickLiveVolumeCount = 0;
    }
    appState.tickLiveVolumeCount += 1;

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
            volume: appState.tickLiveVolumeCount,
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
            volume: appState.tickLiveVolumeCount,
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

        const volumeFromBar = pickVolumeFromFxcmBar(bar);
        const tickVolumeFallback =
            appState.tickLiveOpenTimeSec === candle.time && appState.tickLiveVolumeCount > 0
                ? appState.tickLiveVolumeCount
                : 0;
        const previousLiveVolumeSameCandle =
            appState.ohlcvLiveCandle && appState.ohlcvLiveOpenTimeSec === candle.time
                ? Number(appState.ohlcvLiveCandle.volume)
                : 0;

        // Не даємо volume скидатися в 0 у межах однієї live-свічки.
        candle.volume = Math.max(volumeFromBar, tickVolumeFallback, previousLiveVolumeSameCandle);

        // Діагностика: показуємо, звідки береться volume (volume vs tick_count).
        // Якщо FXCM не дав обсяг, але tick fallback дав — маркуємо як tick_count.
        if (volumeFromBar > 0) {
            appState.lastOhlcvVolSrc = pickVolumeSourceFromFxcmBar(bar);
        } else if (tickVolumeFallback > 0) {
            appState.lastOhlcvVolSrc = "tick_count";
        } else {
            appState.lastOhlcvVolSrc = pickVolumeSourceFromFxcmBar(bar);
        }
        if (
            !Number.isFinite(candle.open) ||
            !Number.isFinite(candle.high) ||
            !Number.isFinite(candle.low) ||
            !Number.isFinite(candle.close)
        ) {
            continue;
        }

        const isComplete = bar.complete !== false;
        if (!isComplete) {
            noteFxcmLiveSeen();
            if (typeof appState.chart.setLiveBar === "function") {
                appState.chart.setLiveBar(candle);
                appState.ohlcvLiveOpenTimeSec = candle.time;
                appState.ohlcvLiveCandle = candle;
            }
            continue;
        }

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
    const pools = mapPoolsFromViewerState(state);
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
    appState.chart.setLiquidityPools(layersVisibility.pools ? pools : []);
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

function scheduleChartResize() {
    if (typeof window === "undefined") {
        return;
    }
    if (!appState.chart || typeof appState.chart.resizeToContainer !== "function") {
        return;
    }
    const raf = window.requestAnimationFrame || window.setTimeout;
    raf(() => {
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
    const rows = Array.isArray(zones) ? zones.slice(0, 8) : [];
    renderRows(
        elements.tables.zones,
        rows,
        (zone) => {
            const role = (zone.role || "-").toUpperCase();
            return `<tr>
        <td>${zone.type || "-"}</td>
        <td>${role}</td>
        <td class="numeric">${formatNumber(zone.price_min)}</td>
        <td class="numeric">${formatNumber(zone.price_max)}</td>
      </tr>`;
        },
        4
    );
}

function renderRows(tbody, rows, renderer, columnsCount) {
    if (!rows || rows.length === 0) {
        tbody.innerHTML = `<tr class="empty-row"><td colspan="${columnsCount}">Немає даних</td></tr>`;
        return;
    }
    tbody.innerHTML = rows.map(renderer).join("");
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
