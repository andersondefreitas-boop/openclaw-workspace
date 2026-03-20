#!/usr/bin/env python3
"""
Scanner Report — Relatório horário completo (Opção B)
Setups Day Trade: VWAP+EMA (5m/15m/1h) + Power Breakout (15m/1h)
Setups Swing:    Setup 9.1 Larry Williams (4h/1d) + Golden/Death Cross (1d)
Extras:          RSI extremo + BB Squeeze + EMA20/50 cross (4h)

Mensagens enviadas a cada hora:
  Msg 1 — Sinais ativos (com entrada, SL, alvo, R/R)
  Msg 2 — ⚡ Quase lá: ativos a 1 condição do sinal
  Msgs 3+ — Análise completa por ativo
"""

import requests
import numpy as np
import os
import subprocess
from datetime import datetime

ASSETS = [
    "BTC", "SOL", "XRP", "TAO", "VIRTUAL",
    "ADA", "RENDER", "NEAR", "AAVE", "LINK",
    "AVAX", "HBAR", "ZEC", "TON",
    "DOGE", "TRX", "POL", "LTC", "ICP",
    "XMR", "UNI", "VET", "SUI",
]
SYMBOL_MAP = {a: a + "USDT" for a in ASSETS}

# ─── Binance ──────────────────────────────────────────────────────────────────

def fetch_klines(symbol, interval, limit=250):
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

# ─── Indicadores ──────────────────────────────────────────────────────────────

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

def bollinger(closes, period=20):
    mid = np.mean(closes[-period:])
    std = np.std(closes[-period:])
    upper = mid + 2 * std
    lower = mid - 2 * std
    bw = (std * 4) / mid
    return mid, upper, lower, bw

def vwap_with_bands(opens, highs, lows, closes, volumes):
    typical = (highs + lows + closes) / 3
    cumvol  = np.cumsum(volumes)
    cumtpv  = np.cumsum(typical * volumes)
    vwap    = cumtpv / cumvol
    cumvar  = np.cumsum(((typical - vwap) ** 2) * volumes)
    std     = np.sqrt(cumvar / cumvol)
    return vwap, vwap + std, vwap + 2*std, vwap - std, vwap - 2*std

def fmt(price):
    if price >= 1000:   return f"${price:,.2f}"
    elif price >= 1:    return f"${price:.4f}"
    else:               return f"${price:.6f}"

def pct(a, b):
    return (b - a) / a * 100

# ─── Resultado padrão ─────────────────────────────────────────────────────────
# active: sinal disparado
# near:   falta exatamente 1 condição (True/False)
# near_label: texto curto do que falta
# signal: texto completo do sinal (se active)
# gaps:   lista de strings explicando o que falta

def empty_result(interval=None):
    return {"interval": interval, "active": False, "near": False,
            "near_label": "", "signal": None, "gaps": []}

# ─── Setup A: VWAP + EMA 9/20 ─────────────────────────────────────────────────

