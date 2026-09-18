#!/usr/bin/env python3
"""Central 360 — builds index.html from real, freshly scraped data.

Design principle: never fabricate. Each data section is scraped independently;
if a section's scrape fails, we fall back to whatever that section already
says in the previously published index.html (recovered via HTML comment
markers), rather than crashing the whole page or inventing placeholder data.
"""
from __future__ import annotations
import base64
import calendar
import html as htmlmod
import os
import re
import sys
import traceback
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import moon  # noqa: E402
import sources  # noqa: E402
import browser_sources  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEMPLATE_PATH = os.path.join(REPO_ROOT, "template.html")
OUTPUT_PATH = os.path.join(REPO_ROOT, "index.html")
ASSETS_DIR = os.path.join(REPO_ROOT, "assets")

BRT = timezone(timedelta(hours=-3))
WEEKDAYS_PT_SUN_FIRST = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"]


def log(msg):
    print(f"[scrape] {msg}", flush=True)


def read_prev_output():
    if not os.path.exists(OUTPUT_PATH):
        return ""
    with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
        return f.read()


# FRAMES_JS lives inside a <script> block: HTML comment markers (<!-- -->) are
# unsafe there because JS treats a bare "<!--" as a legacy single-line comment
# opener wherever it appears, silently eating the rest of that line (which, glued
# directly after the marker with no newline, is the first FRAMES entry). Use JS
# block comments for that one section; HTML comments everywhere else.
JS_COMMENT_SECTIONS = {"FRAMES_JS"}


def _markers(name):
    if name in JS_COMMENT_SECTIONS:
        return f"/*S:{name}*/", f"/*E:{name}*/"
    return f"<!--S:{name}-->", f"<!--E:{name}-->"


def get_fallback(prev_html, name):
    start, end = _markers(name)
    m = re.search(re.escape(start) + r"(.*?)" + re.escape(end), prev_html, re.DOTALL)
    if m:
        return m.group(1)
    return None


def wrap(name, content):
    start, end = _markers(name)
    return f"{start}{content}{end}"


def run_section(name, prev_html, fn, *args, **kwargs):
    """Run a scraper fn; on failure, fall back to the previous committed value."""
    try:
        content = fn(*args, **kwargs)
        log(f"OK   {name}")
        return wrap(name, content), True
    except Exception as e:
        log(f"FAIL {name}: {e}")
        traceback.print_exc()
        fallback = get_fallback(prev_html, name)
        if fallback is not None:
            log(f"     -> using previous value for {name}")
            return wrap(name, fallback), False
        log(f"     -> NO previous value available for {name}; leaving empty")
        return wrap(name, ""), False


# ---------------------------------------------------------------------------
# Section builders (each returns an HTML string fragment)
# ---------------------------------------------------------------------------

def build_forecast_rows():
    days = sources.fetch_climatempo_forecast()
    rows = []
    for d in days:
        rain_mm = float(d["rain_mm"].replace(",", "."))
        if rain_mm <= 0:
            emoji = "☁️"
            rain_txt = "0,0 mm"
        else:
            emoji = "🌦️" if rain_mm < 5 else ("🌧️" if rain_mm < 15 else "⛈️")
            rain_txt = f"≈ {d['rain_mm'].replace('.', ',')} mm"
            if d.get("rain_pct"):
                rain_txt += f" ({d['rain_pct']}%)"
        cls = ' class="today"' if d["is_today"] else ""
        desc = htmlmod.escape(d["desc"]) or "Sem descrição disponível"
        tmin = d["tmin"].rstrip("°")
        tmax = d["tmax"].rstrip("°")
        rows.append(
            f'          <tr{cls}>\n'
            f'            <td class="date">{d["date"]}</td>\n'
            f'            <td>{d["weekday"]}</td>\n'
            f'            <td class="cond">{emoji} {desc}</td>\n'
            f'            <td class="num">{tmin}°C</td>\n'
            f'            <td class="num">{tmax}°C</td>\n'
            f'            <td class="num">{rain_txt}</td>\n'
            f'          </tr>'
        )
    return "\n".join(rows)


