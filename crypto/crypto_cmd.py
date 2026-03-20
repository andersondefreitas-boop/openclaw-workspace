#!/usr/bin/env python3
"""
Crypto Commands — Day Trade + Swing Trade separados
Uso: python3 crypto_cmd.py scan
     python3 crypto_cmd.py ativo BTC
     python3 crypto_cmd.py top
     python3 crypto_cmd.py status
     python3 crypto_cmd.py ajuda
"""

import os, sys, json, time, logging
from datetime import datetime, timezone
from typing import Optional
import subprocess

import requests
import pandas as pd
import numpy as np
import ccxt

# ── Config ────────────────────────────────────────────────────
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
TELEGRAM_TARGET    = "127842708"
OPENCLAW_BIN       = "/home/anderson/.npm-global/bin/openclaw"
CACHE_FILE         = os.path.join(os.path.dirname(__file__), "crypto_cache.json")

ASSETS = [
    "BTC/USDT",  "SOL/USDT",  "XRP/USDT",  "TAO/USDT",  "VIRTUAL/USDT",
    "ADA/USDT",  "RENDER/USDT","NEAR/USDT", "AAVE/USDT", "LINK/USDT",
    "AVAX/USDT", "HBAR/USDT", "ZEC/USDT",  "TON/USDT",  "DOGE/USDT",
    "TRX/USDT",  "POL/USDT",  "LTC/USDT",  "ICP/USDT",  "XMR/USDT",
    "UNI/USDT",  "VET/USDT",  "SUI/USDT",
]

# Day Trade
DAY_ALERT     = 2      # score mínimo p/ alerta
DAY_WATCH     = 1      # score mínimo p/ monitorar
ATR_MULT_DAY  = 1.5
VOLUME_MIN    = 0.70
VOLUME_SPIKE  = 1.50
BB_SQUEEZE_BW = 0.03   # bandwidth < 3%

# Swing Trade
SWING_ALERT   = 2      # score mínimo p/ alerta (max 3)
SWING_WATCH   = 1
ATR_MULT_SWING= 2.0
GC_NEAR_PCT   = 5.0    # Golden Cross: pontua 0.5 se SMA50 a ≤5% da SMA200

logging.basicConfig(level=logging.WARNING)

# ── Exchange ──────────────────────────────────────────────────
EXCHANGE = ccxt.binance({
    "apiKey": BINANCE_API_KEY,
    "secret": BINANCE_API_SECRET,
    "enableRateLimit": True,
    "options": {"defaultType": "spot"},
})

def fetch_ohlcv(symbol, timeframe, limit=250):
    try:
        raw = EXCHANGE.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df  = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df.set_index("timestamp").astype(float)
    except Exception:
        return None

# ── Indicadores ───────────────────────────────────────────────
def ema(s, n):    return s.ewm(span=n, adjust=False).mean()
def sma(s, n):    return s.rolling(n).mean()

