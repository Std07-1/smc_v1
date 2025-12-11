const HTTP_BASE_URL = "http://127.0.0.1:8080";
const DEFAULT_SYMBOL = "xauusd";
const DEFAULT_TIMEFRAME = "1m";
const DEFAULT_LIMIT = 500;

const demoState = {
    chart: null,
    symbol: DEFAULT_SYMBOL,
    timeframe: DEFAULT_TIMEFRAME,
    lastViewerState: null,
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
    });
    dom.tfSelect.addEventListener("change", () => {
        demoState.timeframe = dom.tfSelect.value || DEFAULT_TIMEFRAME;
        refreshBars();
    });
}

function bootstrap() {
    const initialSymbol = getSymbolFromUrl() || DEFAULT_SYMBOL;
    demoState.symbol = initialSymbol;
    dom.symbolSelect.value = initialSymbol.toLowerCase();
    refreshBars();
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
        demoState.chart.setBars(bars);
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
