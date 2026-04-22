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
    """
    Busca mercado BTC de 5 minutos ativo.
    Tenta 3 estratégias em sequência:
      1. CLOB API com filtro por slug/keyword "btc"
      2. Gamma API buscando por end_date próximo (mercados de curta duração)
      3. Gamma API com query de texto
    """
    try:
        # Estratégia 1: CLOB API — busca por mercados BTC ativos
        try:
            async with session.get(
                f"{CLOB_API}/markets",
                params={"active": "true", "closed": "false", "limit": 100},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                clob_data = await resp.json()
            clob_markets = clob_data if isinstance(clob_data, list) else clob_data.get("data", [])
            for m in clob_markets:
                q = m.get("question", "").lower()
                slug = m.get("market_slug", m.get("slug", "")).lower()
                if _is_btc_5min(q, slug):
                    log.info(f"[CLOB] Mercado: {m.get('question', '')}")
                    return _parse_market(m)
        except Exception as e:
            log.debug(f"CLOB search failed: {e}")

        # Estratégia 2: Gamma API — mercados com end_date nos próximos 10 minutos
        now = int(time.time())
        end_max = now + 600
        try:
            async with session.get(
                f"{GAMMA_API}/markets",
                params={
                    "limit": 100,
                    "active": "true",
                    "closed": "false",
                    "end_date_max": end_max,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
            markets = data if isinstance(data, list) else data.get("markets", [])
            for m in markets:
                q = m.get("question", "").lower()
                slug = m.get("slug", "").lower()
                if _is_btc_5min(q, slug):
                    log.info(f"[Gamma/enddate] Mercado: {m.get('question', '')}")
                    return _parse_market(m)
        except Exception as e:
            log.debug(f"Gamma end_date search failed: {e}")

        # Estratégia 3: Gamma API com busca por texto
        for query in ["BTC 5", "Bitcoin 5-minute", "BTC above", "BTC below"]:
            try:
                async with session.get(
                    f"{GAMMA_API}/markets",
                    params={"limit": 50, "active": "true", "closed": "false", "_q": query},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                markets = data if isinstance(data, list) else data.get("markets", [])
                for m in markets:
                    q = m.get("question", "").lower()
                    slug = m.get("slug", "").lower()
                    if _is_btc_5min(q, slug):
                        log.info(f"[Gamma/query] Mercado: {m.get('question', '')}")
                        return _parse_market(m)
            except Exception as e:
                log.debug(f"Gamma query '{query}' failed: {e}")

        log.debug("Nenhum mercado BTC 5-min ativo encontrado")
        return None
    except Exception as e:
        log.error(f"get_active_btc_5min_market error: {e}")
        return None


def _is_btc_5min(question: str, slug: str) -> bool:
    """Verifica se o mercado é um BTC de 5 minutos."""
    is_btc = any(x in question or x in slug for x in ["btc", "bitcoin"])
    is_5min = any(x in question or x in slug for x in [
        "5-minute", "5 minute", "5min", "five-minute", "five minute",
        "5-min", "5 min",
    ])
    return is_btc and is_5min


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

async def list_crypto_markets(session: aiohttp.ClientSession, limit: int = 20) -> list:
    """Busca mercados ativos — sem filtro de tag, para diagnóstico real."""
    results = []
    try:
        # CLOB API
        async with session.get(
            f"{CLOB_API}/markets",
            params={"active": "true", "closed": "false", "limit": limit},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
        clob = data if isinstance(data, list) else data.get("data", [])
        results += [f"[CLOB] {m.get('question', m.get('market_slug', '?'))}" for m in clob[:10]]
    except Exception as e:
        results.append(f"[CLOB erro] {e}")

    try:
        # Gamma API sem filtro
        async with session.get(
            f"{GAMMA_API}/markets",
            params={"active": "true", "closed": "false", "limit": limit},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
        gamma = data if isinstance(data, list) else data.get("markets", [])
        results += [f"[Gamma] {m.get('question', '?')}" for m in gamma[:10]]
    except Exception as e:
        results.append(f"[Gamma erro] {e}")

    return results


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
