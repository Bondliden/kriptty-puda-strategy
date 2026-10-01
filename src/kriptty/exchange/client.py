"""Cliente de Bitget por cuenta, construido sobre ccxt (async).

Sustituye al cliente REST artesanal del diseño original (firma HMAC, retry,
parseo de respuestas). ccxt 4.5.x mantiene: firma v2 y UTA v3, Demo Trading
(cabecera paptrading), precisión/mínimos por mercado, rate limiting y los
nombres correctos de campos (p. ej. presetStopSurplusPrice para el TP).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import ccxt.async_support as ccxt
import pandas as pd

from ..config import Credentials, Settings
from ..indicators import ohlcv_to_df
from ..risk.models import OrderRequest, Position

log = logging.getLogger(__name__)

TIMEFRAME_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
                "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000}


def perp(base: str) -> str:
    """"BTC" o "BTCUSDT" -> "BTC/USDT:USDT" (perpetuo USDT-M)."""
    base = base.upper().removesuffix("USDT").removesuffix("/")
    return f"{base}/USDT:USDT"


def spot(base: str) -> str:
    base = base.upper().removesuffix("USDT").removesuffix("/")
    return f"{base}/USDT"


# Bitget lista perpetuos USDT-M de acciones, metales e índices (RWA) con el mismo formato
# que los cripto (p. ej. NVDA/USDT:USDT, XAU/USDT:USDT). Tienen horario, gaps y topes de
# funding propios, así que quedan fuera de los universos cripto de SUB2 y SUB6.
NON_CRYPTO_BASES = {
    "XAU", "XAG", "XPT", "XPD", "XCU", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "GOOG", "META",
    "NFLX", "AMD", "INTC", "COIN", "MSTR", "HOOD", "PLTR", "CRCL", "SPY", "QQQ", "NDX", "DJI", "US30",
    "NAS100", "SPX500", "USOIL", "UKOIL", "WTI", "BRENT", "NATGAS", "EURUSD", "GBPUSD", "USDJPY",
}
_RWA_FLAGS = ("isRwa", "isRWA", "rwa")
_NON_CRYPTO_TYPES = {"stock", "stocks", "equity", "metal", "metals", "index", "indices", "forex", "commodity"}


def is_crypto_market(symbol: str, market: dict | None = None, extra_excluded: set[str] | None = None) -> bool:
    """False para perpetuos de acciones/metales/índices (por la info del mercado o por el activo base)."""
    base = symbol.split("/")[0].upper()
    if base in NON_CRYPTO_BASES or base in (extra_excluded or set()):
        return False
    info = (market or {}).get("info") or {}
    if any(str(info.get(k, "")).upper() in ("YES", "TRUE", "1") for k in _RWA_FLAGS):
        return False
    for key in ("symbolType", "category", "assetType", "underlyingType"):
        if str(info.get(key, "")).lower() in _NON_CRYPTO_TYPES:
            return False
    return True


class ExchangeClient:
    """Una instancia por cuenta/subcuenta (cada una con sus propias API keys)."""

    def __init__(self, account_id: str, credentials: Credentials | None, settings: Settings,
                 mode: str | None = None):
        self.account_id = account_id
        self.settings = settings
        self.mode = mode or settings.trading_mode
        config: dict[str, Any] = {"enableRateLimit": True, "options": {"defaultType": "swap"}}
        if credentials:
            config.update(apiKey=credentials.api_key, secret=credentials.secret,
                          password=credentials.passphrase)
        self.ex = ccxt.bitget(config)
        if self.mode == "demo":
            self.ex.set_sandbox_mode(True)
        if settings.bitget_uta:
            self.ex.options["uta"] = True
        self._markets_lock = asyncio.Lock()
        self._setup_done: set[str] = set()

    # ── Mercado ─────────────────────────────────────────────────────────
    async def load_markets(self) -> None:
        async with self._markets_lock:
            if not self.ex.markets:
                await self.ex.load_markets()

    def market(self, symbol: str) -> dict:
        return self.ex.market(symbol)

    def is_crypto(self, symbol: str) -> bool:
        try:
            market = self.market(symbol)
        except Exception:  # noqa: BLE001 — mercado desconocido: decide solo por el activo base
            market = None
        extra = {b.strip().upper() for b in self.settings.excluded_bases.split(",") if b.strip()}
        return is_crypto_market(symbol, market, extra)

    async def clock_offset_ms(self) -> float:
        """Diferencia (ms) entre el reloj local y el del exchange. Bitget rechaza firmas con
        timestamps desfasados; Freqtrade 2026.9 añadió el mismo aviso."""
        before = time.time() * 1000
        server = float(await self.ex.fetch_time())
        after = time.time() * 1000
        return server - (before + after) / 2

    async def ohlcv(self, symbol: str, timeframe: str, limit: int = 200,
                    closed_only: bool = True) -> pd.DataFrame:
        await self.load_markets()
        rows = await self.ex.fetch_ohlcv(symbol, timeframe, limit=limit + 1)
        df = ohlcv_to_df(rows)
        if closed_only and len(df):
            last_open = int(df.index[-1].timestamp() * 1000)
            if last_open + TIMEFRAME_MS.get(timeframe, 0) > time.time() * 1000:
                df = df.iloc[:-1]  # la vela en curso no está cerrada
        return df.tail(limit)

    async def ticker(self, symbol: str) -> dict:
        await self.load_markets()
        return await self.ex.fetch_ticker(symbol)

    async def last_price(self, symbol: str) -> float:
        t = await self.ticker(symbol)
        return float(t["last"])

    async def tickers(self, market_type: str = "swap") -> dict[str, dict]:
        await self.load_markets()
        return await self.ex.fetch_tickers(params={"type": market_type})

    async def funding_rate(self, symbol: str) -> dict:
        """Tasa actual + intervalo real del par (Bitget ya no usa 8H para todos)."""
        await self.load_markets()
        fr = await self.ex.fetch_funding_rate(symbol)
        interval = fr.get("interval") or "8h"
        hours = float(str(interval).rstrip("h") or 8)
        return {"rate": float(fr.get("fundingRate") or 0.0), "interval_hours": hours,
                "next_ts": fr.get("fundingTimestamp"), "mark": fr.get("markPrice"),
                "index": fr.get("indexPrice")}

    # ── Precisión ───────────────────────────────────────────────────────
    def amount_to_precision(self, symbol: str, amount: float, price: float | None = None) -> float:
        """Trunca a la precisión del mercado; devuelve 0 si queda bajo el mínimo
        (nunca se sube la cantidad: eso multiplicaría el riesgo previsto)."""
        if amount <= 0:
            return 0.0
        try:
            rounded = float(self.ex.amount_to_precision(symbol, amount))
        except (ccxt.InvalidOrder, ccxt.ArgumentsRequired):
            return 0.0
        limits = self.market(symbol).get("limits") or {}
        min_amt = (limits.get("amount") or {}).get("min") or 0
        min_cost = (limits.get("cost") or {}).get("min") or 0
        if rounded < min_amt:
            return 0.0
        if min_cost and price and rounded * price < min_cost:
            return 0.0
        return rounded

    def price_to_precision(self, symbol: str, price: float) -> float:
        return float(self.ex.price_to_precision(symbol, price))

    # ── Cuenta ──────────────────────────────────────────────────────────
    async def equity(self, account: str = "swap") -> float:
        bal = await self.ex.fetch_balance(params={"type": account})
        return float((bal.get("USDT") or {}).get("total") or 0.0)

    async def free(self, coin: str = "USDT", account: str = "swap") -> float:
        bal = await self.ex.fetch_balance(params={"type": account})
        return float((bal.get(coin) or {}).get("free") or 0.0)

    async def holding(self, coin: str) -> float:
        """Saldo spot TOTAL (incluye lo bloqueado por órdenes stop)."""
        bal = await self.ex.fetch_balance(params={"type": "spot"})
        return float((bal.get(coin) or {}).get("total") or 0.0)

    async def positions(self, symbols: list[str] | None = None) -> list[Position]:
        await self.load_markets()
        raw = await self.ex.fetch_positions(symbols)
        out = []
        for p in raw:
            contracts = float(p.get("contracts") or 0)
            if contracts <= 0:
                continue
            size = contracts * float(p.get("contractSize") or 1)
            out.append(Position(
                symbol=p["symbol"], side=p["side"], amount=size,
                entry_price=float(p.get("entryPrice") or 0),
                mark_price=float(p.get("markPrice") or p.get("entryPrice") or 0),
                unrealized_pnl=float(p.get("unrealizedPnl") or 0),
                stop_loss=p.get("stopLossPrice"), take_profit=p.get("takeProfitPrice"),
                extra={"leverage": p.get("leverage"), "liquidation": p.get("liquidationPrice")},
            ))
        return out

    async def position(self, symbol: str) -> Position | None:
        for p in await self.positions([symbol]):
            if p.symbol == symbol:
                return p
        return None

    async def open_orders(self, symbol: str, trigger: bool = False) -> list[dict]:
        await self.load_markets()
        params: dict[str, Any] = {}
        if trigger:
            params = {"trigger": True}
            if ":" in symbol:
                params["planType"] = "profit_loss"
        return await self.ex.fetch_open_orders(symbol, params=params)

    async def ensure_setup(self, symbol: str, leverage: int) -> None:
        """Modo one-way + margen aislado + apalancamiento explícito (el diseño
        original nunca lo configuraba y enviaba tradeSide, válido solo en hedge)."""
        key = f"{symbol}:{leverage}"
        if key in self._setup_done or ":" not in symbol:
            return
        await self.load_markets()
        for name, coro in (
            ("position_mode", lambda: self.ex.set_position_mode(False, symbol)),
            ("margin_mode", lambda: self.ex.set_margin_mode("isolated", symbol)),
            ("leverage", lambda: self.ex.set_leverage(leverage, symbol)),
        ):
            try:
                await coro()
            except ccxt.BaseError as e:  # ya configurado o con posición abierta
                log.debug("[%s] %s %s: %s", self.account_id, name, symbol, e)
        self._setup_done.add(key)

    # ── Órdenes ─────────────────────────────────────────────────────────
    async def place(self, order: OrderRequest) -> dict:
        await self.load_markets()
        params: dict[str, Any] = {}
        if order.client_id:
            params["clientOrderId"] = order.client_id
        if not order.is_spot:
            params["marginMode"] = "isolated"
            if order.reduce_only:
                params["reduceOnly"] = True
            if order.stop_loss is not None:
                params["stopLoss"] = {"triggerPrice": order.stop_loss}
            if order.take_profit is not None:
                params["takeProfit"] = {"triggerPrice": order.take_profit}
        return await self.ex.create_order(order.symbol, order.order_type, order.side,
                                          order.amount, order.price, params)

    async def order_status(self, order_id: str, symbol: str) -> str:
        """"open" | "closed" (ejecutada) | "canceled"."""
        order = await self.ex.fetch_order(order_id, symbol)
        return order.get("status") or "open"

    async def place_spot_stop(self, symbol: str, amount: float, trigger_price: float) -> dict:
        """Orden plan de venta spot (SL de posiciones spot: DCA / pata spot)."""
        await self.load_markets()
        return await self.ex.create_order(symbol, "market", "sell", amount, None,
                                          {"triggerPrice": trigger_price})

    async def cancel(self, order_id: str, symbol: str, trigger: bool = False,
                     plan_type: str | None = None) -> None:
        params: dict[str, Any] = {"trigger": True} if trigger else {}
        if plan_type:
            params["planType"] = plan_type
        await self.ex.cancel_order(order_id, symbol, params)

    async def cancel_all(self, symbol: str) -> None:
        for trigger in (False, True):
            try:
                await self.ex.cancel_all_orders(symbol, params={"trigger": True} if trigger else {})
            except ccxt.BaseError as e:
                log.debug("[%s] cancel_all %s trigger=%s: %s", self.account_id, symbol, trigger, e)

    async def replace_stop_loss(self, position: Position, new_sl: float) -> dict:
        """Mueve el SL de una posición de perpetuo (trailing / breakeven)."""
        for o in await self.open_orders(position.symbol, trigger=True):
            plan = (o.get("info") or {}).get("planType", "")
            if plan in ("loss_plan", "pos_loss"):
                await self.cancel(o["id"], position.symbol, trigger=True, plan_type=plan)
        close_side = "sell" if position.side == "long" else "buy"
        return await self.ex.create_order(position.symbol, "market", close_side, position.amount,
                                          None, {"stopLossPrice": new_sl})

    async def close_position(self, position: Position, tag: str = "") -> dict:
        close_side = "sell" if position.side == "long" else "buy"
        return await self.ex.create_order(position.symbol, "market", close_side, position.amount,
                                          None, {"reduceOnly": True, "marginMode": "isolated"})

    async def transfer(self, coin: str, amount: float, from_account: str, to_account: str) -> dict:
        return await self.ex.transfer(coin, amount, from_account, to_account)

    async def close(self) -> None:
        await self.ex.close()
