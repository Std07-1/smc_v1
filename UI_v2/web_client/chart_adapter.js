(function () {
    const DEFAULT_CHART_OPTIONS = {
        layout: {
            background: { color: "#0e111d" },
            textColor: "#d1d4dc",
        },
        grid: {
            vertLines: { color: "rgba(197, 203, 206, 0.15)" },
            horzLines: { color: "rgba(197, 203, 206, 0.15)" },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
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
            borderColor: "rgba(255, 255, 255, 0.08)",
            autoScale: true,
            scaleMargins: {
                top: 0.12,
                bottom: 0.18,
            },
        },
        timeScale: {
            borderColor: "rgba(255, 255, 255, 0.08)",
            rightOffset: 2,
            timeVisible: true,
            secondsVisible: false,
            fixLeftEdge: false,
            fixRightEdge: false,
            lockVisibleTimeRangeOnResize: false,
        },
    };

    const STRUCTURE_TRIANGLE = {
        widthBars: 4,
        minWidthSec: 60,
        heightRatio: 0.25,
        minHeight: 0.003,
        colors: {
            bos: "#4ade80",
            choch: "#facc15",
        },
        maxEvents: 8,
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

    function createChartController(container) {
        if (!container) {
            throw new Error("chart_adapter: контейнер не передано");
        }
        if (typeof LightweightCharts === "undefined") {
            throw new Error("chart_adapter: lightweight-charts не доступний");
        }

        const chart = LightweightCharts.createChart(container, DEFAULT_CHART_OPTIONS);
        const candles = chart.addCandlestickSeries({
            upColor: "#1ed760",
            wickUpColor: "#1ed760",
            downColor: "#ef476f",
            wickDownColor: "#ef476f",
            borderVisible: false,
        });
        let lastBar = null;
        let eventMarkers = [];
        let poolLines = [];
        let rangeAreas = [];
        let zoneLines = [];
        let structureTriangles = [];
        let oteOverlays = [];
        let barTimeSpanSeconds = 60;
        let chartTimeRange = { min: null, max: null };
        const priceScaleState = {
            manualRange: null,
            lastAutoRange: null,
        };
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

        function setBars(bars) {
            resetManualPriceScale({ silent: true });
            if (!Array.isArray(bars) || bars.length === 0) {
                candles.setData([]);
                lastBar = null;
                chartTimeRange = { min: null, max: null };
                return;
            }
            const normalized = bars
                .map(normalizeBar)
                .filter(Boolean)
                .sort((a, b) => a.time - b.time);
            candles.setData(normalized);
            lastBar = normalized.length ? normalized[normalized.length - 1] : null;
            updateBarTimeSpanFromBars(normalized);
            updateTimeRangeFromBars(normalized);
            chart.timeScale().fitContent();
        }

        function updateLastBar(bar) {
            const normalized = normalizeBar(bar);
            if (!normalized) {
                return;
            }
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
                candles.update(normalized);
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
            if (!structureTriangles.length) {
                return;
            }
            structureTriangles.forEach((series) => {
                try {
                    chart.removeSeries(series);
                } catch (err) {
                    console.warn("chart_adapter: не вдалося прибрати трикутник", err);
                }
            });
            structureTriangles = [];
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
                const recentEvents = structureEvents.slice(-STRUCTURE_TRIANGLE.maxEvents);
                eventMarkers = structureEvents
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
            lastBar = null;
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
            setEvents,
            setOteZones,
            setLiquidityPools,
            setRanges,
            setZones,
            clearAll,
            dispose() {
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
            const rangeSpan = priceRange
                ? priceRange.max - priceRange.min
                : Math.max(Math.abs(price) * 0.02, 1);
            const widthSeconds = Math.max(
                STRUCTURE_TRIANGLE.minWidthSec,
                Math.round(barTimeSpanSeconds * STRUCTURE_TRIANGLE.widthBars)
            );
            const halfWidth = Math.max(1, Math.round(widthSeconds / 2));
            const leftTime = Math.max(0, normalizedTime - halfWidth);
            const rightTime = normalizedTime + halfWidth;
            const height = Math.max(
                STRUCTURE_TRIANGLE.minHeight,
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
