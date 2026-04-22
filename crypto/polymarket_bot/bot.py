"""
╔══════════════════════════════════════════════════════════════════╗
║  POLYMARKET BTC 5-MIN BOT                                       ║
║                                                                  ║
║  Estratégia:                                                     ║
║    1. Detecta divergência de preço (Binance/Coinbase vs target)  ║
║    2. Confirma com desequilíbrio do order book (smart money)     ║
║    3. Opera apenas quando AMBOS os sinais concordam              ║
║    4. Saída antecipada: 75c alvo | 35c stop | ou resolução      ║
║                                                                  ║
║  Comandos Telegram:                                              ║
║    /status  — PnL do dia e trades abertos                       ║
║    /stats   — Estatísticas de win rate                          ║
║    /pause   — Pausa o bot (sem novas entradas)                  ║
║    /resume  — Retoma operações                                   ║
║    /ajuda   — Lista de comandos                                  ║
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import requests

from polymarket_client import (
    get_active_btc_5min_market,
    list_crypto_markets,
    get_order_book,
    get_market_prices,
    subscribe_order_book,
    build_clob_client,
    place_limit_order,
    sell_shares,
)
from signals import (
    get_external_prices,
    calculate_imbalance,
    should_trade,
)

# ──────────────────────────────────────────────────────────────
# CONFIGURAÇÃO
# ──────────────────────────────────────────────────────────────
PRIVATE_KEY      = os.getenv("PRIVATE_KEY", "")
WALLET_ADDRESS   = os.getenv("WALLET_ADDRESS", "")
POLY_API_KEY     = os.getenv("POLY_API_KEY", "")
POLY_API_SECRET  = os.getenv("POLY_API_SECRET", "")
POLY_PASSPHRASE  = os.getenv("POLY_API_PASSPHRASE", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BANKROLL         = float(os.getenv("BANKROLL", "200"))
MAX_TRADE_PCT    = float(os.getenv("MAX_TRADE_PCT", "0.05"))
DRY_RUN          = os.getenv("DRY_RUN", "true").lower() == "true"

PROFIT_TARGET = 0.75   # sair quando share chegar a 75c
STOP_LOSS     = 0.35   # sair quando share cair a 35c
ENTRY_WINDOW  = (60, 180)  # segundos após abertura para entrar

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# ESTADO GLOBAL
# ──────────────────────────────────────────────────────────────
_state = {
    "paused":        False,
    "bankroll":      BANKROLL,
    "daily_pnl":     0.0,
    "trades_today":  0,
    "wins":          0,
    "losses":        0,
    "skipped":       0,
    "current_market": None,
    "open_position":  None,
    "history":       [],
}


# ──────────────────────────────────────────────────────────────
# TELEGRAM
# ──────────────────────────────────────────────────────────────
_tg_base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
_tg_offset = 0


def tg_send(text: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"{_tg_base}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"tg_send error: {e}")


def tg_get_updates() -> list:
    global _tg_offset
    try:
        r = requests.get(
            f"{_tg_base}/getUpdates",
            params={"offset": _tg_offset, "timeout": 20},
            timeout=30,
        )
        data = r.json()
        updates = data.get("result", [])
        if updates:
            _tg_offset = updates[-1]["update_id"] + 1
        return updates
    except Exception:
        return []


def handle_telegram_update(update: dict) -> None:
    msg = update.get("message", {})
    if not msg:
        return
    chat_id = str(msg["chat"]["id"])
    if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
        return

    text = msg.get("text", "").strip()
    cmd  = text.split()[0].lower().split("@")[0] if text else ""

    if cmd in ["/start", "/ajuda", "/help"]:
        tg_send(
            "🤖 <b>Polymarket BTC 5-min Bot</b>\n\n"
            "/status   — PnL do dia e posição aberta\n"
            "/stats    — Win rate e histórico\n"
            "/mercados — Lista mercados ativos na API\n"
            "/pause    — Pausa novas entradas\n"
            "/resume   — Retoma operações\n"
            "/ajuda    — Esta mensagem\n\n"
            f"<i>DRY_RUN={'ON' if DRY_RUN else 'OFF'} | Banca: ${_state['bankroll']:.2f}</i>"
        )

    elif cmd == "/status":
        pos = _state["open_position"]
        mkt = _state["current_market"]
        pos_txt = "Sem posição aberta"
        if pos:
            pos_txt = (
                f"📌 {pos['direction']} @ ${pos['entry']:.3f}\n"
                f"   Size: {pos['size']:.2f} | Token: {pos['token_id'][:10]}..."
            )
        mkt_txt = "Aguardando mercado"
        if mkt:
            remaining = int(mkt["resolution_time"] - time.time())
            mkt_txt = (
                f"📊 {mkt['question'][:50]}...\n"
                f"   Alvo: ${mkt.get('price_to_beat', '?')} | Restam {remaining}s"
            )
        tg_send(
            f"<b>Status</b>\n\n"
            f"💰 Banca  : ${_state['bankroll']:.2f}\n"
            f"📈 PnL hoje: ${_state['daily_pnl']:+.2f}\n"
            f"🎯 Trades : {_state['trades_today']} (W:{_state['wins']} L:{_state['losses']})\n"
            f"⏸️  Pausado: {'Sim' if _state['paused'] else 'Não'}\n\n"
            f"{mkt_txt}\n\n"
            f"{pos_txt}"
        )

    elif cmd == "/stats":
        total = _state["wins"] + _state["losses"]
        wr = (100 * _state["wins"] / total) if total > 0 else 0
        recent = _state["history"][-10:]
        recent_txt = "\n".join(
            f"  {'✅' if r['pnl'] > 0 else '❌'} {r['direction']} {r['exit']} ${r['pnl']:+.2f}"
            for r in reversed(recent)
        ) or "  Sem histórico ainda"
        tg_send(
            f"<b>Estatísticas</b>\n\n"
            f"Total trades: {total}\n"
            f"Win rate: {wr:.1f}%\n"
            f"PnL acumulado: ${_state['daily_pnl']:+.2f}\n\n"
            f"<b>Últimos 10:</b>\n{recent_txt}"
        )

    elif cmd == "/mercados":
        async def _fetch_and_send():
            async with aiohttp.ClientSession() as s:
                markets = await list_crypto_markets(s, limit=20)
            if not markets:
                tg_send("Nenhum mercado crypto ativo encontrado na API.")
                return
            txt = "\n".join(f"• {q[:80]}" for q in markets[:15])
            tg_send(f"<b>Mercados crypto ativos (Polymarket):</b>\n\n{txt}")
        threading.Thread(target=lambda: asyncio.run(_fetch_and_send()), daemon=True).start()

    elif cmd == "/pause":
        _state["paused"] = True
        tg_send("⏸️ Bot pausado. Novas entradas suspensas.")

    elif cmd == "/resume":
        _state["paused"] = False
        tg_send("▶️ Bot retomado.")

    else:
        tg_send("❓ Comando desconhecido. Use /ajuda.")


def _telegram_polling_loop():
    while True:
        try:
            updates = tg_get_updates()
            for upd in updates:
                handle_telegram_update(upd)
        except Exception as e:
            log.error(f"telegram loop error: {e}")
        time.sleep(2)


# ──────────────────────────────────────────────────────────────
# MOTOR PRINCIPAL
# ──────────────────────────────────────────────────────────────

async def wait_for_entry_window(market: dict) -> bool:
    """Aguarda até a janela de entrada (60-180s após abertura). Retorna False se muito tarde."""
    elapsed = market["elapsed_seconds"]

    if elapsed > ENTRY_WINDOW[1]:
        log.info(f"Janela de entrada expirada (elapsed={elapsed:.0f}s)")
        return False

    if elapsed < ENTRY_WINDOW[0]:
        wait = ENTRY_WINDOW[0] - elapsed
        log.info(f"Aguardando janela de entrada ({wait:.0f}s)...")
        await asyncio.sleep(wait)

    return True


async def monitor_position(
    position: dict,
    market:   dict,
    session:  aiohttp.ClientSession,
    client,
) -> dict:
    """
    Monitora posição aberta e sai quando:
    - Shares atingem 75c (alvo de lucro)
    - Shares caem a 35c (stop loss)
    - Menos de 60s para resolução (mantém até resolver)
    """
    token_id = position["token_id"]
    entry    = position["entry"]
    size     = position["size"]

    while True:
        time_remaining = market["resolution_time"] - time.time()

        if time_remaining < 60:
            pnl_pct = (1.0 - entry) * size if position["direction"] == "UP" else entry * size
            log.info(f"Tempo esgotado — mantendo até resolução | PnL estimado: ${pnl_pct:+.2f}")
            return {"exit": "HOLD_TO_RESOLUTION", "pnl": pnl_pct}

        price_data = await get_market_prices(session, token_id)
        if price_data:
            current = float(price_data.get("price", entry))

            if current >= PROFIT_TARGET:
                await sell_shares(client, token_id, current, size, dry_run=DRY_RUN)
                pnl = (current - entry) * size
                log.info(f"ALVO ATINGIDO @ {current:.3f} | PnL: ${pnl:+.2f}")
                return {"exit": "PROFIT_TARGET", "pnl": pnl}

            if current <= STOP_LOSS:
                await sell_shares(client, token_id, current, size, dry_run=DRY_RUN)
                pnl = (current - entry) * size
                log.info(f"STOP LOSS @ {current:.3f} | PnL: ${pnl:+.2f}")
                return {"exit": "STOP_LOSS", "pnl": pnl}

        await asyncio.sleep(3)


async def run_market_cycle(session: aiohttp.ClientSession, client) -> None:
    """Executa um ciclo completo de mercado (5 minutos)."""

    # 1. Descobrir mercado ativo
    market = await get_active_btc_5min_market(session)
    if not market:
        log.info("Nenhum mercado BTC 5-min ativo encontrado — aguardando 30s")
        await asyncio.sleep(30)
        return

    _state["current_market"] = market
    ptb = market.get("price_to_beat")
    remaining = int(market["resolution_time"] - time.time())

    log.info(f"{'─'*60}")
    log.info(f"Mercado: {market['question'][:70]}")
    log.info(f"Price to Beat: ${ptb} | Restam: {remaining}s")

    if _state["paused"]:
        log.info("Bot pausado — pulando mercado")
        await asyncio.sleep(remaining + 5)
        return

    if remaining < 120:
        log.info(f"Mercado quase resolvendo ({remaining}s) — aguardando próximo")
        await asyncio.sleep(remaining + 5)
        return

    # 2. Aguardar janela de entrada
    ok = await wait_for_entry_window(market)
    if not ok:
        _state["skipped"] += 1
        await asyncio.sleep(max(0, market["resolution_time"] - time.time()) + 5)
        return

    # 3. Coletar sinais
    prices = await get_external_prices(session)
    up_book = await get_order_book(session, market["up_token"]) if market.get("up_token") else {}

    imbalance_now = calculate_imbalance(up_book or {})
    elapsed = market["elapsed_seconds"] + (time.time() - market.get("_fetched_at", time.time()))
    imbalance_history = [{"elapsed": elapsed, "ratio": imbalance_now}] if imbalance_now else []

    signal = should_trade(prices, up_book or {}, market, imbalance_history, _state["bankroll"])

    if signal is None:
        log.info(f"SKIP — sem convergência de sinal | "
                 f"BTC Binance={prices.get('binance')} Coinbase={prices.get('coinbase')} PTB={ptb}")
        _state["skipped"] += 1
        await asyncio.sleep(max(0, market["resolution_time"] - time.time()) + 5)
        return

    log.info(f"SINAL: {signal['direction']} | conf={signal['confidence']:.2%} | "
             f"size=${signal['size']:.2f} | Δ={signal['b_delta']:+.0f}/{signal['c_delta']:+.0f}")

    # 4. Buscar preço de entrada e executar
    token_id = signal["token_id"]
    price_info = await get_market_prices(session, token_id)
    entry_price = float(price_info.get("price", 0.50)) if price_info else 0.50

    order = await place_limit_order(client, token_id, entry_price, signal["size"], dry_run=DRY_RUN)
    if not order:
        log.warning("Falha ao colocar ordem")
        _state["skipped"] += 1
        await asyncio.sleep(max(0, market["resolution_time"] - time.time()) + 5)
        return

    position = {
        "direction": signal["direction"],
        "token_id":  token_id,
        "entry":     entry_price,
        "size":      signal["size"],
        "order_id":  order.get("id"),
        "ts":        time.time(),
    }
    _state["open_position"] = position
    _state["trades_today"]  += 1

    tg_send(
        f"🎯 <b>ENTRADA {signal['direction']}</b>\n"
        f"Mercado: {market['question'][:50]}...\n"
        f"Preço: ${entry_price:.3f} | Size: ${signal['size']:.2f}\n"
        f"Confiança: {signal['confidence']:.0%} | Imbalance: {signal.get('imbalance', 'N/A')}\n"
        f"{'⚠️ DRY RUN' if DRY_RUN else '✅ REAL'}"
    )

    # 5. Monitorar e sair
    result = await monitor_position(position, market, session, client)
    _state["open_position"] = None

    pnl = result.get("pnl", 0.0)
    _state["bankroll"]  += pnl
    _state["daily_pnl"] += pnl
    if pnl > 0:
        _state["wins"] += 1
    else:
        _state["losses"] += 1

    _state["history"].append({
        "direction": signal["direction"],
        "exit":      result["exit"],
        "pnl":       pnl,
        "ts":        datetime.now(timezone.utc).isoformat(),
    })

    icon = "✅" if pnl > 0 else "❌"
    log.info(f"{icon} {result['exit']} | PnL: ${pnl:+.2f} | Banca: ${_state['bankroll']:.2f} | "
             f"Hoje: ${_state['daily_pnl']:+.2f} W:{_state['wins']} L:{_state['losses']}")

    tg_send(
        f"{icon} <b>{result['exit']}</b>\n"
        f"PnL: ${pnl:+.2f} | Banca: ${_state['bankroll']:.2f}\n"
        f"Hoje: ${_state['daily_pnl']:+.2f} | W:{_state['wins']} L:{_state['losses']}"
    )

    # Aguardar resolução do mercado antes do próximo ciclo
    wait = max(0, market["resolution_time"] - time.time()) + 5
    if wait > 0:
        await asyncio.sleep(wait)


async def run_engine():
    """Loop principal — 400+ mercados/dia, opera 50-60 deles."""
    log.info("=" * 60)
    log.info("POLYMARKET BTC 5-MIN BOT iniciando")
    log.info(f"  Banca inicial : ${_state['bankroll']:.2f} USDC")
    log.info(f"  Modo          : {'DRY RUN (simulação)' if DRY_RUN else '⚠️  REAL MONEY'}")
    log.info(f"  Alvo de lucro : {PROFIT_TARGET:.0%} por share")
    log.info(f"  Stop loss     : {STOP_LOSS:.0%} por share")
    log.info(f"  Janela entrada: {ENTRY_WINDOW[0]}-{ENTRY_WINDOW[1]}s após abertura")
    log.info("=" * 60)

    client = None
    if not DRY_RUN:
        client = build_clob_client(PRIVATE_KEY, POLY_API_KEY, POLY_API_SECRET, POLY_PASSPHRASE)
        if not client:
            log.error("Falha ao criar cliente CLOB — verifique as credenciais")
            return

    tg_send(
        f"🟢 <b>Polymarket BTC 5-min Bot online</b>\n"
        f"Banca: ${_state['bankroll']:.2f} USDC\n"
        f"Modo: {'🔵 DRY RUN' if DRY_RUN else '🔴 REAL'}\n"
        f"Use /ajuda para ver os comandos."
    )

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await run_market_cycle(session, client)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"run_market_cycle error: {e}", exc_info=True)
                await asyncio.sleep(10)


# ──────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────

def main():
    if not DRY_RUN and not PRIVATE_KEY:
        log.error("PRIVATE_KEY não configurado. Configure o .env ou use DRY_RUN=true")
        return

    threading.Thread(target=_telegram_polling_loop, daemon=True).start()

    try:
        asyncio.run(run_engine())
    except KeyboardInterrupt:
        log.info("Bot encerrado pelo usuário")
        tg_send(f"🔴 Bot encerrado\nPnL final: ${_state['daily_pnl']:+.2f}")
