#!/usr/bin/env python3
"""
Crypto Pattern Scanner — Bot de varredura automática
Roda via cron a cada 1h e envia alertas no Telegram via OpenClaw.

Setups detectados:
  Day Trade (5min, 15min, 1h):
    - VWAP + EMA 9/20
    - Power Breakout (consolidação + volume)
  Swing Trade (4h, Diário):
    - Setup 9.1 Larry Williams (EMA9 vira)
    - Golden/Death Cross (SMA50 x SMA200)
  Extras:
    - RSI extremo
    - BB Squeeze
    - Cruzamento EMA20/50
"""

import requests
import numpy as np
import json
import os
import sys
import subprocess
from datetime import datetime, timezone

ASSETS = [
    "BTC", "SOL", "XRP", "TAO", "VIRTUAL",
    "ADA", "RENDER", "NEAR", "AAVE", "LINK",
    "AVAX", "HBAR", "ZEC", "TON",
    "DOGE", "TRX", "POL", "LTC", "ICP",
    "XMR", "UNI", "VET", "SUI",
]

# Ativos em exchanges alternativas: {símbolo: (exchange, url_base)}
ALT_ASSETS = {
    "QUBIC": ("mexc", "https://api.mexc.com"),
}

SYMBOL_MAP = {a: a + "USDT" for a in ASSETS}

STATE_FILE = os.path.join(os.path.dirname(__file__), "scanner_state.json")

# ─── Binance helpers ──────────────────────────────────────────────────────────

def fetch_klines(symbol, interval, limit=250, exchange="binance"):
    if exchange == "mexc":
        url = "https://api.mexc.com/api/v3/klines"
    else:
        url = "https://api.binance.com/api/v3/klines"
    r = requests.get(url, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=15)
    r.raise_for_status()
    data = r.json()
    opens   = np.array([float(d[1]) for d in data])
    highs   = np.array([float(d[2]) for d in data])
    lows    = np.array([float(d[3]) for d in data])
    closes  = np.array([float(d[4]) for d in data])
    volumes = np.array([float(d[5]) for d in data])
    return opens, highs, lows, closes, volumes

# ─── Indicator helpers ────────────────────────────────────────────────────────

def ema(series, period):
    result = np.zeros_like(series)
    k = 2 / (period + 1)
    result[0] = series[0]
    for i in range(1, len(series)):
        result[i] = series[i] * k + result[i-1] * (1 - k)
    return result

def sma(series, period):
    result = np.full_like(series, np.nan)
    for i in range(period - 1, len(series)):
        result[i] = np.mean(series[i - period + 1:i + 1])
    return result

def rsi(closes, period=14):
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))

def bollinger_bandwidth(closes, period=20):
    std = np.std(closes[-period:])
    mid = np.mean(closes[-period:])
    return (std * 4) / mid  # normalized bandwidth

def vwap_daily(opens, highs, lows, closes, volumes):
    """VWAP simples do período disponível (proxy diário)."""
    typical = (highs + lows + closes) / 3
    cumvol = np.cumsum(volumes)
    cumtpv = np.cumsum(typical * volumes)
    vwap = cumtpv / cumvol
    return vwap

# ─── Setup detectors ──────────────────────────────────────────────────────────

