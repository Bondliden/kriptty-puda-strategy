"""Cliente de la API de administración de Kriptty (Antbot, rama ``agentes-api``).

El token se lee de ``KRIPTTY_ADMIN_TOKEN`` y nunca se guarda en archivos ni informes. En modo simulación
el cliente no envía ningún cambio: solo lee.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


class KripttyError(RuntimeError):
    pass


@dataclass
class BotInfo:
    id: int
    name: str
    symbol: str          # nombre corto: ALGOUSDT
    coin: str            # ALGO
    lm: str
    lwe: float
    sm: str
    swe: float
    leverage: int
    running: bool
    grid_id: int | None


@dataclass
class Posicion:
    symbol: str
    side: str            # long | short (normalizado)
    size: float
    entry_price: float
    unrealised_pnl: float


class Kriptty:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 60.0):
        self.base = base_url.rstrip("/") + "/api/admin"
        self.token = token or os.environ.get("KRIPTTY_ADMIN_TOKEN", "")
        self.timeout = timeout
        if not self.token:
            raise KripttyError("Falta KRIPTTY_ADMIN_TOKEN en el entorno")

    def _req(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/json",
            "Content-Type": "application/json", "User-Agent": "kriptty-agentes/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode() or "null")
        except urllib.error.HTTPError as e:
            detalle = e.read().decode(errors="replace")[:300]
            raise KripttyError(f"{method} {path} → {e.code}: {detalle}") from e
        except urllib.error.URLError as e:
            raise KripttyError(f"{method} {path} → sin conexión: {e.reason}") from e

    # ── lectura ──
    def bots(self, exchange_id: int, ids: list[int] | None = None) -> dict[int, BotInfo]:
        """Bots de una subcuenta. Con ``ids``, solo esos (una subcuenta puede tener más de cien bots y el
        estado se pide de uno en uno)."""
        out = {}
        quiero = set(ids) if ids else None
        for b in self._req("GET", f"/bots?exchange_id={exchange_id}"):
            if quiero is not None and b["id"] not in quiero:
                continue
            st = self._req("GET", f"/bots/{b['id']}/status")
            sym = (st.get("symbol") or "").upper()
            out[b["id"]] = BotInfo(
                id=b["id"], name=b.get("name") or "", symbol=sym, coin=sym[:-4] if sym.endswith("USDT") else sym,
                lm=str(b.get("lm") or "m"), lwe=float(b.get("lwe") or 0), sm=str(b.get("sm") or "m"),
                swe=float(b.get("swe") or 0), leverage=int(b.get("leverage") or 1),
                running=bool(st.get("running")), grid_id=b.get("grid_id"))
        return out

    def posiciones(self, exchange_id: int) -> list[Posicion]:
        out = []
        for p in self._req("GET", f"/exchanges/{exchange_id}/positions"):
            side = str(p.get("side", "")).lower()
            side = "long" if side in ("long", "buy") else "short" if side in ("short", "sell") else side
            sym = str(p.get("symbol", "")).upper().replace("_UMCBL", "").replace("-", "").replace("SWAP", "")
            out.append(Posicion(sym, side, abs(float(p.get("size") or 0)), float(p.get("entry_price") or 0),
                                float(p.get("unrealised_pnl") or 0)))
        return out

    def _simbolos(self, exchange_id: int) -> dict[int, str]:
        """id del símbolo en Kriptty → nombre corto (ALGOUSDT). Se pide una vez por subcuenta."""
        cache = self.__dict__.setdefault("_cache_simbolos", {})
        if exchange_id not in cache:
            cache[exchange_id] = {int(s["id"]): s["nice_name"].upper()
                                  for s in self._req("GET", f"/exchanges/{exchange_id}/symbols")}
        return cache[exchange_id]

    def simbolos(self, exchange_id: int) -> set[str]:
        return set(self._simbolos(exchange_id).values())

    def monedas_en_uso(self, exchange_id: int) -> dict[int, str]:
        """Moneda de **todos** los bots de la subcuenta (también los que no gestionan los agentes): dos bots
        con la misma moneda en la misma subcuenta se pisarían la posición."""
        ids = self._simbolos(exchange_id)
        out = {}
        for b in self._req("GET", f"/bots?exchange_id={exchange_id}"):
            sym = ids.get(int(b.get("symbol_id") or 0), "")
            if sym:
                out[b["id"]] = sym[:-4] if sym.endswith("USDT") else sym
        return out

    def grids(self, user_id: int | None = None) -> list[dict]:
        """Configuraciones de grid guardadas en Kriptty (id, nombre, descripción, usuario, bots que la usan)."""
        return self._req("GET", "/grids" + (f"?user_id={user_id}" if user_id else ""))

    def grid(self, grid_id: int) -> dict:
        """Una configuración con su JSON de Passivbot en ``config``."""
        return self._req("GET", f"/grids/{grid_id}")

    # ── escritura (solo en modo aplicar) ──
    def actualizar_bot(self, bot_id: int, cambios: dict, reiniciar: bool = True) -> dict:
        body = dict(cambios)
        body["restart"] = reiniciar
        return self._req("PUT", f"/bots/{bot_id}", body)

    def arrancar_bot(self, bot_id: int) -> dict:
        return self._req("POST", f"/bots/{bot_id}/start")
