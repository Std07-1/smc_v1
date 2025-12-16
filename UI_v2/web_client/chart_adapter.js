(function () {
    const CANDLE_COLORS = {
        up: "#26a69a",
        down: "#ef5350",
        live: "#f6c343",
    };

    const VOLUME_WINDOW_SIZE = 200;
    const OPACITY_MIN = 0.25;
    const OPACITY_MAX = 1.0;
    const VOLUME_BAR_ALPHA = 0.55;
    const VOLUME_SCALE_QUANTILE = 0.98;

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
            rightOffset: 0,
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
            border: "rgba(34, 197, 94, 0.45)",
            arrow: "rgba(34, 197, 94, 0.65)",
            axisLabel: "rgba(34, 197, 94, 0.65)",
        },
        SHORT: {
            border: "rgba(248, 113, 113, 0.45)",
            arrow: "rgba(248, 113, 113, 0.65)",
            axisLabel: "rgba(248, 113, 113, 0.65)",
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

    function computeVolumeScaleMax(volumes, quantile = VOLUME_SCALE_QUANTILE) {
        if (!Array.isArray(volumes) || volumes.length === 0) {
            return 1;
        }
        const cleaned = volumes
            .map((v) => Number(v))
            .filter((v) => Number.isFinite(v) && v > 0)
            .sort((a, b) => a - b);
        if (!cleaned.length) {
            return 1;
        }

        // Кеп по квантилю, щоб один спайк не "вбивав" масштаб для всіх інших брусків.
        const q = clamp(Number(quantile), 0.5, 1.0);
        const idx = Math.min(cleaned.length - 1, Math.floor((cleaned.length - 1) * q));
        const qValue = cleaned[idx] ?? 1;
        const maxAll = cleaned[cleaned.length - 1] ?? qValue;

        // Якщо даних мало — краще показати повний max, ніж різко обрізати.
        const useMaxAll = cleaned.length < 40;
        const chosen = useMaxAll ? maxAll : qValue;
        return Math.max(1, chosen);
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
        const tooltipEl =
            typeof document !== "undefined"
                ? container
                    ?.closest(".chart-overlay-shell")
                    ?.querySelector("#chart-hover-tooltip")
                : null;

        const sessionsScaleId = "sessions";
        const sessionSeries = {
            enabled: true,
            // Сесії малюємо як «суцільні блоки» у власній шкалі 0..1.
            // Критично: кожен блок — окрема BaselineSeries з 2 точками (start/end),
            // щоб не було «підкошених» країв (діагоналей) і щоб не з'єднувались різні дні.
            bands: {
                asia: [],
                london: [],
                newYork: [],
            },
        };

        const SESSION_BAND_POOL_SIZE = 24;
        const SESSION_BAND_VALUE = 1;

        function createSessionBand(fillRgba) {
            const band = chart.addBaselineSeries({
                priceScaleId: sessionsScaleId,
                baseValue: { type: "price", price: 0 },
                autoscaleInfoProvider: () => ({
                    priceRange: {
                        minValue: 0,
                        maxValue: 1,
                    },
                }),
                baseLineVisible: false,
                baseLineWidth: 0,
                topFillColor1: fillRgba,
                topFillColor2: fillRgba,
                bottomFillColor1: fillRgba,
                bottomFillColor2: fillRgba,
                lineWidth: 0,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            band.applyOptions({ visible: false });
            band.setData([]);
            return band;
        }

        for (let i = 0; i < SESSION_BAND_POOL_SIZE; i += 1) {
            sessionSeries.bands.asia.push(createSessionBand("rgba(38, 166, 154, 0.06)"));
            sessionSeries.bands.london.push(createSessionBand("rgba(246, 195, 67, 0.055)"));
            sessionSeries.bands.newYork.push(createSessionBand("rgba(239, 83, 80, 0.055)"));
        }

        // “A по даних”: бокс поточної сесії (high/low) на price-scale.
        // Без ліній/лейблів — лише заливка між low↔high.
        const sessionRangeBox = chart.addBaselineSeries({
            baseValue: { type: "price", price: 0 },
            baseLineVisible: false,
            baseLineWidth: 0,
            lineVisible: false,
            lineColor: "rgba(0, 0, 0, 0)",
            topLineColor: "rgba(0, 0, 0, 0)",
            bottomLineColor: "rgba(0, 0, 0, 0)",
            topFillColor1: "rgba(209, 212, 220, 0.08)",
            topFillColor2: "rgba(209, 212, 220, 0.04)",
            bottomFillColor1: "rgba(209, 212, 220, 0.08)",
            bottomFillColor2: "rgba(209, 212, 220, 0.04)",
            lineWidth: 0,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        });
        sessionRangeBox.applyOptions({ visible: false });
        sessionRangeBox.setData([]);
        let lastSessionRangeRequest = null;
        chart.priceScale(sessionsScaleId).applyOptions({
            scaleMargins: {
                top: 0.0,
                bottom: 0.0,
            },
            borderVisible: false,
            ticksVisible: false,
        });

        const candles = chart.addCandlestickSeries({
            upColor: CANDLE_COLORS.up,
            wickUpColor: CANDLE_COLORS.up,
            downColor: CANDLE_COLORS.down,
            wickDownColor: CANDLE_COLORS.down,
            borderVisible: false,
            // Вимикаємо дефолтний «лейбл поточної ціни» серії,
            // щоб керувати ним вручну (менший текст + динамічний колір up/down).
            priceLineVisible: false,
            lastValueVisible: false,
        });
        const liveCandles = chart.addCandlestickSeries({
            upColor: "rgba(246, 195, 67, 0.18)",
            wickUpColor: CANDLE_COLORS.live,
            downColor: "rgba(246, 195, 67, 0.18)",
            wickDownColor: CANDLE_COLORS.live,
            borderVisible: true,
            borderUpColor: CANDLE_COLORS.live,
            borderDownColor: CANDLE_COLORS.live,
            // Важливо для UX: «жива» ціна має оновлюватися разом зі свічкою,
            // а не лише по закритій свічці (історичний candles-series).
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
        volume.applyOptions({
            lastValueVisible: false,
            priceLineVisible: false,
        });
        const liveVolume = chart.addHistogramSeries({
            priceScaleId: "volume",
            priceFormat: {
                type: "volume",
            },
            base: 0,
        });
        liveVolume.applyOptions({
            lastValueVisible: false,
            priceLineVisible: false,
        });
        chart.priceScale("volume").applyOptions({
            scaleMargins: {
                top: 0.76,
                bottom: 0.0,
            },
            borderVisible: false,
            ticksVisible: false,
        });

        let lastBar = null;
        let lastLiveBar = null;
        let lastLiveVolume = 0;
        let lastCandleDataset = [];
        let lastCandleTimes = [];
        let currentPriceLine = null;
        let currentPriceLineOwner = null;
        let currentPriceLineState = { price: null, color: null, owner: null };
        // Глобальний max обсягу для фіксованого autoscale.
        // Якщо автоскейл рахувати лише по видимому діапазону, при скролі/зумі volume «стрибає».
        let volumeScaleMax = 1;
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
        let lastBarsSignature = null;
        let autoFitDone = false;
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
            pointerId: null,
        };
        const DRAG_ACTIVATION_PX = 6;
        const WHEEL_OPTIONS = { passive: false };
        const MIN_PRICE_SPAN = 1e-4;

        function priceScaleAutoscaleInfoProvider(baseImplementation) {
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

            // Коли активний manualRange (наш vertical-pan), всі серії на правій шкалі
            // мають повертати ОДНАКОВИЙ priceRange, інакше lightweight-charts буде
            // “склеювати” діапазони і виходить ефект «стеля/підлога».
            const range = priceScaleState.manualRange;
            const base = baseImplementation();
            return {
                priceRange: {
                    minValue: range.min,
                    maxValue: range.max,
                },
                margins: base?.margins,
            };
        }

        function sessionRangeBoxAutoscaleInfoProvider(baseImplementation) {
            // Під час manualRange — поводимось ідентично до інших серій (щоб не було «стеля/підлога»).
            if (priceScaleState.manualRange) {
                return priceScaleAutoscaleInfoProvider(baseImplementation);
            }

            const base = baseImplementation?.() || null;
            const range = lastSessionRangeRequest;
            const low = Number(range?.low);
            const high = Number(range?.high);
            const from = Number(range?.from);
            const to = Number(range?.to);

            if (
                !range ||
                !Number.isFinite(low) ||
                !Number.isFinite(high) ||
                !Number.isFinite(from) ||
                !Number.isFinite(to) ||
                !(to > from) ||
                !(high >= low)
            ) {
                return base;
            }

            // Важливо: точки baseline можуть бути поза видимим time-range,
            // але сегмент між ними все одно видно. Тому явно додаємо low/high в autoscale,
            // якщо бокс перетинає видиму область часу.
            const visible = chart?.timeScale?.()?.getVisibleRange?.() || null;
            if (visible && Number.isFinite(visible.from) && Number.isFinite(visible.to)) {
                const boxFrom = Math.floor(from);
                const boxTo = Math.floor(to);
                const overlaps = !(visible.to < boxFrom || visible.from > boxTo);
                if (!overlaps) {
                    return base;
                }
            }

            const baseMin = Number(base?.priceRange?.minValue);
            const baseMax = Number(base?.priceRange?.maxValue);
            const minValue = Number.isFinite(baseMin) ? Math.min(baseMin, low) : low;
            const maxValue = Number.isFinite(baseMax) ? Math.max(baseMax, high) : high;

            return {
                priceRange: {
                    minValue,
                    maxValue,
                },
                margins: base?.margins,
            };
        }

        candles.applyOptions({ autoscaleInfoProvider: priceScaleAutoscaleInfoProvider });
        liveCandles.applyOptions({ autoscaleInfoProvider: priceScaleAutoscaleInfoProvider });
        sessionRangeBox.applyOptions({ autoscaleInfoProvider: sessionRangeBoxAutoscaleInfoProvider });

        setupPriceScaleInteractions();
        setupResizeHandling();
        setupHoverTooltip();

        function setupHoverTooltip() {
            if (!tooltipEl || typeof chart.subscribeCrosshairMove !== "function") {
                return;
            }

            let hoverTimer = null;
            let lastPayload = null;

            const clearHoverTimer = () => {
                if (hoverTimer) {
                    clearTimeout(hoverTimer);
                    hoverTimer = null;
                }
            };

            const hideTooltip = () => {
                clearHoverTimer();
                tooltipEl.hidden = true;
                tooltipEl.textContent = "";
            };

            const formatCompact = (value) => {
                const num = Number(value);
                if (!Number.isFinite(num)) return "-";
                if (Math.abs(num) >= 1000) return String(Math.round(num));
                if (Math.abs(num) >= 1) return num.toFixed(2);
                return num.toPrecision(4);
            };

            const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

            chart.subscribeCrosshairMove((param) => {
                if (!param || !param.time || !param.point) {
                    hideTooltip();
                    return;
                }

                const seriesData = param.seriesData;
                const candle = seriesData?.get?.(candles) || seriesData?.get?.(liveCandles) || null;
                const volRow =
                    seriesData?.get?.(liveVolume) ||
                    seriesData?.get?.(volume) ||
                    null;

                if (!candle) {
                    hideTooltip();
                    return;
                }

                lastPayload = {
                    point: param.point,
                    candle,
                    volume: volRow?.value ?? null,
                };

                clearHoverTimer();
                tooltipEl.hidden = true;

                hoverTimer = setTimeout(() => {
                    const payload = lastPayload;
                    if (!payload) {
                        hideTooltip();
                        return;
                    }

                    const price = payload.candle?.close;
                    const vol = payload.volume;
                    tooltipEl.textContent = `Ціна: ${formatCompact(price)}\nОбсяг: ${formatCompact(vol)}`;

                    // Перетворимо \n на реальні рядки без innerHTML.
                    tooltipEl.style.whiteSpace = "pre";
                    tooltipEl.hidden = false;

                    const shell = tooltipEl.offsetParent || tooltipEl.parentElement;
                    if (!shell || typeof shell.getBoundingClientRect !== "function") {
                        return;
                    }
                    const shellRect = shell.getBoundingClientRect();
                    const containerRect = container.getBoundingClientRect();

                    // Координати param.point — відносно області графіка (container).
                    const baseLeft = (containerRect.left - shellRect.left) + payload.point.x + 12;
                    const baseTop = (containerRect.top - shellRect.top) + payload.point.y + 12;

                    const tipRect = tooltipEl.getBoundingClientRect();
                    const maxLeft = shellRect.width - tipRect.width - 8;
                    const maxTop = shellRect.height - tipRect.height - 8;
                    const left = clamp(baseLeft, 8, Math.max(8, maxLeft));
                    const top = clamp(baseTop, 8, Math.max(8, maxTop));

                    tooltipEl.style.left = `${left}px`;
                    tooltipEl.style.top = `${top}px`;
                }, 1000);
            });

            container.addEventListener("mouseleave", hideTooltip);
            interactionCleanup.push(() => container.removeEventListener("mouseleave", hideTooltip));
            interactionCleanup.push(() => {
                clearHoverTimer();
                if (tooltipEl) {
                    tooltipEl.hidden = true;
                }
            });
        }

        function setBars(bars) {
            // Якщо користувач «відмотав» графік вліво, не маємо права зсувати viewport
            // під час періодичного оновлення датасету (polling/rehydrate шарів).
            const prevLogicalRange = chart.timeScale().getVisibleLogicalRange();
            const prevScrollPos = chart.timeScale().scrollPosition();
            const prevLen = Array.isArray(lastCandleDataset) ? lastCandleDataset.length : 0;
            const wasFollowingRightEdge =
                prevLogicalRange && prevLen
                    ? Number(prevLogicalRange.to) >= prevLen - 2
                    : true;

            resetManualPriceScale({ silent: true });
            if (!Array.isArray(bars) || bars.length === 0) {
                candles.setData([]);
                liveCandles.setData([]);
                volume.setData([]);
                liveVolume.setData([]);
                setSessionsData([]);
                lastBar = null;
                lastLiveBar = null;
                lastLiveVolume = 0;
                clearCurrentPriceLine();
                recentVolumeMax = 0;
                recentVolumes = [];
                chartTimeRange = { min: null, max: null };
                lastBarsSignature = null;
                autoFitDone = false;
                lastCandleDataset = [];
                lastCandleTimes = [];
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
            lastCandleDataset = candleData.slice();
            lastCandleTimes = candleData.map((bar) => bar.time);

            // Фіксуємо шкалу volume по всьому датасету (а не по видимому фрагменту).
            // Це прибирає "провалювання" обсягів при горизонтальному скролі.
            volumeScaleMax = computeVolumeScaleMax(volumeValues);

            const signature = {
                firstTime: candleData[0]?.time ?? null,
                lastTime: candleData[candleData.length - 1]?.time ?? null,
                length: candleData.length,
            };
            const looksLikeNewDataset =
                !lastBarsSignature ||
                signature.firstTime !== lastBarsSignature.firstTime ||
                signature.length < lastBarsSignature.length ||
                signature.lastTime < lastBarsSignature.lastTime;
            if (looksLikeNewDataset) {
                autoFitDone = false;
            }

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
            setSessionsData(candleData);
            if (recentVolumeMax > 0) {
                const volumeData = candleData.map((bar, index) => {
                    const vol = volumeValues[index] ?? 0;
                    const isUp = Number(bar.close) >= Number(bar.open);
                    // Важливо для UX: при великих піках volume відносна прозорість робить
                    // більшість брусків майже невидимими (особливо при зумі/скролі).
                    // Тому для гістограми тримаємо сталу альфу.
                    const alpha = clamp(VOLUME_BAR_ALPHA, 0.18, 0.85);
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
            updateCurrentPriceLine();
            updateBarTimeSpanFromBars(candleData);
            updateTimeRangeFromBars(candleData);

            if (!autoFitDone) {
                chart.timeScale().fitContent();
                autoFitDone = true;
            } else if (prevLogicalRange && !wasFollowingRightEdge) {
                chart.timeScale().setVisibleLogicalRange({
                    from: prevLogicalRange.from,
                    to: prevLogicalRange.to,
                });
            } else if (!prevLogicalRange && Number.isFinite(prevScrollPos) && !wasFollowingRightEdge) {
                chart.timeScale().scrollToPosition(prevScrollPos, false);
            }
            lastBarsSignature = signature;
        }

        function utcDayStartSec(timeSec) {
            const d = new Date(Number(timeSec) * 1000);
            return Math.floor(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) / 1000);
        }

        function setSessionsData(candleData) {
            const applyBlock = (series, visible, from, to) => {
                if (!series || typeof series.setData !== "function") {
                    return;
                }
                if (!visible) {
                    series.setData([]);
                    if (typeof series.applyOptions === "function") {
                        series.applyOptions({ visible: false });
                    }
                    return;
                }
                const start = Math.floor(from);
                const end = Math.floor(to);
                series.setData([
                    { time: start, value: SESSION_BAND_VALUE },
                    { time: end, value: SESSION_BAND_VALUE },
                ]);
                if (typeof series.applyOptions === "function") {
                    series.applyOptions({ visible: true });
                }
            };

            const clearAllBands = () => {
                for (const band of sessionSeries.bands.asia) {
                    applyBlock(band, false);
                }
                for (const band of sessionSeries.bands.london) {
                    applyBlock(band, false);
                }
                for (const band of sessionSeries.bands.newYork) {
                    applyBlock(band, false);
                }
            };

            if (!sessionSeries.enabled) {
                clearAllBands();
                return;
            }

            // Стару «підкладку» сесій (Asia/London/NY як кольоровий фон) вимкнено,
            // щоб не накладались дві візуалізації сесій одночасно.
            // “A по даних” (high/low бокс) лишається окремо у setSessionRangeBox().
            clearAllBands();
            return;
        }

        function setSessionsEnabled(enabled) {
            const next = Boolean(enabled);
            if (sessionSeries.enabled === next) {
                return;
            }
            sessionSeries.enabled = next;

            const applyVisible = (series, value) => {
                if (!series || typeof series.applyOptions !== "function") {
                    return;
                }
                series.applyOptions({ visible: value });
            };
            for (const band of sessionSeries.bands.asia) {
                applyVisible(band, next);
            }
            for (const band of sessionSeries.bands.london) {
                applyVisible(band, next);
            }
            for (const band of sessionSeries.bands.newYork) {
                applyVisible(band, next);
            }

            setSessionsData(lastCandleDataset);

            // Синхронізуємо також бокс поточної сесії.
            setSessionRangeBox(lastSessionRangeRequest);
        }

        function setSessionRangeBox(range) {
            lastSessionRangeRequest = range || null;
            if (!sessionSeries.enabled) {
                sessionRangeBox.setData([]);
                sessionRangeBox.applyOptions({ visible: false });
                return;
            }

            const pickSessionFill = (tag) => {
                const key = String(tag || "").trim().toLowerCase();
                // Більш видимі «зони» сесій: New York — зелений, Tokyo — синій, London — оранжевий.
                // Лінії лишаємо вимкненими (працює лише заливка).
                if (key === "new_york" || key === "newyork" || key === "ny") {
                    return {
                        a1: "rgba(34, 197, 94, 0.16)",
                        a2: "rgba(34, 197, 94, 0.07)",
                    };
                }
                if (key === "tokyo" || key === "asia") {
                    return {
                        a1: "rgba(59, 130, 246, 0.16)",
                        a2: "rgba(59, 130, 246, 0.07)",
                    };
                }
                if (key === "london") {
                    return {
                        a1: "rgba(249, 115, 22, 0.16)",
                        a2: "rgba(249, 115, 22, 0.07)",
                    };
                }
                return {
                    a1: "rgba(209, 212, 220, 0.10)",
                    a2: "rgba(209, 212, 220, 0.04)",
                };
            };

            const from = Number(range?.from);
            const to = Number(range?.to);
            const low = Number(range?.low);
            const high = Number(range?.high);
            const fill = pickSessionFill(range?.session);
            if (
                !Number.isFinite(from) ||
                !Number.isFinite(to) ||
                !Number.isFinite(low) ||
                !Number.isFinite(high) ||
                !(to > from) ||
                !(high >= low)
            ) {
                sessionRangeBox.setData([]);
                sessionRangeBox.applyOptions({ visible: false });
                return;
            }

            sessionRangeBox.applyOptions({
                visible: true,
                baseValue: { type: "price", price: low },
                baseLineVisible: false,
                baseLineWidth: 0,
                lineVisible: false,
                lineColor: "rgba(0, 0, 0, 0)",
                topLineColor: "rgba(0, 0, 0, 0)",
                bottomLineColor: "rgba(0, 0, 0, 0)",
                topFillColor1: fill.a1,
                topFillColor2: fill.a2,
                bottomFillColor1: fill.a1,
                bottomFillColor2: fill.a2,
            });
            sessionRangeBox.setData([
                { time: Math.floor(from), value: high },
                { time: Math.floor(to), value: high },
            ]);
        }

        function setLiveBar(bar) {
            const normalized = normalizeBar(bar);
            if (!normalized) {
                return;
            }
            let vol = normalizeVolume(bar);
            // Якщо live volume вже накопичене у межах свічки — не даємо йому миготіти в 0.
            if (vol <= 0 && lastLiveBar && normalized.time === lastLiveBar.time && lastLiveVolume > 0) {
                vol = lastLiveVolume;
            } else {
                lastLiveVolume = vol;
            }

            if (vol > volumeScaleMax) {
                volumeScaleMax = vol;
            }
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

            updateCurrentPriceLine();
        }

        function clearLiveBar() {
            liveCandles.setData([]);
            liveVolume.setData([]);
            lastLiveBar = null;
            lastLiveVolume = 0;
            updateCurrentPriceLine();
        }

        function updateLastBar(bar) {
            const normalized = normalizeBar(bar);
            if (!normalized) {
                return;
            }
            const vol = normalizeVolume(bar);
            if (vol > volumeScaleMax) {
                volumeScaleMax = vol;
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
                    const alpha = clamp(VOLUME_BAR_ALPHA, 0.18, 0.85);
                    const base = isUp ? CANDLE_COLORS.up : CANDLE_COLORS.down;
                    volume.update({
                        time: normalized.time,
                        value: vol,
                        color: hexToRgba(base, alpha),
                    });
                }
                lastBar = normalized;
                if (Array.isArray(lastCandleDataset) && lastCandleDataset.length) {
                    const lastIdx = lastCandleDataset.length - 1;
                    const prev = lastCandleDataset[lastIdx];
                    const prevTime = Number(prev?.time);
                    if (Number.isFinite(prevTime) && normalized.time === prevTime) {
                        lastCandleDataset[lastIdx] = normalized;
                        if (Array.isArray(lastCandleTimes) && lastCandleTimes.length) {
                            lastCandleTimes[lastCandleTimes.length - 1] = normalized.time;
                        }
                    } else if (!Number.isFinite(prevTime) || normalized.time > prevTime) {
                        lastCandleDataset.push(normalized);
                        if (Array.isArray(lastCandleTimes)) {
                            lastCandleTimes.push(normalized.time);
                        } else {
                            lastCandleTimes = [normalized.time];
                        }
                    }
                } else {
                    lastCandleDataset = [normalized];
                    lastCandleTimes = [normalized.time];
                }
                if (chartTimeRange.min == null) {
                    chartTimeRange.min = normalized.time;
                }
                chartTimeRange.max = Math.max(chartTimeRange.max ?? normalized.time, normalized.time);
                updateCurrentPriceLine();
                setSessionsData(lastCandleDataset);
            }
        }

        function clearCurrentPriceLine() {
            if (!currentPriceLine) {
                return;
            }
            try {
                if (currentPriceLineOwner === "live") {
                    liveCandles.removePriceLine(currentPriceLine);
                } else {
                    candles.removePriceLine(currentPriceLine);
                }
            } catch (err) {
                console.warn("chart_adapter: не вдалося прибрати current price line", err);
            }
            currentPriceLine = null;
            currentPriceLineOwner = null;
            currentPriceLineState = { price: null, color: null, owner: null };
        }

        function updateCurrentPriceLine() {
            const source = lastLiveBar || lastBar;
            if (!source) {
                clearCurrentPriceLine();
                return;
            }

            const owner = lastLiveBar ? "live" : "candles";
            const price = Number(source.close);
            if (!Number.isFinite(price)) {
                clearCurrentPriceLine();
                return;
            }

            // Колір бейджа: якщо є попередня закрита свічка — порівнюємо з нею;
            // інакше — по open/close поточного бару.
            let ref = null;
            if (lastBar && lastLiveBar) {
                const refPrice = Number(lastBar.close);
                if (Number.isFinite(refPrice)) {
                    ref = refPrice;
                }
            }
            if (ref == null) {
                const open = Number(source.open);
                if (Number.isFinite(open)) {
                    ref = open;
                }
            }
            const isUp = ref == null ? true : price >= ref;
            // Менш яскравий бейдж на шкалі (приглушуємо колір).
            const colorBase = isUp ? CANDLE_COLORS.up : CANDLE_COLORS.down;
            const color = hexToRgba(colorBase, 0.6);

            const stateUnchanged =
                currentPriceLineState.price === price &&
                currentPriceLineState.color === color &&
                currentPriceLineState.owner === owner;
            if (stateUnchanged) {
                return;
            }

            // Якщо власник змінився або змінився price/color — пересоздаємо.
            clearCurrentPriceLine();
            const series = owner === "live" ? liveCandles : candles;
            currentPriceLine = series.createPriceLine({
                price,
                color,
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                // Щоб не перевантажувати графік: лишаємо компактний маркер на шкалі,
                // без додаткової горизонтальної лінії на полі.
                lineVisible: false,
                // Без title -> компактніший бейдж на шкалі.
            });
            currentPriceLineOwner = owner;
            currentPriceLineState = { price, color, owner };
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

        function clamp01(value) {
            const num = Number(value);
            if (!Number.isFinite(num)) return 0;
            return Math.max(0, Math.min(1, num));
        }

        function pickRefPrice() {
            const liveClose = Number(lastLiveBar?.close);
            if (Number.isFinite(liveClose)) return liveClose;
            const close = Number(lastBar?.close);
            if (Number.isFinite(close)) return close;
            const open = Number(lastBar?.open);
            if (Number.isFinite(open)) return open;
            return null;
        }

        function estimatePriceWindowAbs(refPrice) {
            const ref = Number(refPrice);
            const refComponent = Number.isFinite(ref) ? Math.abs(ref) * 0.0015 : 0;

            const bars = Array.isArray(lastCandleDataset) ? lastCandleDataset : [];
            const tail = bars.slice(Math.max(0, bars.length - 80));
            let maxHigh = null;
            let minLow = null;
            for (const bar of tail) {
                const h = Number(bar?.high);
                const l = Number(bar?.low);
                if (!Number.isFinite(h) || !Number.isFinite(l)) continue;
                maxHigh = maxHigh == null ? h : Math.max(maxHigh, h);
                minLow = minLow == null ? l : Math.min(minLow, l);
            }
            const n = Math.max(1, tail.length);
            const span = maxHigh != null && minLow != null ? Math.max(0, maxHigh - minLow) : 0;
            const atrLike = (span / n) * 14;
            const volComponent = atrLike * 0.6;
            return Math.max(refComponent, volComponent, 0.5);
        }

        function estimateMergeTolAbs(refPrice, priceWindowAbs) {
            const ref = Number(refPrice);
            const refComponent = Number.isFinite(ref) ? Math.abs(ref) * 0.00025 : 0;
            const windowComponent = Number(priceWindowAbs) * 0.08;
            return Math.max(refComponent, windowComponent, 0.2);
        }

        function roleWeight(role) {
            const r = String(role || "").toUpperCase();
            if (r === "PRIMARY") return 1.0;
            if (r === "COUNTER") return 0.6;
            return 0.5;
        }

        function poolScore(pool, refPrice, priceWindowAbs) {
            const price = Number(pool?.price);
            if (!Number.isFinite(price)) return -Infinity;
            const ref = Number(refPrice);
            if (!Number.isFinite(ref)) return -Infinity;

            const strength = Number(pool?.strength);
            const strengthNorm = Number.isFinite(strength) ? clamp01(strength / 100) : 0.3;
            const distNormRaw = Math.abs(price - ref) / Math.max(1e-9, Number(priceWindowAbs) || 1);
            const distNorm = Math.min(6, Math.max(0, distNormRaw));
            return roleWeight(pool?.role) * (1 + strengthNorm) / (1 + distNorm);
        }

        function chooseBetterPool(a, b, refPrice) {
            const ra = roleWeight(a?.role);
            const rb = roleWeight(b?.role);
            if (ra !== rb) return ra > rb ? a : b;

            const sa = Number(a?.strength);
            const sb = Number(b?.strength);
            const saN = Number.isFinite(sa) ? sa : -Infinity;
            const sbN = Number.isFinite(sb) ? sb : -Infinity;
            if (saN !== sbN) return saN > sbN ? a : b;

            const ta = Number(a?.touches);
            const tb = Number(b?.touches);
            const taN = Number.isFinite(ta) ? ta : -Infinity;
            const tbN = Number.isFinite(tb) ? tb : -Infinity;
            if (taN !== tbN) return taN > tbN ? a : b;

            const ref = Number(refPrice);
            const da = Number.isFinite(ref) ? Math.abs(Number(a?.price) - ref) : Infinity;
            const db = Number.isFinite(ref) ? Math.abs(Number(b?.price) - ref) : Infinity;
            return da <= db ? a : b;
        }

        function dedupPoolsByPrice(pools, mergeTolAbs, refPrice) {
            const cleaned = (Array.isArray(pools) ? pools : [])
                .map((p) => ({ ...p, price: Number(p?.price) }))
                .filter((p) => Number.isFinite(p.price))
                .sort((a, b) => a.price - b.price);
            if (!cleaned.length) return [];

            const tol = Math.max(0, Number(mergeTolAbs) || 0);
            const out = [];
            for (const p of cleaned) {
                const last = out[out.length - 1];
                if (!last) {
                    out.push(p);
                    continue;
                }
                if (Math.abs(p.price - last.price) <= tol) {
                    out[out.length - 1] = chooseBetterPool(last, p, refPrice);
                } else {
                    out.push(p);
                }
            }
            return out;
        }

        function shortPoolTitle(pool) {
            const type = String(pool?.type || pool?.kind || "POOL").toUpperCase();
            const role = String(pool?.role || "").toUpperCase();
            const roleMark = role === "PRIMARY" ? "P" : role === "COUNTER" ? "C" : "";
            const typeShort = type.length > 6 ? type.slice(0, 6) : type;
            return `${typeShort}${roleMark ? " " + roleMark : ""}`.trim();
        }

        function selectPoolsForRender(pools) {
            const refPrice = pickRefPrice();
            if (!Number.isFinite(Number(refPrice))) {
                return { local: [], global: [], refPrice: null, priceWindowAbs: 1, mergeTolAbs: 0.2 };
            }

            const priceWindowAbs = estimatePriceWindowAbs(refPrice);
            const mergeTolAbs = estimateMergeTolAbs(refPrice, priceWindowAbs);

            const deduped = dedupPoolsByPrice(pools, mergeTolAbs, refPrice);
            const ref = Number(refPrice);

            const above = deduped.filter((p) => Number(p.price) >= ref);
            const below = deduped.filter((p) => Number(p.price) < ref);

            const scored = (arr) =>
                arr
                    .map((p) => ({ pool: p, score: poolScore(p, ref, priceWindowAbs) }))
                    .filter((row) => Number.isFinite(row.score))
                    .sort((a, b) => b.score - a.score);

            const aboveScored = scored(above);
            const belowScored = scored(below);

            const pickPrimary = (rows) => rows.find((r) => String(r.pool?.role || "").toUpperCase() === "PRIMARY")?.pool;

            const localAbove = [];
            const localBelow = [];
            const primaryAbove = pickPrimary(aboveScored);
            const primaryBelow = pickPrimary(belowScored);
            if (primaryAbove) localAbove.push(primaryAbove);
            if (primaryBelow) localBelow.push(primaryBelow);

            const fillSide = (rows, target, maxCount) => {
                for (const row of rows) {
                    if (target.length >= maxCount) break;
                    if (target.some((p) => p.price === row.pool.price)) continue;
                    target.push(row.pool);
                }
            };

            fillSide(aboveScored, localAbove, 3);
            fillSide(belowScored, localBelow, 3);

            const local = [...localAbove, ...localBelow];

            const localNearest = {
                above: localAbove
                    .slice()
                    .sort((a, b) => Math.abs(a.price - ref) - Math.abs(b.price - ref))[0] || null,
                below: localBelow
                    .slice()
                    .sort((a, b) => Math.abs(a.price - ref) - Math.abs(b.price - ref))[0] || null,
            };

            const isLocal = (p) => local.some((x) => x.price === p.price);
            const farEnough = (p) => Math.abs(Number(p.price) - ref) >= priceWindowAbs * 1.2;

            const pickGlobal = (rows) =>
                rows
                    .map((r) => r.pool)
                    .filter((p) => !isLocal(p))
                    .filter((p) => farEnough(p))[0] || null;

            const global = [];
            const globalAbove = pickGlobal(aboveScored);
            const globalBelow = pickGlobal(belowScored);
            if (globalAbove) global.push(globalAbove);
            if (globalBelow) global.push(globalBelow);

            return {
                local: local.map((p) => ({
                    ...p,
                    _axisLabel: p.price === localNearest.above?.price || p.price === localNearest.below?.price,
                    _lineVisible: true,
                })),
                global: global.map((p) => ({
                    ...p,
                    _axisLabel: true,
                    _lineVisible: false,
                })),
                refPrice: ref,
                priceWindowAbs,
                mergeTolAbs,
            };
        }

        function selectZonesForRender(zones) {
            const refPrice = pickRefPrice();
            if (!Number.isFinite(Number(refPrice))) {
                return { zones: [], mergeTolAbs: 0.2 };
            }
            const priceWindowAbs = estimatePriceWindowAbs(refPrice);
            const mergeTolAbs = estimateMergeTolAbs(refPrice, priceWindowAbs);
            const ref = Number(refPrice);
            const focusMin = ref - priceWindowAbs * 1.2;
            const focusMax = ref + priceWindowAbs * 1.2;

            const candidates = (Array.isArray(zones) ? zones : [])
                .map((z) => {
                    const min = Number(z?.min ?? z?.price_min ?? z?.ote_min);
                    const max = Number(z?.max ?? z?.price_max ?? z?.ote_max);
                    if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
                    const zMin = Math.min(min, max);
                    const zMax = Math.max(min, max);
                    if (zMax < focusMin || zMin > focusMax) return null;
                    const center = (zMin + zMax) / 2;
                    const role = String(z?.role || "").toUpperCase();
                    const w = roleWeight(role);
                    const distNorm = Math.abs(center - ref) / Math.max(1e-9, priceWindowAbs);
                    const score = w / (1 + Math.min(6, distNorm));
                    return {
                        ...z,
                        min: zMin,
                        max: zMax,
                        _center: center,
                        _score: score,
                    };
                })
                .filter(Boolean)
                .sort((a, b) => b._score - a._score);

            const picked = [];
            for (const z of candidates) {
                if (picked.length >= 3) break;
                if (picked.some((p) => Math.abs(Number(p._center) - Number(z._center)) <= mergeTolAbs)) {
                    continue;
                }
                picked.push(z);
            }

            const normalized = picked.map((z) => {
                const thin = Math.abs(Number(z.max) - Number(z.min)) < mergeTolAbs;
                if (!thin) return z;
                const center = Number(z._center);
                return {
                    ...z,
                    min: center,
                    max: center,
                };
            });

            return { zones: normalized, mergeTolAbs };
        }

        function setEvents(events) {
            clearEvents();
            if (!Array.isArray(events) || !events.length) {
                return;
            }

            const toUnixSeconds = (value) => {
                const num = Number(value);
                if (!Number.isFinite(num)) return null;
                return Math.floor(num / (num > 1e12 ? 1000 : 1));
            };

            const snapToNearestBarTime = (timeSec) => {
                if (!Number.isFinite(timeSec)) return null;
                const times = lastCandleTimes;
                if (!Array.isArray(times) || times.length === 0) {
                    return Math.floor(timeSec);
                }

                const target = Math.floor(timeSec);
                let lo = 0;
                let hi = times.length;
                while (lo < hi) {
                    const mid = (lo + hi) >> 1;
                    const v = times[mid];
                    if (v < target) lo = mid + 1;
                    else hi = mid;
                }

                const rightIdx = Math.min(times.length - 1, lo);
                const leftIdx = Math.max(0, rightIdx - 1);
                const left = Number(times[leftIdx]);
                const right = Number(times[rightIdx]);
                const pick =
                    !Number.isFinite(left) ? right :
                        !Number.isFinite(right) ? left :
                            Math.abs(target - left) <= Math.abs(right - target) ? left : right;

                if (!Number.isFinite(pick)) {
                    return null;
                }

                const maxDiff = Math.max(1, Number(barTimeSpanSeconds) || 60) * 1.5;
                if (Math.abs(pick - target) > maxDiff) {
                    return null;
                }
                return Math.floor(pick);
            };

            withViewportPreserved(() => {
                const structureEvents = events.filter(isStructureEvent);
                if (!structureEvents.length) {
                    return;
                }
                const getEventTime = (evt) => {
                    const raw = evt.time ?? evt.ts ?? evt.timestamp ?? 0;
                    const sec = toUnixSeconds(raw);
                    return sec ?? 0;
                };
                const sortedEvents = structureEvents
                    .slice()
                    .sort((a, b) => getEventTime(a) - getEventTime(b));
                eventMarkers = sortedEvents
                    .map((evt) => {
                        const timeRaw = evt.time ?? evt.ts ?? evt.timestamp;
                        const time = toUnixSeconds(timeRaw);
                        if (!Number.isFinite(time)) return null;

                        const snapped = snapToNearestBarTime(time);
                        if (!Number.isFinite(snapped)) return null;

                        const direction = (evt.direction || evt.dir || "").toUpperCase();
                        const kind = (evt.type || evt.event_type || "").toUpperCase();
                        const isChoch = kind.includes("CHOCH");
                        const isBos = !isChoch && kind.includes("BOS");

                        const isShort = direction === "SHORT";
                        const isLong = direction === "LONG";
                        // BOS: окремий (стабільний) стиль, щоб було читабельно.
                        // CHOCH лишаємо залежним від direction.
                        const color = isBos ? "#3b82f6" : isShort ? "#ef476f" : "#1ed760";

                        const arrowShape = isShort ? "arrowDown" : "arrowUp";
                        const shape = isChoch ? arrowShape : isBos ? "square" : arrowShape;
                        const text = isChoch ? "CHOCH" : isBos ? "BOS" : kind;
                        return {
                            time: snapped,
                            // На вимогу UX: лишаємо лише напис НАД свічкою.
                            position: "aboveBar",
                            color,
                            shape,
                            text,
                        };
                    })
                    .filter(Boolean);
                candles.setMarkers(eventMarkers);
            });
        }

        function setLiquidityPools(pools) {
            clearPools();
            if (!Array.isArray(pools) || !pools.length) {
                return;
            }

            const selection = selectPoolsForRender(pools);
            const local = selection.local;
            const global = selection.global;

            const renderOne = (pool) => {
                const price = Number(pool.price);
                if (!Number.isFinite(price)) return;
                const role = (pool.role || "").toUpperCase();
                const line = candles.createPriceLine({
                    price,
                    color: role === "PRIMARY" ? "rgba(249, 199, 79, 0.65)" : "#577590",
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dashed,
                    axisLabelVisible: Boolean(pool._axisLabel),
                    // Щоб не засмічувати поле: для «глобальних» рівнів лишаємо лише бейдж на шкалі.
                    lineVisible: pool._lineVisible !== false,
                    title: shortPoolTitle(pool),
                });
                poolLines.push(line);
            };

            local.forEach(renderOne);
            global.forEach(renderOne);
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
                // AreaSeries заливає до baseline=0, тож для «box» між min↔max використовуємо BaselineSeries.
                const band = chart.addBaselineSeries({
                    baseValue: { type: "price", price: minPrice },
                    topFillColor1: "rgba(59, 130, 246, 0.18)",
                    topFillColor2: "rgba(59, 130, 246, 0.06)",
                    bottomFillColor1: "rgba(59, 130, 246, 0.18)",
                    bottomFillColor2: "rgba(59, 130, 246, 0.06)",
                    lineWidth: 0,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    crosshairMarkerVisible: false,
                });
                band.setData([
                    { time: Math.floor(from), value: maxPrice },
                    { time: Math.floor(to), value: maxPrice },
                ]);
                rangeAreas.push(band);
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

                // Якщо зона надто тонка — малюємо як один рівень (центр), а не 2 лінії.
                if (Math.abs(maxPrice - minPrice) < 1e-9) {
                    const line = candles.createPriceLine({
                        price: minPrice,
                        color: colors.max,
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        axisLabelVisible: false,
                        title: `${label}`,
                    });
                    zoneLines.push(line);
                    return;
                }

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
            const selection = selectZonesForRender(zones);
            setBandZones(selection.zones, { min: "#ffd166", max: "#ef476f" });
        }

        function clearAll() {
            candles.setData([]);
            liveCandles.setData([]);
            volume.setData([]);
            liveVolume.setData([]);
            setSessionsData([]);
            setSessionRangeBox(null);
            lastBar = null;
            lastLiveBar = null;
            lastLiveVolume = 0;
            recentVolumeMax = 0;
            recentVolumes = [];
            lastBarsSignature = null;
            autoFitDone = false;
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
            setSessionsEnabled,
            setSessionRangeBox,
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

            // Для OTE робимо лінії «тоншими» візуально: dotted стиль + приглушений колір.
            const createOteBorderSeries = () =>
                chart.addLineSeries({
                    color: palette.border,
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dotted,
                    priceScaleId: "right",
                    lastValueVisible: false,
                    priceLineVisible: false,
                    crosshairMarkerVisible: false,
                    autoscaleInfoProvider: () => null,
                });

            const topSeries = createOteBorderSeries();
            topSeries.setData([
                { time: safeLeft, value: maxPrice },
                { time: safeRight, value: maxPrice },
            ]);
            const bottomSeries = createOteBorderSeries();
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
                // Короткий title: менше «шуму» на шкалі.
                title: direction === "SHORT" ? "↓" : "↑",
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

            // Під час нашого vertical-pan тимчасово блокуємо drag-скрол бібліотеки,
            // щоб не було «упирання» і переходу в масштабування.
            const setLibraryDragEnabled = (enabled) => {
                try {
                    chart.applyOptions({
                        handleScroll: {
                            pressedMouseMove: Boolean(enabled),
                        },
                    });
                } catch (_e) {
                    // ignore
                }
            };

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

            const stopVerticalPan = () => {
                if (!verticalPanState.pending) {
                    return;
                }
                verticalPanState.pending = false;
                verticalPanState.active = false;
                verticalPanState.startRange = null;
                verticalPanState.baseRange = null;
                verticalPanState.pointerId = null;
                container.classList.remove("vertical-pan-active");
                setLibraryDragEnabled(true);
            };

            const beginPan = (clientX, clientY, pointerId = null) => {
                const currentRange = getEffectivePriceRange();
                if (!currentRange) {
                    return;
                }
                verticalPanState.pending = true;
                verticalPanState.active = false;
                verticalPanState.startY = clientY;
                verticalPanState.startX = clientX;
                verticalPanState.baseRange = currentRange;
                verticalPanState.startRange = null;
                verticalPanState.pointerId = pointerId;
            };

            const movePan = (event, clientX, clientY) => {
                if (!verticalPanState.pending) {
                    return;
                }
                if (verticalPanState.pointerId !== null && event?.pointerId !== undefined) {
                    if (event.pointerId !== verticalPanState.pointerId) {
                        return;
                    }
                }

                const paneHeight = getPaneMetrics().paneHeight;
                if (!paneHeight) {
                    return;
                }
                const deltaY = clientY - verticalPanState.startY;
                const deltaX = clientX - verticalPanState.startX;

                if (!verticalPanState.active) {
                    if (Math.abs(deltaY) < DRAG_ACTIVATION_PX || Math.abs(deltaY) <= Math.abs(deltaX)) {
                        return;
                    }
                    ensureManualRange(verticalPanState.baseRange);
                    verticalPanState.startRange = { ...priceScaleState.manualRange };
                    verticalPanState.active = true;
                    container.classList.add("vertical-pan-active");

                    // Блокуємо drag бібліотеки тільки коли точно почали vertical-pan.
                    setLibraryDragEnabled(false);
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

            const usePointerEvents = typeof window.PointerEvent !== "undefined";

            if (usePointerEvents) {
                const handlePointerDown = (event) => {
                    if (!event || event.button !== 0) {
                        return;
                    }
                    if (!isPointerInsidePane(event)) {
                        return;
                    }
                    beginPan(event.clientX, event.clientY, event.pointerId);
                };
                container.addEventListener("pointerdown", handlePointerDown, true);
                interactionCleanup.push(() => container.removeEventListener("pointerdown", handlePointerDown, true));

                const handlePointerMove = (event) => {
                    movePan(event, event.clientX, event.clientY);
                };
                window.addEventListener("pointermove", handlePointerMove, true);
                interactionCleanup.push(() => window.removeEventListener("pointermove", handlePointerMove, true));

                const handlePointerUp = () => {
                    stopVerticalPan();
                };
                window.addEventListener("pointerup", handlePointerUp, true);
                interactionCleanup.push(() => window.removeEventListener("pointerup", handlePointerUp, true));
                window.addEventListener("pointercancel", handlePointerUp, true);
                interactionCleanup.push(() => window.removeEventListener("pointercancel", handlePointerUp, true));
                window.addEventListener("blur", stopVerticalPan);
                interactionCleanup.push(() => window.removeEventListener("blur", stopVerticalPan));
            } else {
                const handleMouseDown = (event) => {
                    if (event.button !== 0 || !isPointerInsidePane(event)) {
                        return;
                    }
                    beginPan(event.clientX, event.clientY, null);
                };
                container.addEventListener("mousedown", handleMouseDown, true);
                interactionCleanup.push(() => container.removeEventListener("mousedown", handleMouseDown, true));

                const handleMouseMove = (event) => {
                    movePan(event, event.clientX, event.clientY);
                };
                window.addEventListener("mousemove", handleMouseMove, true);
                interactionCleanup.push(() => window.removeEventListener("mousemove", handleMouseMove, true));

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
            }

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

        // Фіксований autoscale для volume-шкали.
        // LightweightCharts інакше масштабує по видимому діапазону, через що volume «падає/росте» при скролі.
        const volumeAutoscaleInfoProvider = () => {
            const maxValue = Number.isFinite(volumeScaleMax) && volumeScaleMax > 0 ? volumeScaleMax : 1;
            return {
                priceRange: {
                    minValue: 0,
                    maxValue,
                },
            };
        };
        volume.applyOptions({ autoscaleInfoProvider: volumeAutoscaleInfoProvider });
        liveVolume.applyOptions({ autoscaleInfoProvider: volumeAutoscaleInfoProvider });

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
