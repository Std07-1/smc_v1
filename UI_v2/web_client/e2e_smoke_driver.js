/*
 * Драйвер для E2E smoke тестів UI_v2 графіка.
 *
 * Очікування:
 * - lightweight_charts_stub.js створює global LightweightCharts і кладе chart в container.__stubChart
 * - chart_adapter.js створює window.createChartController
 */

(function () {
    "use strict";

    function makeBars(count) {
        const out = [];
        const start = 1700000000; // seconds
        let price = 100.0;
        for (let i = 0; i < count; i += 1) {
            const t = start + i * 60;
            const open = price;
            const close = price + (i % 2 === 0 ? 0.2 : -0.15);
            const high = Math.max(open, close) + 0.1;
            const low = Math.min(open, close) - 0.1;
            out.push({ time: t, open, high, low, close, volume: 10 + (i % 5) });
            price = close;
        }
        return out;
    }

    function seriesDataShim(candleRow, volumeValue) {
        return {
            get(series) {
                if (series && series.__kind === "candlestick") {
                    return candleRow;
                }
                if (series && series.__kind === "histogram") {
                    return { value: volumeValue };
                }
                return null;
            },
        };
    }

    function init() {
        const container = document.getElementById("chart-container");
        if (!container || typeof window.createChartController !== "function") {
            throw new Error("e2e_smoke: createChartController/container не доступні");
        }

        const controller = window.createChartController(container);
        controller.setBars(makeBars(300));

        const chart = container.__stubChart;
        if (!chart || typeof chart.__emitCrosshairMove !== "function") {
            throw new Error("e2e_smoke: stub chart не знайдено");
        }

        const api = {
            ready: true,
            tfChanged: false,
            controller,
            getPriceScaleState() {
                return controller.__debugGetPriceScaleState ? controller.__debugGetPriceScaleState() : null;
            },
            getEffectiveRange() {
                return controller.__debugGetEffectivePriceRange ? controller.__debugGetEffectivePriceRange() : null;
            },
            simulateTfChange() {
                // Симуляція "зміни таймфрейму": переподаємо нові бари.
                // Для smoke нам важливо, щоб після цього перша взаємодія не давала "ривка".
                const next = makeBars(220);
                // Трохи зсуваємо час, щоб дані точно відрізнялись.
                for (let i = 0; i < next.length; i += 1) {
                    next[i] = { ...next[i], time: next[i].time + 5 * 60 };
                }
                controller.setBars(next);
                this.tfChanged = true;
            },
            emitCrosshairMove(pos) {
                // Викликаємо подію, яка має показати tooltip.
                const rect = container.getBoundingClientRect();
                const point = {
                    x: Number.isFinite(pos?.x) ? Math.floor(pos.x) : Math.floor(rect.width / 2),
                    y: Number.isFinite(pos?.y) ? Math.floor(pos.y) : 120,
                };
                const time = 1700000000 + 60 * 299;
                const candle = { open: 100, high: 101, low: 99, close: 100.25 };
                chart.__emitCrosshairMove({
                    point,
                    time,
                    seriesData: seriesDataShim(candle, 42),
                });
            },
            emitCrosshairNull() {
                chart.__emitCrosshairMove(null);
            },
            getTooltip() {
                const el = document.getElementById("chart-hover-tooltip");
                return {
                    visible: !el?.hidden,
                    text: String(el?.textContent || ""),
                };
            },
        };

        window.__e2e__ = api;
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