def analyze_vwap_ema(symbol, interval):
    opens, highs, lows, closes, volumes = fetch_klines(symbol, interval, 100)
    vwap, vp1, vp2, vm1, vm2 = vwap_with_bands(opens, highs, lows, closes, volumes)
    e9   = ema(closes, 9)
    e20  = ema(closes, 20)
    price = closes[-1]

    cross_up   = e9[-2] <= e20[-2] and e9[-1] > e20[-1]
    cross_down = e9[-2] >= e20[-2] and e9[-1] < e20[-1]
    above_vwap = price > vwap[-1]
    below_vwap = price < vwap[-1]
    pullback   = lows[-2] <= vwap[-2] <= highs[-2]

    r = empty_result(interval)

    if cross_up and above_vwap:
        sl     = min(lows[-1], vwap[-1])
        target = vp1[-1] if price < vp1[-1] else vp2[-1]
        rr     = (target - price) / (price - sl) if (price - sl) > 0 else 0
        pb_tag = " ✅ Pullback" if pullback else ""
        r["active"] = True
        r["signal"] = (
            f"📗 VWAP+EMA [{interval}] LONG{pb_tag}\n"
            f"  Entrada: {fmt(price)} | SL: {fmt(sl)} | Alvo: {fmt(target)}\n"
            f"  R/R: {rr:.1f}x | EMA9 {pct(e20[-1],e9[-1]):+.2f}% acima EMA20 | VWAP: {fmt(vwap[-1])}"
        )
    elif cross_down and below_vwap:
        sl     = max(highs[-1], vwap[-1])
        target = vm1[-1] if price > vm1[-1] else vm2[-1]
        rr     = (price - target) / (sl - price) if (sl - price) > 0 else 0
        r["active"] = True
        r["signal"] = (
            f"📕 VWAP+EMA [{interval}] SHORT\n"
            f"  Entrada: {fmt(price)} | SL: {fmt(sl)} | Alvo: {fmt(target)}\n"
            f"  R/R: {rr:.1f}x | EMA9 {pct(e20[-1],e9[-1]):+.2f}% abaixo EMA20 | VWAP: {fmt(vwap[-1])}"
        )
    else:
        ema_gap  = pct(e20[-1], e9[-1])
        vwap_d   = pct(vwap[-1], price)

        # Conta condições satisfeitas
        if ema_gap >= 0:  # LONG path
            ema_ok  = True
            vwap_ok = above_vwap
        else:             # SHORT path
            ema_ok  = True   # EMA9 já cruzou p/ baixo
            vwap_ok = below_vwap

        # near = cruzamento já ocorreu, só falta confirmação VWAP (ou vice-versa)
        # Aqui: EMA9/20 estão convergindo mas ainda não cruzaram → near se distância < 0.15%
        cross_imminent = abs(ema_gap) < 0.15

        if ema_gap >= 0:  # tendência LONG
            if cross_imminent and above_vwap:
                r["near"] = True
                r["near_label"] = f"VWAP+EMA [{interval}] LONG — EMA9 a {ema_gap:.3f}% de cruzar ↑, preço acima VWAP"
            elif not cross_imminent and above_vwap:
                r["near"] = True
                r["near_label"] = f"VWAP+EMA [{interval}] LONG — aguarda cruzamento EMA ({ema_gap:+.2f}%)"
            gap_vwap = pct(price, vwap[-1]) if not above_vwap else 0.0
            if above_vwap:
                r["gaps"].append(f"VWAP+EMA [{interval}] LONG: aguarda cruzamento EMA9↑EMA20 (gap {ema_gap:+.2f}%)")
            else:
                r["gaps"].append(f"VWAP+EMA [{interval}] LONG: preço precisa subir {abs(gap_vwap):.2f}% p/ acima da VWAP ({fmt(vwap[-1])})")
        else:             # tendência SHORT
            if cross_imminent and below_vwap:
                r["near"] = True
                r["near_label"] = f"VWAP+EMA [{interval}] SHORT — EMA9 a {abs(ema_gap):.3f}% de cruzar ↓, preço abaixo VWAP"
            elif not cross_imminent and below_vwap:
                r["near"] = True
                r["near_label"] = f"VWAP+EMA [{interval}] SHORT — aguarda cruzamento EMA ({ema_gap:.2f}%)"
            gap_vwap = pct(price, vwap[-1]) if above_vwap else 0.0
            if not above_vwap:
                r["gaps"].append(f"VWAP+EMA [{interval}] SHORT: aguarda cruzamento EMA9↓EMA20 (gap {ema_gap:.2f}%)")
            else:
                r["gaps"].append(f"VWAP+EMA [{interval}] SHORT: preço precisa cair {abs(gap_vwap):.2f}% p/ abaixo da VWAP ({fmt(vwap[-1])})")

    return r

# ─── Setup B: Power Breakout ──────────────────────────────────────────────────

