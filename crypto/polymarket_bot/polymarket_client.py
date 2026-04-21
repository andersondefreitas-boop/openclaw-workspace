"""
Cliente Polymarket CLOB — busca mercados, order book e executa ordens.
"""

import asyncio
import json
import logging
import time
from typing import Optional

import aiohttp
import websockets

log = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"
CLOB_WS   = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


# ──────────────────────────────────────────────────────────────
# DESCOBERTA DE MERCADO
# ──────────────────────────────────────────────────────────────

async def get_active_btc_5min_market(session: aiohttp.ClientSession) -> Optional[dict]:
    """Retorna o mercado BTC de 5 minutos ativo agora."""
    try:
        params = {"limit": 100, "active": "true", "closed": "false", "tag_slug": "crypto"}
        async with session.get(f"{GAMMA_API}/markets", params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()

        markets = data if isinstance(data, list) else data.get("markets", [])

        for m in markets:
            q = m.get("question", "").lower()
            if "btc" in q and ("5-minute" in q or "5 minute" in q or "5min" in q):
                return _parse_market(m)

        return None
    except Exception as e:
        log.error(f"get_active_btc_5min_market error: {e}")
        return None


def _parse_market(raw: dict) -> dict:
    """Normaliza o objeto de mercado para uso interno."""
    outcomes  = raw.get("outcomes", ["YES", "NO"])
    token_ids = raw.get("clob_token_ids", [None, None])
    if isinstance(token_ids, str):
        try:
            token_ids = json.loads(token_ids)
        except Exception:
            token_ids = [None, None]

    end_ts = raw.get("end_date_iso") or raw.get("endDate")
    if end_ts:
        from datetime import datetime, timezone
        try:
            if isinstance(end_ts, str):
                end_ts = end_ts.rstrip("Z")
                dt = datetime.fromisoformat(end_ts).replace(tzinfo=timezone.utc)
                resolution_time = dt.timestamp()
            else:
                resolution_time = float(end_ts) / 1000
        except Exception:
            resolution_time = time.time() + 300
    else:
        resolution_time = time.time() + 300

    return {
        "id":              raw.get("id") or raw.get("conditionId", ""),
        "question":        raw.get("question", ""),
        "price_to_beat":   _extract_price_to_beat(raw.get("question", "")),
        "resolution_time": resolution_time,
        "elapsed_seconds": max(0, time.time() - (resolution_time - 300)),
        "up_token":        token_ids[0] if len(token_ids) > 0 else None,
        "down_token":      token_ids[1] if len(token_ids) > 1 else None,
        "outcomes":        outcomes,
        "raw":             raw,
    }


def _extract_price_to_beat(question: str) -> Optional[float]:
    """Extrai o preço alvo da pergunta do mercado, ex: 'above $65,000'."""
    import re
    match = re.search(r"\$([0-9,]+(?:\.[0-9]+)?)", question)
    if match:
        return float(match.group(1).replace(",", ""))
    return None


# ──────────────────────────────────────────────────────────────
# ORDER BOOK
# ──────────────────────────────────────────────────────────────

async def get_order_book(session: aiohttp.ClientSession, token_id: str) -> Optional[dict]:
    """Busca o order book via REST para um token específico."""
    try:
        url = f"{CLOB_API}/book"
        params = {"token_id": token_id}
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            data = await resp.json()
        return data
    except Exception as e:
        log.warning(f"get_order_book error ({token_id[:8]}...): {e}")
        return None


async def get_market_prices(session: aiohttp.ClientSession, token_id: str) -> Optional[dict]:
    """Retorna o melhor ask/bid para um token."""
    try:
        url = f"{CLOB_API}/price"
        params = {"token_id": token_id, "side": "buy"}
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            data = await resp.json()
        return data
    except Exception as e:
        log.warning(f"get_market_prices error: {e}")
        return None


async def subscribe_order_book(token_id: str, callback, stop_event: asyncio.Event):
    """
    Abre WebSocket e recebe atualizações do order book em tempo real.
    Chama callback(book_update) para cada mensagem recebida.
    """
    subscribe_msg = json.dumps({
        "auth":    {},
        "type":    "Market",
        "markets": [token_id],
        "assets":  [token_id],
    })

    while not stop_event.is_set():
        try:
            async with websockets.connect(CLOB_WS, ping_interval=20, ping_timeout=30) as ws:
                await ws.send(subscribe_msg)
                log.info(f"WebSocket conectado para token {token_id[:8]}...")

                while not stop_event.is_set():
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30)
                        data = json.loads(msg)
                        await callback(data)
                    except asyncio.TimeoutError:
                        continue
        except Exception as e:
            if not stop_event.is_set():
                log.warning(f"WebSocket desconectado: {e} — reconectando em 3s")
                await asyncio.sleep(3)


# ──────────────────────────────────────────────────────────────
# EXECUÇÃO DE ORDENS
# ──────────────────────────────────────────────────────────────

def build_clob_client(private_key: str, api_key: str, api_secret: str, api_passphrase: str):
    """Instancia o cliente CLOB da Polymarket."""
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.constants import POLYGON

        client = ClobClient(
            host=CLOB_API,
            chain_id=POLYGON,
            key=private_key,
            creds={
                "apiKey":      api_key,
                "secret":      api_secret,
                "passphrase":  api_passphrase,
            },
            signature_type=2,
        )
        return client
    except ImportError:
        log.error("py-clob-client não instalado. Execute: pip install py-clob-client")
        return None
    except Exception as e:
        log.error(f"build_clob_client error: {e}")
        return None


async def place_limit_order(client, token_id: str, price: float, size: float, dry_run: bool = True) -> Optional[dict]:
    """
    Coloca ordem limit de compra no CLOB.
    price  = preço por share em USDC (ex: 0.52)
    size   = quantidade de shares
    """
    if dry_run:
        order_id = f"DRY_{token_id[:6]}_{int(time.time())}"
        log.info(f"[DRY RUN] BUY {size:.2f} shares @ ${price:.3f} | token={token_id[:10]}...")
        return {"id": order_id, "price": price, "size": size, "status": "dry_run"}

    try:
        from py_clob_client.clob_types import OrderArgs, OrderType

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side="BUY",
        )
        resp = client.create_and_post_order(order_args)
        log.info(f"Ordem enviada: {resp}")
        return resp
    except Exception as e:
        log.error(f"place_limit_order error: {e}")
        return None


async def sell_shares(client, token_id: str, price: float, size: float, dry_run: bool = True) -> Optional[dict]:
    """Vende shares no mercado (ordem limit de venda)."""
    if dry_run:
        order_id = f"DRY_SELL_{token_id[:6]}_{int(time.time())}"
        log.info(f"[DRY RUN] SELL {size:.2f} shares @ ${price:.3f} | token={token_id[:10]}...")
        return {"id": order_id, "price": price, "size": size, "status": "dry_run"}

    try:
        from py_clob_client.clob_types import OrderArgs

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side="SELL",
        )
        resp = client.create_and_post_order(order_args)
        log.info(f"Venda enviada: {resp}")
        return resp
    except Exception as e:
        log.error(f"sell_shares error: {e}")
        return None
