"""Servidor MCP (SDK oficial ``mcp`` v2, especificación 2026-07-28).

Sustituye al servidor WebSocket artesanal del diseño original, que NO cumplía
el protocolo MCP (enviaba "tools/list" como notificación con "result", sin
initialize ni esquemas JSON) y por tanto no era usable desde Antigravity,
Claude ni otros clientes MCP.

    kriptty-mcp                          # stdio (recomendado: el cliente lanza el proceso)
    kriptty-mcp --transport streamable-http   # HTTP en MCP_HOST:MCP_PORT/mcp

Por defecto MCP_READ_ONLY=true: las herramientas de escritura ni siquiera se
registran (el LLM no puede verlas). Con MCP_READ_ONLY=false, place_order exige
stop_loss como parámetro obligatorio del esquema y pasa por el mismo
guardián de riesgo que las estrategias.
"""
from __future__ import annotations

import argparse
import logging
from typing import Literal

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from .config import ACCOUNT_DESCRIPTIONS, ACCOUNT_IDS
from .data.news import NewsCollector
from .engine import build_context
from .risk.models import OrderRequest

ctx = build_context()
settings = ctx.settings
READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)

mcp = MCPServer(
    "kriptty",
    instructions=(
        "Sistema de trading Kriptty en Bitget: MAIN + SUB1..SUB11, una estrategia (agente) por subcuenta. "
        f"Modo actual: {settings.trading_mode}. Toda orden requiere stop_loss. "
        + ("Servidor en solo lectura." if settings.mcp_read_only else "Escritura habilitada.")
    ),
)


def _check_account(account_id: str) -> str:
    account_id = account_id.upper()
    if account_id not in ACCOUNT_IDS:
        raise ValueError(f"Cuenta inválida. Usa una de: {ACCOUNT_IDS}")
    return account_id


@mcp.tool(annotations=READ)
async def list_accounts() -> dict:
    """Lista las cuentas del sistema, la estrategia asignada a cada una y su modo."""
    return {"mode": settings.trading_mode, "enabled": sorted(settings.enabled), "accounts": ACCOUNT_DESCRIPTIONS,
            "modes": {acc: settings.mode_for(acc) for acc in ACCOUNT_DESCRIPTIONS}}


@mcp.tool(annotations=READ)
async def get_agents_status() -> list[dict]:
    """Estado de cada agente (una estrategia por subcuenta): modo (dry_run/demo/live), límite de
    capital actual con la rampa, días en el modo, resultado, drawdown y si está lista para graduarse."""
    return ctx.router.agents_status(sorted(settings.enabled, key=lambda a: int(a[3:])))


@mcp.tool(annotations=READ)
async def get_market_data(symbol: str = "BTC/USDT:USDT", timeframe: str = "1h", limit: int = 50) -> dict:
    """Velas OHLCV y ticker. symbol en notación ccxt: 'BTC/USDT:USDT' (perpetuo) o 'BTC/USDT' (spot)."""
    client = ctx.router.client("MAIN")
    df = await client.ohlcv(symbol, timeframe, min(limit, 500), closed_only=False)
    t = await client.ticker(symbol)
    return {"symbol": symbol, "last": t.get("last"), "bid": t.get("bid"), "ask": t.get("ask"),
            "change_24h_pct": t.get("percentage"), "volume_24h_usdt": t.get("quoteVolume"),
            "candles": [{"t": i.isoformat(), **{k: float(v) for k, v in r.items()}} for i, r in df.iterrows()]}


@mcp.tool(annotations=READ)
async def get_funding_rate(symbol: str = "BTC/USDT:USDT") -> dict:
    """Funding rate actual, intervalo real del par y APY anualizado."""
    fr = await ctx.router.client("MAIN").funding_rate(symbol)
    return {**fr, "apy": fr["rate"] * (24 / fr["interval_hours"]) * 365}


