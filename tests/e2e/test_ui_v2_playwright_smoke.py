"""Playwright smoke-тести для UI_v2 графіка.

Мета: зловити регресії типу "перший wheel/drag після refresh/TF -> ривок" та
нестабільний tooltip.

Тести використовують сторінку `UI_v2/web_client/e2e_smoke.html`, яка не
залежить від CDN (lightweight-charts замінено на локальний stub).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from playwright.async_api import async_playwright


class _FakeStore:
    async def get_state(self, _symbol: str):
        return None

    async def get_all_states(self):
        return {}


@asynccontextmanager
async def _running_viewer_http_server() -> AsyncIterator[str]:
    from UI_v2.viewer_state_server import ViewerStateHttpServer

    server = ViewerStateHttpServer(
        store=cast(Any, _FakeStore()),
        host="127.0.0.1",
        port=0,
    )
    await server.start()
    try:
        base = server.get_listen_url()
        assert base is not None
        yield base
    finally:
        await server.stop()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_ui_v2_refresh_first_wheel_no_jump() -> None:
    """Після refresh перший wheel по осі цін не має давати "ривок"."""

    async with _running_viewer_http_server() as base_url:
        url = f"{base_url}/e2e_smoke.html?test_hooks=1"

        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": 1100, "height": 700})

            await page.goto(url, wait_until="load")
            await page.wait_for_function(
                "window.__e2e__ && window.__e2e__.ready === true"
            )

            r0 = await page.evaluate("window.__e2e__.getEffectiveRange()")
            assert r0 and r0.get("min") is not None and r0.get("max") is not None

            # Крутимо wheel над правою зоною price-axis.
            # Важливо: wheel має змінити діапазон, але не зробити його NaN/перевернутим/нульовим.
            await page.mouse.move(1065, 220)
            await page.mouse.wheel(0, -220)

            r1 = await page.evaluate("window.__e2e__.getEffectiveRange()")
            assert r1 and r1.get("min") is not None and r1.get("max") is not None

            span0 = float(r0["max"]) - float(r0["min"])
            span1 = float(r1["max"]) - float(r1["min"])

            assert span0 > 0
            assert span1 > 0
            # Страховка від "ривка": діапазон не має вибухати на порядки.
            assert span1 / span0 < 5.0

            await browser.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_ui_v2_tf_change_first_interaction_no_jump() -> None:
    """Після "зміни TF" (переподачі dataset) перша взаємодія має бути стабільною."""

    async with _running_viewer_http_server() as base_url:
        url = f"{base_url}/e2e_smoke.html?test_hooks=1"

        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": 1100, "height": 700})

            await page.goto(url, wait_until="load")
            await page.wait_for_function(
                "window.__e2e__ && window.__e2e__.ready === true"
            )

            # Симулюємо TF change: драйвер перебудує дані та "перевстановить" серію.
            await page.evaluate("window.__e2e__.simulateTfChange()")
            await page.wait_for_function("window.__e2e__.tfChanged === true")

            r0 = await page.evaluate("window.__e2e__.getEffectiveRange()")
            assert r0 and r0.get("min") is not None and r0.get("max") is not None

            await page.mouse.move(1065, 240)
            await page.mouse.wheel(0, -180)

            r1 = await page.evaluate("window.__e2e__.getEffectiveRange()")
            assert r1 and r1.get("min") is not None and r1.get("max") is not None

            span0 = float(r0["max"]) - float(r0["min"])
            span1 = float(r1["max"]) - float(r1["min"])
            assert span0 > 0
            assert span1 > 0
            assert span1 / span0 < 5.0

            await browser.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_ui_v2_tooltip_stable_on_crosshair_events() -> None:
    """Tooltip не має "флапати" при повторних crosshair подіях."""

    async with _running_viewer_http_server() as base_url:
        url = f"{base_url}/e2e_smoke.html?test_hooks=1"

        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": 1100, "height": 700})

            await page.goto(url, wait_until="load")
            await page.wait_for_function(
                "window.__e2e__ && window.__e2e__.ready === true"
            )

            # Відправляємо серію валідних crosshair подій.
            await page.evaluate(
                """
                () => {
                  for (let i = 0; i < 5; i++) {
                    window.__e2e__.emitCrosshairMove({x: 200 + i * 2, y: 120 + i});
                  }
                }
                """
            )

            # Даємо час SHOW_DELAY_MS (200ms) + невеликий запас.
            await page.wait_for_timeout(260)

            tip = await page.evaluate("window.__e2e__.getTooltip()")
            assert tip["visible"] is True
            assert isinstance(tip["text"], str) and tip["text"].strip() != ""

            # Null подія має сховати tooltip (але не миттєво/хаотично).
            await page.evaluate("window.__e2e__.emitCrosshairNull()")
            # Даємо час HIDE_GRACE_MS (250ms) + невеликий запас.
            await page.wait_for_timeout(320)

            tip2 = await page.evaluate("window.__e2e__.getTooltip()")
            assert tip2["visible"] is False

            await browser.close()
