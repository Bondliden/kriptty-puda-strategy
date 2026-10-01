"""Configuración central (variables de entorno / .env).

Modos de operación (TRADING_MODE):
    dry_run  — datos de mercado reales, órdenes simuladas en memoria (por defecto).
    demo     — Bitget Demo Trading (cabecera paptrading=1): exchange real, fondos ficticios.
    live     — dinero real. Requiere además CONFIRM_LIVE_TRADING=yes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ACCOUNT_IDS = ["MAIN"] + [f"SUB{i}" for i in range(1, 12)]

ACCOUNT_DESCRIPTIONS = {
    "MAIN": "Cuenta principal — gestión de capital",
    "SUB1": "News Sentiment (NLP)",
    "SUB2": "Arbitraje estadístico — vasos comunicantes",
    "SUB3": "Copy trading nativo + guardián de riesgo",
    "SUB4": "Scalping intradía (WebSocket)",
    "SUB5": "Macro-shorting (solo cortos)",
    "SUB6": "Funding rate arbitrage (delta-neutral)",
    "SUB7": "Grid adaptativo (Bollinger + ATR)",
    "SUB8": "DCA inteligente (RSI + EMA200 semanal)",
    "SUB9": "Collar dinámico (exposición neta por régimen macro)",
    "SUB10": "Pairs trading por cointegración (market-neutral)",
    "SUB11": "Seguimiento de tendencia SuperTrend 4H",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    trading_mode: Literal["dry_run", "demo", "live"] = "dry_run"
    confirm_live_trading: str = ""
    bitget_uta: bool = False

    # SUB3 queda fuera por defecto: depende de copy trading nativo y de traderIds manuales.
    # SUB10 y SUB11 (nuevas) también: activarlas tras validarlas con kriptty-backtest.
    enabled_strategies: str = "SUB1,SUB2,SUB4,SUB5,SUB6,SUB7,SUB8,SUB9"

    state_path: str = "data/state.db"
    log_level: str = "INFO"
    dry_run_equity: float = 10_000.0

    # Límites globales de riesgo por cuenta
    max_sl_distance_pct: float = 0.25
    max_daily_loss_pct: float = 0.05
    # Corte por drawdown: si el equity de una subcuenta (spot + futuros) cae más de
    # este % desde su máximo, no abre posiciones durante DRAWDOWN_COOLDOWN_DAYS.
    # Añadido tras el test de estrés de 3 años (SUB7/SUB11 encadenaban pérdidas).
    max_drawdown_pct: float = 0.25
    drawdown_cooldown_days: int = 14
    # Cuentas que acumulan en caídas por diseño (DCA) y quedan fuera del corte.
    drawdown_exempt: str = "SUB8"
    default_leverage: int = 3
    # Activos base excluidos de los universos (además de acciones/metales/índices conocidos).
    excluded_bases: str = ""
    # Aviso si el reloj local se desvía del de Bitget más de esto (ms).
    max_clock_offset_ms: float = 1000.0

    # Datos externos
    cryptopanic_api_key: str = ""
    cryptopanic_plan: str = "developer"
    newsdata_api_key: str = ""
    fred_api_key: str = ""
    coingecko_api_key: str = ""
    use_finbert: bool = False

    # SUB3
    sub3_trader_ids: str = ""

    # MCP
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8765
    mcp_read_only: bool = True
    mcp_allowed_hosts: str = "localhost:8765,127.0.0.1:8765"
    # SDK MCP 2.2 (sept. 2026): las sesiones HTTP con estado se cierran tras 30 min sin
    # actividad y el cliente recibe 404. Las herramientas de Kriptty no guardan estado por
    # sesión, así que por defecto el servidor HTTP es stateless (sin sesiones que caduquen).
    mcp_stateless_http: bool = True
    mcp_session_idle_timeout: float = 1800.0

    @property
    def enabled(self) -> set[str]:
        return {s.strip().upper() for s in self.enabled_strategies.split(",") if s.strip()}

    @property
    def sends_real_orders(self) -> bool:
        return self.trading_mode in ("demo", "live")

    def check_live_guard(self) -> None:
        if self.trading_mode == "live" and self.confirm_live_trading.lower() != "yes":
            raise RuntimeError(
                "TRADING_MODE=live requiere CONFIRM_LIVE_TRADING=yes. "
                "Prueba antes en dry_run y demo."
            )


@dataclass(frozen=True)
class Credentials:
    api_key: str
    secret: str
    passphrase: str

    def __repr__(self) -> str:  # nunca volcar secretos en logs
        return f"Credentials(api_key={self.api_key[:4]}…)"


def load_credentials(account_id: str) -> Credentials | None:
    prefix = f"BITGET_{account_id.upper()}_"
    key = os.getenv(prefix + "API_KEY", "")
    secret = os.getenv(prefix + "SECRET", "")
    passphrase = os.getenv(prefix + "PASSPHRASE", "")
    if key and secret and passphrase:
        return Credentials(key, secret, passphrase)
    return None


@lru_cache
def get_settings() -> Settings:
    from dotenv import load_dotenv  # dependencia de pydantic-settings

    load_dotenv()  # para que load_credentials() vea también las claves del .env
    return Settings()