@mcp.tool(annotations=READ)
async def get_account_state(account_id: str) -> dict:
    """Equity, posiciones abiertas y últimas órdenes de una cuenta (MAIN, SUB1..SUB9)."""
    account_id = _check_account(account_id)
    client = ctx.router.client(account_id)
    positions = await client.positions()
    return {
        "account": account_id, "strategy": ACCOUNT_DESCRIPTIONS[account_id],
        "equity_futures_usdt": await client.equity("swap"),
        "equity_spot_usdt": await client.equity("spot"),
        "positions": [p.__dict__ for p in positions],
        "strategy_state": ctx.state.all(account_id),
        "recent_orders": ctx.state.recent_journal(20, account_id),
    }


@mcp.tool(annotations=READ)
async def get_macro_signals() -> dict:
    """Dashboard macro compartido por SUB5/SUB9: score ±10, régimen y señales por variable."""
    d = await ctx.macro.get()
    return {"score": d.score, "regime": d.regime, "valid": d.valid, "coverage": d.coverage,
            "signals": d.signals, "values": d.values}


@mcp.tool(annotations=READ)
async def get_sentiment_score(asset: str = "BTC", lookback_hours: float = 6) -> dict:
    """Score de sentimiento de noticias 0 (bajista) – 1 (alcista) para un activo (BTC, ETH, SOL…)."""
    from .strategies.sub1_news_sentiment import NewsSentimentStrategy

    strat = NewsSentimentStrategy(ctx, NewsCollector(settings.cryptopanic_api_key, settings.cryptopanic_plan,
                                                     settings.newsdata_api_key))
    articles = await strat.collector.collect(asset.upper(), lookback_hours)
    return {"asset": asset.upper(), "articles": len(articles), "score": strat.weighted_score(articles),
            "headlines": [a.title[:160] for a in articles[:10]]}


@mcp.tool(annotations=READ)
async def get_order_journal(limit: int = 50, account_id: str | None = None) -> list[dict]:
    """Diario de órdenes (enviadas, rechazadas por el guardián de SL, errores)."""
    return ctx.state.recent_journal(min(limit, 500), _check_account(account_id) if account_id else None)


if not settings.mcp_read_only:

    @mcp.tool(annotations=WRITE)
    async def place_order(account_id: str, symbol: str, side: Literal["buy", "sell"], amount: float,
                          stop_loss: float, order_type: Literal["market", "limit"] = "market",
                          price: float | None = None, take_profit: float | None = None) -> dict:
        """Envía una orden a Bitget. stop_loss es OBLIGATORIO (precio absoluto). amount en activo base."""
        order = OrderRequest(symbol=symbol, side=side, amount=amount, order_type=order_type, price=price,
                             stop_loss=stop_loss, take_profit=take_profit, tag=f"MCP:{account_id.upper()}")
        return await ctx.router.execute(_check_account(account_id), order)

    @mcp.tool(annotations=WRITE)
    async def close_position(account_id: str, symbol: str) -> dict:
        """Cierra a mercado la posición de un perpetuo y cancela sus órdenes."""
        account_id = _check_account(account_id)
        pos = await ctx.router.client(account_id).position(symbol)
        if pos is None:
            return {"status": "sin posición"}
        return await ctx.router.close_position(account_id, pos, "cierre manual vía MCP", "MCP")


def cli() -> None:
    parser = argparse.ArgumentParser(description="Servidor MCP de Kriptty")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    args = parser.parse_args()
    logging.basicConfig(level=settings.log_level)
    if args.transport == "stdio":
        mcp.run("stdio")
    else:
        security = TransportSecuritySettings(
            allowed_hosts=[h.strip() for h in settings.mcp_allowed_hosts.split(",") if h.strip()])
        mcp.run("streamable-http", host=settings.mcp_host, port=settings.mcp_port, transport_security=security,
                stateless_http=settings.mcp_stateless_http,
                session_idle_timeout=settings.mcp_session_idle_timeout)


if __name__ == "__main__":
    cli()
