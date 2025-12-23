/*
 * Мінімальний stub lightweight-charts для E2E smoke тестів.
 *
 * Ціль: дати chart_adapter.js достатній API для підписки на wheel/drag і
 * показу tooltip без мережевого завантаження реальної бібліотеки.
 *
 * НЕ для продакшну.
 */

(function (root) {
    "use strict";

    const CrosshairMode = { Normal: 0 };
    const LineStyle = { Dashed: 2 };

    function createSeries(kind) {
        const series = {
            __kind: kind,
            _data: [],
            _options: {},
            applyOptions(opts) {
                this._options = { ...this._options, ...(opts || {}) };
            },
            setData(data) {
                this._data = Array.isArray(data) ? data.slice() : [];
            },
            update(point) {
                if (point) {
                    this._data.push(point);
                }
            },
            setMarkers(_markers) {
                // noop
            },
            createPriceLine(opts) {
                return { __priceLine: true, _opts: opts || {}, applyOptions(next) { this._opts = { ...this._opts, ...(next || {}) }; } };
            },
            removePriceLine(_line) {
                // noop
            },
            priceToCoordinate(price) {
                const p = Number(price);
                if (!Number.isFinite(p)) return null;
                // Лінійна мапа для тестів: price 100 -> y=0
                return (100 - p) / 0.1;
            },
            coordinateToPrice(y) {
                const yy = Number(y);
                if (!Number.isFinite(yy)) return null;
                return 100 - yy * 0.1;
            },
        };
        return series;
    }

    function createChart(container, _options) {
        const chart = {
            _container: container,
            _width: 1000,
            _height: 520,
            _crosshairCb: null,
            _timeScale: {
                _logical: { from: 0, to: 100 },
                _scroll: 0,
                getVisibleLogicalRange() {
                    return this._logical;
                },
                setVisibleLogicalRange(r) {
                    if (r && Number.isFinite(r.from) && Number.isFinite(r.to)) {
                        this._logical = { from: r.from, to: r.to };
                    }
                },
                scrollPosition() {
                    return this._scroll;
                },
                scrollToPosition(pos, _animated) {
                    if (Number.isFinite(pos)) {
                        this._scroll = pos;
                    }
                },
                fitContent() {
                    // noop
                },
                subscribeVisibleLogicalRangeChange(_cb) {
                    // noop
                },
                unsubscribeVisibleLogicalRangeChange(_cb) {
                    // noop
                },
                subscribeVisibleTimeRangeChange(_cb) {
                    // noop
                },
                unsubscribeVisibleTimeRangeChange(_cb) {
                    // noop
                },
                timeToCoordinate(_time) {
                    return 50;
                },
                getVisibleRange() {
                    return { from: 0, to: 1e12 };
                },
            },
            _priceScales: {
                right: {
                    width() {
                        return 56;
                    },
                    applyOptions(_opts) {
                        // noop
                    },
                },
                volume: {
                    width() {
                        return 0;
                    },
                    applyOptions(_opts) {
                        // noop
                    },
                },
                sessions: {
                    width() {
                        return 0;
                    },
                    applyOptions(_opts) {
                        // noop
                    },
                },
            },
            paneSize() {
                // Використовуємо реальний rect, якщо можна.
                try {
                    const rect = container.getBoundingClientRect();
                    const w = Math.floor(rect.width);
                    const h = Math.floor(rect.height);
                    if (Number.isFinite(w) && w > 0) this._width = w;
                    if (Number.isFinite(h) && h > 0) this._height = h;
                } catch (_e) {
                    // noop
                }
                return { width: this._width - 56, height: this._height };
            },
            applyOptions(opts) {
                if (opts && Number.isFinite(opts.width)) this._width = Math.floor(opts.width);
                if (opts && Number.isFinite(opts.height)) this._height = Math.floor(opts.height);
            },
            addCandlestickSeries(_opts) {
                return createSeries("candlestick");
            },
            addHistogramSeries(_opts) {
                return createSeries("histogram");
            },
            addBaselineSeries(_opts) {
                return createSeries("baseline");
            },
            timeScale() {
                return this._timeScale;
            },
            priceScale(id) {
                return this._priceScales[id] || this._priceScales.right;
            },
            subscribeCrosshairMove(cb) {
                this._crosshairCb = cb;
            },
            unsubscribeCrosshairMove(_cb) {
                this._crosshairCb = null;
            },
            __emitCrosshairMove(payload) {
                if (typeof this._crosshairCb === "function") {
                    this._crosshairCb(payload);
                }
            },
            remove() {
                // noop
            },
        };

        // Експонуємо для драйвера (не для продакшну).
        container.__stubChart = chart;
        return chart;
    }

    root.LightweightCharts = {
        createChart,
        CrosshairMode,
        LineStyle,
    };
})(typeof globalThis !== "undefined" ? globalThis : window);