def build_moon_section(today_local):
    year, month, day = today_local.year, today_local.month, today_local.day
    phase_days = moon.month_phase_days(year, month)
    best_days = {phase_days["new"], phase_days["full"]}
    good_days = {phase_days["first_quarter"], phase_days["last_quarter"]}

    days_in_month = calendar.monthrange(year, month)[1]
    first_weekday_py = datetime(year, month, 1).weekday()  # Monday=0
    lead_pad = (first_weekday_py + 1) % 7  # convert to Sunday-first offset

    cells = ['        <div class="moon-day pad"></div>' for _ in range(lead_pad)]
    for d in range(1, days_in_month + 1):
        icon = moon.phase_icon_for_date(datetime(year, month, d).date())
        classes = ["moon-day"]
        badge = ""
        if d == day:
            classes.append("today")
        if d in best_days:
            classes.append("best-day")
            badge = '<span class="badge best">★ Melhor</span>'
        elif d in good_days:
            classes.append("good-day")
            badge = '<span class="badge good">Técnica</span>'
        cls = " ".join(classes)
        cells.append(f'        <div class="{cls}"><span class="num">{d}</span><span class="icon">{icon}</span>{badge}</div>')
    trail_pad = (7 - (len(cells) % 7)) % 7
    cells.extend(['        <div class="moon-day pad"></div>' for _ in range(trail_pad)])
    grid = "\n".join(cells)

    def fmt(day_num):
        return f"{day_num:02d}/{month:02d}"

    legend = (
        f'        <div><span class="icon">🌑</span> Lua nova — {fmt(phase_days["new"])}</div>\n'
        f'        <div><span class="icon">🌓</span> Quarto crescente — {fmt(phase_days["first_quarter"])}</div>\n'
        f'        <div><span class="icon">🌕</span> Lua cheia — {fmt(phase_days["full"])}</div>\n'
        f'        <div><span class="icon">🌗</span> Quarto minguante — {fmt(phase_days["last_quarter"])}</div>'
    )
    return grid, legend


def build_corinthians_and_standings():
    teams, rodada_atual = sources.fetch_brasileirao_standings()
    cor = next(t for t in teams if t["sigla"] == "COR")
    rodada_disputada = cor["jogos"]

    pos_text = f'{rodada_disputada}ª rodada disputada'
    stats = (
        f'        <div><span class="k">Posição</span><span class="v">{cor["ordem"]}º lugar</span></div>\n'
        f'        <div><span class="k">Pontos</span><span class="v">{cor["pontos"]} pts</span></div>\n'
        f'        <div><span class="k">Aproveitamento</span><span class="v">{cor["aproveitamento"]}%</span></div>\n'
        f'        <div><span class="k">Saldo de gols</span><span class="v">{cor["saldo_gols"]} ({cor["gols_pro"]}–{cor["gols_contra"]})</span></div>'
    )
    streak = sources.describe_streak(cor["ultimos_jogos"])
    note = f'O Corinthians {streak} no Brasileirão.'

    standings_rows = []
    for t in teams:
        cls_parts = []
        if t["ordem"] <= 6:
            cls_parts.append("zone-lib")
        elif t["ordem"] >= 17:
            cls_parts.append("zone-releg")
        if t["sigla"] == "COR":
            cls_parts.append("highlight")
        cls = f' class="{" ".join(cls_parts)}"' if cls_parts else ""
        standings_rows.append(
            f'          <tr{cls}><td class="pos">{t["ordem"]}</td><td class="team">{htmlmod.escape(t["nome_popular"])}</td>'
            f'<td>{t["pontos"]}</td><td>{t["jogos"]}</td><td>{t["vitorias"]}</td><td>{t["empates"]}</td><td>{t["derrotas"]}</td>'
            f'<td>{t["gols_pro"]}</td><td>{t["gols_contra"]}</td><td>{t["saldo_gols"]}</td><td>{t["aproveitamento"]}</td></tr>'
        )
    standings_note = f'Classificação do Campeonato Brasileiro Série A {datetime.now().year} após a {rodada_disputada}ª rodada (times com jogos atrasados podem ter uma partida a mais ou a menos disputada).'

    # next matches (agenda) — round-number inference for consecutive Brasileirão fixtures
    agenda = sources.fetch_team_agenda("264", "COR")
    matches_html = []
    next_round = rodada_atual
    for a in agenda:
        if a["is_libertadores"]:
            comp_html = '<span class="comp lib">Libertadores</span>'
            round_txt = "Fase eliminatória"
        else:
            comp_html = '<span class="comp">Brasileirão</span>'
            round_txt = f"{next_round}ª rodada"
            next_round += 1
        meta = f'{round_txt} · {a["weekday"].lower()} {a["date"]} · {a["time"]} · {htmlmod.escape(a["venue"])}'
        matches_html.append(
            f'        <div class="next-match">\n'
            f'          <div>\n'
            f'            <div class="meta">{comp_html}{meta}</div>\n'
            f'            <div class="teams">{htmlmod.escape(a["home"])} × {htmlmod.escape(a["away"])}</div>\n'
            f'          </div>\n'
            f'        </div>'
        )
    matches = "\n".join(matches_html)

    return pos_text, stats, matches, note, "\n".join(standings_rows), standings_note


