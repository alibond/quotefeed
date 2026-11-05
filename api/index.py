"""
BTP Quotes Service — versione Vercel (serverless)
Nota: Vercel è stateless, quindi i dati non vengono salvati su disco.
Ogni chiamata fa scraping fresco da Borsa Italiana.
"""

import re
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests as req
from bs4 import BeautifulSoup

app = Flask(__name__)
CORS(app)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

def validate_isin(isin):
    return bool(ISIN_RE.match(isin))

# Sezioni di Borsa Italiana in cui cercare lo strumento.
# L'ordine conta: la prima che restituisce un prezzo vince.
SOURCES = {
    "MOTX": "https://www.borsaitaliana.it/borsa/obbligazioni/mot/btp/scheda/{isin}-MOTX.html?lang=it",
    "ETLX": "https://www.borsaitaliana.it/borsa/obbligazioni/eurotlx/scheda/{isin}-ETLX.html?lang=it",
}

def candidate_sources(isin: str):
    """BTP italiani (IT...) prima su MOT, tutto il resto prima su EuroTLX."""
    if isin.startswith("IT"):
        return ["MOTX", "ETLX"]
    return ["ETLX", "MOTX"]

def parse_number(text: str) -> float:
    """Converte '92,23' o '1.234,56' (formato italiano) in float."""
    t = text.strip().replace(".", "").replace(",", ".")
    return float(t)

def extract_price(soup: BeautifulSoup):
    # 1) Prezzo in intestazione (stesso selettore usato per i BTP)
    el = soup.select_one("span.-formatPrice strong")
    if el:
        try:
            return parse_number(el.get_text(strip=True))
        except ValueError:
            pass
    # 2) Fallback: tabella "Dati Mercato" (pagine EuroTLX)
    for label in ("Prezzo ultimo contratto", "Prezzo di riferimento"):
        for strong in soup.find_all("strong"):
            if strong.get_text(strip=True).lower() == label.lower():
                cell = strong.find_parent(["td", "th"])
                value_cell = cell.find_next_sibling(["td", "th"]) if cell else None
                if value_cell:
                    try:
                        return parse_number(value_cell.get_text(strip=True))
                    except ValueError:
                        continue
    return None

def scrape_current_price(isin: str) -> dict:
    errors = []
    for key in candidate_sources(isin):
        url = SOURCES[key].format(isin=isin)
        try:
            r = req.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            price = extract_price(soup)
            if price is not None:
                return {"price": price, "market": key, "url": url}
            errors.append(f"{key}: prezzo non trovato")
        except Exception as e:
            errors.append(f"{key}: {e}")
    raise RuntimeError(f"Prezzo non trovato per {isin} ({'; '.join(errors)})")

# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return jsonify({
        "service": "BTP Quotes Service",
        "usage": "/api/quotes?isin=IT0005634800 (anche ISIN esteri su EuroTLX, es. US912828YS30)",
        "pp_config": {
            "provider": "JSON Quote Feed",
            "feed_url": "https://<tuo-dominio>.vercel.app/api/quotes?isin=IT0005634800",
            "path_to_date": "$[*].date",
            "path_to_close": "$[*].close"
        }
    })

@app.route("/api/quotes")
def quotes():
    isin = request.args.get("isin", "").strip().upper()
    if not isin:
        return jsonify({"error": "Parametro 'isin' obbligatorio"}), 400
    if not validate_isin(isin):
        return jsonify({"error": f"ISIN non valido: {isin}"}), 400

    try:
        result = scrape_current_price(isin)
        price = result["price"]
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    from datetime import date
    today = date.today().isoformat()

    # Vercel è stateless: restituisce solo la quotazione odierna.
    # Lo storico viene accumulato da Portfolio Performance stesso
    # che salva ogni quotazione ricevuta nel suo database locale.
    return jsonify([{"date": today, "close": price}])
