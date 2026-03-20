#!/usr/bin/env python3
"""
Jarvis Crypto News
Puxa notícias de fontes relevantes, filtra por impacto e envia resumo via Telegram.
"""

import urllib.request
import urllib.parse
import json
import subprocess
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET

# ─── CONFIGURAÇÃO ──────────────────────────────────────────────────────────────

# Ativos monitorados (para filtro de relevância)
WATCHLIST = [
    "bitcoin", "btc", "solana", "sol", "xrp", "ripple",
    "tao", "bittensor", "virtual", "virtuals", "cardano", "ada",
    "render", "rndr", "near", "aave", "chainlink", "link",
    "avalanche", "avax", "hbar", "hedera", "zcash", "zec",
    "ton", "toncoin"
]

# Palavras-chave de impacto de mercado
IMPACT_KEYWORDS = [
    "sec", "etf", "fed", "interest rate", "regulation", "ban", "hack", "exploit",
    "whale", "liquidation", "all-time high", "ath", "crash", "dump", "pump",
    "listing", "delisting", "partnership", "adoption", "institutional",
    "blackrock", "fidelity", "binance", "coinbase", "bybit", "grayscale",
    "trump", "congress", "china", "russia", "stablecoin", "usdt", "usdc",
    "cpi", "inflation", "recession", "halving", "fork", "upgrade", "mainnet",
    "bull", "bear", "breakout", "support", "resistance"
]

# Fontes RSS
RSS_FEEDS = [
    ("CoinTelegraph",  "https://cointelegraph.com/rss"),
    ("CoinDesk",       "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Decrypt",        "https://decrypt.co/feed"),
    ("The Block",      "https://www.theblock.co/rss.xml"),
]

# API CryptoCompare (gratuita)
CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=latest"

# Janela de tempo: últimas N horas
HOURS_BACK = 2

# ─── FUNÇÕES ───────────────────────────────────────────────────────────────────

def fetch_url(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  Erro ao buscar {url}: {e}")
        return None


def is_relevant(text):
    text_lower = text.lower()
    coin_match   = any(kw in text_lower for kw in WATCHLIST)
    impact_match = any(kw in text_lower for kw in IMPACT_KEYWORDS)
    return coin_match or impact_match


def fetch_cryptocompare():
    items = []
    data = fetch_url(CRYPTOCOMPARE_URL)
    if not data:
        return items
    try:
        j = json.loads(data)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
        for article in j.get("Data", []):
            pub = datetime.fromtimestamp(article["published_on"], tz=timezone.utc)
            if pub < cutoff:
                continue
            title = article.get("title", "")
            body  = article.get("body", "")
            url   = article.get("url", "")
            if is_relevant(title + " " + body):
                items.append({"title": title, "url": url, "source": "CryptoCompare", "time": pub})
    except Exception as e:
        print(f"  Erro ao parsear CryptoCompare: {e}")
    return items


def fetch_rss(name, url):
    items = []
    data = fetch_url(url)
    if not data:
        return items
    try:
        root = ET.fromstring(data)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
        for item in root.iter("item"):
            title   = item.findtext("title", "")
            link    = item.findtext("link", "")
            pub_str = item.findtext("pubDate", "")
            # Parse da data
            try:
                from email.utils import parsedate_to_datetime
                pub = parsedate_to_datetime(pub_str).astimezone(timezone.utc)
            except Exception:
                pub = datetime.now(timezone.utc)

            if pub < cutoff:
                continue
            if is_relevant(title):
                items.append({"title": title, "url": link, "source": name, "time": pub})
    except Exception as e:
        print(f"  Erro ao parsear RSS {name}: {e}")
    return items


def categorize(title):
    t = title.lower()
    if any(k in t for k in ["sec", "regulation", "ban", "law", "congress", "trump", "government"]):
        return "📜 Regulação"
    elif any(k in t for k in ["hack", "exploit", "stolen", "breach", "attack"]):
        return "🚨 Segurança"
    elif any(k in t for k in ["etf", "institutional", "blackrock", "fidelity", "grayscale"]):
        return "🏦 Institucional"
    elif any(k in t for k in ["fed", "cpi", "inflation", "interest rate", "macro"]):
        return "📊 Macro"
    elif any(k in t for k in ["listing", "partnership", "mainnet", "upgrade", "fork", "launch"]):
        return "🔧 Desenvolvimento"
    elif any(k in t for k in ["crash", "dump", "liquidation", "bear"]):
        return "📉 Queda"
    elif any(k in t for k in ["pump", "ath", "all-time", "bull", "breakout", "rally"]):
        return "📈 Alta"
    else:
        return "📰 Geral"


TELEGRAM_CHAT_ID = "127842708"

def send_telegram(message):
    try:
        subprocess.run(
            ["openclaw", "message", "send",
             "--channel", "telegram",
             "--target", TELEGRAM_CHAT_ID,
             "--message", message],
            check=True, capture_output=True
        )
    except subprocess.CalledProcessError as e:
        print(f"Erro ao enviar Telegram: {e.stderr.decode()}")


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Buscando notícias das últimas {HOURS_BACK}h...")

    all_items = []

    # CryptoCompare API
    items = fetch_cryptocompare()
    print(f"  CryptoCompare: {len(items)} relevantes")
    all_items.extend(items)

    # RSS feeds
    for name, url in RSS_FEEDS:
        items = fetch_rss(name, url)
        print(f"  {name}: {len(items)} relevantes")
        all_items.extend(items)

    # Deduplica por título similar
    seen = set()
    unique = []
    for item in all_items:
        key = item["title"][:60].lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)

    # Ordena por tempo (mais recente primeiro)
    unique.sort(key=lambda x: x["time"], reverse=True)

    # Limita a 10 notícias
    unique = unique[:10]

    if not unique:
        print("Nenhuma notícia relevante encontrada.")
        return

    # Monta mensagem
    now_br = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3)))
    header = f"📡 *RADAR DE NOTÍCIAS CRIPTO*\n{now_br.strftime('%H:%M · %d/%m/%Y')} | Últimas {HOURS_BACK}h\n"
    header += "─" * 30 + "\n\n"

    body = ""
    for item in unique:
        cat = categorize(item["title"])
        body += f"{cat}\n*{item['title']}*\n_{item['source']}_\n\n"

    footer = f"─" * 30 + f"\n_{len(unique)} notícias relevantes encontradas_"

    message = header + body + footer
    send_telegram(message)
    print(f"Resumo enviado com {len(unique)} notícias.")


if __name__ == "__main__":
    main()