def analyze_power_breakout(symbol, interval):
    opens, highs, lows, closes, volumes = fetch_klines(symbol, interval, 50)
    s20    = sma(closes, 20)
    vol_ma = np.mean(volumes[-20:])
    price  = closes[-1]
    vol_r  = volumes[-1] / vol_ma

    recent_high   = np.max(highs[-6:-1])
    recent_low    = np.min(lows[-6:-1])
    recent_mid    = np.mean(closes[-6:-1])
    range_pct     = (recent_high - recent_low) / recent_mid * 100
    consolidating = range_pct < 2.5
    sma20_dist    = abs(pct(s20[-1], price)) if not np.isnan(s20[-1]) else 999

    breakout_up   = price > recent_high and vol_r > 1.3
    breakout_down = price < recent_low  and vol_r > 1.3

    r = empty_result(interval)

    if consolidating and breakout_up:
        r["active"] = True
        r["signal"] = (
            f"💥 BREAKOUT [{interval}] LONG\n"
            f"  Entrada: {fmt(price)} | SL: {fmt(recent_low)} (base consolidação)\n"
            f"  Vol: {vol_r:.1f}x média | Range: {range_pct:.1f}% | SMA20: {fmt(s20[-1])}"
        )
    elif consolidating and breakout_down:
        r["active"] = True
        r["signal"] = (
            f"💥 BREAKOUT [{interval}] SHORT\n"
            f"  Entrada: {fmt(price)} | SL: {fmt(recent_high)} (topo consolidação)\n"
            f"  Vol: {vol_r:.1f}x média | Range: {range_pct:.1f}% | SMA20: {fmt(s20[-1])}"
        )
    else:
        up_dist   = pct(price, recent_high)
        down_dist = pct(price, recent_low)

        if consolidating:
            r["near"] = True
            r["near_label"] = (
                f"Breakout [{interval}] — consolidado {range_pct:.1f}% | "
                f"falta {up_dist:.2f}% ↑ ou {abs(down_dist):.2f}% ↓ + vol >{1.3:.1f}x (atual {vol_r:.2f}x)"
            )
            r["gaps"].append(
                f"Breakout [{interval}]: falta romper {fmt(recent_high)} (+{up_dist:.2f}%) ↑ "
                f"ou {fmt(recent_low)} (-{abs(down_dist):.2f}%) ↓ com vol >{1.3:.1f}x (atual {vol_r:.2f}x)"
            )
        else:
            r["gaps"].append(
                f"Breakout [{interval}]: range {range_pct:.1f}% ainda largo (precisa <2.5%) | "
                f"zona {fmt(recent_low)}–{fmt(recent_high)}"
            )

    return r

# ─── Setup 9.1 Larry Williams ─────────────────────────────────────────────────

def analyze_larry_91(symbol, interval):
    _, highs, lows, closes, _ = fetch_klines(symbol, interval, 60)
    e9 = ema(closes, 9)

    turned_up   = e9[-3] > e9[-2] and e9[-1] > e9[-2]
    turned_down = e9[-3] < e9[-2] and e9[-1] < e9[-2]

    slope     = e9[-1] - e9[-3]
    slope_pct = pct(e9[-3], e9[-1])
    direction = "↑ subindo" if slope > 0 else "↓ caindo"
    delta_up   = e9[-2] - e9[-1]
    delta_down = e9[-1] - e9[-2]

    # Próximo de virar: desaceleração (últimas 2 variações divergindo)
    decel_up   = slope > 0 and (e9[-1] - e9[-2]) < (e9[-2] - e9[-3])   # subindo mas desacelerando
    decel_down = slope < 0 and (e9[-2] - e9[-1]) < (e9[-3] - e9[-2])   # caindo mas desacelerando

    r = empty_result(interval)

    if turned_up:
        entry = highs[-2]
        sl    = lows[-2]
        dist  = pct(closes[-1], entry)
        r["active"] = True
        r["signal"] = (
            f"🔄 SETUP 9.1 [{interval}] LONG\n"
            f"  EMA9 virou ↑ | Entrada: {fmt(entry)} ({dist:+.2f}% do preço)\n"
            f"  SL: {fmt(sl)} | Risco: {abs(pct(entry,sl)):.2f}%"
        )
    elif turned_down:
        entry = lows[-2]
        sl    = highs[-2]
        dist  = pct(closes[-1], entry)
        r["active"] = True
        r["signal"] = (
            f"🔄 SETUP 9.1 [{interval}] SHORT\n"
            f"  EMA9 virou ↓ | Entrada: {fmt(entry)} ({dist:+.2f}% do preço)\n"
            f"  SL: {fmt(sl)} | Risco: {abs(pct(entry,sl)):.2f}%"
        )
    else:
        if decel_up:
            r["near"] = True
            r["near_label"] = f"Setup 9.1 [{interval}] SHORT — EMA9 {direction} desacelerando ({slope_pct:+.3f}%) → virada iminente"
        elif decel_down:
            r["near"] = True
            r["near_label"] = f"Setup 9.1 [{interval}] LONG — EMA9 {direction} desacelerando ({slope_pct:+.3f}%) → virada iminente"

        if slope > 0:
            r["gaps"].append(
                f"Setup 9.1 [{interval}]: EMA9 {direction} | p/ virar ↓ precisa recuar "
                f"~{abs(delta_down/e9[-1]*100):.3f}% nos próx 2 candles (atual {fmt(e9[-1])})"
            )
        else:
            r["gaps"].append(
                f"Setup 9.1 [{interval}]: EMA9 {direction} | p/ virar ↑ precisa avançar "
                f"~{abs(delta_up/e9[-1]*100):.3f}% nos próx 2 candles (atual {fmt(e9[-1])})"
            )

    return r