def rsi_calc(s, n=14):
    d    = s.diff()
    gain = d.clip(lower=0).rolling(n).mean()
    loss = (-d.clip(upper=0)).rolling(n).mean()
    rs   = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def atr_calc(df, n=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(n).mean()

def bollinger_calc(df, n=20, mult=2.0):
    mid   = sma(df["close"], n)
    sigma = df["close"].rolling(n).std()
    upper = mid + mult * sigma
    lower = mid - mult * sigma
    width = (upper - lower) / mid
    return upper, mid, lower, width

def vwap_with_std(df):
    d         = df.copy()
    d["date"] = d.index.normalize()
    d["tp"]   = (d["high"] + d["low"] + d["close"]) / 3
    d["tpv"]  = d["tp"] * d["volume"]
    d["ctpv"] = d.groupby("date")["tpv"].cumsum()
    d["cvol"] = d.groupby("date")["volume"].cumsum()
    vwap      = d["ctpv"] / d["cvol"]
    cumvar    = d.groupby("date").apply(
        lambda g: ((g["tp"] - (g["tpv"].cumsum() / g["volume"].cumsum())) ** 2 * g["volume"]).cumsum()
    ).reset_index(level=0, drop=True)
    std = np.sqrt(cumvar / d["cvol"])
    return vwap, vwap + std, vwap + 2*std, vwap - std

def vol_ok(df, ratio=VOLUME_MIN):
    avg  = df["volume"].rolling(20).mean().iloc[-1]
    last = df["volume"].iloc[-1]
    return bool(avg > 0 and last >= ratio * avg)

def fmt_price(p):
    if p is None: return "?"
    if p >= 1000: return f"${p:,.2f}"
    if p >= 1:    return f"${p:.4f}"
    return f"${p:.6f}"

def calc_rr(entry, stop, target):
    risk   = abs(entry - stop)
    reward = abs(target - entry)
    if risk == 0: return 0
    return round(reward / risk, 1)

# ══════════════════════════════════════════════════════════════
# DAY TRADE — Setups 1h
# ══════════════════════════════════════════════════════════════

def day_vwap_ema(df):
    """VWAP + EMA9/20 no 1h. Score 1."""
    r = {"active": False, "near": False, "score": 0, "detail": "—", "stop": None, "target": None}
    if df is None or len(df) < 30: return r
    try:
        e9  = ema(df["close"], 9)
        e20 = ema(df["close"], 20)
        vwap, vp1, vp2, vm1 = vwap_with_std(df)
        rsi_ = rsi_calc(df["close"]).iloc[-1]
        atr_ = atr_calc(df).iloc[-1]
        price = df["close"].iloc[-1]

        above_vwap = price > vwap.iloc[-1]
        cross_up   = e9.iloc[-1] > e20.iloc[-1] and e9.iloc[-2] <= e20.iloc[-2]
        rsi_ok     = 40 <= rsi_ <= 70
        vol        = vol_ok(df)

        conds = [above_vwap, cross_up, rsi_ok, vol]
        ok    = sum(conds)

        if all(conds):
            r["active"] = True
            r["score"]  = 1
            r["stop"]   = round(vwap.iloc[-1] - ATR_MULT_DAY * atr_, 6)
            r["target"] = round(vp1.iloc[-1] if price < vp1.iloc[-1] else vp2.iloc[-1], 6)
            r["detail"] = f"VWAP ✅ | EMA× ✅ | RSI {rsi_:.0f} ✅ | Vol ✅"
        elif ok >= 3:
            r["near"]   = True
            r["score"]  = 0.5
            missing = []
            if not above_vwap: missing.append(f"preço {((price/vwap.iloc[-1]-1)*100):+.2f}% VWAP")
            if not cross_up:   missing.append("cruzamento EMA")
            if not rsi_ok:     missing.append(f"RSI {rsi_:.0f} fora 40–70")
            if not vol:        missing.append("volume baixo")
            r["detail"] = f"Falta: {' | '.join(missing)}"
        else:
            parts = []
            if above_vwap: parts.append("VWAP ✅")
            else: parts.append(f"VWAP ❌ ({((price/vwap.iloc[-1]-1)*100):+.2f}%)")
            if cross_up: parts.append("EMA× ✅")
            else: parts.append("EMA× ❌")
            parts.append(f"RSI {rsi_:.0f}")
            r["detail"] = " | ".join(parts)
    except Exception:
        pass
    return r

def day_bb_squeeze(df):
    """BB Squeeze + rompimento no 1h. Score 1."""
    r = {"active": False, "near": False, "score": 0, "detail": "—", "stop": None, "target": None}
    if df is None or len(df) < 30: return r
    try:
        upper, mid, lower, width = bollinger_calc(df)
        atr_  = atr_calc(df).iloc[-1]
        price = df["close"].iloc[-1]

        squeeze  = width.iloc[-1] < BB_SQUEEZE_BW
        breakout = price > upper.iloc[-1]
        spike    = vol_ok(df, VOLUME_SPIKE)
        bw_pct   = round(width.iloc[-1] * 100, 2)

        if squeeze and breakout and spike:
            r["active"] = True
            r["score"]  = 1
            r["stop"]   = round(lower.iloc[-1] - ATR_MULT_DAY * atr_, 6)
            r["target"] = round(price + (price - lower.iloc[-1]), 6)
            r["detail"] = f"Squeeze {bw_pct}% ✅ | Break ✅ | Spike ✅"
        elif squeeze and (breakout or spike):
            r["near"]   = True
            r["score"]  = 0.5
            missing = []
            if not breakout: missing.append("rompimento banda sup")
            if not spike:    missing.append(f"spike vol ({df['volume'].iloc[-1]/df['volume'].rolling(20).mean().iloc[-1]:.1f}x)")
            r["detail"] = f"Squeeze {bw_pct}% ✅ | Falta: {' + '.join(missing)}"
        else:
            r["detail"] = f"BB {bw_pct}% {'✅' if squeeze else '❌'} | Break {'✅' if breakout else '❌'} | Spike {'✅' if spike else '❌'}"
    except Exception:
        pass
    return r

def score_day(symbol, df_1h, df_4h):
    """Score day trade 0–2."""
    hf_ok = False
    try:
        if df_4h is not None and len(df_4h) >= 22:
            hf_ok = bool(df_4h["close"].iloc[-1] > ema(df_4h["close"], 20).iloc[-1])
    except Exception:
        pass

    vw = day_vwap_ema(df_1h)
    bb = day_bb_squeeze(df_1h)

    # Filtro HF: desativa (não zera — apenas penaliza) setups se HF contrário
    if not hf_ok:
        if vw["active"]: vw["active"] = False; vw["score"] = 0.5; vw["near"] = True
        if bb["active"]: bb["active"] = False; bb["score"] = 0.5; bb["near"] = True

    score = round(vw["score"] + bb["score"], 1)
    price = df_1h["close"].iloc[-1] if df_1h is not None else None

    stops   = [s for s in [vw.get("stop"), bb.get("stop")] if s]
    targets = [t for t in [vw.get("target"), bb.get("target")] if t]
    stop    = round(min(stops), 6)   if stops   else None
    target  = round(max(targets), 6) if targets else None
    rr      = calc_rr(price, stop, target) if (price and stop and target) else None

    return {
        "type": "day", "symbol": symbol, "score": score,
        "price": round(price, 6) if price else None,
        "hf": hf_ok, "vw": vw, "bb": bb,
        "stop": stop, "target": target, "rr": rr,
        "ts": datetime.now(timezone.utc).strftime("%d/%m %H:%M UTC"),
    }

# ══════════════════════════════════════════════════════════════
# SWING TRADE — Setups 4h + Diário
# ══════════════════════════════════════════════════════════════

def swing_golden_cross(df_1d):
    """Golden Cross SMA50/200. Score 0–1 (0.5 se near)."""
    r = {"active": False, "near": False, "score": 0, "detail": "—"}
    if df_1d is None or len(df_1d) < 201: return r
    try:
        s50  = sma(df_1d["close"], 50).iloc[-1]
        s200 = sma(df_1d["close"], 200).iloc[-1]
        rsi_ = rsi_calc(df_1d["close"]).iloc[-1]
        gap  = (s50 - s200) / s200 * 100

        if s50 > s200:
            r["active"] = True
            r["score"]  = 1
            r["detail"] = f"SMA50 > SMA200 (+{gap:.1f}%) | RSI {rsi_:.0f} {'✅' if 50<=rsi_<=70 else '⚠️'}"
        elif gap >= -GC_NEAR_PCT:
            r["near"]  = True
            r["score"] = 0.5
            r["detail"] = f"SMA50 a {abs(gap):.1f}% da SMA200 ⚠️ | RSI {rsi_:.0f}"
        else:
            r["detail"] = f"SMA50 < SMA200 ({gap:.1f}%) | RSI {rsi_:.0f}"
    except Exception:
        pass
    return r

def swing_setup_91(df_4h):
    """Setup 9.1 Larry Williams no 4h. Score 1."""
    r = {"active": False, "near": False, "score": 0, "detail": "—", "stop": None, "target": None}
    if df_4h is None or len(df_4h) < 25: return r
    try:
        e9   = ema(df_4h["close"], 9)
        rsi_ = rsi_calc(df_4h["close"]).iloc[-1]
        atr_ = atr_calc(df_4h).iloc[-1]
        vol  = vol_ok(df_4h)

        turned_up   = e9.iloc[-1] > e9.iloc[-2] and e9.iloc[-2] <= e9.iloc[-3]
        decel_down  = (e9.iloc[-2] - e9.iloc[-1]) < (e9.iloc[-3] - e9.iloc[-2]) and e9.iloc[-1] < e9.iloc[-2]

        if turned_up and rsi_ > 50 and vol:
            entry = df_4h["high"].iloc[-2]
            sl    = df_4h["low"].iloc[-2] - ATR_MULT_SWING * atr_
            tgt   = entry + 3 * atr_
            r["active"] = True
            r["score"]  = 1
            r["stop"]   = round(sl, 6)
            r["target"] = round(tgt, 6)
            r["detail"] = f"EMA9 virou ↑ | RSI {rsi_:.0f} ✅ | Vol ✅ | Entrada {fmt_price(entry)}"
        elif turned_up or decel_down:
            r["near"]   = True
            r["score"]  = 0.5
            r["detail"] = f"EMA9 {'virou ↑' if turned_up else 'desacelerando ↓'} | RSI {rsi_:.0f} {'✅' if rsi_>50 else '❌'} | Vol {'✅' if vol else '❌'}"
        else:
            slope = (e9.iloc[-1] - e9.iloc[-3]) / e9.iloc[-3] * 100
            r["detail"] = f"EMA9 {'↑' if slope>0 else '↓'} {abs(slope):.3f}% | RSI {rsi_:.0f}"
    except Exception:
        pass
    return r

def swing_hf_filter(df_4h):
    """HF: 4h acima da EMA20. Score 1 como condição de força."""
    r = {"active": False, "near": False, "score": 0, "detail": "—"}
    if df_4h is None or len(df_4h) < 22: return r
    try:
        e20   = ema(df_4h["close"], 20)
        price = df_4h["close"].iloc[-1]
        dist  = (price - e20.iloc[-1]) / e20.iloc[-1] * 100

        if price > e20.iloc[-1]:
            r["active"] = True
            r["score"]  = 1
            r["detail"] = f"4h > EMA20 (+{dist:.1f}%)"
        elif dist >= -2.0:
            r["near"]  = True
            r["score"] = 0.5
            r["detail"] = f"4h a {abs(dist):.1f}% da EMA20 ⚠️"
        else:
            r["detail"] = f"4h < EMA20 ({dist:.1f}%)"
    except Exception:
        pass
    return r

def score_swing(symbol, df_4h, df_1d):
    """Score swing trade 0–3."""
    gc = swing_golden_cross(df_1d)
    s9 = swing_setup_91(df_4h)
    hf = swing_hf_filter(df_4h)

    score = round(gc["score"] + s9["score"] + hf["score"], 1)
    price = df_4h["close"].iloc[-1] if df_4h is not None else None

    stops   = [s for s in [s9.get("stop")] if s]
    targets = [t for t in [s9.get("target")] if t]
    stop    = round(min(stops), 6)   if stops   else None
    target  = round(max(targets), 6) if targets else None
    rr      = calc_rr(price, stop, target) if (price and stop and target) else None

    return {
        "type": "swing", "symbol": symbol, "score": score,
        "price": round(price, 6) if price else None,
        "gc": gc, "s9": s9, "hf": hf,
        "stop": stop, "target": target, "rr": rr,
        "ts": datetime.now(timezone.utc).strftime("%d/%m %H:%M UTC"),
    }

# ══════════════════════════════════════════════════════════════
# ANÁLISE COMPLETA POR ATIVO
# ══════════════════════════════════════════════════════════════

def analyze_asset(symbol):
    df_1h = fetch_ohlcv(symbol, "1h",  100)
    df_4h = fetch_ohlcv(symbol, "4h",  120)
    df_1d = fetch_ohlcv(symbol, "1d",  250)
    day   = score_day(symbol, df_1h, df_4h)
    swing = score_swing(symbol, df_4h, df_1d)
    return day, swing

# ══════════════════════════════════════════════════════════════
# FORMATAÇÃO
# ══════════════════════════════════════════════════════════════

def bar(score, max_score):
    filled = int(score)
    half   = 1 if (score - filled) >= 0.5 else 0
    empty  = max_score - filled - half
    return "█" * filled + ("▒" if half else "") + "░" * empty

def fmt_day(r):
    sym   = r["symbol"].replace("/USDT","")
    sc    = r["score"]
    b     = bar(sc, 2)
    hf    = "✅" if r["hf"] else "⚠️ HF contra"

    if sc >= DAY_ALERT:   head = f"⚡ {sym} — DAY [{b}] {sc}/2 — ENTRADA"
    elif sc >= DAY_WATCH: head = f"👁 {sym} — DAY [{b}] {sc}/2 — MONITORAR"
    else:                 head = f"📊 {sym} — DAY [{b}] {sc}/2"

    rr_str = f" | R/R 1:{r['rr']}" if r.get("rr") else ""

    lines = [
        head, "",
        f"💰 {fmt_price(r['price'])} | 🔴 Stop {fmt_price(r['stop'])} (ATR {ATR_MULT_DAY}×){rr_str}",
        f"🎯 Alvo: {fmt_price(r['target'])}",
        f"📡 Filtro HF (4h>EMA20): {hf}",
        "",
        f"  💹 VWAP+EMA : {'✅' if r['vw']['active'] else '⚠️' if r['vw']['near'] else '❌'} {r['vw']['detail']}",
        f"  🎯 BB Squeeze: {'✅' if r['bb']['active'] else '⚠️' if r['bb']['near'] else '❌'} {r['bb']['detail']}",
        "",
        f"⏰ {r['ts']}",
    ]
    return "\n".join(lines)

def fmt_swing(r):
    sym = r["symbol"].replace("/USDT","")
    sc  = r["score"]
    b   = bar(sc, 3)

    if sc >= SWING_ALERT:   head = f"📈 {sym} — SWING [{b}] {sc}/3 — ENTRADA"
    elif sc >= SWING_WATCH: head = f"👁 {sym} — SWING [{b}] {sc}/3 — MONITORAR"
    else:                   head = f"📊 {sym} — SWING [{b}] {sc}/3"

    rr_str = f" | R/R 1:{r['rr']}" if r.get("rr") else ""

    lines = [
        head, "",
        f"💰 {fmt_price(r['price'])} | 🔴 Stop {fmt_price(r['stop'])} (ATR {ATR_MULT_SWING}×){rr_str}",
        f"🎯 Alvo: {fmt_price(r['target'])}",
        "",
        f"  🌍 Golden Cross (D) : {'✅' if r['gc']['active'] else '⚠️' if r['gc']['near'] else '❌'} {r['gc']['detail']}",
        f"  🔄 Setup 9.1   (4h): {'✅' if r['s9']['active'] else '⚠️' if r['s9']['near'] else '❌'} {r['s9']['detail']}",
        f"  📡 HF EMA20    (4h): {'✅' if r['hf']['active'] else '⚠️' if r['hf']['near'] else '❌'} {r['hf']['detail']}",
        "",
        f"⏰ {r['ts']}",
    ]
    return "\n".join(lines)

def fmt_ativo_completo(day, swing):
    sym = day["symbol"].replace("/USDT","")
    return f"{'─'*28}\n{fmt_day(day)}\n\n{fmt_swing(swing)}"

def fmt_summary(cache):
    days   = [c["day"]   for c in cache]
    swings = [c["swing"] for c in cache]
    ts     = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    day_alerts   = [r for r in sorted(days,   key=lambda x:-x["score"]) if r["score"] >= DAY_ALERT]
    day_watch    = [r for r in sorted(days,   key=lambda x:-x["score"]) if DAY_WATCH <= r["score"] < DAY_ALERT]
    swing_alerts = [r for r in sorted(swings, key=lambda x:-x["score"]) if r["score"] >= SWING_ALERT]
    swing_watch  = [r for r in sorted(swings, key=lambda x:-x["score"]) if SWING_WATCH <= r["score"] < SWING_ALERT]

    lines = [f"📋 VARREDURA — {ts}", f"{len(days)} ativos | Day 0–2 | Swing 0–3", ""]

    if swing_alerts:
        lines.append("📈 SWING — ENTRADA:")
        for r in swing_alerts:
            sym = r["symbol"].replace("/USDT","")
            b   = bar(r["score"], 3)
            rr  = f" R/R 1:{r['rr']}" if r.get("rr") else ""
            lines.append(f"  {sym} [{b}] {r['score']}/3 💰{fmt_price(r['price'])} 🔴{fmt_price(r['stop'])}{rr}")
        lines.append("")

    if day_alerts:
        lines.append("⚡ DAY — ENTRADA:")
        for r in day_alerts:
            sym = r["symbol"].replace("/USDT","")
            b   = bar(r["score"], 2)
            rr  = f" R/R 1:{r['rr']}" if r.get("rr") else ""
            lines.append(f"  {sym} [{b}] {r['score']}/2 💰{fmt_price(r['price'])} 🔴{fmt_price(r['stop'])}{rr}")
        lines.append("")

    if swing_watch:
        lines.append("👁 SWING — MONITORAR:")
        for r in swing_watch:
            sym = r["symbol"].replace("/USDT","")
            b   = bar(r["score"], 3)
            lines.append(f"  {sym} [{b}] {r['score']}/3")
        lines.append("")

    if day_watch:
        lines.append("👁 DAY — MONITORAR:")
        for r in day_watch:
            sym = r["symbol"].replace("/USDT","")
            b   = bar(r["score"], 2)
            lines.append(f"  {sym} [{b}] {r['score']}/2")
        lines.append("")

    lines.append("Ranking completo (Swing | Day):")
    all_syms = sorted(set(r["symbol"] for r in days))
    day_map   = {r["symbol"]: r for r in days}
    swing_map = {r["symbol"]: r for r in swings}
    for sym in sorted(all_syms, key=lambda s: -(swing_map[s]["score"] + day_map[s]["score"])):
        name = sym.replace("/USDT","").ljust(9)
        dr   = day_map[sym];   db = bar(dr["score"], 2)
        sr   = swing_map[sym]; sb = bar(sr["score"], 3)
        lines.append(f"  {name} S:[{sb}]{sr['score']}/3  D:[{db}]{dr['score']}/2")

    return "\n".join(lines)

def fmt_top(cache, n=5):
    combined = sorted(cache, key=lambda c: -(c["swing"]["score"] + c["day"]["score"]))[:n]
    lines = [f"🏆 Top {n} ativos (Swing + Day)\n"]
    for i, c in enumerate(combined, 1):
        sym  = c["day"]["symbol"].replace("/USDT","")
        ds   = c["day"]["score"];   db = bar(ds, 2)
        ss   = c["swing"]["score"]; sb = bar(ss, 3)
        total = round(ds + ss, 1)
        lines.append(
            f"{i}. {sym} (total {total})\n"
            f"   Swing [{sb}] {ss}/3 💰{fmt_price(c['swing']['price'])} 🔴{fmt_price(c['swing']['stop'])}\n"
            f"   Day   [{db}] {ds}/2 💰{fmt_price(c['day']['price'])} 🔴{fmt_price(c['day']['stop'])}"
        )
    return "\n".join(lines)

# ── Cache ─────────────────────────────────────────────────────
def save_cache(results):
    with open(CACHE_FILE, "w") as f:
        json.dump({"ts": datetime.now(timezone.utc).isoformat(), "results": results}, f)

def load_cache():
    if not os.path.exists(CACHE_FILE): return None, None
    with open(CACHE_FILE) as f:
        data = json.load(f)
    return data.get("results", []), data.get("ts","?")

# ── Send ──────────────────────────────────────────────────────
def send(msg):
    MAX = 4000
    chunks = [msg[i:i+MAX] for i in range(0, len(msg), MAX)]
    for chunk in chunks:
        subprocess.run(
            [OPENCLAW_BIN, "message", "send", "--channel", "telegram",
             "--target", TELEGRAM_TARGET, "--message", chunk],
            capture_output=True, text=True
        )

# ── Comandos ──────────────────────────────────────────────────
def cmd_scan():
    send(f"⏳ Varrendo {len(ASSETS)} ativos... ~2 min.")
    results = []
    swing_alerts = []
    day_alerts   = []

    for sym in ASSETS:
        try:
            day, swing = analyze_asset(sym)
            results.append({"day": day, "swing": swing})
            if swing["score"] >= SWING_ALERT: swing_alerts.append(swing)
            if day["score"]   >= DAY_ALERT:   day_alerts.append(day)
            time.sleep(0.3)
        except Exception:
            pass

    save_cache(results)

    # Envia alertas individuais primeiro (swing depois day)
    for r in sorted(swing_alerts, key=lambda x: -x["score"]):
        send(fmt_swing(r))
    for r in sorted(day_alerts, key=lambda x: -x["score"]):
        send(fmt_day(r))

    # Summary
    send(fmt_summary(results))

def cmd_ativo(sym_raw):
    sym = sym_raw.upper().replace("USDT","").strip("/") + "/USDT"
    if sym not in ASSETS:
        send(f"❌ {sym} não está na lista monitorada.")
        return
    send(f"🔍 Analisando {sym}...")
    day, swing = analyze_asset(sym)
    send(fmt_swing(swing))
    send(fmt_day(day))

def cmd_top():
    results, ts = load_cache()
    if not results:
        send("⚠️ Nenhuma varredura ainda. Envie: /scan")
        return
    send(fmt_top(results))

def cmd_status():
    results, ts = load_cache()
    if not results:
        send("⚠️ Nenhuma varredura ainda. Envie: /scan")
        return
    send(f"📋 Último scan: {ts}\n\n" + fmt_summary(results))

def cmd_ajuda():
    send(
        "🤖 Crypto Scanner — Comandos\n\n"
        "/scan        — Varre os 23 ativos agora\n"
        "/ativo BTC   — Análise Day + Swing de um ativo\n"
        "/top         — Top 5 por score combinado\n"
        "/status      — Último scan\n"
        "/ajuda       — Esta mensagem\n\n"
        f"⚡ Day Trade  : alerta ≥ {DAY_ALERT}/2 | monitorar ≥ {DAY_WATCH}/2\n"
        f"📈 Swing Trade: alerta ≥ {SWING_ALERT}/3 | monitorar ≥ {SWING_WATCH}/3\n"
        "Varredura automática: todo :00"
    )

# ── Main ──────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Uso: python3 crypto_cmd.py scan | ativo BTC | top | status | ajuda")
        sys.exit(1)

    cmd = args[0].lower().lstrip("/")

    if cmd == "scan":
        cmd_scan()
    elif cmd == "ativo" and len(args) >= 2:
        cmd_ativo(args[1])
    elif cmd == "top":
        cmd_top()
    elif cmd in ("status", "último", "ultimo"):
        cmd_status()
    elif cmd in ("ajuda", "help", "start"):
        cmd_ajuda()
    else:
        send(f"❓ Comando desconhecido: {' '.join(args)}\nUse /ajuda.")
