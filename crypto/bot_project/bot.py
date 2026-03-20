#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║  CRYPTO BOT — Telegram com Polling                          ║
║  Roda localmente, sem servidor público                      ║
║                                                              ║
║  Comandos:                                                   ║
║    /scan   — Varre todos os 23 ativos agora                 ║
║    /ativo BTC — Score detalhado de um ativo                 ║
║    /top    — Top ativos com maior score                     ║
║    /status — Último resultado da varredura                  ║
║    /ajuda  — Lista de comandos                              ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

import requests
import pandas as pd
import numpy as np
import ccxt

# ──────────────────────────────────────────────────────────────
# CONFIGURAÇÃO
# ──────────────────────────────────────────────────────────────
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

ASSETS = [
    "BTC/USDT",  "SOL/USDT",  "XRP/USDT",  "TAO/USDT",  "VIRTUAL/USDT",
    "ADA/USDT",  "RENDER/USDT","NEAR/USDT", "AAVE/USDT", "LINK/USDT",
    "AVAX/USDT", "HBAR/USDT", "ZEC/USDT",  "TON/USDT",  "DOGE/USDT",
    "TRX/USDT",  "POL/USDT",  "LTC/USDT",  "ICP/USDT",  "XMR/USDT",
    "UNI/USDT",  "VET/USDT",  "SUI/USDT",
]

SCORE_ALERT            = 3
SCAN_INTERVAL_SECONDS  = 3600   # varredura automática a cada 1h
ATR_MULT_DAY           = 1.5
ATR_MULT_SWING         = 2.0
VOLUME_MIN             = 0.70
VOLUME_SPIKE           = 1.50

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Cache do último scan
_last_results: list = []
_last_scan_ts: str  = "nunca"

# ──────────────────────────────────────────────────────────────
# EXCHANGE
# ──────────────────────────────────────────────────────────────
def get_exchange() -> ccxt.binance:
    return ccxt.binance({
        "apiKey": BINANCE_API_KEY,
        "secret": BINANCE_API_SECRET,
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })

EXCHANGE = get_exchange()

def fetch_ohlcv(symbol: str, timeframe: str, limit: int = 250) -> Optional[pd.DataFrame]:
    try:
        raw = EXCHANGE.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df  = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df.set_index("timestamp").astype(float)
    except Exception as e:
        log.warning(f"[{symbol}] Erro fetch {timeframe}: {e}")
        return None

# ──────────────────────────────────────────────────────────────
# INDICADORES
# ──────────────────────────────────────────────────────────────
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def sma(s, n):
    return s.rolling(n).mean()

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