# ─── Golden / Death Cross ─────────────────────────────────────────────────────

def analyze_golden_cross(symbol):
    _, _, _, closes, _ = fetch_klines(symbol, "1d", 210)
    r = empty_result("1d")

    if len(closes) < 202:
        r["gaps"].append("Golden Cross: dados insuficientes")
        return r

    s50     = sma(closes, 50)
    s200    = sma(closes, 200)
    rsi_val = rsi(closes)
    gap_pct = pct(s200[-1], s50[-1])

    golden = s50[-2] <= s200[-2] and s50[-1] > s200[-1]
    death  = s50[-2] >= s200[-2] and s50[-1] < s200[-1]

    if golden:
        rsi_ok  = 50 < rsi_val < 70
        rsi_tag = "✅ RSI OK" if rsi_ok else f"⚠️ RSI {rsi_val:.1f} (ideal 50–70)"
        r["active"] = True
        r["signal"] = (
            f"✨ GOLDEN CROSS [1d]\n"
            f"  SMA50 {fmt(s50[-1])} cruzou SMA200 {fmt(s200[-1])} ↑\n"
            f"  RSI: {rsi_val:.1f} {rsi_tag}"
        )
    elif death:
        r["active"] = True
        r["signal"] = (
            f"💀 DEATH CROSS [1d]\n"
            f"  SMA50 {fmt(s50[-1])} cruzou SMA200 {fmt(s200[-1])} ↓\n"
            f"  RSI: {rsi_val:.1f}"
        )
    else:
        if gap_pct < 0:
            if abs(gap_pct) < 2.0:
                r["near"] = True
                r["near_label"] = f"Golden Cross [1d] — SMA50 a apenas {abs(gap_pct):.2f}% da SMA200 ↑ | RSI {rsi_val:.1f}"
            r["gaps"].append(
                f"Golden Cross [1d]: SMA50 precisa subir {abs(gap_pct):.2f}% p/ cruzar SMA200 ({fmt(s200[-1])}) | RSI {rsi_val:.1f} (ideal 50–70)"
            )
        else:
            if gap_pct < 2.0:
                r["near"] = True
                r["near_label"] = f"Death Cross [1d] — SMA50 a apenas {gap_pct:.2f}% da SMA200 ↓ | RSI {rsi_val:.1f}"
            r["gaps"].append(
                f"Death Cross [1d]: SMA50 precisa cair {gap_pct:.2f}% p/ cruzar SMA200 ({fmt(s200[-1])}) | RSI {rsi_val:.1f}"
            )

    return r

# ─── Extras ───────────────────────────────────────────────────────────────────