def detect_vwap_ema(symbol, interval="15m", exchange="binance"):
    """Setup A Day Trade: VWAP + EMA9/20 pullback."""
    signals = []
    try:
        opens, highs, lows, closes, volumes = fetch_klines(symbol, interval, 100, exchange)
        vwap = vwap_daily(opens, highs, lows, closes, volumes)
        e9  = ema(closes, 9)
        e20 = ema(closes, 20)
        price = closes[-1]

        # Cruzamento EMA9 sobre EMA20 (últimos 2 candles)
        cross_up   = e9[-2] <= e20[-2] and e9[-1] > e20[-1]
        cross_down = e9[-2] >= e20[-2] and e9[-1] < e20[-1]

        above_vwap = price > vwap[-1]
        below_vwap = price < vwap[-1]

        # Pullback: preço tocou VWAP no candle anterior e voltou
        pullback = lows[-2] <= vwap[-2] <= highs[-2]

        if cross_up and above_vwap:
            msg = f"📗 VWAP+EMA [{interval}] LONG\n  EMA9 cruzou EMA20 p/ cima | Preço acima da VWAP"
            if pullback:
                msg += " | ✅ Pullback na VWAP"
            signals.append(msg)

        if cross_down and below_vwap:
            msg = f"📕 VWAP+EMA [{interval}] SHORT\n  EMA9 cruzou EMA20 p/ baixo | Preço abaixo da VWAP"
            signals.append(msg)

    except Exception as e:
        pass
    return signals

def detect_power_breakout(symbol, interval="1h", exchange="binance"):
    """Setup B Day Trade: Rompimento de consolidação + volume."""
    signals = []
    try:
        opens, highs, lows, closes, volumes = fetch_klines(symbol, interval, 50, exchange)
        s20  = sma(closes, 20)
        s200 = sma(closes, 200) if len(closes) >= 200 else None
        vol_ma = np.mean(volumes[-20:])
        price = closes[-1]
        vol_now = volumes[-1]

        # Consolidação: range estreito nas últimas 5 velas
        recent_range = (np.max(highs[-6:-1]) - np.min(lows[-6:-1])) / np.mean(closes[-6:-1])
        consolidating = recent_range < 0.025  # menos de 2.5% de range

        # Rompimento: fechou acima da máxima do range com volume acima da média
        range_high = np.max(highs[-6:-1])
        range_low  = np.min(lows[-6:-1])
        breakout_up   = closes[-1] > range_high and vol_now > vol_ma * 1.3
        breakout_down = closes[-1] < range_low  and vol_now > vol_ma * 1.3

        if consolidating and breakout_up:
            signals.append(
                f"💥 POWER BREAKOUT [{interval}] LONG\n"
                f"  Rompimento c/ volume {vol_now/vol_ma:.1f}x acima da média | Range {recent_range*100:.1f}%"
            )
        if consolidating and breakout_down:
            signals.append(
                f"💥 POWER BREAKOUT [{interval}] SHORT\n"
                f"  Rompimento p/ baixo c/ volume {vol_now/vol_ma:.1f}x | Range {recent_range*100:.1f}%"
            )
    except Exception:
        pass
    return signals

def detect_larry_williams_91(symbol, interval="4h", exchange="binance"):
    """Setup 9.1 Larry Williams: EMA9 muda de direção."""
    signals = []
    try:
        _, highs, lows, closes, _ = fetch_klines(symbol, interval, 50, exchange)
        e9 = ema(closes, 9)

        # EMA9 direção: compara os últimos 3 valores
        # Virou para cima: e9[-3] > e9[-2] (caindo) e e9[-1] > e9[-2] (subiu)
        turned_up   = e9[-3] > e9[-2] and e9[-1] > e9[-2]
        turned_down = e9[-3] < e9[-2] and e9[-1] < e9[-2]

        if turned_up:
            entry = highs[-2]   # rompimento da máxima do candle de virada
            sl    = lows[-2]
            signals.append(
                f"🔄 SETUP 9.1 [{interval}] LONG\n"
                f"  EMA9 virou p/ cima | Entrada no rompimento: ${entry:.4f} | SL: ${sl:.4f}"
            )
        if turned_down:
            entry = lows[-2]
            sl    = highs[-2]
            signals.append(
                f"🔄 SETUP 9.1 [{interval}] SHORT\n"
                f"  EMA9 virou p/ baixo | Entrada na perda: ${entry:.4f} | SL: ${sl:.4f}"
            )
    except Exception:
        pass
    return signals

