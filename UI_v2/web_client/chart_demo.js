const HTTP_BASE_URL = "http://127.0.0.1:8080";
const FXCM_OHLCV_WS_BASE_URL = "ws://127.0.0.1:8082";
const DEFAULT_SYMBOL = "xauusd";
const DEFAULT_TIMEFRAME = "1m";
const DEFAULT_LIMIT = 500;

const demoState = {
    chart: null,
    symbol: DEFAULT_SYMBOL,
    timeframe: DEFAULT_TIMEFRAME,
    lastViewerState: null,
    ws: null,
    streamState: Object.create(null),
};

const dom = {};

document.addEventListener("DOMContentLoaded", () => {
    cacheDom();
    initChart();
    bindControls();
    bootstrap();
});

function cacheDom() {
    dom.symbolSelect = document.getElementById("chart-symbol");
    dom.tfSelect = document.getElementById("chart-tf");
    dom.status = document.getElementById("chart-status");
    dom.container = document.getElementById("chart-container");
}

function initChart() {
    if (!window.createChartController) {
        throw new Error("chart_adapter.js не завантажено");
    }
    demoState.chart = window.createChartController(dom.container);
}

function bindControls() {
    dom.symbolSelect.addEventListener("change", () => {
        demoState.symbol = dom.symbolSelect.value || DEFAULT_SYMBOL;
        refreshBars();
        restartStream();
    });
    dom.tfSelect.addEventListener("change", () => {
        demoState.timeframe = dom.tfSelect.value || DEFAULT_TIMEFRAME;
        refreshBars();
        restartStream();
    });
}

function bootstrap() {
    const initialSymbol = getSymbolFromUrl() || DEFAULT_SYMBOL;
    demoState.symbol = initialSymbol;
    dom.symbolSelect.value = initialSymbol.toLowerCase();
    refreshBars();
    restartStream();
}

function getSymbolFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const raw = params.get("symbol");
    return raw ? raw.toLowerCase() : null;
}

async function refreshBars() {
    setStatus(`Завантаження ${demoState.symbol.toUpperCase()} ${demoState.timeframe}…`, "loading");
    try {
        const bars = await fetchOhlcv(demoState.symbol, demoState.timeframe, DEFAULT_LIMIT);
        if (!bars.length) {
            demoState.chart.clearAll();
            setStatus("Дані відсутні", "warning");
            return;
        }
        const key = streamKey(demoState.symbol, demoState.timeframe);
        ensureStreamBucket(key);
        // Заповнюємо history з HTTP (це complete-бари з UDS).
        demoState.streamState[key].history = bars.map((bar) => ({
            open_time: Number(bar.time) * 1000,
            close_time: Number(bar.time) * 1000,
            open: Number(bar.open),
            high: Number(bar.high),
            low: Number(bar.low),
            close: Number(bar.close),
            volume: 0,
            complete: true,
            synthetic: false,
        }));

        renderFromStreamState(key);
        setStatus(`Bars loaded: ${bars.length}`, "success");
        await loadViewerStateAndOverlays();
    } catch (error) {
        console.error("[chart_demo] OHLCV fetch failed", error);
        demoState.chart.clearAll();
        setStatus("Помилка завантаження OHLCV", "error");
    }
}

async function fetchOhlcv(symbol, timeframe, limit) {
    const params = new URLSearchParams({
        symbol: symbol.toLowerCase(),
        tf: timeframe,
        limit: String(limit),
    });
    const url = `${HTTP_BASE_URL}/smc-viewer/ohlcv?${params.toString()}`;
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    const bars = Array.isArray(payload?.bars) ? payload.bars : [];
    return bars.map((bar) => ({
        time: Math.floor(Number(bar.time || 0) / 1000),
        open: Number(bar.open),
        high: Number(bar.high),
        low: Number(bar.low),
        close: Number(bar.close),
    }));
}

function streamKey(symbol, tf) {
    return `${String(symbol || "").toUpperCase()}:${String(tf || "").toLowerCase()}`;
}

function ensureStreamBucket(key) {
    if (!demoState.streamState[key]) {
        demoState.streamState[key] = { history: [], live: null };
    }
}

function restartStream() {
    closeStream();
    const symbol = String(demoState.symbol || DEFAULT_SYMBOL).toUpperCase();
    const tf = String(demoState.timeframe || DEFAULT_TIMEFRAME).toLowerCase();
    const wsUrl = `${FXCM_OHLCV_WS_BASE_URL}/fxcm/ohlcv?symbol=${encodeURIComponent(symbol)}&tf=${encodeURIComponent(tf)}`;

    try {
        const ws = new WebSocket(wsUrl);
        demoState.ws = ws;

        ws.onopen = () => {
            console.info(`[chart_demo] fxcm:ohlcv stream connected ${symbol} ${tf}`);
        };

        ws.onmessage = (event) => {
            try {
                const payload = JSON.parse(event.data);
                onFxcmOhlcvPayload(payload);
            } catch (err) {
                console.warn("[chart_demo] bad fxcm:ohlcv payload", err);
            }
        };

        ws.onclose = () => {
            console.warn("[chart_demo] fxcm:ohlcv stream closed");
        };

        ws.onerror = (err) => {
            console.warn("[chart_demo] fxcm:ohlcv stream error", err);
        };
    } catch (err) {
        console.warn("[chart_demo] WS init failed", err);
    }
}

