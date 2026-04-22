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

MARKET_SLUG_BASE = "btc-updown-5m"


async def get_active_btc_5min_market(session: aiohttp.ClientSession) -> Optional[dict]:
    """
    Busca o mercado 'BTC 5 Minute Up or Down' ativo.
    Slug pattern: btc-updown-5m-{timestamp}  — muda a cada 5 minutos.
    """
    try:
        # Estratégia 1: buscar eventos com slug contendo o padrão base
        for param in [{"slug": MARKET_SLUG_BASE}, {"_q": MARKET_SLUG_BASE}, {"q": MARKET_SLUG_BASE}]:
            try:
                async with session.get(
                    f"{GAMMA_API}/events",
                    params={"active": "true", "closed": "false", "limit": 10, **param},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                events = data if isinstance(data, list) else data.get("events", [])
                for ev in events:
                    slug = ev.get("slug", "")
                    if MARKET_SLUG_BASE in slug:
                        log.info(f"[events] Encontrado via {param}: {slug}")
                        markets = ev.get("markets", [])
                        if markets:
                            return _parse_market(markets[0], event=ev)
                        return _parse_market(ev)
            except Exception as e:
                log.debug(f"events {param} failed: {e}")

        # Estratégia 2: construir slug com timestamp atual arredondado a 5 min
        now = int(time.time())
        for offset in [0, 300, -300, 600, -600]:
            candidate_ts = (now + offset) // 300 * 300
            slug = f"{MARKET_SLUG_BASE}-{candidate_ts}"
            try:
                async with session.get(
                    f"{GAMMA_API}/events",
                    params={"slug": slug},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    data = await resp.json()
                events = data if isinstance(data, list) else data.get("events", [])
                if events:
                    ev = events[0]
                    log.info(f"[slug] Encontrado: {slug}")
                    markets = ev.get("markets", [])
                    if markets:
                        return _parse_market(markets[0], event=ev)
                    return _parse_market(ev)
            except Exception as e:
                log.debug(f"slug {slug} failed: {e}")

        # Estratégia 3: buscar mercados com o slug base diretamente
        try:
            async with session.get(
                f"{GAMMA_API}/markets",
                params={"slug": MARKET_SLUG_BASE, "active": "true", "limit": 5},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                data = await resp.json()
            markets = data if isinstance(data, list) else data.get("markets", [])
            if markets:
                log.info(f"[markets/slug] Encontrado: {markets[0].get('slug', '')}")
                return _parse_market(markets[0])
        except Exception as e:
            log.debug(f"markets/slug failed: {e}")

        log.info("Nenhum mercado BTC 5-min ativo encontrado — aguardando 30s")
        return None
    except Exception as e:
        log.error(f"get_active_btc_5min_market error: {e}")
        return None


def _parse_market(raw: dict, event: dict = None) -> dict:
    """Normaliza o objeto de mercado para uso interno."""
    outcomes  = raw.get("outcomes", ["YES", "NO"])
    token_ids = raw.get("clob_token_ids", [None, None])
    if isinstance(token_ids, str):
        try:
            token_ids = json.loads(token_ids)
        except Exception:
            token_ids = [None, None]

    # Tenta extrair token_ids do evento pai se não vier no market
    if (not token_ids or token_ids == [None, None]) and event:
        ev_tokens = event.get("clob_token_ids", [None, None])
        if isinstance(ev_tokens, str):
            try:
                ev_tokens = json.loads(ev_tokens)
            except Exception:
                ev_tokens = [None, None]
        token_ids = ev_tokens

    end_ts = (raw.get("end_date_iso") or raw.get("endDate") or
              (event or {}).get("end_date_iso") or (event or {}).get("endDate"))
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

    # price_to_beat: tenta extrair do texto, depois de outros campos
    question = raw.get("question", "") or (event or {}).get("title", "")
    ptb = (
        _extract_price_to_beat(question) or
        _extract_price_to_beat(raw.get("description", "")) or
        raw.get("startPrice") or
        raw.get("start_price") or
        (event or {}).get("startPrice") or
        None  # será preenchido com preço Binance no ciclo
    )
    if ptb is not None:
        try:
            ptb = float(ptb)
        except (TypeError, ValueError):
            ptb = None

    log.debug(f"_parse_market: question='{question[:60]}' ptb={ptb} tokens={token_ids}")

    return {
        "id":              raw.get("id") or raw.get("conditionId", "") or (event or {}).get("id", ""),
        "question":        question,
        "price_to_beat":   ptb,
        "resolution_time": resolution_time,
        "elapsed_seconds": max(0, time.time() - (resolution_time - 300)),
        "up_token":        token_ids[0] if token_ids and len(token_ids) > 0 else None,
        "down_token":      token_ids[1] if token_ids and len(token_ids) > 1 else None,
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
    """Diagnóstico: busca o mercado btc-updown-5m via slug e eventos."""
    results = []
    now = int(time.time())

    # Tenta slugs com timestamps próximos
    for offset in [0, 300, -300, 600]:
        ts = (now + offset) // 300 * 300
        slug = f"{MARKET_SLUG_BASE}-{ts}"
        try:
            async with session.get(
                f"{GAMMA_API}/events",
                params={"slug": slug},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                data = await resp.json()
            events = data if isinstance(data, list) else data.get("events", [])
            if events:
                ev = events[0]
                results.append(f"[FOUND slug={slug}] {ev.get('title', ev.get('question', '?'))}")
            else:
                results.append(f"[vazio slug={slug}]")
        except Exception as e:
            results.append(f"[erro slug={slug}] {e}")

    # Busca genérica por texto
    for q in ["btc-updown", "BTC 5 Minute", "up or down"]:
        try:
            async with session.get(
                f"{GAMMA_API}/events",
                params={"_q": q, "active": "true", "limit": 3},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                data = await resp.json()
            events = data if isinstance(data, list) else data.get("events", [])
            for ev in events:
                results.append(f"[_q={q}] slug={ev.get('slug','')} | {ev.get('title','?')[:60]}")
        except Exception as e:
            results.append(f"[_q={q} erro] {e}")

    return results or ["Nenhum resultado encontrado"]


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
