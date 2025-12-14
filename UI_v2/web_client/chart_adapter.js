(function () {
    const CANDLE_COLORS = {
        up: "#26a69a",
        down: "#ef5350",
        live: "#f6c343",
    };

    const VOLUME_WINDOW_SIZE = 200;
    const OPACITY_MIN = 0.25;
    const OPACITY_MAX = 1.0;

    const DEFAULT_CHART_OPTIONS = {
        layout: {
            background: { color: "#131722" },
            textColor: "#d1d4dc",
        },
        grid: {
            vertLines: { color: "rgba(42, 46, 57, 0.7)" },
            horzLines: { color: "rgba(42, 46, 57, 0.7)" },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: {
                color: "rgba(209, 212, 220, 0.35)",
                width: 1,
                style: LightweightCharts.LineStyle.Dashed,
                labelBackgroundColor: "#2a2e39",
            },
            horzLine: {
                color: "rgba(209, 212, 220, 0.35)",
                width: 1,
                style: LightweightCharts.LineStyle.Dashed,
                labelBackgroundColor: "#2a2e39",
            },
        },
        handleScroll: {
            mouseWheel: true,
            pressedMouseMove: true,
            vertTouchDrag: true,
            horzTouchDrag: true,
        },
        handleScale: {
            axisPressedMouseMove: {
                time: true,
                price: true,
            },
            axisDoubleClickReset: true,
            mouseWheel: true,
            pinch: true,
        },
        rightPriceScale: {
            borderColor: "rgba(54, 58, 69, 0.9)",
            borderVisible: true,
            ticksVisible: true,
            autoScale: true,
            scaleMargins: {
                top: 0.12,
                bottom: 0.18,
            },
        },
        timeScale: {
            borderColor: "rgba(54, 58, 69, 0.9)",
            borderVisible: true,
            rightOffset: 6,
            barSpacing: 8,
            timeVisible: true,
            secondsVisible: false,
            fixLeftEdge: false,
            fixRightEdge: false,
            lockVisibleTimeRangeOnResize: false,
        },
    };

    const STRUCTURE_TRIANGLE = {
        widthBars: 6,
        minWidthSec: 180,
        heightRatio: 0.35,
        minHeight: 0.01,
        minHeightPct: 0.0006,
        colors: {
            bos: "#4ade80",
            choch: "#facc15",
        },
        maxEvents: 12,
        edgeWidth: 3,
        baseWidth: 2,
    };

    const OTE_STYLES = {
        LONG: {
            border: "rgba(34, 197, 94, 0.85)",
            arrow: "#22c55e",
            axisLabel: "#22c55e",
        },
        SHORT: {
            border: "rgba(248, 113, 113, 0.85)",
            arrow: "#f87171",
            axisLabel: "#f87171",
        },
    };

    function normalizeBar(bar) {
        if (!bar) {
            return null;
        }
        const timeSec = Number(bar.time);
        if (!Number.isFinite(timeSec)) {
            return null;
        }
        return {
            time: Math.floor(timeSec),
            open: Number(bar.open),
            high: Number(bar.high),
            low: Number(bar.low),
            close: Number(bar.close),
        };
    }

    function normalizeVolume(bar) {
        if (!bar) {
            return 0;
        }
        const value = Number(bar.volume);
        if (!Number.isFinite(value) || value <= 0) {
            return 0;
        }
        return value;
    }

    function clamp(value, min, max) {
        if (!Number.isFinite(value)) {
            return min;
        }
        return Math.min(max, Math.max(min, value));
    }

    function hexToRgba(hex, alpha) {
        if (typeof hex !== "string" || !hex.startsWith("#") || hex.length !== 7) {
            return hex;
        }
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        if (![r, g, b].every(Number.isFinite)) {
            return hex;
        }
        const a = clamp(alpha, 0, 1);
        return `rgba(${r}, ${g}, ${b}, ${a})`;
    }

    function computeRecentMaxVolume(volumes) {
        if (!Array.isArray(volumes) || volumes.length === 0) {
            return 0;
        }
        const tail = volumes.slice(Math.max(0, volumes.length - VOLUME_WINDOW_SIZE));
        let maxValue = 0;
        for (const v of tail) {
            const num = Number(v);
            if (Number.isFinite(num) && num > maxValue) {
                maxValue = num;
            }
        }
        return maxValue;
    }

    function volumeToOpacity(volume, recentMax) {
        if (!Number.isFinite(recentMax) || recentMax <= 0) {
            return OPACITY_MAX;
        }
        const norm = clamp(Number(volume) / recentMax, 0, 1);
        return OPACITY_MIN + norm * (OPACITY_MAX - OPACITY_MIN);
    }

    function createChartController(container) {
        if (!container) {
            throw new Error("chart_adapter: контейнер не передано");
        }
        if (typeof LightweightCharts === "undefined") {
            throw new Error("chart_adapter: lightweight-charts не доступний");
        }

        const chart = LightweightCharts.createChart(container, DEFAULT_CHART_OPTIONS);
        const candles = chart.addCandlestickSeries({
            upColor: CANDLE_COLORS.up,
            wickUpColor: CANDLE_COLORS.up,
            downColor: CANDLE_COLORS.down,
            wickDownColor: CANDLE_COLORS.down,
            borderVisible: false,
            priceLineVisible: true,
            lastValueVisible: true,
        });
        const liveCandles = chart.addCandlestickSeries({
            upColor: "rgba(246, 195, 67, 0.18)",
            wickUpColor: CANDLE_COLORS.live,
            downColor: "rgba(246, 195, 67, 0.18)",
            wickDownColor: CANDLE_COLORS.live,
            borderVisible: true,
            borderUpColor: CANDLE_COLORS.live,
            borderDownColor: CANDLE_COLORS.live,
            priceLineVisible: false,
            lastValueVisible: false,
        });

        const volume = chart.addHistogramSeries({
            priceScaleId: "volume",
            priceFormat: {
                type: "volume",
            },
            base: 0,
        });
        const liveVolume = chart.addHistogramSeries({
            priceScaleId: "volume",
            priceFormat: {
                type: "volume",
            },
            base: 0,
        });
        chart.priceScale("volume").applyOptions({
            scaleMargins: {
                top: 0.82,
                bottom: 0.0,
            },
            borderVisible: false,
            ticksVisible: false,
        });

        let lastBar = null;
        let lastLiveBar = null;
        let lastLiveVolume = 0;
        let recentVolumeMax = 0;
        let recentVolumes = [];
        let eventMarkers = [];
        let poolLines = [];
        let rangeAreas = [];
        let zoneLines = [];
        let structureTriangles = [];
        let structureTriangleLabels = [];
        let oteOverlays = [];
        let barTimeSpanSeconds = 60;
        let chartTimeRange = { min: null, max: null };
        const priceScaleState = {
            manualRange: null,
            lastAutoRange: null,
        };
        let lastContainerSize = { width: 0, height: 0 };
        const interactionCleanup = [];
        const verticalPanState = {
            active: false,
            pending: false,
            startY: 0,
            startX: 0,
            startRange: null,
            baseRange: null,
        };
        const DRAG_ACTIVATION_PX = 6;
        const WHEEL_OPTIONS = { passive: false };
        const MIN_PRICE_SPAN = 1e-4;

        candles.applyOptions({
            autoscaleInfoProvider: (baseImplementation) => {
                if (!priceScaleState.manualRange) {
                    const base = baseImplementation();
                    if (base?.priceRange) {
                        priceScaleState.lastAutoRange = {
                            min: base.priceRange.minValue,
                            max: base.priceRange.maxValue,
                        };
                    }
                    return base;
                }
                const range = priceScaleState.manualRange;
                const base = baseImplementation();
                return {
                    priceRange: {
                        minValue: range.min,
                        maxValue: range.max,
                    },
                    margins: base?.margins,
                };
            },
        });

        setupPriceScaleInteractions();
        setupResizeHandling();

        function setBars(bars) {
            resetManualPriceScale({ silent: true });
            if (!Array.isArray(bars) || bars.length === 0) {
                candles.setData([]);
                liveCandles.setData([]);
                volume.setData([]);
                liveVolume.setData([]);
                lastBar = null;
                lastLiveBar = null;
                lastLiveVolume = 0;
                recentVolumeMax = 0;
                recentVolumes = [];
                chartTimeRange = { min: null, max: null };
                return;
            }

            const normalized = bars
                .map((bar) => {
                    const candle = normalizeBar(bar);
                    if (!candle) {
                        return null;
                    }
                    return {
                        candle,
                        volume: normalizeVolume(bar),
                    };
                })
                .filter(Boolean)
                .sort((a, b) => a.candle.time - b.candle.time);

            const candleData = normalized.map((row) => row.candle);
            const volumeValues = normalized.map((row) => row.volume);
            recentVolumes = volumeValues.slice(Math.max(0, volumeValues.length - VOLUME_WINDOW_SIZE));
            recentVolumeMax = computeRecentMaxVolume(volumeValues);

            const styledCandles = candleData.map((bar, index) => {
                const vol = volumeValues[index] ?? 0;
                if (!(recentVolumeMax > 0)) {
                    return bar;
                }
                const isUp = Number(bar.close) >= Number(bar.open);
                const alpha = volumeToOpacity(vol, recentVolumeMax);
                const base = isUp ? CANDLE_COLORS.up : CANDLE_COLORS.down;
                const rgba = hexToRgba(base, alpha);
                return {
                    ...bar,
                    color: rgba,
                    wickColor: rgba,
                    borderColor: rgba,
                };
            });

            candles.setData(styledCandles);
            if (recentVolumeMax > 0) {
                const volumeData = candleData.map((bar, index) => {
                    const vol = volumeValues[index] ?? 0;
                    const isUp = Number(bar.close) >= Number(bar.open);
                    const alpha = clamp(volumeToOpacity(vol, recentVolumeMax) * 0.6, 0.12, 0.85);
                    const base = isUp ? CANDLE_COLORS.up : CANDLE_COLORS.down;
                    return {
                        time: bar.time,
                        value: vol,
                        color: hexToRgba(base, alpha),
                    };
                });
                volume.setData(volumeData);
            } else {
                volume.setData([]);
            }

            lastBar = candleData.length ? candleData[candleData.length - 1] : null;
            // Якщо live-бар більше не відповідає історії — скинемо.
            if (lastLiveBar && lastBar && lastLiveBar.time < lastBar.time) {
                clearLiveBar();
            }
            updateBarTimeSpanFromBars(candleData);
            updateTimeRangeFromBars(candleData);
            chart.timeScale().fitContent();
        }

        function setLiveBar(bar) {
            const normalized = normalizeBar(bar);
            if (!normalized) {
                return;
            }
            const vol = normalizeVolume(bar);
            lastLiveVolume = vol;
            // Тримаємо рівно одну "живу" свічку.
            if (!lastLiveBar || normalized.time !== lastLiveBar.time) {
                liveCandles.setData([normalized]);
            } else {
                liveCandles.update(normalized);
            }
            lastLiveBar = normalized;

            if (vol > 0) {
                liveVolume.setData([
                    {
                        time: normalized.time,
                        value: vol,
                        color: "rgba(250, 204, 21, 0.35)",
                    },
                ]);
            } else {
                liveVolume.setData([]);
            }
        }

        function clearLiveBar() {
            liveCandles.setData([]);
            liveVolume.setData([]);
            lastLiveBar = null;
            lastLiveVolume = 0;
        }

        function updateLastBar(bar) {
            const normalized = normalizeBar(bar);
            if (!normalized) {
                return;
            }
            const vol = normalizeVolume(bar);
            if (!lastBar || normalized.time >= lastBar.time) {
                if (lastBar && normalized.time > lastBar.time) {
                    const diff = normalized.time - lastBar.time;
                    if (Number.isFinite(diff) && diff > 0) {
                        barTimeSpanSeconds = Math.max(
                            1,
                            Math.round((barTimeSpanSeconds * 3 + diff) / 4)
                        );
                    }
                }

                if (lastBar && normalized.time === lastBar.time && recentVolumes.length) {
                    recentVolumes[recentVolumes.length - 1] = vol;
                } else if (lastBar && normalized.time > lastBar.time) {
                    recentVolumes.push(vol);
                    if (recentVolumes.length > VOLUME_WINDOW_SIZE) {
                        recentVolumes.shift();
                    }
                }
                recentVolumeMax = computeRecentMaxVolume(recentVolumes);

                let candleToWrite = normalized;
                if (recentVolumeMax > 0) {
                    const isUp = Number(normalized.close) >= Number(normalized.open);
                    const alpha = volumeToOpacity(vol, recentVolumeMax);
                    const base = isUp ? CANDLE_COLORS.up : CANDLE_COLORS.down;
                    const rgba = hexToRgba(base, alpha);
                    candleToWrite = {
                        ...normalized,
                        color: rgba,
                        wickColor: rgba,
                        borderColor: rgba,
                    };
                }
                candles.update(candleToWrite);

                if (recentVolumeMax > 0) {
                    const isUp = Number(normalized.close) >= Number(normalized.open);
                    const alpha = clamp(volumeToOpacity(vol, recentVolumeMax) * 0.6, 0.12, 0.85);
                    const base = isUp ? CANDLE_COLORS.up : CANDLE_COLORS.down;
                    volume.update({
                        time: normalized.time,
                        value: vol,
                        color: hexToRgba(base, alpha),
                    });
                }
                lastBar = normalized;
                if (chartTimeRange.min == null) {
                    chartTimeRange.min = normalized.time;
                }
                chartTimeRange.max = Math.max(chartTimeRange.max ?? normalized.time, normalized.time);
            }
        }

        function clearEvents() {
            if (eventMarkers.length) {
                candles.setMarkers([]);
                eventMarkers = [];
            }
            clearStructureTriangles();
        }

        function clearPools() {
            poolLines.forEach((line) => candles.removePriceLine(line));
            poolLines = [];
        }

        function clearRanges() {
            rangeAreas.forEach((series) => chart.removeSeries(series));
            rangeAreas = [];
        }

        function clearZones() {
            zoneLines.forEach((line) => candles.removePriceLine(line));
            zoneLines = [];
        }

        function clearStructureTriangles() {
            if (structureTriangles.length) {
                structureTriangles.forEach((series) => {
                    try {
                        chart.removeSeries(series);
                    } catch (err) {
                        console.warn("chart_adapter: не вдалося прибрати трикутник", err);
                    }
                });
                structureTriangles = [];
            }
            if (structureTriangleLabels.length) {
                structureTriangleLabels.forEach((line) => {
                    try {
                        candles.removePriceLine(line);
                    } catch (err) {
                        console.warn("chart_adapter: не вдалося прибрати структуральний label", err);
                    }
                });
                structureTriangleLabels = [];
            }
        }

        function clearOteOverlays() {
            if (!oteOverlays.length) {
                return;
            }
            oteOverlays.forEach((overlay) => {
                overlay.series?.forEach((series) => {
                    try {
                        chart.removeSeries(series);
                    } catch (err) {
                        console.warn("chart_adapter: не вдалося прибрати OTE серію", err);
                    }
                });
                if (overlay.priceLine) {
                    try {
                        candles.removePriceLine(overlay.priceLine);
                    } catch (err) {
                        console.warn("chart_adapter: не вдалося прибрати OTE label", err);
                    }
                }
            });
            oteOverlays = [];
        }

        function setEvents(events) {
            clearEvents();
            if (!Array.isArray(events) || !events.length) {
                return;
            }
            withViewportPreserved(() => {
                const structureEvents = events.filter(isStructureEvent);
                if (!structureEvents.length) {
                    return;
                }
                const getEventTime = (evt) => {
                    const value = Number(evt.time ?? evt.ts ?? evt.timestamp ?? 0);
                    return Number.isFinite(value) ? value : 0;
                };
                const sortedEvents = structureEvents
                    .slice()
                    .sort((a, b) => getEventTime(a) - getEventTime(b));
                const recentEvents = sortedEvents.slice(-STRUCTURE_TRIANGLE.maxEvents);
                eventMarkers = sortedEvents
                    .map((evt) => {
                        const time = Number(evt.time);
                        if (!Number.isFinite(time)) return null;
                        const direction = (evt.direction || evt.dir || "").toUpperCase();
                        const color = direction === "SHORT" ? "#ef476f" : "#1ed760";
                        return {
                            time: Math.floor(time),
                            position: direction === "SHORT" ? "aboveBar" : "belowBar",
                            color,
                            shape: (evt.type || "").includes("CHOCH") ? "arrowUp" : "arrowDown",
                            text: `${evt.type || evt.event_type || ""}`.toUpperCase(),
                        };
                    })
                    .filter(Boolean);
                candles.setMarkers(eventMarkers);
                recentEvents.forEach((evt) => {
                    renderStructureTriangle(evt);
                });
            });
        }

        function setLiquidityPools(pools) {
            clearPools();
            if (!Array.isArray(pools) || !pools.length) {
                return;
            }
            pools.forEach((pool) => {
                const price = Number(pool.price);
                if (!Number.isFinite(price)) {
                    return;
                }
                const role = (pool.role || "").toUpperCase();
                const type = (pool.type || pool.kind || "").toUpperCase();
                const line = candles.createPriceLine({
                    price,
                    color: role === "PRIMARY" ? "#f9c74f" : "#577590",
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dashed,
                    axisLabelVisible: false,
                    title: `${type || "POOL"}`,
                });
                poolLines.push(line);
            });
        }

        function setRanges(ranges) {
            clearRanges();
            if (!Array.isArray(ranges) || !ranges.length) {
                return;
            }
            ranges.forEach((range) => {
                const minPrice = Number(range.min || range.price_min);
                const maxPrice = Number(range.max || range.price_max);
                const from = Number(range.start_time || range.from || range.time_start);
                const to = Number(range.end_time || range.to || range.time_end);
                if (
                    !Number.isFinite(minPrice) ||
                    !Number.isFinite(maxPrice) ||
                    !Number.isFinite(from) ||
                    !Number.isFinite(to)
                ) {
                    return;
                }
                const area = chart.addAreaSeries({
                    lineColor: "rgba(59, 130, 246, 0.8)",
                    topColor: "rgba(59, 130, 246, 0.2)",
                    bottomColor: "rgba(59, 130, 246, 0.05)",
                    priceLineVisible: false,
                });
                area.setData([
                    { time: Math.floor(from), value: maxPrice },
                    { time: Math.floor(to), value: maxPrice },
                ]);
                const bottom = chart.addAreaSeries({
                    lineColor: "rgba(59, 130, 246, 0.8)",
                    topColor: "rgba(59, 130, 246, 0.05)",
                    bottomColor: "rgba(59, 130, 246, 0.2)",
                    priceLineVisible: false,
                });
                bottom.setData([
                    { time: Math.floor(from), value: minPrice },
                    { time: Math.floor(to), value: minPrice },
                ]);
                rangeAreas.push(area, bottom);
            });
        }

        function setBandZones(zones, colors) {
            clearZones();
            if (!Array.isArray(zones) || !zones.length) {
                return;
            }
            zones.forEach((zone) => {
                const minPrice = Number(zone.min || zone.price_min || zone.ote_min);
                const maxPrice = Number(zone.max || zone.price_max || zone.ote_max);
                if (!Number.isFinite(minPrice) || !Number.isFinite(maxPrice)) {
                    return;
                }
                const label = zone.label || zone.type || zone.role || "ZONE";
                const lineMin = candles.createPriceLine({
                    price: minPrice,
                    color: colors.min,
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: false,
                    title: `${label} min`,
                });
                const lineMax = candles.createPriceLine({
                    price: maxPrice,
                    color: colors.max,
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: false,
                    title: `${label} max`,
                });
                zoneLines.push(lineMin, lineMax);
            });
        }

        function setOteZones(zones) {
            withViewportPreserved(() => {
                clearOteOverlays();
                if (!Array.isArray(zones) || !zones.length) {
                    return;
                }
                const domain = getChartTimeDomain();
                if (!domain) {
                    return;
                }
                const span = Math.max(domain.max - domain.min, barTimeSpanSeconds * 50);
                const left = domain.min ?? domain.max - span;
                const right = domain.max ?? left + span;
                zones.forEach((zone, index) => {
                    renderOteZone(zone, index, left, right);
                });
            });
        }

        function setZones(zones) {
            setBandZones(zones, { min: "#ffd166", max: "#ef476f" });
        }

        function clearAll() {
            candles.setData([]);
            liveCandles.setData([]);
            volume.setData([]);
            liveVolume.setData([]);
            lastBar = null;
            lastLiveBar = null;
            lastLiveVolume = 0;
            recentVolumeMax = 0;
            recentVolumes = [];
            clearEvents();
            clearPools();
            clearRanges();
            clearZones();
            clearOteOverlays();
            resetManualPriceScale({ silent: true });
            structureTriangles = [];
            chartTimeRange = { min: null, max: null };
        }

        return {
            setBars,
            updateLastBar,
            setLiveBar,
            clearLiveBar,
            setEvents,
            setOteZones,
            setLiquidityPools,
            setRanges,
            setZones,
            resizeToContainer,
            clearAll,
            dispose() {
                clearLiveBar();
                clearStructureTriangles();
                clearOteOverlays();
                interactionCleanup.splice(0).forEach((cleanup) => {
                    try {
                        cleanup();
                    } catch (err) {
                        console.warn("chart_adapter: не вдалося очистити обробник", err);
                    }
                });
                container.classList.remove("vertical-pan-active");
                chart.remove();
            },
        };

        function resizeToContainer() {
            if (!container || typeof container.getBoundingClientRect !== "function") {
                return;
            }
            const rect = container.getBoundingClientRect();
            const width = Math.floor(rect.width);
            const height = Math.floor(rect.height);
            if (
                !Number.isFinite(width) ||
                !Number.isFinite(height) ||
                width <= 0 ||
                height <= 0
            ) {
                return;
            }
            if (lastContainerSize.width === width && lastContainerSize.height === height) {
                return;
            }
            lastContainerSize = { width, height };
            chart.applyOptions({ width, height });
        }

        function renderStructureTriangle(evt) {
            if (!evt) {
                return;
            }
            const price = Number(evt.price ?? evt.level);
            const time = Number(evt.time ?? evt.ts ?? evt.timestamp);
            if (!Number.isFinite(price) || !Number.isFinite(time)) {
                return;
            }
            const normalizedTime = Math.floor(time);
            const direction = (evt.direction || evt.dir || "").toUpperCase();
            const type = (evt.type || evt.event_type || "").toUpperCase();
            const color = type.includes("CHOCH")
                ? STRUCTURE_TRIANGLE.colors.choch
                : STRUCTURE_TRIANGLE.colors.bos;
            const priceRange = getEffectivePriceRange();
            const fallbackSpan = Math.max(Math.abs(price) * 0.02, 1);
            const rangeSpan = priceRange
                ? priceRange.max - priceRange.min
                : fallbackSpan;
            const widthSeconds = Math.max(
                STRUCTURE_TRIANGLE.minWidthSec,
                Math.round(barTimeSpanSeconds * STRUCTURE_TRIANGLE.widthBars)
            );
            const halfWidth = Math.max(1, Math.round(widthSeconds / 2));
            const leftTime = Math.max(0, normalizedTime - halfWidth);
            const rightTime = normalizedTime + halfWidth;
            const minHeightFromPrice = Math.max(
                STRUCTURE_TRIANGLE.minHeight,
                Math.abs(price) * (STRUCTURE_TRIANGLE.minHeightPct || 0)
            );
            const height = Math.max(
                minHeightFromPrice,
                rangeSpan * STRUCTURE_TRIANGLE.heightRatio
            );
            const isShort = direction === "SHORT";
            const basePrice = isShort ? price + height : price - height;
            const edgesSeries = createOverlaySeries(color, STRUCTURE_TRIANGLE.edgeWidth);
            edgesSeries.setData([
                { time: leftTime, value: basePrice },
                { time: normalizedTime, value: price },
                { time: rightTime, value: basePrice },
            ]);
            const baseSeries = createOverlaySeries(color, STRUCTURE_TRIANGLE.baseWidth);
            baseSeries.setData([
                { time: leftTime, value: basePrice },
                { time: rightTime, value: basePrice },
            ]);
            structureTriangles.push(edgesSeries, baseSeries);
            const priceLineTitle = [type || "STRUCT", direction || ""]
                .map((part) => part.trim())
                .filter(Boolean)
                .join(" ");
            const priceLine = candles.createPriceLine({
                price,
                color,
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                lineVisible: false,
                title: priceLineTitle || "STRUCT",
            });
            structureTriangleLabels.push(priceLine);
        }

        function renderOteZone(zone, index, left, right) {
            if (!zone) {
                return;
            }
            const minPrice = Number(zone.min ?? zone.price_min ?? zone.ote_min);
            const maxPrice = Number(zone.max ?? zone.price_max ?? zone.ote_max);
            if (!Number.isFinite(minPrice) || !Number.isFinite(maxPrice) || minPrice >= maxPrice) {
                return;
            }
            const direction = (zone.direction || "").toUpperCase();
            const palette = direction === "SHORT" ? OTE_STYLES.SHORT : OTE_STYLES.LONG;
            const safeLeft = Math.floor(left);
            const safeRight = Math.max(safeLeft + 1, Math.floor(right));
            const topSeries = createOverlaySeries(palette.border, 1);
            topSeries.setData([
                { time: safeLeft, value: maxPrice },
                { time: safeRight, value: maxPrice },
            ]);
            const bottomSeries = createOverlaySeries(palette.border, 1);
            bottomSeries.setData([
                { time: safeLeft, value: minPrice },
                { time: safeRight, value: minPrice },
            ]);
            const overlaySeries = [topSeries, bottomSeries];
            const priceLine = candles.createPriceLine({
                price: (minPrice + maxPrice) / 2,
                color: palette.axisLabel,
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                lineVisible: false,
                title: `${direction || (direction === "SHORT" ? "SHORT" : "LONG")} OTE`,
            });
            oteOverlays.push({
                series: overlaySeries,
                priceLine,
            });
        }

        function isStructureEvent(evt) {
            if (!evt) {
                return false;
            }
            const kind = (evt.type || evt.event_type || "").toUpperCase();
            return kind.includes("BOS") || kind.includes("CHOCH");
        }

        function getChartTimeDomain() {
            if (
                chartTimeRange.min != null &&
                chartTimeRange.max != null &&
                chartTimeRange.max > chartTimeRange.min
            ) {
                return {
                    min: chartTimeRange.min,
                    max: chartTimeRange.max,
                };
            }
            if (lastBar?.time) {
                const fallbackMin = lastBar.time - barTimeSpanSeconds * 200;
                return {
                    min: Math.max(0, fallbackMin),
                    max: lastBar.time,
                };
            }
            return null;
        }

        function updateBarTimeSpanFromBars(bars) {
            if (!Array.isArray(bars) || bars.length < 2) {
                return;
            }
            let total = 0;
            let count = 0;
            for (let i = bars.length - 1; i > 0 && count < 32; i -= 1) {
                const diff = bars[i].time - bars[i - 1].time;
                if (Number.isFinite(diff) && diff > 0) {
                    total += diff;
                    count += 1;
                }
            }
            if (count) {
                barTimeSpanSeconds = Math.max(1, Math.round(total / count));
            }
        }

        function updateTimeRangeFromBars(bars) {
            if (!Array.isArray(bars) || !bars.length) {
                chartTimeRange = { min: null, max: null };
                return;
            }
            chartTimeRange = {
                min: bars[0].time,
                max: bars[bars.length - 1].time,
            };
        }

        function clampTime(value, min, max) {
            if (!Number.isFinite(value)) {
                return min;
            }
            return Math.max(min, Math.min(max, value));
        }

        function createOverlaySeries(color, lineWidth) {
            return chart.addLineSeries({
                color,
                lineWidth,
                priceScaleId: "right",
                lastValueVisible: false,
                priceLineVisible: false,
                crosshairMarkerVisible: false,
                autoscaleInfoProvider: () => null,
            });
        }

        function withViewportPreserved(action) {
            const logicalRange = chart.timeScale().getVisibleLogicalRange();
            const scrollPos = chart.timeScale().scrollPosition();
            action();
            if (logicalRange) {
                chart.timeScale().setVisibleLogicalRange({
                    from: logicalRange.from,
                    to: logicalRange.to,
                });
            } else if (Number.isFinite(scrollPos)) {
                chart.timeScale().scrollToPosition(scrollPos, false);
            }
        }

        function setupPriceScaleInteractions() {
            if (!container || typeof window === "undefined") {
                return;
            }

            const handleWheel = (event) => {
                const pointerInAxis = isPointerInPriceAxis(event);
                const pointerInPane = isPointerInsidePane(event);
                if (!pointerInAxis && !(event.shiftKey && pointerInPane)) {
                    return;
                }
                if (!getEffectivePriceRange()) {
                    return;
                }
                event.preventDefault();
                event.stopPropagation();
                if (event.shiftKey) {
                    applyWheelPan(event);
                    return;
                }
                if (pointerInAxis) {
                    applyWheelZoom(event);
                }
            };
            container.addEventListener("wheel", handleWheel, WHEEL_OPTIONS);
            interactionCleanup.push(() => container.removeEventListener("wheel", handleWheel, WHEEL_OPTIONS));

            const handleMouseDown = (event) => {
                if (event.button !== 0 || !isPointerInsidePane(event)) {
                    return;
                }
                const currentRange = getEffectivePriceRange();
                if (!currentRange) {
                    return;
                }
                verticalPanState.pending = true;
                verticalPanState.active = false;
                verticalPanState.startY = event.clientY;
                verticalPanState.startX = event.clientX;
                verticalPanState.baseRange = currentRange;
                verticalPanState.startRange = null;
            };
            container.addEventListener("mousedown", handleMouseDown, true);
            interactionCleanup.push(() => container.removeEventListener("mousedown", handleMouseDown, true));

            const handleMouseMove = (event) => {
                if (!verticalPanState.pending) {
                    return;
                }
                const paneHeight = getPaneMetrics().paneHeight;
                if (!paneHeight) {
                    return;
                }
                const deltaY = event.clientY - verticalPanState.startY;
                const deltaX = event.clientX - verticalPanState.startX;
                if (!verticalPanState.active) {
                    if (
                        Math.abs(deltaY) < DRAG_ACTIVATION_PX ||
                        Math.abs(deltaY) <= Math.abs(deltaX)
                    ) {
                        return;
                    }
                    ensureManualRange(verticalPanState.baseRange);
                    verticalPanState.startRange = { ...priceScaleState.manualRange };
                    verticalPanState.active = true;
                    container.classList.add("vertical-pan-active");
                }
                event.preventDefault();
                event.stopPropagation();
                const span = verticalPanState.startRange.max - verticalPanState.startRange.min;
                if (!(span > 0)) {
                    return;
                }
                const offset = (deltaY / paneHeight) * span;
                applyManualRange({
                    min: verticalPanState.startRange.min + offset,
                    max: verticalPanState.startRange.max + offset,
                });
            };
            window.addEventListener("mousemove", handleMouseMove);
            interactionCleanup.push(() => window.removeEventListener("mousemove", handleMouseMove));

            const stopVerticalPan = () => {
                if (!verticalPanState.pending) {
                    return;
                }
                verticalPanState.pending = false;
                verticalPanState.active = false;
                verticalPanState.startRange = null;
                verticalPanState.baseRange = null;
                container.classList.remove("vertical-pan-active");
            };
            const handleMouseUp = () => {
                stopVerticalPan();
            };
            window.addEventListener("mouseup", handleMouseUp);
            interactionCleanup.push(() => window.removeEventListener("mouseup", handleMouseUp));
            window.addEventListener("blur", stopVerticalPan);
            interactionCleanup.push(() => window.removeEventListener("blur", stopVerticalPan));

            const handleLeave = () => {
                stopVerticalPan();
            };
            container.addEventListener("mouseleave", handleLeave);
            interactionCleanup.push(() => container.removeEventListener("mouseleave", handleLeave));

            const handleDblClick = (event) => {
                if (isPointerInPriceAxis(event)) {
                    resetManualPriceScale();
                }
            };
            container.addEventListener("dblclick", handleDblClick);
            interactionCleanup.push(() => container.removeEventListener("dblclick", handleDblClick));

            function applyWheelPan(event) {
                const currentRange = getEffectivePriceRange();
                if (!currentRange) {
                    return;
                }
                ensureManualRange(currentRange);
                const paneHeight = getPaneMetrics().paneHeight;
                if (!paneHeight) {
                    return;
                }
                const span = priceScaleState.manualRange.max - priceScaleState.manualRange.min;
                if (!(span > 0)) {
                    return;
                }
                const offset = (-event.deltaY / paneHeight) * span * 0.5;
                applyManualRange({
                    min: priceScaleState.manualRange.min + offset,
                    max: priceScaleState.manualRange.max + offset,
                });
            }

            function applyWheelZoom(event) {
                const currentRange = getEffectivePriceRange();
                if (!currentRange) {
                    return;
                }
                const anchor = getAnchorPriceFromEvent(event);
                if (!Number.isFinite(anchor)) {
                    return;
                }
                const span = currentRange.max - currentRange.min;
                if (!(span > 0)) {
                    return;
                }
                const intensity = 0.002;
                const scale = Math.exp(Math.min(Math.abs(event.deltaY), 600) * intensity);
                const factor = event.deltaY < 0 ? 1 / scale : scale;
                const distanceMin = anchor - currentRange.min;
                const distanceMax = currentRange.max - anchor;
                const nextRange = normalizeRange({
                    min: anchor - distanceMin * factor,
                    max: anchor + distanceMax * factor,
                });
                if (nextRange) {
                    applyManualRange(nextRange);
                }
            }
        }

        function setupResizeHandling() {
            if (!container || typeof window === "undefined") {
                return;
            }
            const schedule = () => {
                const raf = window.requestAnimationFrame || window.setTimeout;
                raf(() => resizeToContainer());
            };
            if (typeof ResizeObserver !== "undefined") {
                const resizeObserver = new ResizeObserver(() => {
                    schedule();
                });
                resizeObserver.observe(container);
                interactionCleanup.push(() => {
                    try {
                        resizeObserver.disconnect();
                    } catch (err) {
                        console.warn("chart_adapter: не вдалося відписатися від ResizeObserver", err);
                    }
                });
            } else {
                const handleResize = () => {
                    schedule();
                };
                window.addEventListener("resize", handleResize);
                interactionCleanup.push(() => window.removeEventListener("resize", handleResize));
            }
            schedule();
        }

        function getRelativePointer(event) {
            const rect = container.getBoundingClientRect();
            return {
                x: event.clientX - rect.left,
                y: event.clientY - rect.top,
                width: rect.width,
                height: rect.height,
            };
        }

        function getPaneMetrics() {
            const paneSize = chart.paneSize() || {};
            const priceScaleWidth = chart.priceScale("right").width() || 0;
            return {
                paneWidth: paneSize.width || 0,
                paneHeight: paneSize.height || 0,
                priceScaleWidth,
            };
        }

        function isPointerInPriceAxis(event) {
            const pointer = getRelativePointer(event);
            const { paneWidth, paneHeight, priceScaleWidth } = getPaneMetrics();
            if (!paneHeight || !priceScaleWidth) {
                return false;
            }
            return (
                pointer.x >= paneWidth &&
                pointer.x <= paneWidth + priceScaleWidth &&
                pointer.y >= 0 &&
                pointer.y <= paneHeight
            );
        }

        function isPointerInsidePane(event) {
            const pointer = getRelativePointer(event);
            const { paneWidth, paneHeight } = getPaneMetrics();
            if (!paneWidth || !paneHeight) {
                return false;
            }
            return (
                pointer.x >= 0 &&
                pointer.x <= paneWidth &&
                pointer.y >= 0 &&
                pointer.y <= paneHeight
            );
        }

        function getAnchorPriceFromEvent(event) {
            const { paneHeight } = getPaneMetrics();
            if (!paneHeight) {
                return null;
            }
            const pointer = getRelativePointer(event);
            const clampedY = Math.max(0, Math.min(pointer.y, paneHeight));
            return candles.coordinateToPrice(clampedY);
        }

        function normalizeRange(range) {
            if (!range) {
                return null;
            }
            let { min, max } = range;
            if (!Number.isFinite(min) || !Number.isFinite(max)) {
                return null;
            }
            if (min === max) {
                min -= MIN_PRICE_SPAN / 2;
                max += MIN_PRICE_SPAN / 2;
            }
            if (max - min < MIN_PRICE_SPAN) {
                const mid = (max + min) / 2;
                min = mid - MIN_PRICE_SPAN / 2;
                max = mid + MIN_PRICE_SPAN / 2;
            }
            if (max <= min) {
                return null;
            }
            return { min, max };
        }

        function applyManualRange(range) {
            const normalized = normalizeRange(range);
            if (!normalized) {
                return;
            }
            priceScaleState.manualRange = normalized;
            requestPriceScaleSync();
        }

        function ensureManualRange(baseRange) {
            if (!priceScaleState.manualRange && baseRange) {
                priceScaleState.manualRange = { ...baseRange };
            }
        }

        function getEffectivePriceRange() {
            if (priceScaleState.manualRange) {
                return { ...priceScaleState.manualRange };
            }
            if (priceScaleState.lastAutoRange) {
                return { ...priceScaleState.lastAutoRange };
            }
            const { paneHeight } = getPaneMetrics();
            if (!paneHeight) {
                return null;
            }
            const top = candles.coordinateToPrice(0);
            const bottom = candles.coordinateToPrice(paneHeight);
            if (!Number.isFinite(top) || !Number.isFinite(bottom)) {
                return null;
            }
            const min = Math.min(top, bottom);
            const max = Math.max(top, bottom);
            if (!(max > min)) {
                return null;
            }
            priceScaleState.lastAutoRange = { min, max };
            return { min, max };
        }

        function requestPriceScaleSync() {
            const logicalRange = chart.timeScale().getVisibleLogicalRange();
            if (logicalRange) {
                chart.timeScale().setVisibleLogicalRange({
                    from: logicalRange.from,
                    to: logicalRange.to,
                });
                return;
            }
            const position = chart.timeScale().scrollPosition();
            if (Number.isFinite(position)) {
                chart.timeScale().scrollToPosition(position, false);
            }
        }

        function resetManualPriceScale(options = {}) {
            priceScaleState.manualRange = null;
            if (!options.silent) {
                requestPriceScaleSync();
            }
        }
    }

    window.createChartController = createChartController;
})();