function closeStream() {
    if (demoState.ws) {
        try {
            demoState.ws.close();
        } catch (err) {
            // ignore
        }
        demoState.ws = null;
    }
}

function onFxcmOhlcvPayload(payload) {
    const symbol = String(payload?.symbol || "").toUpperCase();
    const tf = String(payload?.tf || "").toLowerCase();
    const bars = Array.isArray(payload?.bars) ? payload.bars : [];
    if (!symbol || !tf || !bars.length) {
        return;
    }

    const key = streamKey(symbol, tf);
    ensureStreamBucket(key);

    for (const bar of bars) {
        const bucketStart = Number(bar?.open_time);
        if (!Number.isFinite(bucketStart)) {
            continue;
        }

        const normalized = {
            open_time: bucketStart,
            close_time: Number(bar?.close_time),
            open: Number(bar?.open),
            high: Number(bar?.high),
            low: Number(bar?.low),
            close: Number(bar?.close),
            volume: Number(bar?.volume),
            complete: bar?.complete !== false,
            synthetic: bar?.synthetic === true,
        };

        if (bar?.complete === false) {
            // live-бар
            demoState.streamState[key].live = normalized;
        } else {
            // complete-бар
            upsertHistoryBar(demoState.streamState[key].history, normalized);
            if (
                demoState.streamState[key].live &&
                demoState.streamState[key].live.open_time === bucketStart
            ) {
                demoState.streamState[key].live = null;
            }
        }
    }

    // Рендеримо лише активний символ/tf
    const activeKey = streamKey(demoState.symbol, demoState.timeframe);
    if (key === activeKey) {
        renderFromStreamState(key);
    }
}

function upsertHistoryBar(history, bar) {
    if (!Array.isArray(history)) {
        return;
    }
    const idx = history.findIndex((it) => Number(it?.open_time) === Number(bar?.open_time));
    if (idx >= 0) {
        history[idx] = bar;
        return;
    }
    history.push(bar);
}

function renderFromStreamState(key) {
    if (!demoState.chart) {
        return;
    }
    const bucket = demoState.streamState[key];
    if (!bucket) {
        return;
    }
    const historyBars = Array.isArray(bucket.history) ? bucket.history : [];
    const chartBars = historyBars
        .map((bar) => ({
            time: Math.floor(Number(bar.open_time || 0) / 1000),
            open: Number(bar.open),
            high: Number(bar.high),
            low: Number(bar.low),
            close: Number(bar.close),
            volume: Number(bar.volume),
        }))
        .filter((bar) => Number.isFinite(bar.time) && bar.time > 0)
        .sort((a, b) => a.time - b.time);

    demoState.chart.setBars(chartBars);

    if (bucket.live) {
        demoState.chart.setLiveBar({
            time: Math.floor(Number(bucket.live.open_time || 0) / 1000),
            open: Number(bucket.live.open),
            high: Number(bucket.live.high),
            low: Number(bucket.live.low),
            close: Number(bucket.live.close),
            volume: Number(bucket.live.volume),
        });
    } else if (typeof demoState.chart.clearLiveBar === "function") {
        demoState.chart.clearLiveBar();
    }
}

window.addEventListener("beforeunload", () => {
    closeStream();
});

function setStatus(message, variant) {
    if (!dom.status) return;
    dom.status.textContent = message;
    dom.status.dataset.state = variant || "";
}

async function loadViewerStateAndOverlays() {
    try {
        const state = await fetchViewerState(demoState.symbol);
        if (!state) {
            demoState.chart.setEvents([]);
            demoState.chart.setLiquidityPools([]);
            demoState.chart.setRanges([]);
            demoState.chart.setOteZones([]);
            demoState.chart.setZones([]);
            return;
        }
        demoState.lastViewerState = state;
        demoState.chart.setEvents(mapEventsFromViewerState(state));
        demoState.chart.setLiquidityPools(mapPoolsFromViewerState(state));
        demoState.chart.setRanges(mapRangesFromViewerState(state));
        demoState.chart.setOteZones(mapOteZonesFromViewerState(state));
        demoState.chart.setZones(mapZonesFromViewerState(state));
    } catch (error) {
        console.warn("[chart_demo] ViewerState overlays error", error);
    }
}

async function fetchViewerState(symbol) {
    const url = `${HTTP_BASE_URL}/smc-viewer/snapshot?symbol=${encodeURIComponent(symbol)}`;
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    if (response.status === 404) {
        return null;
    }
    if (!response.ok) {
        throw new Error(`ViewerState HTTP ${response.status}`);
    }
    return response.json();
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