def detect_golden_death_cross(symbol, interval="1d", exchange="binance"):
    """Golden Cross / Death Cross: SMA50 x SMA200 no diário."""
    signals = []
    try:
        _, _, _, closes, _ = fetch_klines(symbol, interval, 210, exchange)
        if len(closes) < 202:
            return signals
        s50  = sma(closes, 50)
        s200 = sma(closes, 200)
        rsi_val = rsi(closes)

        golden = s50[-2] <= s200[-2] and s50[-1] > s200[-1]
        death  = s50[-2] >= s200[-2] and s50[-1] < s200[-1]

        if golden and 50 < rsi_val < 70:
            signals.append(
                f"✨ GOLDEN CROSS [1d]\n"
                f"  SMA50 cruzou SMA200 p/ cima | RSI: {rsi_val:.1f} ✅"
            )
        elif golden:
            signals.append(
                f"✨ GOLDEN CROSS [1d]\n"
                f"  SMA50 cruzou SMA200 p/ cima | RSI: {rsi_val:.1f} ⚠️ (fora da zona ideal)"
            )
        if death:
            signals.append(
                f"💀 DEATH CROSS [1d]\n"
                f"  SMA50 cruzou SMA200 p/ baixo | RSI: {rsi_val:.1f}"
            )
    except Exception:
        pass
    return signals

def detect_extras(symbol, exchange="binance"):
    """RSI extremo, BB Squeeze e cruzamento EMA20/50 no 4h."""
    signals = []
    try:
        _, _, _, closes, _ = fetch_klines(symbol, "4h", 100, exchange)
        rsi_val = rsi(closes)
        bw = bollinger_bandwidth(closes)
        e20 = ema(closes, 20)
        e50 = ema(closes, 50)

        if rsi_val <= 25:
            signals.append(f"🟢 RSI EXTREMO [4h] Sobrevendido: {rsi_val:.1f} — potencial reversão de alta")
        elif rsi_val >= 80:
            signals.append(f"🔴 RSI EXTREMO [4h] Sobrecomprado: {rsi_val:.1f} — potencial reversão de baixa")

        if bw < 0.04:
            signals.append(f"🔵 BB SQUEEZE [4h] Compressão extrema ({bw*100:.1f}%) — grande movimento iminente")

        cross_up   = e20[-2] <= e50[-2] and e20[-1] > e50[-1]
        cross_down = e20[-2] >= e50[-2] and e20[-1] < e50[-1]
        if cross_up:
            signals.append(f"📈 EMA CROSS [4h] EMA20 cruzou EMA50 p/ cima")
        if cross_down:
            signals.append(f"📉 EMA CROSS [4h] EMA20 cruzou EMA50 p/ baixo")

    except Exception:
        pass
    return signals

# ─── Deduplicação de alertas ──────────────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def is_new_signal(state, asset, signal_key, ttl_hours=6):
    key = f"{asset}:{signal_key}"
    if key not in state:
        return True
    last = state[key]
    now  = datetime.now(timezone.utc).timestamp()
    return (now - last) > ttl_hours * 3600

def mark_signal(state, asset, signal_key):
    key = f"{asset}:{signal_key}"
    state[key] = datetime.now(timezone.utc).timestamp()

# ─── Send alert via OpenClaw CLI ─────────────────────────────────────────────

OPENCLAW_BIN = "/home/anderson/.npm-global/bin/openclaw"

def send_telegram(message):
    result = subprocess.run(
        [OPENCLAW_BIN, "message", "send", "--channel", "telegram", "--target", "127842708", "--message", message],
        capture_output=True, text=True
    )
    return result.returncode == 0

# ─── Main scanner ─────────────────────────────────────────────────────────────