def analyze_extras(symbol):
    _, _, _, closes, _ = fetch_klines(symbol, "4h", 100)
    rsi_val = rsi(closes)
    mid_bb, upper_bb, lower_bb, bw = bollinger(closes)
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)

    signals = []
    gaps    = []
    near    = []  # (near_label)

    # RSI
    if rsi_val <= 25:
        signals.append(f"🟢 RSI SOBREVENDIDO [4h] {rsi_val:.1f} ← reversão potencial ↑")
    elif rsi_val >= 80:
        signals.append(f"🔴 RSI SOBRECOMPRADO [4h] {rsi_val:.1f} ← reversão potencial ↓")
    else:
        to_os = rsi_val - 25
        to_ob = 80 - rsi_val
        if to_os <= 5:
            near.append(f"RSI [4h] — {rsi_val:.1f}, falta {to_os:.1f}pts p/ sobrevendido ≤25")
        elif to_ob <= 5:
            near.append(f"RSI [4h] — {rsi_val:.1f}, falta {to_ob:.1f}pts p/ sobrecomprado ≥80")
        # Mostra apenas a condição mais próxima
        if to_os <= to_ob:
            gaps.append(f"RSI [4h]: {rsi_val:.1f} — falta {to_os:.1f}pts p/ sobrevendido (≤25)")
        else:
            gaps.append(f"RSI [4h]: {rsi_val:.1f} — falta {to_ob:.1f}pts p/ sobrecomprado (≥80)")

    # BB Squeeze
    if bw < 0.04:
        signals.append(f"🔵 BB SQUEEZE [4h] {bw*100:.2f}% ← grande movimento iminente")
    else:
        if bw < 0.06:
            near.append(f"BB Squeeze [4h] — largura {bw*100:.2f}% (precisa <4%, quase lá)")
        gaps.append(f"BB Squeeze [4h]: precisa fechar em {bw*100 - 4:.2f}% a mais (atual {bw*100:.2f}%, alvo <4%)")

    # EMA20/50
    cross_up   = e20[-2] <= e50[-2] and e20[-1] > e50[-1]
    cross_down = e20[-2] >= e50[-2] and e20[-1] < e50[-1]
    ema_gap    = pct(e50[-1], e20[-1])

    if cross_up:
        signals.append(f"📈 EMA CROSS [4h] EMA20 cruzou EMA50 ↑ | gap {ema_gap:+.2f}%")
    elif cross_down:
        signals.append(f"📉 EMA CROSS [4h] EMA20 cruzou EMA50 ↓ | gap {ema_gap:+.2f}%")
    else:
        if abs(ema_gap) < 0.3:
            near.append(f"EMA Cross [4h] — EMA20 a {abs(ema_gap):.3f}% da EMA50, cruzamento iminente")
        if ema_gap < 0:
            gaps.append(f"EMA Cross [4h]: EMA20 precisa subir {abs(ema_gap):.2f}% p/ cruzar EMA50 ({fmt(e50[-1])})")
        else:
            gaps.append(f"EMA Cross [4h]: EMA20 precisa cair {ema_gap:.2f}% p/ cruzar EMA50 ({fmt(e50[-1])})")

    return signals, gaps, near

# ─── Análise completa por ativo ───────────────────────────────────────────────

def analyze_asset(asset):
    symbol  = SYMBOL_MAP[asset]
    signals = []
    gaps    = []
    near    = []   # lista de strings descrevendo o que falta (1 condição)

    for tf in ["5m", "15m", "1h"]:
        try:
            r = analyze_vwap_ema(symbol, tf)
            if r["active"]:
                signals.append(r["signal"])
            else:
                gaps.extend(r["gaps"])
                if r["near"]:
                    near.append(r["near_label"])
        except Exception:
            pass

    for tf in ["15m", "1h"]:
        try:
            r = analyze_power_breakout(symbol, tf)
            if r["active"]:
                signals.append(r["signal"])
            else:
                gaps.extend(r["gaps"])
                if r["near"]:
                    near.append(r["near_label"])
        except Exception:
            pass

    for tf in ["4h", "1d"]:
        try:
            r = analyze_larry_91(symbol, tf)
            if r["active"]:
                signals.append(r["signal"])
            else:
                gaps.extend(r["gaps"])
                if r["near"]:
                    near.append(r["near_label"])
        except Exception:
            pass

    try:
        r = analyze_golden_cross(symbol)
        if r["active"]:
            signals.append(r["signal"])
        else:
            gaps.extend(r["gaps"])
            if r["near"]:
                near.append(r["near_label"])
    except Exception:
        pass

    try:
        extra_sigs, extra_gaps, extra_near = analyze_extras(symbol)
        signals.extend(extra_sigs)
        gaps.extend(extra_gaps)
        near.extend(extra_near)
    except Exception:
        pass

    return signals, gaps, near

