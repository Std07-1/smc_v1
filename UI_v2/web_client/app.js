// Базове HTTP/WS налаштування для локального режиму.
const HTTP_BASE_URL = "http://127.0.0.1:8080";
const WS_BASE_URL = "ws://127.0.0.1:8081";
const DEFAULT_SYMBOL = "xauusd";
const OHLCV_DEFAULT_TF = "1m";
const OHLCV_DEFAULT_LIMIT = 500;
const AVAILABLE_TIMEFRAMES = ["1m", "5m"];
const STORAGE_KEYS = {
    symbol: "smc_viewer_selected_symbol",
    timeframe: "smc_viewer_selected_tf",
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

const appState = {
    snapshot: {},
    latestStates: {},
    currentSymbol: null,
    currentTimeframe: OHLCV_DEFAULT_TF,
    preferredSymbol: null,
    ws: null,
    reconnectAttempt: 0,
    reconnectTimer: null,
    lastPayloadTs: null,
    lastLagSeconds: null,
    chart: null,
    chartState: {
        overlaySeqBySymbol: Object.create(null),
        layersVisibility: {
            events: true,
            pools: true,
            ranges: true,
            ote: true,
            zones: true,
        },
    },
};

const elements = {};

loadPersistedPreferences();

document.addEventListener("DOMContentLoaded", () => {
    cacheElements();
    bindUi();
    initChartController();
    bootstrap().catch((err) => console.error("[UI] Помилка старту:", err));
});

function cacheElements() {
    elements.symbolSelect = document.getElementById("symbol-select");
    elements.refreshBtn = document.getElementById("refresh-btn");
    elements.reconnectBtn = document.getElementById("reconnect-btn");
    elements.wsStatus = document.getElementById("ws-status");
    elements.payloadTs = document.getElementById("payload-ts");
    elements.payloadLag = document.getElementById("payload-lag");
    elements.timeframeSelect = document.getElementById("timeframe-select");

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
    };

    elements.tables = {
        events: document.getElementById("events-body"),
        ote: document.getElementById("ote-body"),
        pools: document.getElementById("pools-body"),
        zones: document.getElementById("zones-body"),
    };
    elements.chartContainer = document.getElementById("chart-container");
    elements.layerToggles = {
        events: document.getElementById("layer-toggle-events"),
        pools: document.getElementById("layer-toggle-pools"),
        ote: document.getElementById("layer-toggle-ote"),
        zones: document.getElementById("layer-toggle-zones"),
    };
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

    elements.refreshBtn.addEventListener("click", async () => {
        try {
            await reloadSnapshot(true);
            if (appState.currentSymbol) {
                await fetchOhlcv(appState.currentSymbol, appState.currentTimeframe);
            }
        } catch (err) {
            console.error("[UI] Snapshot error:", err);
        }
    });

    elements.reconnectBtn.addEventListener("click", () => {
        if (appState.currentSymbol) {
            openViewerSocket(appState.currentSymbol);
        }
    });

    bindLayerToggles();
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
            }
            return;
        }
        const data = await response.json();
        lastOhlcvResponse = data;
        renderOhlcvSummary(data);
        pushBarsToChart(data);
    } catch (err) {
        console.error("[UI] OHLCV request error", err);
        lastOhlcvResponse = null;
        renderOhlcvSummary(null);
        if (appState.chart) {
            appState.chart.clearAll();
        }
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
}