def build_news_items():
    rendered_html = browser_sources.fetch_g1_mundo_headlines()
    items = sources.parse_g1_feed_html(rendered_html)
    blocks = []
    for it in items:
        blocks.append(
            f'        <div class="news-item">\n'
            f'          <span class="meta">{htmlmod.escape(it["time"])} · {htmlmod.escape(it["section"])}</span>\n'
            f'          <h3>{htmlmod.escape(it["title"])}</h3>\n'
            f'          <p>{htmlmod.escape(it["desc"])}</p>\n'
            f'        </div>'
        )
    return "\n".join(blocks)


def build_inmet():
    init_label, frames = browser_sources.fetch_inmet_frames()
    init_dt = browser_sources.parse_inmet_init(init_label)
    lines = []
    for f in frames:
        h = int(f["h"])
        valid_dt = init_dt + timedelta(hours=h)
        valid_str = valid_dt.strftime("%d/%m/%Y %H UTC")
        lines.append(f'    {{h:"{f["h"]}", valid:"{valid_str}", src:"{f["src"]}"}}')
    frames_js = ",\n".join(lines)
    valid_first = (init_dt + timedelta(hours=int(frames[0]["h"]))).strftime("%d/%m/%Y %H UTC")
    return init_label, valid_first, frames_js


