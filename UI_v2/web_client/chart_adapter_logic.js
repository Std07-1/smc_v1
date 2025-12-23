/*
 * UI_v2: чиста логіка для chart_adapter.js (без DOM/LightweightCharts).
 *
 * Ціль: мати unit-тести на критичні інваріанти (range-нормалізація,
 * hit-test price-axis/pane, вибір effective price range) без браузера.
 */

(function (root, factory) {
    if (typeof module === "object" && module && module.exports) {
        module.exports = factory();
        return;
    }
    root.ChartAdapterLogic = factory();
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
    "use strict";

    function clamp(value, min, max) {
        if (!Number.isFinite(value)) {
            return min;
        }
        return Math.min(max, Math.max(min, value));
    }

    function normalizeRange(range, minPriceSpan) {
        if (!range) {
            return null;
        }
        const span = Number.isFinite(minPriceSpan) && minPriceSpan > 0 ? Number(minPriceSpan) : 1e-4;

        let min = range.min;
        let max = range.max;
        if (!Number.isFinite(min) || !Number.isFinite(max)) {
            return null;
        }

        if (min === max) {
            min -= span / 2;
            max += span / 2;
        }

        if (max - min < span) {
            const mid = (max + min) / 2;
            min = mid - span / 2;
            max = mid + span / 2;
        }

        if (!(max > min)) {
            return null;
        }
        return { min, max };
    }

    function isPointerInPriceAxis(args, priceAxisFallbackWidthPx) {
        const fallbackWidth = Math.max(0, Number(priceAxisFallbackWidthPx) || 56);
        if (!args) {
            return false;
        }

        const x = Number(args.x);
        const y = Number(args.y);
        const width = Number(args.width);
        const height = Number(args.height);
        const paneWidth = Number(args.paneWidth) || 0;
        const paneHeight = Number(args.paneHeight) || 0;
        const priceScaleWidth = Number(args.priceScaleWidth) || 0;

        if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(width) || !Number.isFinite(height)) {
            return false;
        }
        if (!(width > 0) || !(height > 0)) {
            return false;
        }

        const effectivePaneHeight = paneHeight > 0 ? paneHeight : height;

        const axisLeft = paneWidth > 0 ? paneWidth : Math.max(0, width - fallbackWidth);
        const axisRight = paneWidth > 0 && priceScaleWidth > 0 ? paneWidth + priceScaleWidth : width;

        return x >= axisLeft && x <= axisRight && y >= 0 && y <= effectivePaneHeight;
    }

    function isPointerInsidePane(args, priceAxisFallbackWidthPx) {
        const fallbackWidth = Math.max(0, Number(priceAxisFallbackWidthPx) || 56);
        if (!args) {
            return false;
        }

        const x = Number(args.x);
        const y = Number(args.y);
        const width = Number(args.width);
        const height = Number(args.height);
        const paneWidth = Number(args.paneWidth) || 0;
        const paneHeight = Number(args.paneHeight) || 0;

        if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(width) || !Number.isFinite(height)) {
            return false;
        }
        if (!(width > 0) || !(height > 0)) {
            return false;
        }

        const effectivePaneHeight = paneHeight > 0 ? paneHeight : height;
        const effectivePaneWidth = paneWidth > 0 ? paneWidth : Math.max(0, width - fallbackWidth);

        return x >= 0 && x <= effectivePaneWidth && y >= 0 && y <= effectivePaneHeight;
    }

    function computeEffectivePriceRange(args) {
        if (!args) {
            return { range: null, nextLastAutoRange: null };
        }

        const manualRange = args.manualRange ? { ...args.manualRange } : null;
        const lastAutoRange = args.lastAutoRange ? { ...args.lastAutoRange } : null;

        if (manualRange && Number.isFinite(manualRange.min) && Number.isFinite(manualRange.max)) {
            return { range: manualRange, nextLastAutoRange: lastAutoRange };
        }

        if (lastAutoRange && Number.isFinite(lastAutoRange.min) && Number.isFinite(lastAutoRange.max)) {
            return { range: lastAutoRange, nextLastAutoRange: lastAutoRange };
        }

        const paneHeight = Number(args.paneHeight);
        if (!Number.isFinite(paneHeight) || !(paneHeight > 0)) {
            return { range: null, nextLastAutoRange: lastAutoRange };
        }

        const topPrice = Number(args.topPrice);
        const bottomPrice = Number(args.bottomPrice);
        if (!Number.isFinite(topPrice) || !Number.isFinite(bottomPrice)) {
            return { range: null, nextLastAutoRange: lastAutoRange };
        }

        const min = Math.min(topPrice, bottomPrice);
        const max = Math.max(topPrice, bottomPrice);
        if (!(max > min)) {
            return { range: null, nextLastAutoRange: lastAutoRange };
        }

        const next = { min, max };
        return { range: next, nextLastAutoRange: next };
    }

    function computeWheelZoomRange(args) {
        if (!args) {
            return null;
        }
        const range = args.range ? { ...args.range } : null;
        const anchor = Number(args.anchor);
        const deltaY = Number(args.deltaY);
        const minPriceSpan = args.minPriceSpan;

        if (!range || !Number.isFinite(range.min) || !Number.isFinite(range.max)) {
            return null;
        }
        if (!Number.isFinite(anchor) || !Number.isFinite(deltaY)) {
            return null;
        }

        const span = Number(range.max) - Number(range.min);
        if (!(span > 0)) {
            return null;
        }

        const intensity = Number.isFinite(args.intensity) ? Number(args.intensity) : 0.002;
        const maxDelta = Number.isFinite(args.maxDelta) ? Number(args.maxDelta) : 600;
        const d = Math.min(Math.abs(deltaY), maxDelta);
        const scale = Math.exp(d * intensity);
        const factor = deltaY < 0 ? 1 / scale : scale;

        const distanceMin = anchor - Number(range.min);
        const distanceMax = Number(range.max) - anchor;
        const nextRange = {
            min: anchor - distanceMin * factor,
            max: anchor + distanceMax * factor,
        };
        return normalizeRange(nextRange, minPriceSpan);
    }

    function computeWheelPanRange(args) {
        if (!args) {
            return null;
        }
        const range = args.range ? { ...args.range } : null;
        const paneHeight = Number(args.paneHeight);
        const deltaY = Number(args.deltaY);
        const minPriceSpan = args.minPriceSpan;

        if (!range || !Number.isFinite(range.min) || !Number.isFinite(range.max)) {
            return null;
        }
        if (!Number.isFinite(paneHeight) || !(paneHeight > 0) || !Number.isFinite(deltaY)) {
            return null;
        }

        const span = Number(range.max) - Number(range.min);
        if (!(span > 0)) {
            return null;
        }

        const panFactor = Number.isFinite(args.panFactor) ? Number(args.panFactor) : 0.5;
        const offset = (-deltaY / paneHeight) * span * panFactor;
        return normalizeRange(
            {
                min: Number(range.min) + offset,
                max: Number(range.max) + offset,
            },
            minPriceSpan
        );
    }

    return {
        clamp,
        normalizeRange,
        isPointerInPriceAxis,
        isPointerInsidePane,
        computeEffectivePriceRange,
        computeWheelZoomRange,
        computeWheelPanRange,
    };
});