function handleTimeframeChange(nextTf) {
    const normalized = normalizeTimeframe(nextTf);
    if (normalized === appState.currentTimeframe) {
        return;
    }
    appState.currentTimeframe = normalized;
    syncTimeframeSelect(normalized);
    persistTimeframe(normalized);
    if (appState.currentSymbol) {
        fetchOhlcv(appState.currentSymbol, normalized);
    }
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

    ws.onclose = () => {
        setStatus("stale", `${symbol}: стрім втрачено`);
        console.warn(`[WS] Disconnected from ${symbol}`);
        scheduleReconnect();
    };

    ws.onerror = (err) => {
        console.error("[WS] Помилка", err);
        setStatus("error", `${symbol}: WS помилка`);
        ws.close();
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
    const pill = elements.wsStatus;
    pill.classList.remove(
        "status-connected",
        "status-disconnected",
        "status-reconnecting",
        "status-stale",
    );
    switch (state) {
        case "connected":
            pill.classList.add("status-connected");
            pill.textContent = detail ? `Підключено (${detail})` : "Підключено";
            break;
        case "connecting":
            pill.classList.add("status-reconnecting");
            pill.textContent = detail ? `Підключення (${detail})` : "Підключення";
            break;
        case "reconnecting":
            pill.classList.add("status-reconnecting");
            pill.textContent = detail
                ? `Перепідключення (${detail})`
                : "Перепідключення";
            break;
        case "stale":
            pill.classList.add("status-stale");
            pill.textContent = detail ? `Без стріму (${detail})` : "Без стріму";
            break;
        case "error":
            pill.classList.add("status-disconnected");
            pill.textContent = detail ? `Помилка (${detail})` : "Помилка";
            break;
        case "loading":
            pill.classList.add("status-reconnecting");
            pill.textContent = detail || "Завантаження...";
            break;
        default:
            pill.classList.add("status-disconnected");
            pill.textContent = detail ? `Відключено (${detail})` : "Відключено";
    }
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
    const lagSeconds = state.meta?.fxcm?.lag_seconds ?? state.fxcm?.lag_seconds ?? null;

    appState.lastPayloadTs = payloadTs;
    appState.lastLagSeconds = lagSeconds;

    if (elements.payloadTs) {
        elements.payloadTs.textContent = payloadTs ? formatIsoDateTime(payloadTs) : "-";
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
    setText(elements.summary.price, "-");
    setText(elements.summary.session, "-");
    setBadgeText("summary-trend", null);
    setBadgeText("summary-bias", null);
    setBadgeText("summary-range", null);
    setBadgeText("summary-amd", null);
    setText(elements.summary.market, "-");
    setText(elements.summary.process, "-");
    setText(elements.summary.lag, "-");

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
        ...(appState.chartState.layersVisibility || {}),
    };

    appState.chart.setEvents(layersVisibility.events ? events : []);
    appState.chart.setLiquidityPools(layersVisibility.pools ? pools : []);
    appState.chart.setRanges(layersVisibility.ranges ? ranges : []);
    appState.chart.setOteZones(layersVisibility.ote ? oteZones : []);
    appState.chart.setZones(layersVisibility.zones ? zones : []);

    if (seqKey !== null) {
        appState.chartState.overlaySeqBySymbol[symbol] = String(seqKey);
    } else if (force) {
        appState.chartState.overlaySeqBySymbol[symbol] = `force-${Date.now()}`;
    }
}

function renderSummary(state) {
    const struct = state.structure || {};
    const fxcm = state.meta?.fxcm || state.fxcm || {};
    setText(elements.summary.symbol, state.symbol || "-");
    setText(elements.summary.price, formatNumber(state.price));
    setText(elements.summary.session, state.session || "-");
    setBadgeText("summary-trend", struct.trend || "UNKNOWN");
    setBadgeText("summary-bias", struct.bias || "UNKNOWN");
    setBadgeText("summary-range", struct.range_state || "UNKNOWN");
    setBadgeText("summary-amd", state.liquidity?.amd_phase || "UNKNOWN");
    setText(elements.summary.market, fxcm.market_state || "-");
    setText(elements.summary.process, fxcm.process_state || fxcm.price_state || "-");
    setText(elements.summary.lag, formatNumber(fxcm.lag_seconds, 2));
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
    } catch (err) {
        console.warn("[UI] Не вдалося зчитати налаштування з localStorage", err);
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
