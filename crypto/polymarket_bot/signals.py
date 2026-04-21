"""
Módulo de sinais de trading:
  1. Divergência de preço (Binance + Coinbase vs Price to Beat)
  2. Desequilíbrio do order book (smart money detection)
  3. Combinador de sinais + Kelly sizing
"""

import logging
import time
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

PRICE_DELTA_MIN = 50.0   # USD mínimo de divergência nos dois exchanges
IMBALANCE_UP    = 1.8    # ratio bid/ask para sinal bullish
IMBALANCE_DOWN  = 0.55   # ratio bid/ask para sinal bearish
SMART_WINDOW    = (30, 90)  # segundos após abertura para detectar smart money


# ──────────────────────────────────────────────────────────────
# PREÇOS
# ──────────────────────────────────────────────────────────────

async def get_external_prices(session: aiohttp.ClientSession) -> dict:
    """Busca preço BTC em Binance e Coinbase simultaneamente."""
    import asyncio

    async def fetch_binance():
        try:
            url = "https://api.binance.com/api/v3/ticker/price"
            async with session.get(url, params={"symbol": "BTCUSDT"}, timeout=aiohttp.ClientTimeout(total=5)) as r:
                data = await r.json()
                return float(data["price"])
        except Exception as e:
            log.warning(f"Binance price error: {e}")
            return None

    async def fetch_coinbase():
        try:
            url = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                data = await r.json()
                return float(data["data"]["amount"])
        except Exception as e:
            log.warning(f"Coinbase price error: {e}")
            return None

    binance, coinbase = await asyncio.gather(fetch_binance(), fetch_coinbase())

    return {
        "binance":   binance,
        "coinbase":  coinbase,
        "timestamp": time.time(),
    }


# ──────────────────────────────────────────────────────────────
# ORDER BOOK
# ──────────────────────────────────────────────────────────────

def calculate_imbalance(order_book: dict, depth: int = 10) -> Optional[float]:
    """
    Ratio bid_depth / ask_depth nos top N níveis.
    > 1.8 = pressão compradora | < 0.55 = pressão vendedora
    """
    bids = order_book.get("bids", [])
    asks = order_book.get("asks", [])

    if not bids or not asks:
        return None

    def total_size(orders, n):
        total = 0.0
        for o in orders[:n]:
            size = o.get("size") or o.get("quantity") or 0
            try:
                total += float(size)
            except (TypeError, ValueError):
                pass
        return total

    bid_depth = total_size(bids, depth)
    ask_depth = total_size(asks, depth)

    if ask_depth == 0:
        return 9.99

    return round(bid_depth / ask_depth, 3)


def detect_smart_entry(imbalance_history: list) -> Optional[dict]:
    """
    Smart money entra entre 30-90 segundos após abertura do mercado.
    Se o imbalance disparar nessa janela, é sinal.
    """
    early = [
        ib for ib in imbalance_history
        if SMART_WINDOW[0] <= ib["elapsed"] <= SMART_WINDOW[1]
    ]

    if not early:
        return None

    max_ib = max(ib["ratio"] for ib in early)
    min_ib = min(ib["ratio"] for ib in early)

    if max_ib >= IMBALANCE_UP:
        return {
            "direction":  "UP",
            "strength":   max_ib,
            "confidence": min(max_ib / 2.5, 0.95),
        }
    if min_ib <= IMBALANCE_DOWN:
        inv = 1 / min_ib if min_ib > 0 else 9.99
        return {
            "direction":  "DOWN",
            "strength":   inv,
            "confidence": min(inv / 2.5, 0.95),
        }

    return None


# ──────────────────────────────────────────────────────────────
# COMBINADOR DE SINAIS
# ──────────────────────────────────────────────────────────────

def should_trade(
    price_data:        dict,
    order_book:        dict,
    market:            dict,
    imbalance_history: list,
    bankroll:          float,
) -> Optional[dict]:
    """
    Só opera quando AMBOS os sinais concordam:
      1. Binance E Coinbase divergem ≥ $50 do Price to Beat na mesma direção
      2. Order book confirma a direção (smart money já entrou)

    Retorna None se nenhum sinal claro, ou dict com direction/size/confidence.
    """
    ptb = market.get("price_to_beat")
    if ptb is None:
        log.warning("price_to_beat ausente no mercado, pulando.")
        return None

    binance  = price_data.get("binance")
    coinbase = price_data.get("coinbase")

    if binance is None or coinbase is None:
        log.warning("Preços externos indisponíveis, pulando.")
        return None

    b_delta = binance  - ptb
    c_delta = coinbase - ptb

    exchanges_agree = (
        (b_delta > PRICE_DELTA_MIN  and c_delta > PRICE_DELTA_MIN) or
        (b_delta < -PRICE_DELTA_MIN and c_delta < -PRICE_DELTA_MIN)
    )

    if not exchanges_agree:
        log.debug(f"Sem divergência clara. Binance Δ={b_delta:+.0f} Coinbase Δ={c_delta:+.0f}")
        return None

    direction = "UP" if b_delta > 0 else "DOWN"

    imbalance = calculate_imbalance(order_book)
    book_entry = detect_smart_entry(imbalance_history)

    book_confirms = (
        (imbalance is not None and (
            (direction == "UP"   and imbalance >= IMBALANCE_UP) or
            (direction == "DOWN" and imbalance <= IMBALANCE_DOWN)
        )) or
        (book_entry is not None and book_entry["direction"] == direction)
    )

    if not book_confirms:
        log.debug(f"Divergência sem confirmação do book. Imbalance={imbalance} dir={direction}")
        return None

    avg_delta   = (abs(b_delta) + abs(c_delta)) / 2
    ib_strength = abs(imbalance - 1.0) if imbalance is not None else 0.5
    confidence  = min(0.95, 0.60 + avg_delta / 500 + ib_strength / 5)

    token_id = market["up_token"] if direction == "UP" else market["down_token"]

    return {
        "direction":  direction,
        "confidence": round(confidence, 3),
        "size":       kelly_size(confidence, bankroll),
        "token_id":   token_id,
        "b_delta":    round(b_delta, 2),
        "c_delta":    round(c_delta, 2),
        "imbalance":  imbalance,
    }


def kelly_size(confidence: float, bankroll: float, max_pct: float = 0.05) -> float:
    """
    Fração de Kelly conservadora: f = (p - q) / 1 ≈ 2p - 1
    Limitada a max_pct da banca para gestão de risco.
    """
    p = confidence
    q = 1 - p
    kelly_fraction = p - q            # full Kelly
    half_kelly      = kelly_fraction / 2  # metade do Kelly (mais conservador)

    capped = min(half_kelly, max_pct)
    capped = max(capped, 0.01)        # mínimo 1% da banca

    return round(bankroll * capped, 2)