def scan():
    state = load_state()
    all_alerts = []

    for asset in ASSETS:
        symbol = SYMBOL_MAP[asset]
        asset_alerts = []

        # Day Trade
        for tf in ["5m", "15m", "1h"]:
            sigs = detect_vwap_ema(symbol, tf)
            for sig in sigs:
                key = sig[:40].replace(" ", "_")
                if is_new_signal(state, asset, key):
                    asset_alerts.append(sig)
                    mark_signal(state, asset, key)

        for tf in ["15m", "1h"]:
            sigs = detect_power_breakout(symbol, tf)
            for sig in sigs:
                key = sig[:40].replace(" ", "_")
                if is_new_signal(state, asset, key):
                    asset_alerts.append(sig)
                    mark_signal(state, asset, key)

        # Swing Trade
        for tf in ["4h", "1d"]:
            sigs = detect_larry_williams_91(symbol, tf)
            for sig in sigs:
                key = sig[:40].replace(" ", "_")
                if is_new_signal(state, asset, key):
                    asset_alerts.append(sig)
                    mark_signal(state, asset, key)

        sigs = detect_golden_death_cross(symbol)
        for sig in sigs:
            key = sig[:40].replace(" ", "_")
            if is_new_signal(state, asset, key):
                asset_alerts.append(sig)
                mark_signal(state, asset, key)

        # Extras
        sigs = detect_extras(symbol)
        for sig in sigs:
            key = sig[:40].replace(" ", "_")
            if is_new_signal(state, asset, key, ttl_hours=4):
                asset_alerts.append(sig)
                mark_signal(state, asset, key)

        if asset_alerts:
            block = f"🔔 {asset}\n" + "\n".join(f"  {s}" for s in asset_alerts)
            all_alerts.append(block)

    # ── Ativos alternativos (MEXC, etc.) ──────────────────────────────────────
    for asset, (exch, _) in ALT_ASSETS.items():
        symbol = asset + "USDT"
        asset_alerts = []

        for tf in ["15m", "1h"]:
            sigs = detect_vwap_ema(symbol, tf, exch)
            for sig in sigs:
                key = sig[:40].replace(" ", "_")
                if is_new_signal(state, asset, key):
                    asset_alerts.append(sig)
                    mark_signal(state, asset, key)

        for tf in ["15m", "1h"]:
            sigs = detect_power_breakout(symbol, tf, exch)
            for sig in sigs:
                key = sig[:40].replace(" ", "_")
                if is_new_signal(state, asset, key):
                    asset_alerts.append(sig)
                    mark_signal(state, asset, key)

        for tf in ["4h", "1d"]:
            sigs = detect_larry_williams_91(symbol, tf, exch)
            for sig in sigs:
                key = sig[:40].replace(" ", "_")
                if is_new_signal(state, asset, key):
                    asset_alerts.append(sig)
                    mark_signal(state, asset, key)

        sigs = detect_golden_death_cross(symbol, "1d", exch)
        for sig in sigs:
            key = sig[:40].replace(" ", "_")
            if is_new_signal(state, asset, key):
                asset_alerts.append(sig)
                mark_signal(state, asset, key)

        sigs = detect_extras(symbol, exch)
        for sig in sigs:
            key = sig[:40].replace(" ", "_")
            if is_new_signal(state, asset, key, ttl_hours=4):
                asset_alerts.append(sig)
                mark_signal(state, asset, key)

        if asset_alerts:
            block = f"🔔 {asset} [{exch.upper()}]\n" + "\n".join(f"  {s}" for s in asset_alerts)
            all_alerts.append(block)

    save_state(state)

    if all_alerts:
        now_str = datetime.now().strftime("%d/%m %H:%M")
        msg = f"📡 Scanner Cripto — {now_str}\n{'━'*28}\n\n"
        msg += "\n\n".join(all_alerts)
        msg += "\n\n⚠️ Análise informativa. Não é recomendação financeira."
        send_telegram(msg)
        print(f"[{now_str}] {len(all_alerts)} alertas enviados.")
    else:
        now_str = datetime.now().strftime("%d/%m %H:%M")
        print(f"[{now_str}] Nenhum padrão novo detectado.")

if __name__ == "__main__":
    scan()
