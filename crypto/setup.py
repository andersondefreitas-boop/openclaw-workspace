#!/usr/bin/env python3
"""
Crypto Trade Setup Analyzer
Uso: python3 setup.py BTC
     python3 setup.py SOLUSDT
     python3 setup.py BTC 1h
"""

import sys
import requests
import numpy as np

SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
    "TAO": "TAOUSDT",
    "VIRTUAL": "VIRTUALUSDT",
    "VIRTUALS": "VIRTUALUSDT",
    "ADA": "ADAUSDT",
    "RENDER": "RENDERUSDT",
    "NEAR": "NEARUSDT",
    "AAVE": "AAVEUSDT",
    "LINK": "LINKUSDT",
    "AVAX": "AVAXUSDT",
    "HBAR": "HBARUSDT",
    "ZEC": "ZECUSDT",
    "TON": "TONUSDT",
    "DOGE": "DOGEUSDT",
    "TRX": "TRXUSDT",
    "POL": "POLUSDT",
    "LTC": "LTCUSDT",
    "ICP": "ICPUSDT",
    "XMR": "XMRUSDT",
    "UNI": "UNIUSDT",
    "VET": "VETUSDT",
    "SUI": "SUIUSDT",
}

def get_symbol(raw):
    raw = raw.upper()
    if raw in SYMBOL_MAP:
        return SYMBOL_MAP[raw]
    if not raw.endswith("USDT"):
        return raw + "USDT"
    return raw

def fetch_klines(symbol, interval="4h", limit=100):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    closes = np.array([float(d[4]) for d in data])
    highs  = np.array([float(d[2]) for d in data])
    lows   = np.array([float(d[3]) for d in data])
    return closes, highs, lows

def fetch_price(symbol):
    url = "https://api.binance.com/api/v3/ticker/price"
    r = requests.get(url, params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])

def ema(series, period):
    result = np.zeros_like(series)
    k = 2 / (period + 1)
    result[0] = series[0]
    for i in range(1, len(series)):
        result[i] = series[i] * k + result[i-1] * (1 - k)
    return result

def rsi(closes, period=14):
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    if avg_loss == 0:
        return 100.0
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    rs = avg_gain / avg_loss if avg_loss != 0 else 100
    return 100 - (100 / (1 + rs))

def bollinger(closes, period=20, std_dev=2):
    sma = np.mean(closes[-period:])
    std = np.std(closes[-period:])
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    return upper, sma, lower

def support_resistance(highs, lows, n=20):
    recent_highs = highs[-n:]
    recent_lows = lows[-n:]
    resistance = float(np.max(recent_highs))
    support = float(np.min(recent_lows))
    return support, resistance

def format_price(price):
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:.4f}"
    else:
        return f"${price:.6f}"

def pct(a, b):
    return ((b - a) / a) * 100

def analyze(symbol_raw, interval="4h"):
    symbol = get_symbol(symbol_raw)
    
    try:
        closes, highs, lows = fetch_klines(symbol, interval)
        price = fetch_price(symbol)
    except Exception as e:
        return f"❌ Erro ao buscar dados para {symbol}: {e}"

    rsi_val = rsi(closes)
    ema20 = ema(closes, 20)[-1]
    ema50 = ema(closes, 50)[-1]
    bb_upper, bb_mid, bb_lower = bollinger(closes)
    support, resistance = support_resistance(highs, lows)

    # Tendência
    if ema20 > ema50:
        trend = "📈 ALTA (EMA20 > EMA50)"
        trend_bias = "long"
    else:
        trend = "📉 BAIXA (EMA20 < EMA50)"
        trend_bias = "short"

    # RSI signal
    if rsi_val < 30:
        rsi_signal = "🟢 Sobrevendido — potencial reversão de alta"
    elif rsi_val > 70:
        rsi_signal = "🔴 Sobrecomprado — potencial reversão de baixa"
    elif rsi_val < 45:
        rsi_signal = "🟡 Neutro-baixo"
    elif rsi_val > 55:
        rsi_signal = "🟡 Neutro-alto"
    else:
        rsi_signal = "⚪ Neutro"

    # Setup sugerido
    if trend_bias == "long" and rsi_val < 60:
        setup = "LONG"
        entry = price
        sl = round(support * 0.99, 6)
        tp1 = round(price + (price - sl) * 1.5, 6)
        tp2 = round(price + (price - sl) * 3.0, 6)
    elif trend_bias == "short" and rsi_val > 40:
        setup = "SHORT"
        entry = price
        sl = round(resistance * 1.01, 6)
        tp1 = round(price - (sl - price) * 1.5, 6)
        tp2 = round(price - (sl - price) * 3.0, 6)
    else:
        setup = "AGUARDAR"
        entry = price
        sl = None
        tp1 = None
        tp2 = None

    # Monta output
    lines = [
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🔍 {symbol} — Setup ({interval})",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"💰 Preço atual: {format_price(price)}",
        f"",
        f"📊 Indicadores",
        f"  RSI(14): {rsi_val:.1f} — {rsi_signal}",
        f"  EMA20:   {format_price(ema20)}",
        f"  EMA50:   {format_price(ema50)}",
        f"  BB sup:  {format_price(bb_upper)}",
        f"  BB inf:  {format_price(bb_lower)}",
        f"",
        f"🗺 Zonas",
        f"  Suporte:    {format_price(support)}",
        f"  Resistência: {format_price(resistance)}",
        f"",
        f"📈 Tendência: {trend}",
        f"",
    ]

    if setup == "AGUARDAR":
        lines += [
            f"⏳ Setup: AGUARDAR",
            f"  Sem confluência clara no momento.",
        ]
    else:
        risk = abs(pct(entry, sl))
        rr1 = abs(pct(entry, tp1)) / risk if risk > 0 else 0
        rr2 = abs(pct(entry, tp2)) / risk if risk > 0 else 0
        lines += [
            f"🎯 Setup: {setup}",
            f"  Entrada: {format_price(entry)}",
            f"  SL:      {format_price(sl)} ({pct(entry, sl):+.2f}%)",
            f"  TP1:     {format_price(tp1)} ({pct(entry, tp1):+.2f}%) | R:R {rr1:.1f}",
            f"  TP2:     {format_price(tp2)} ({pct(entry, tp2):+.2f}%) | R:R {rr2:.1f}",
        ]

    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⚠️ Análise informativa. Não é recomendação financeira.")

    return "\n".join(lines)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 setup.py <ATIVO> [intervalo]")
        print("Ex:  python3 setup.py BTC")
        print("     python3 setup.py SOL 1h")
        print(f"\nAtivos rápidos: {', '.join(SYMBOL_MAP.keys())}")
        sys.exit(1)

    symbol = sys.argv[1]
    interval = sys.argv[2] if len(sys.argv) > 2 else "4h"
    print(analyze(symbol, interval))
