#!/usr/bin/env python3
"""
Jarvis Trader — Hyperliquid
Executa ordens com base nos sinais do scanner.
Gestão de risco automática.
"""

import json
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ─── CONFIGURAÇÃO DE RISCO ─────────────────────────────────────────────────────
RISK_CONFIG = {
    "capital_usd":       70.0,    # Capital total alocado
    "risk_pct_min":      0.02,    # Risco mínimo por trade (2%)
    "risk_pct_max":      0.04,    # Risco máximo por trade (4%)
    "leverage":          5,       # Alavancagem padrão (5x-10x, começa conservador)
    "max_drawdown_pct":  0.40,    # Para tudo se capital cair 40%
    "capital_floor":     42.0,    # $70 * (1 - 0.40) = $42
    "max_open_trades":   3,       # Máximo de posições simultâneas
    "tp_ratio":          2.0,     # Take profit = 2x o risco (RR 1:2)
    "operate_24h":       True,
}

BASE_DIR      = Path(__file__).parent
CONFIG_FILE   = BASE_DIR / "hl_config.json"
STATE_FILE    = BASE_DIR / "trader_state.json"
TELEGRAM_CHAT = "127842708"
TZ            = timezone(timedelta(hours=-3))

# Mapeamento ccxt → Hyperliquid
HL_SYMBOLS = {
    "BTC/USDT": "BTC", "SOL/USDT": "SOL", "XRP/USDT": "XRP",
    "TAO/USDT": "TAO", "VIRTUAL/USDT": "VIRTUAL", "ADA/USDT": "ADA",
    "RENDER/USDT": "RENDER", "NEAR/USDT": "NEAR", "AAVE/USDT": "AAVE",
    "LINK/USDT": "LINK", "AVAX/USDT": "AVAX", "HBAR/USDT": "HBAR",
    "ZEC/USDT": "ZEC", "TON/USDT": "TON",
}


# ─── HELPERS ───────────────────────────────────────────────────────────────────
def send_telegram(msg):
    subprocess.run(
        ["openclaw", "message", "send",
         "--channel", "telegram",
         "--target", TELEGRAM_CHAT,
         "--message", msg],
        capture_output=True
    )


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"open_trades": [], "capital_current": RISK_CONFIG["capital_usd"], "total_pnl": 0.0}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def load_hl_config():
    if not CONFIG_FILE.exists():
        return None
    return json.loads(CONFIG_FILE.read_text())


# ─── CÁLCULO DE POSIÇÃO ────────────────────────────────────────────────────────
def calc_position(capital, entry_price, stop_price, direction, leverage):
    """Calcula tamanho da posição baseado no risco definido."""
    risk_usd = capital * RISK_CONFIG["risk_pct_max"]  # usa 3% como padrão
    sl_distance_pct = abs(entry_price - stop_price) / entry_price

    # Tamanho em USD com alavancagem
    position_usd = (risk_usd / sl_distance_pct)
    # Limita ao capital disponível * alavancagem
    max_position = capital * leverage
    position_usd = min(position_usd, max_position)

    size = position_usd / entry_price

    # Take profit: RR 1:2
    sl_dist = abs(entry_price - stop_price)
    if direction == "long":
        tp_price = entry_price + (sl_dist * RISK_CONFIG["tp_ratio"])
    else:
        tp_price = entry_price - (sl_dist * RISK_CONFIG["tp_ratio"])

    return {
        "size": round(size, 4),
        "position_usd": round(position_usd, 2),
        "risk_usd": round(risk_usd, 2),
        "tp_price": round(tp_price, 4),
        "sl_price": round(stop_price, 4),
        "sl_pct": round(sl_distance_pct * 100, 2),
    }


# ─── EXECUÇÃO HYPERLIQUID ──────────────────────────────────────────────────────
def get_hl_client():
    cfg = load_hl_config()
    if not cfg:
        return None, None
    from hyperliquid.exchange import Exchange
    from hyperliquid.info import Info
    from eth_account import Account

    account = Account.from_key(cfg["private_key"])
    info     = Info(cfg.get("api_url", "https://api.hyperliquid.xyz"), skip_ws=True)
    exchange = Exchange(account, cfg.get("api_url", "https://api.hyperliquid.xyz"))
    return exchange, info


def get_current_price(info, symbol):
    try:
        meta = info.all_mids()
        return float(meta.get(symbol, 0))
    except:
        return 0


def open_order(symbol, direction, entry_price, size, sl_price, tp_price, leverage):
    exchange, info = get_hl_client()
    if not exchange:
        return False, "HL não configurado"

    try:
        # Seta alavancagem
        exchange.update_leverage(leverage, symbol, is_cross=False)

        is_buy = direction == "long"

        # Ordem principal (market)
        result = exchange.market_open(symbol, is_buy, size)

        if result and result.get("status") == "ok":
            # Stop loss
            exchange.order(symbol, not is_buy, size, sl_price,
                          {"trigger": {"triggerPx": sl_price, "isMarket": True, "tpsl": "sl"}},
                          reduce_only=True)
            # Take profit
            exchange.order(symbol, not is_buy, size, tp_price,
                          {"trigger": {"triggerPx": tp_price, "isMarket": True, "tpsl": "tp"}},
                          reduce_only=True)
            return True, result
        return False, result
    except Exception as e:
        return False, str(e)


