"""Real-data scrapers for Central 360. No fabricated values: every function either
returns real extracted data or raises, letting the caller fall back to the last
known-good value already committed in index.html.
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA}
TIMEOUT = 25

WEEKDAYS_PT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]  # Monday=0 (Python)


def _weekday_pt(d):
    return WEEKDAYS_PT[d.weekday()]


# ---------------------------------------------------------------------------
# ClimaTempo — 7-day forecast for a city (default: Piracicaba-SP, city id 513)
# ---------------------------------------------------------------------------

def fetch_climatempo_forecast(city_id: int = 513, city_slug: str = "piracicaba-sp", days: int = 7):
    url = f"https://www.climatempo.com.br/previsao-do-tempo/15-dias/cidade/{city_id}/{city_slug}"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    cards = soup.select("#forecast-stories .forecast-stories__item .card")
    first_card = soup.select_one("#forecast-stories .card")
    if first_card is not None and (not cards or cards[0] is not first_card):
        cards = [first_card] + cards
    if len(cards) < days:
        raise RuntimeError(f"ClimaTempo: esperava >= {days} cards, achei {len(cards)}")

    active = soup.select_one(".timeline-days .-active .day")
    if active is None:
        raise RuntimeError("ClimaTempo: dia âncora (-active) não encontrado")
    anchor_day = int(active.get_text(strip=True))

    # anchor date: current month/year assumed (ClimaTempo always starts at "hoje")
    now = datetime.now(timezone(timedelta(hours=-3)))  # America/Sao_Paulo, fixed -03:00
    anchor_date = now.replace(day=anchor_day, hour=0, minute=0, second=0, microsecond=0)

    results = []
    for i in range(days):
        card = cards[i]
        desc_el = card.select_one("p.-line-height-24")
        desc = desc_el.get_text(strip=True) if desc_el else ""

        tmin = tmax = rain_mm = rain_pct = None
        for vc in card.select(".variable-card"):
            ident = vc.select_one(".variables-border-identifier")
            cls = ident.get("class", []) if ident else []
            if "-temperature" in cls:
                spans = vc.select("div.-gray span")
                nums = [s.get_text(strip=True) for s in spans if re.match(r"^\d+°$", s.get_text(strip=True))]
                if len(nums) >= 2:
                    tmin, tmax = nums[0], nums[1]
            elif "-rain" in cls:
                span = vc.select_one("span._margin-l-5")
                if span:
                    m = re.match(r"([\d,.]+)mm(?:\s*-\s*(\d+)%)?", span.get_text(strip=True))
                    if m:
                        rain_mm, rain_pct = m.group(1), m.group(2)

        if tmin is None or tmax is None or rain_mm is None:
            raise RuntimeError(f"ClimaTempo: dados incompletos no card {i}")

        d = anchor_date + timedelta(days=i)
        results.append({
            "date": d.strftime("%d/%m"),
            "weekday": _weekday_pt(d),
            "desc": desc,
            "tmin": tmin,
            "tmax": tmax,
            "rain_mm": rain_mm,
            "rain_pct": rain_pct,
            "is_today": i == 0,
        })
    return results


# ---------------------------------------------------------------------------
# ge.globo — Brasileirão Série A standings (embedded JSON, no JS needed)
# ---------------------------------------------------------------------------

def _extract_balanced(html: str, start: int, open_ch: str, close_ch: str) -> str:
    depth = 0
    i = start
    in_str = False
    escape = False
    while i < len(html):
        c = html[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    return html[start:i + 1]
        i += 1
    raise RuntimeError("Não foi possível balancear o bloco JSON (chaves não fecharam)")


def _extract_const(html: str, varname: str) -> str:
    marker = f"const {varname} = "
    idx = html.find(marker)
    if idx == -1:
        raise RuntimeError(f"Variável '{varname}' não encontrada na página")
    start = idx + len(marker)
    open_ch = html[start]
    close_ch = "}" if open_ch == "{" else "]"
    return _extract_balanced(html, start, open_ch, close_ch)


def fetch_brasileirao_standings():
    url = "https://ge.globo.com/futebol/brasileirao-serie-a/"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    raw = _extract_const(r.text, "classificacao")
    data = json.loads(raw)
    teams = data["classificacao"]
    teams.sort(key=lambda t: t["ordem"])
    rodada_atual = data.get("rodada", {}).get("atual")
    return teams, rodada_atual


def describe_streak(ultimos_jogos):
    """ultimos_jogos: list of 'v'/'e'/'d', oldest -> newest."""
    if not ultimos_jogos:
        return ""
    last = ultimos_jogos[-1]
    count = 0
    for r in reversed(ultimos_jogos):
        if r == last:
            count += 1
        else:
            break
    labels_singular = {"v": "vitória", "d": "derrota", "e": "empate"}
    labels_plural = {"v": "vitórias", "d": "derrotas", "e": "empates"}
    if count == 1:
        return f"vem de uma {labels_singular[last]} na última rodada"
    return f"vem de {count} {labels_plural[last]} seguidas"


# ---------------------------------------------------------------------------
# ge.globo — team agenda (next matches), embedded JSON per team page
# ---------------------------------------------------------------------------

STADIUM_BY_TEAM = {
    "COR": "Neo Química Arena", "FLA": "Maracanã", "FLU": "Maracanã",
    "PAL": "Allianz Parque", "SAO": "MorumBIS", "SAN": "Vila Belmiro",
    "BOT": "Nilton Santos", "VAS": "São Januário", "CRU": "Mineirão",
    "CAM": "Arena MRV", "GRE": "Arena do Grêmio", "INT": "Beira-Rio",
    "BAH": "Arena Fonte Nova", "CAP": "Ligga Arena", "CFC": "Couto Pereira",
    "RBB": "Nabi Abi Chedid", "VIT": "Barradão", "MIR": "Maião",
    "REM": "Baenão", "CHA": "Condá", "EST": "Ciudad de La Plata",
}


def fetch_team_agenda(equipe_sde_id: str, own_sigla: str, limit: int = 3):
    url = f"https://ge.globo.com/futebol/times/{_team_slug(equipe_sde_id)}/"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    html = r.text
    anchor = '"apiName":"post-lista-de-jogos"'
    idx = html.find(anchor)
    if idx == -1:
        raise RuntimeError("Bloco 'post-lista-de-jogos' não encontrado na página do time")
    key_marker = '"jogos":['
    kidx = html.find(key_marker, idx)
    if kidx == -1:
        raise RuntimeError("Campo 'jogos' não encontrado no bloco de agenda")
    start = kidx + len(key_marker) - 1
    raw = _extract_balanced(html, start, "[", "]")
    jogos = json.loads(raw)

    out = []
    for j in jogos[:limit]:
        comp = j["campeonato"].get("nome_popular") or j["campeonato"].get("nome") or "Campeonato"
        mandante = j["equipe_mandante"]["sigla"]
        visitante = j["equipe_visitante"]["sigla"]
        mandante_nome = j["equipe_mandante"]["nome_popular"]
        visitante_nome = j["equipe_visitante"]["nome_popular"]
        data_dt = datetime.strptime(j["data_realizacao"], "%Y-%m-%d")
        hora = (j.get("hora_realizacao") or "")[:5]
        home_sigla = mandante
        venue = STADIUM_BY_TEAM.get(home_sigla, "local a definir")
        out.append({
            "competition": comp,
            "date": data_dt.strftime("%d/%m"),
            "weekday": _weekday_pt(data_dt),
            "time": hora,
            "venue": venue,
            "home": mandante_nome,
            "away": visitante_nome,
            "is_libertadores": "libertadores" in comp.lower(),
        })
    return out


_TEAM_SLUGS = {
    "264": "corinthians",
}


def _team_slug(equipe_sde_id: str) -> str:
    return _TEAM_SLUGS.get(equipe_sde_id, equipe_sde_id)


# ---------------------------------------------------------------------------
# g1.globo — Mundo headlines (client-rendered feed, needs a real browser)
# ---------------------------------------------------------------------------

def parse_g1_feed_html(rendered_html: str, limit: int = 6):
    soup = BeautifulSoup(rendered_html, "html.parser")
    items = soup.select("div.feed-post-body")
    out = []
    for it in items:
        title_el = it.select_one(".feed-post-body-title p, .feed-post-link p")
        if title_el is None:
            continue
        title = title_el.get_text(strip=True)
        if not title:
            continue
        resumo_el = it.select_one(".feed-post-body-resumo p")
        resumo = resumo_el.get_text(strip=True) if resumo_el else ""
        time_el = it.select_one(".feed-post-datetime")
        rel_time = time_el.get_text(strip=True) if time_el else ""
        section_el = it.select_one(".feed-post-metadata-section")
        section = section_el.get_text(strip=True) if section_el else "Mundo"
        if not resumo:
            continue
        out.append({"title": title, "desc": resumo, "time": rel_time, "section": section})
        if len(out) >= limit:
            break
    if len(out) < limit:
        raise RuntimeError(f"g1: esperava {limit} manchetes completas, achei {len(out)}")
    return out
