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
                return;
            }
            const normalized = bars
                .map(normalizeBar)
                .filter(Boolean)
                .sort((a, b) => a.time - b.time);
            candles.setData(normalized);
            lastBar = normalized.length ? normalized[normalized.length - 1] : null;
            chart.timeScale().fitContent();
        }

        function updateLastBar(bar) {
            const normalized = normalizeBar(bar);
            if (!normalized) {
                return;
            }
            if (!lastBar || normalized.time >= lastBar.time) {
                candles.update(normalized);
                lastBar = normalized;
            }
        }

        function clearEvents() {
            if (eventMarkers.length) {
                candles.setMarkers([]);
                eventMarkers = [];
            }
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

        function setEvents(events) {
            clearEvents();
            if (!Array.isArray(events) || !events.length) {
                return;
            }
            eventMarkers = events
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
            setBandZones(zones, { min: "#06d6a0", max: "#1b9aaa" });
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
            resetManualPriceScale({ silent: true });
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