def vwap_daily(df_1h):
    df           = df_1h.copy()
    df["date"]   = df.index.normalize()
    df["typical"]= (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["typical"] * df["volume"]
    df["cum_tpv"]= df.groupby("date")["tp_vol"].cumsum()
    df["cum_vol"]= df.groupby("date")["volume"].cumsum()
    return df["cum_tpv"] / df["cum_vol"]

def vol_ok(df, ratio=VOLUME_MIN):
    avg  = df["volume"].rolling(20).mean().iloc[-1]
    last = df["volume"].iloc[-1]
    return bool(avg > 0 and last >= ratio * avg)

# ──────────────────────────────────────────────────────────────
# SETUPS
# ──────────────────────────────────────────────────────────────
def check_golden_cross(df):
    r = {"active": False, "detail": "—"}
    if df is None or len(df) < 201:
        return r
    s50  = sma(df["close"], 50).iloc[-1]
    s200 = sma(df["close"], 200).iloc[-1]
    rsi_ = rsi_calc(df["close"]).iloc[-1]
    r["active"] = bool(s50 > s200)
    r["rsi"]    = round(rsi_, 1)
    r["rsi_ok"] = bool(50 <= rsi_ <= 70)
    r["detail"] = f"SMA50={'>' if r['active'] else '<'}SMA200 | RSI={rsi_:.1f}"
    return r

def check_setup_91(df):
    r = {"active": False, "detail": "—", "stop": None}
    if df is None or len(df) < 25:
        return r
    e9   = ema(df["close"], 9)
    rsi_ = rsi_calc(df["close"]).iloc[-1]
    atr_ = atr_calc(df).iloc[-1]
    turn = (e9.iloc[-1] > e9.iloc[-2]) and (e9.iloc[-2] <= e9.iloc[-3])
    r["active"] = bool(turn and rsi_ > 50 and vol_ok(df))
    r["rsi"]    = round(rsi_, 1)
    r["stop"]   = round(df["low"].iloc[-1] - ATR_MULT_SWING * atr_, 6)
    r["detail"] = f"EMA9↑={'✅' if turn else '❌'} | RSI={rsi_:.1f} | Stop={r['stop']}"
    return r

def check_vwap_ema(df):
    r = {"active": False, "detail": "—", "stop": None}
    if df is None or len(df) < 30:
        return r
    e9   = ema(df["close"], 9)
    e20  = ema(df["close"], 20)
    vwap = vwap_daily(df)
    rsi_ = rsi_calc(df["close"]).iloc[-1]
    atr_ = atr_calc(df).iloc[-1]
    price = df["close"].iloc[-1]

    above_vwap = bool(price > vwap.iloc[-1])
    cross      = bool(e9.iloc[-1] > e20.iloc[-1] and e9.iloc[-2] <= e20.iloc[-2])
    rsi_ok     = bool(40 <= rsi_ <= 70)

    r["active"] = bool(above_vwap and cross and rsi_ok and vol_ok(df))
    r["rsi"]    = round(rsi_, 1)
    r["stop"]   = round(vwap.iloc[-1] - ATR_MULT_DAY * atr_, 6)
    r["detail"] = f"VWAP={'✅' if above_vwap else '❌'} | EMA×={'✅' if cross else '❌'} | RSI={rsi_:.1f}"
    return r

def check_bb_squeeze(df):
    r = {"active": False, "detail": "—", "stop": None}
    if df is None or len(df) < 30:
        return r
    upper, mid, lower, width = bollinger_calc(df)
    atr_  = atr_calc(df).iloc[-1]
    price = df["close"].iloc[-1]

    squeeze  = bool(width.iloc[-1] < 0.02)
    breakout = bool(price > upper.iloc[-1])
    spike    = vol_ok(df, VOLUME_SPIKE)

    r["active"] = bool(squeeze and breakout and spike)
    r["bw"]     = round(width.iloc[-1] * 100, 2)
    r["stop"]   = round(lower.iloc[-1] - ATR_MULT_DAY * atr_, 6)
    r["detail"] = (
        f"Squeeze={'✅' if squeeze else '❌'} BW={r['bw']}% | "
        f"Break={'✅' if breakout else '❌'} | Spike={'✅' if spike else '❌'}"
    )
    return r

def check_hf(df):
    if df is None or len(df) < 22:
        return False
    return bool(df["close"].iloc[-1] > ema(df["close"], 20).iloc[-1])

# ──────────────────────────────────────────────────────────────
# SCORE
# ──────────────────────────────────────────────────────────────
def score_asset(symbol: str) -> dict:
    df_d = fetch_ohlcv(symbol, "1d",  250)
    df_4 = fetch_ohlcv(symbol, "4h",  120)
    df_1 = fetch_ohlcv(symbol, "1h",  100)

    hf = check_hf(df_4)
    gc = check_golden_cross(df_d)
    s9 = check_setup_91(df_4)
    vw = check_vwap_ema(df_1)
    bb = check_bb_squeeze(df_1)

    score = (
        (1 if gc["active"] else 0) +
        (1 if s9["active"] else 0) +
        (1 if vw["active"] and hf else 0) +
        (1 if bb["active"] and hf else 0)
    )

    stops = [r["stop"] for r in [s9, vw, bb] if r.get("stop")]
    price = df_1["close"].iloc[-1] if df_1 is not None else None

    return {
        "symbol":     symbol,
        "score":      score,
        "price":      round(price, 6) if price else None,
        "hf":         hf,
        "gc":         gc,
        "s9":         s9,
        "vw":         vw,
        "bb":         bb,
        "best_stop":  round(min(stops), 6) if stops else None,
        "ts":         datetime.now(timezone.utc).strftime("%d/%m %H:%M UTC"),
    }

# ──────────────────────────────────────────────────────────────
# FORMATAÇÃO DE MENSAGENS
# ──────────────────────────────────────────────────────────────
def fmt_ativo(r: dict) -> str:
    sym   = r["symbol"].replace("/USDT", "")
    score = r["score"]
    bar   = "█" * score + "░" * (4 - score)
    hf    = "✅" if r["hf"] else "❌"

    def icon(k):
        return "✅" if r[k]["active"] else "❌"

    lines = [
        f"<b>{'🚨' if score >= SCORE_ALERT else '📊'} {sym}</b> [{bar}] {score}/4",
        "",
        f"💰 Preço  : <code>{r.get('price','?')}</code>",
        f"🔴 Stop   : <code>{r.get('best_stop','?')}</code>",
        f"📡 Filtro HF (4h > EMA20): {hf}",
        "",
        "<b>Setups:</b>",
        f"  📈 Golden Cross (D) : {icon('gc')} {r['gc'].get('detail','—')}",
        f"  📊 Setup 9.1   (4h): {icon('s9')} {r['s9'].get('detail','—')}",
        f"  💹 VWAP+EMA    (1h): {icon('vw')} {r['vw'].get('detail','—')}",
        f"  🎯 BB Squeeze  (1h): {icon('bb')} {r['bb'].get('detail','—')}",
        "",
        f"⏰ {r['ts']}",
    ]
    return "\n".join(lines)

def fmt_summary(results: list) -> str:
    ts      = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    sorted_ = sorted(results, key=lambda x: -x["score"])
    alerts  = [r for r in sorted_ if r["score"] >= SCORE_ALERT]

    lines = [f"📋 <b>VARREDURA — {ts}</b>", f"<i>{len(results)} ativos analisados</i>", ""]

    if alerts:
        lines.append(f"🚨 <b>Alertas score ≥ {SCORE_ALERT}:</b>")
        for r in alerts:
            sym   = r["symbol"].replace("/USDT","")
            stars = "⭐" * r["score"]
            lines.append(
                f"  <b>{sym}</b>: {r['score']}/4 {stars} "
                f"💰{r.get('price','?')} 🔴{r.get('best_stop','?')}"
            )
        lines.append("")

    lines.append("<b>Ranking completo:</b>")
    for r in sorted_:
        sym = r["symbol"].replace("/USDT","").ljust(9)
        bar = "█" * r["score"] + "░" * (4 - r["score"])
        hf  = "📡" if r["hf"] else "  "
        lines.append(f"{hf}<code>{sym}</code> [{bar}] {r['score']}/4")

    return "\n".join(lines)

def fmt_top(results: list, n=5) -> str:
    top = sorted(results, key=lambda x: -x["score"])[:n]
    if not top:
        return "Nenhum resultado disponível. Use /scan primeiro."
    lines = [f"🏆 <b>Top {n} ativos</b>\n"]
    for i, r in enumerate(top, 1):
        sym = r["symbol"].replace("/USDT","")
        bar = "█" * r["score"] + "░" * (4 - r["score"])
        lines.append(
            f"{i}. <b>{sym}</b> [{bar}] {r['score']}/4 "
            f"💰{r.get('price','?')} 🔴{r.get('best_stop','?')}"
        )
    return "\n".join(lines)

# ──────────────────────────────────────────────────────────────
# TELEGRAM POLLING
# ──────────────────────────────────────────────────────────────
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
_offset   = 0

def tg_send(chat_id, text: str) -> None:
    try:
        requests.post(
            f"{BASE_URL}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def tg_get_updates() -> list:
    global _offset
    try:
        r = requests.get(
            f"{BASE_URL}/getUpdates",
            params={"offset": _offset, "timeout": 30},
            timeout=40,
        )
        data    = r.json()
        updates = data.get("result", [])
        if updates:
            _offset = updates[-1]["update_id"] + 1
        return updates
    except Exception as e:
        log.warning(f"getUpdates error: {e}")
        return []

def handle_update(update: dict) -> None:
    msg = update.get("message", {})
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    text    = msg.get("text", "").strip()

    # Segurança: só responde ao seu chat_id
    if TELEGRAM_CHAT_ID and str(chat_id) != str(TELEGRAM_CHAT_ID):
        log.warning(f"Mensagem ignorada de chat_id desconhecido: {chat_id}")
        return

    log.info(f"Comando recebido: {text}")
    cmd = text.split()[0].lower().split("@")[0]

    # /ajuda ─────────────────────────────────────────────────
    if cmd in ["/start", "/ajuda", "/help"]:
        tg_send(chat_id,
            "🤖 <b>Crypto Scanner Bot</b>\n\n"
            "/scan   — Varre todos os 23 ativos agora\n"
            "/ativo BTC — Score detalhado de um ativo\n"
            "/top    — Top 5 ativos com maior score\n"
            "/status — Resultado da última varredura\n"
            "/ajuda  — Esta mensagem\n\n"
            f"<i>Varredura automática a cada 1h. Alerta ≥ {SCORE_ALERT}/4.</i>"
        )

    # /scan ──────────────────────────────────────────────────
    elif cmd == "/scan":
        tg_send(chat_id, f"⏳ Iniciando varredura de {len(ASSETS)} ativos... aguarde ~2 min.")
        threading.Thread(target=_run_full_scan, args=(chat_id,), daemon=True).start()

    # /ativo ─────────────────────────────────────────────────
    elif cmd == "/ativo":
        parts = text.split()
        if len(parts) < 2:
            tg_send(chat_id, "⚠️ Use: /ativo BTC")
            return
        sym = parts[1].upper().replace("USDT","").strip("/") + "/USDT"
        if sym not in ASSETS:
            tg_send(chat_id, f"❌ {sym} não está na lista de ativos monitorados.")
            return
        tg_send(chat_id, f"🔍 Analisando {sym}...")
        threading.Thread(target=_run_single, args=(chat_id, sym), daemon=True).start()

    # /top ───────────────────────────────────────────────────
    elif cmd == "/top":
        if not _last_results:
            tg_send(chat_id, "⚠️ Nenhuma varredura realizada ainda. Use /scan.")
        else:
            tg_send(chat_id, fmt_top(_last_results))

    # /status ────────────────────────────────────────────────
    elif cmd == "/status":
        if not _last_results:
            tg_send(chat_id, "⚠️ Nenhuma varredura realizada ainda. Use /scan.")
        else:
            tg_send(chat_id,
                f"📋 Último scan: <b>{_last_scan_ts}</b>\n\n" + fmt_summary(_last_results)
            )

    else:
        tg_send(chat_id, "❓ Comando desconhecido. Use /ajuda.")


def _run_full_scan(chat_id):
    global _last_results, _last_scan_ts
    results = []
    for sym in ASSETS:
        try:
            r = score_asset(sym)
            results.append(r)
            if r["score"] >= SCORE_ALERT:
                tg_send(chat_id, fmt_ativo(r))
            time.sleep(0.4)
        except Exception as e:
            log.error(f"{sym}: {e}")
    _last_results = results
    _last_scan_ts = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    tg_send(chat_id, fmt_summary(results))


def _run_single(chat_id, symbol):
    try:
        r = score_asset(symbol)
        tg_send(chat_id, fmt_ativo(r))
    except Exception as e:
        tg_send(chat_id, f"❌ Erro ao analisar {symbol}: {e}")

# ──────────────────────────────────────────────────────────────
# VARREDURA AUTOMÁTICA A CADA 1H
# ──────────────────────────────────────────────────────────────
def _auto_scan_loop():
    while True:
        time.sleep(SCAN_INTERVAL_SECONDS)
        log.info("🔄 Varredura automática iniciada")
        if TELEGRAM_CHAT_ID:
            _run_full_scan(TELEGRAM_CHAT_ID)

# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────
def main():
    if not TELEGRAM_TOKEN:
        log.error("❌ TELEGRAM_TOKEN não configurado. Configure no .env e tente novamente.")
        return

    log.info("🤖 Bot iniciado — aguardando comandos no Telegram...")
    log.info(f"   Ativos : {len(ASSETS)}")
    log.info(f"   Alerta : score ≥ {SCORE_ALERT}/4")
    log.info(f"   Auto   : a cada {SCAN_INTERVAL_SECONDS // 60} minutos")

    if TELEGRAM_CHAT_ID:
        tg_send(TELEGRAM_CHAT_ID,
            "🟢 <b>Crypto Scanner Bot online</b>\n"
            f"Monitorando {len(ASSETS)} ativos | varredura automática a cada 1h\n"
            "Use /ajuda para ver os comandos."
        )

    # Thread da varredura automática
    threading.Thread(target=_auto_scan_loop, daemon=True).start()

    # Polling principal
    while True:
        updates = tg_get_updates()
        for upd in updates:
            try:
                handle_update(upd)
            except Exception as e:
                log.error(f"handle_update error: {e}")