def close_position(symbol):
    exchange, info = get_hl_client()
    if not exchange:
        return False, "HL não configurado"
    try:
        result = exchange.market_close(symbol)
        return True, result
    except Exception as e:
        return False, str(e)


# ─── EXECUÇÃO DE SINAL ─────────────────────────────────────────────────────────
def execute_signal(signal):
    """
    signal = {
        'symbol': 'SOL/USDT',
        'direction': 'long' | 'short',
        'entry': float,
        'ema9': float,
        'ema21': float,
        'rsi': float,
        'mode': 'day' | 'swing',
    }
    """
    state = load_state()
    cfg   = load_hl_config()

    hl_symbol = HL_SYMBOLS.get(signal["symbol"])
    if not hl_symbol:
        return

    # Checagens de risco
    capital = state["capital_current"]

    if capital <= RISK_CONFIG["capital_floor"]:
        send_telegram(
            f"⛔ JARVIS — DRAWDOWN LIMITE ATINGIDO\n"
            f"Capital atual: ${capital:.2f} (limite: ${RISK_CONFIG['capital_floor']:.2f})\n"
            f"Operações suspensas. Aguardando sua ordem."
        )
        return

    if len(state["open_trades"]) >= RISK_CONFIG["max_open_trades"]:
        return  # Máximo de posições abertas

    # Já tem posição nesse ativo?
    if any(t["symbol"] == hl_symbol for t in state["open_trades"]):
        return

    entry  = signal["entry"]
    direction = signal["direction"]

    # Stop loss: 1.5x ATR estimado via distância EMA9-EMA21
    ema_dist = abs(signal["ema9"] - signal["ema21"])
    sl_offset = max(ema_dist * 1.5, entry * 0.015)  # mínimo 1.5%

    if direction == "long":
        sl_price = entry - sl_offset
    else:
        sl_price = entry + sl_offset

    leverage = RISK_CONFIG["leverage"]
    pos = calc_position(capital, entry, sl_price, direction, leverage)

    # Modo simulado se sem config HL
    simulated = cfg is None

    if not simulated:
        ok, result = open_order(hl_symbol, direction, entry, pos["size"], pos["sl_price"], pos["tp_price"], leverage)
        if not ok:
            send_telegram(f"⚠️ Erro ao abrir ordem {hl_symbol}: {result}")
            return
    else:
        ok = True

    # Registra no estado
    trade = {
        "symbol":     hl_symbol,
        "direction":  direction,
        "entry":      entry,
        "size":       pos["size"],
        "sl":         pos["sl_price"],
        "tp":         pos["tp_price"],
        "risk_usd":   pos["risk_usd"],
        "leverage":   leverage,
        "mode":       signal.get("mode", "day"),
        "opened_at":  datetime.now(TZ).isoformat(),
        "simulated":  simulated,
    }
    state["open_trades"].append(trade)
    save_state(state)

    sim_tag = " [SIMULADO]" if simulated else ""
    emoji   = "🟢" if direction == "long" else "🔴"
    dir_str = "LONG" if direction == "long" else "SHORT"

    msg = (
        f"{emoji} {dir_str} ABERTO{sim_tag} — {hl_symbol}\n"
        f"Entrada: ${entry:,.4f} | Size: {pos['size']} {hl_symbol}\n"
        f"Stop: ${pos['sl_price']:,.4f} (-{pos['sl_pct']}%)\n"
        f"Alvo: ${pos['tp_price']:,.4f} (RR 1:{RISK_CONFIG['tp_ratio']:.0f})\n"
        f"Risco: ${pos['risk_usd']:.2f} | Alavancagem: {leverage}x\n"
        f"Capital em risco: {(pos['risk_usd']/capital*100):.1f}% | Modo: {signal.get('mode','').upper()}"
    )
    send_telegram(msg)


def show_status():
    state = load_state()
    cfg   = load_hl_config()
    trades = state["open_trades"]
    capital = state["capital_current"]

    lines = [
        f"📊 STATUS JARVIS TRADER",
        f"Capital: ${capital:.2f} / $100.00",
        f"P&L total: ${state['total_pnl']:+.2f}",
        f"Posições abertas: {len(trades)}/{RISK_CONFIG['max_open_trades']}",
        f"Conexão HL: {'✅' if cfg else '⚠️ Modo simulado'}",
        "",
    ]

    if trades:
        for t in trades:
            sim = " [SIM]" if t.get("simulated") else ""
            emoji = "🟢" if t["direction"] == "long" else "🔴"
            lines.append(f"{emoji} {t['symbol']} {t['direction'].upper()}{sim}")
            lines.append(f"   Entrada: ${t['entry']:,.4f} | SL: ${t['sl']:,.4f} | TP: ${t['tp']:,.4f}")
    else:
        lines.append("Sem posições abertas.")

    send_telegram("\n".join(lines))


# ─── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        show_status()
    elif len(sys.argv) > 1 and sys.argv[1] == "close":
        symbol = sys.argv[2] if len(sys.argv) > 2 else None
        if symbol:
            ok, r = close_position(symbol)
            send_telegram(f"{'✅' if ok else '❌'} Fechar {symbol}: {r}")
    else:
        print("Uso: trader.py status | trader.py close <SYMBOL>")