def b64_data_uri(path, mime):
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{data}"


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sections", choices=["all", "cloud", "inmet"], default="all",
        help=(
            "'cloud' skips INMET (for the ubuntu-latest runner, which INMET's "
            "bot-defense blocks by datacenter IP); 'inmet' skips everything "
            "else (for the self-hosted runner); 'all' runs everything (local)."
        ),
    )
    args = parser.parse_args()
    do_cloud = args.sections in ("all", "cloud")
    do_inmet = args.sections in ("all", "inmet")

    prev_html = read_prev_output()
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(BRT)

    replacements = {}

    # Static assets (never scraped, always fresh from repo)
    replacements["__BGIMG__"] = b64_data_uri(os.path.join(ASSETS_DIR, "background.png"), "image/png")
    replacements["__LOGO__"] = b64_data_uri(os.path.join(ASSETS_DIR, "logo.png"), "image/png")
    replacements["__SEASON_YEAR__"] = str(now_local.year)

    if do_cloud:
        section, ok = run_section("FORECAST_ROWS", prev_html, build_forecast_rows)
    else:
        section = wrap("FORECAST_ROWS", get_fallback(prev_html, "FORECAST_ROWS") or "")
    replacements["__FORECAST_ROWS__"] = section

    if do_cloud:
        try:
            moon_grid, moon_legend = build_moon_section(now_local)
            log("OK   MOON")
            replacements["__MOON_GRID__"] = wrap("MOON_GRID", moon_grid)
            replacements["__MOON_LEGEND__"] = wrap("MOON_LEGEND", moon_legend)
        except Exception as e:
            log(f"FAIL MOON: {e}")
            traceback.print_exc()
            replacements["__MOON_GRID__"] = wrap("MOON_GRID", get_fallback(prev_html, "MOON_GRID") or "")
            replacements["__MOON_LEGEND__"] = wrap("MOON_LEGEND", get_fallback(prev_html, "MOON_LEGEND") or "")
    else:
        replacements["__MOON_GRID__"] = wrap("MOON_GRID", get_fallback(prev_html, "MOON_GRID") or "")
        replacements["__MOON_LEGEND__"] = wrap("MOON_LEGEND", get_fallback(prev_html, "MOON_LEGEND") or "")

    if do_cloud:
        try:
            pos_text, stats, matches, note, standings_rows, standings_note = build_corinthians_and_standings()
            log("OK   CORINTHIANS+STANDINGS")
            replacements["__CORINTHIANS_POS_TEXT__"] = wrap("CORINTHIANS_POS_TEXT", pos_text)
            replacements["__CORINTHIANS_STATS__"] = wrap("CORINTHIANS_STATS", stats)
            replacements["__CORINTHIANS_MATCHES__"] = wrap("CORINTHIANS_MATCHES", matches)
            replacements["__CORINTHIANS_NOTE__"] = wrap("CORINTHIANS_NOTE", note)
            replacements["__STANDINGS_ROWS__"] = wrap("STANDINGS_ROWS", standings_rows)
            replacements["__STANDINGS_NOTE__"] = wrap("STANDINGS_NOTE", standings_note)
        except Exception as e:
            log(f"FAIL CORINTHIANS+STANDINGS: {e}")
            traceback.print_exc()
            for name in ("CORINTHIANS_POS_TEXT", "CORINTHIANS_STATS", "CORINTHIANS_MATCHES",
                         "CORINTHIANS_NOTE", "STANDINGS_ROWS", "STANDINGS_NOTE"):
                replacements[f"__{name}__"] = wrap(name, get_fallback(prev_html, name) or "")
    else:
        for name in ("CORINTHIANS_POS_TEXT", "CORINTHIANS_STATS", "CORINTHIANS_MATCHES",
                     "CORINTHIANS_NOTE", "STANDINGS_ROWS", "STANDINGS_NOTE"):
            replacements[f"__{name}__"] = wrap(name, get_fallback(prev_html, name) or "")

    if do_cloud:
        section, ok = run_section("NEWS_ITEMS", prev_html, build_news_items)
    else:
        section = wrap("NEWS_ITEMS", get_fallback(prev_html, "NEWS_ITEMS") or "")
    replacements["__NEWS_ITEMS__"] = section

    if do_inmet:
        try:
            init_label, valid_first, frames_js = build_inmet()
            log("OK   INMET")
            replacements["__INIT_DATE__"] = wrap("INIT_DATE", init_label)
            replacements["__VALID_DATE__"] = wrap("VALID_DATE", valid_first)
            replacements["__FRAMES_JS__"] = wrap("FRAMES_JS", frames_js)
        except Exception as e:
            log(f"FAIL INMET: {e}")
            traceback.print_exc()
            replacements["__INIT_DATE__"] = wrap("INIT_DATE", get_fallback(prev_html, "INIT_DATE") or "")
            replacements["__VALID_DATE__"] = wrap("VALID_DATE", get_fallback(prev_html, "VALID_DATE") or "")
            replacements["__FRAMES_JS__"] = wrap("FRAMES_JS", get_fallback(prev_html, "FRAMES_JS") or "")
    else:
        replacements["__INIT_DATE__"] = wrap("INIT_DATE", get_fallback(prev_html, "INIT_DATE") or "")
        replacements["__VALID_DATE__"] = wrap("VALID_DATE", get_fallback(prev_html, "VALID_DATE") or "")
        replacements["__FRAMES_JS__"] = wrap("FRAMES_JS", get_fallback(prev_html, "FRAMES_JS") or "")

    replacements["__LAST_UPDATED__"] = now_utc.strftime("%d/%m/%Y %H:%M UTC")

    out = template
    for placeholder, value in replacements.items():
        out = out.replace(placeholder, value)

    remaining = re.findall(r"__[A-Z0-9_]+__", out)
    if remaining:
        log(f"WARNING: placeholders not substituted: {set(remaining)}")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(out)
    log(f"wrote {OUTPUT_PATH} ({len(out)} bytes)")


if __name__ == "__main__":
    main()