# ─── Send ─────────────────────────────────────────────────────────────────────

OPENCLAW_BIN = "/home/anderson/.npm-global/bin/openclaw"

def send_telegram(message):
    subprocess.run(
        [OPENCLAW_BIN, "message", "send", "--channel", "telegram",
         "--target", "127842708", "--message", message],
        capture_output=True, text=True
    )

# ─── Build report ─────────────────────────────────────────────────────────────

def build_report():
    now_str = datetime.now().strftime("%d/%m %H:%M")
    results = {}
    for asset in ASSETS:
        try:
            signals, gaps, near = analyze_asset(asset)
        except Exception as e:
            signals, gaps, near = [], [f"Erro: {e}"], []
        results[asset] = (signals, gaps, near)

    with_signals  = [(a, s, g, n) for a, (s, g, n) in results.items() if s]
    near_only     = [(a, s, g, n) for a, (s, g, n) in results.items() if not s and n]
    without       = [(a, s, g, n) for a, (s, g, n) in results.items() if not s and not n]

    messages = []

    # ── Msg 1: Sinais Ativos ──────────────────────────────────────────────────
    header = f"📡 Scanner Cripto — {now_str}\n{'━'*28}\n"
    if with_signals:
        body = f"🔔 {len(with_signals)} ATIVO(S) COM SINAL\n\n"
        for asset, sigs, _, _ in with_signals:
            body += f"● {asset}\n"
            for s in sigs:
                body += f"  {s}\n"
            body += "\n"
    else:
        body = "ℹ️ Nenhum sinal ativo no momento.\n"
    messages.append((header + body).strip())

    # ── Msg 2: Quase Lá (1 condição faltando) ────────────────────────────────
    if near_only or any(n for _, _, _, n in with_signals):
        body = f"⚡ QUASE LÁ — {now_str}\n{'━'*28}\n"
        body += "Ativos a 1 condição do sinal:\n\n"
        for asset, _, _, near_list in (with_signals + near_only):
            if near_list:
                body += f"🔸 {asset}\n"
                for nl in near_list:
                    body += f"  • {nl}\n"
                body += "\n"
        body = body.strip()
        body += "\n\n⚠️ Informativo. Não é recomendação financeira."
        messages.append(body)

    # ── Msgs 3+: Análise completa ─────────────────────────────────────────────
    MAX_CHARS = 3900

    def asset_block(asset, sigs, gaps_list, near_list):
        lines = []
        if sigs:
            lines.append(f"✅ {asset} — {len(sigs)} sinal(is) ativo(s)")
        elif near_list:
            lines.append(f"⚡ {asset} — quase lá")
        else:
            lines.append(f"⏳ {asset}")
        for g in gaps_list:
            if "+1σ" in g:   # linha de desvios VWAP — omitir
                continue
            lines.append(f"  • {g}")
        return "\n".join(lines) + "\n"

    all_ordered = with_signals + near_only + without
    blocks = [asset_block(a, s, g, n) for a, s, g, n in all_ordered]

    current     = ""
    chunk_parts = []
    for block in blocks:
        if len(current) + len(block) > MAX_CHARS:
            if current:
                chunk_parts.append(current.strip())
            current = block
        else:
            current += block
    if current.strip():
        chunk_parts.append(current.strip())

    total = len(chunk_parts)
    for idx, part in enumerate(chunk_parts):
        hdr = f"📊 Análise Completa ({idx+1}/{total}) — {now_str}\n{'━'*28}\n\n"
        ftr = "\n\n⚠️ Informativo. Não é recomendação financeira."
        messages.append(hdr + part + ftr)

    return messages

# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Coletando dados...", flush=True)
    msgs = build_report()
    for i, msg in enumerate(msgs):
        preview = msg[:120].replace('\n', ' ')
        print(f"[{i+1}/{len(msgs)}] {preview}...", flush=True)
        send_telegram(msg)
    print(f"\nTotal: {len(msgs)} mensagem(ns) enviada(s).")
