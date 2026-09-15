"""Scrapers that need a real (headless) browser: INMET VIME (React SPA, images
only ever exist as JS-computed base64 after interaction) and g1.globo Mundo
(client-side rendered feed).
"""
from __future__ import annotations
import re
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

HOUR_IDS = list(range(0, 33, 2))  # +024h..+120h step 6h, 17 frames

# INMET's VIME sits behind an F5 bot-defense layer (the "TSxxxxx" cookie/
# "/TSbd/..." request seen on the site) that fingerprints automated browsers
# and can withhold the real app (serving a slow/never-resolving challenge
# instead) when it detects one — which happens far more reliably from a cloud
# datacenter IP (GitHub Actions runners) than from a residential/office one.
# This patches the most common headless/automation tells before anything else
# runs, to look like a normal Chrome tab.
STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR', 'pt', 'en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );
}
"""


def _new_stealth_page(browser):
    context = browser.new_context(
        user_agent=UA,
        locale="pt-BR",
        viewport={"width": 1366, "height": 900},
        extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"},
    )
    context.add_init_script(STEALTH_INIT_SCRIPT)
    return context, context.new_page()


def _fetch_inmet_frames_once(browser, timeout_ms: int):
    context, page = _new_stealth_page(browser)
    try:
        page.goto("https://vime.inmet.gov.br/", wait_until="load", timeout=timeout_ms)
        page.wait_for_selector("#BRA", timeout=timeout_ms)
        page.eval_on_selector("#BRA", "el => el.click()")

        selects = page.locator("select")
        selects.nth(0).select_option("COSMO7")
        selects.nth(1).select_option("prec24h")
        init_select = selects.nth(2)
        init_select.select_option(index=0)  # latest rodada available
        init_label = init_select.locator("option:checked").inner_text().strip()
        page.wait_for_timeout(800)

        def read_src():
            return page.eval_on_selector("img.img", "el => el.src")

        # Hour id 0 (+024h) is the app's default-active tab, so the very
        # first click on it can be a no-op (no state transition to detect).
        # Force a real transition away from it first, then let the loop
        # below click back into 0 for real.
        page.evaluate(f'document.getElementById("{HOUR_IDS[-1]}").click()')
        page.wait_for_timeout(1500)

        frames = []
        last_src = None
        for idx, hour_id in enumerate(HOUR_IDS):
            page.evaluate(f'document.getElementById("{hour_id}").click()')
            hour_label = page.evaluate(f'document.getElementById("{hour_id}").textContent').strip()
            # poll until the image actually changes (or timeout)
            src = last_src
            for _ in range(20):
                page.wait_for_timeout(300)
                src = read_src()
                if src and src != last_src and src.startswith("data:image"):
                    break
            if not src or not src.startswith("data:image"):
                raise RuntimeError(f"INMET: imagem inválida no quadro {hour_label}")
            last_src = src
            frames.append({"h": hour_label.lstrip("+"), "src": src})

        if len(frames) != 17:
            raise RuntimeError(f"INMET: esperava 17 quadros, obtive {len(frames)}")
        srcs = [f["src"] for f in frames]
        if len(set(srcs)) != len(srcs):
            raise RuntimeError("INMET: dois ou mais quadros vieram com a mesma imagem (falha de captura)")
        return init_label, frames
    finally:
        context.close()


def fetch_inmet_frames(timeout_ms: int = 90000, attempts: int = 3):
    """Returns (init_str, frames) where init_str is 'DD/MM/YYYY HH UTC' (latest
    rodada) and frames is a list of {h, valid, src} for +024h..+120h step 6h.
    Retries with a fresh browser context, since the site's bot-defense
    occasionally withholds the app on the first attempt.
    """
    last_error = None
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for attempt in range(1, attempts + 1):
                try:
                    return _fetch_inmet_frames_once(browser, timeout_ms)
                except Exception as e:
                    last_error = e
                    print(f"[browser_sources] INMET tentativa {attempt}/{attempts} falhou: {e}", flush=True)
        finally:
            browser.close()
    raise last_error


def parse_inmet_init(init_label: str):
    """'DD/MM/YYYY HH UTC' or similar -> datetime (UTC, naive)."""
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})[ T]?(\d{2})", init_label)
    if not m:
        raise RuntimeError(f"INMET: não consegui interpretar a data de inicialização '{init_label}'")
    dd, mm, yyyy, hh = map(int, m.groups())
    return datetime(yyyy, mm, dd, hh)


def fetch_g1_mundo_headlines(limit: int = 6, timeout_ms: int = 45000):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context, page = _new_stealth_page(browser)
        try:
            page.goto("https://g1.globo.com/mundo/", wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_selector(".feed-post-body", timeout=timeout_ms)
            page.wait_for_timeout(2500)
            html = page.content()
            return html
        finally:
            context.close()
            browser.close()
