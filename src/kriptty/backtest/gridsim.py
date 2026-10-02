"""Simulador rápido de grids tipo Passivbot (neat y recursive) sobre velas de 1 hora.

Aproximación al comportamiento de Passivbot para comparar configuraciones sobre años de histórico:

* **neat**: ``n_entries`` compras repartidas linealmente en ``grid_span`` por debajo (largo) o por encima
  (corto) de la primera entrada; cada compra es ``eqty_exp_base`` veces la anterior y el grid entero
  suma ``wel`` × capital de nocional.
* **recursive**: primera entrada de ``initial_qty_pct`` × ``wel`` × capital; cada reentrada se coloca a
  ``rentry_dist`` × (1 + ``rentry_weight`` × exposición/wel) del precio medio y compra
  ``ddown_factor`` × la posición, sin pasar de ``wel``.
* **cierres**: ``n_close`` órdenes repartidas entre ``min_markup`` y ``min_markup + markup_range`` sobre
  el precio medio; se recolocan tras cada compra (como Passivbot).
* **stop loss** opcional a ``sl`` del precio medio y **liquidación** con margen aislado a ``leverage``.
* Comisiones: maker en las órdenes límite (entradas y cierres del grid), taker en la primera entrada,
  el stop y la liquidación. Funding en cada pago, sobre el nocional de la posición.

Con velas de 1 hora no se ve el recorrido dentro de la hora: se asume que el precio va primero al
extremo contrario al cierre (si la vela cierra arriba: apertura → mínimo → máximo → cierre) y cada
vela completa como mucho un ciclo. Un bot real que opera a segundos cierra **más** ciclos: la
estimación es conservadora en ciclos y realista en atascos y stops.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MAKER, TAKER, MMR = 0.0002, 0.0006, 0.005


@dataclass(frozen=True)
class GridCfg:
    mode: str = "neat"              # "neat" | "recursive"
    side: str = "long"              # "long" | "short"
    wel: float = 0.1                # exposición máxima (nocional / capital del bot)
    leverage: float = 7.0
    sl: float | None = 0.06         # stop desde el precio medio (None = sin stop)
    cooldown_h: int = 24            # horas sin abrir tras un stop o una liquidación
    # neat
    grid_span: float = 0.025
    n_entries: int = 6
    eqty_exp_base: float = 2.15
    eprice_exp_base: float = 1.0    # 1 = entradas equidistantes; > 1 = cada vez más separadas
    # recursive
    initial_qty_pct: float = 0.02
    ddown_factor: float = 0.8
    rentry_dist: float = 0.015
    rentry_weight: float = 1.0
    # cierres
    min_markup: float = 0.01
    markup_range: float = 0.014
    n_close: int = 6


@dataclass
class SimResult:
    pnl: np.ndarray                 # beneficio acumulado (realizado + no realizado), por hora, en $
    exposure: np.ndarray            # nocional abierto / capital, por hora
    cycles: int = 0
    stops: int = 0
    liquidations: int = 0
    fees: float = 0.0
    funding: float = 0.0
    events: list = field(default_factory=list)   # (hora, tipo, resultado del ciclo en $)


class _Bot:
    def __init__(self, cfg: GridCfg, capital: float):
        self.cfg, self.capital = cfg, capital
        self.long = cfg.side == "long"
        self.sgn = 1 if self.long else -1
        self.cap_notional = cfg.wel * capital
        w = cfg.eqty_exp_base ** np.arange(cfg.n_entries)
        self.neat_w = w / w.sum()
        self.qty = self.avg = 0.0
        self.entries: list[tuple[float, float]] = []   # (precio, nocional), la más cercana primero
        self.closes: list[tuple[float, float]] = []    # (precio, cantidad)
        self.realized = self.fees = self.funding = 0.0
        self.cycle_start = 0.0

    # ── órdenes ──
    def buy(self, price: float, notional: float, fee: float) -> None:
        q = notional / price
        self.avg = (self.avg * self.qty + price * q) / (self.qty + q) if self.qty > 0 else price
        self.qty += q
        self.fees += notional * fee
        self.realized -= notional * fee

    def sell(self, price: float, q: float, fee: float) -> None:
        q = min(q, self.qty)
        self.realized += self.sgn * (price - self.avg) * q - price * q * fee
        self.fees += price * q * fee
        self.qty -= q
        if self.qty * price < 1e-6 * self.cap_notional:   # restos de redondeo tras el último tramo
            self.qty = 0.0

    def place_closes(self) -> None:
        c, k = self.cfg, self.cfg.n_close
        q = self.qty / k
        self.closes = [(self.avg * (1 + self.sgn * (c.min_markup + (c.markup_range * j / (k - 1) if k > 1 else 0))), q)
                       for j in range(k)]

    def next_recursive(self) -> None:
        c = self.cfg
        notional = self.qty * self.avg
        room = self.cap_notional - notional
        if room <= self.cap_notional * 1e-3:
            self.entries = []
            return
        dist = c.rentry_dist * (1 + c.rentry_weight * notional / self.cap_notional)
        self.entries = [(self.avg * (1 - self.sgn * dist), min(notional * c.ddown_factor, room))]

    def start(self, price: float, scale: float = 1.0) -> None:
        c = self.cfg
        self.cap_notional = c.wel * self.capital * scale     # exposición de este ciclo (lectura del día)
        self.cycle_start = self.realized
        if c.mode == "neat":
            self.buy(price, self.cap_notional * self.neat_w[0], TAKER)
            n, e = c.n_entries, c.eprice_exp_base
            frac = [(i / (n - 1)) if abs(e - 1) < 1e-9 else (e ** i - 1) / (e ** (n - 1) - 1) for i in range(1, n)]
            self.entries = [(price * (1 - self.sgn * c.grid_span * f), self.cap_notional * self.neat_w[i])
                            for i, f in zip(range(1, n), frac, strict=True)]
        else:
            self.buy(price, self.cap_notional * c.initial_qty_pct, TAKER)
            self.next_recursive()
        self.place_closes()

    def flatten(self, price: float, fee: float) -> float:
        self.sell(price, self.qty, fee)
        self.entries, self.closes = [], []
        return self.realized - self.cycle_start

    # ── niveles de riesgo ──
    def sl_price(self) -> float | None:
        return self.avg * (1 - self.sgn * self.cfg.sl) if self.cfg.sl else None

    def liq_price(self) -> float:
        return self.avg * (1 - self.sgn * (1 / self.cfg.leverage - MMR))


class CoinRunner:
    """Un bot sobre una moneda, avanzando vela a vela (para que una cuenta mueva varios a la vez)."""

    def __init__(self, cfg: GridCfg, o, h, lo, c, capital: float, funding=None):
        self.cfg, self.o, self.h, self.lo, self.c, self.funding = cfg, o, h, lo, c, funding
        self.b = _Bot(cfg, capital)
        self.capital = capital
        self.cycles = self.stops = self.liqs = 0
        self.cool_until = -1
        self.events: list = []
        self.graceful_ref: float | None = None    # precio al pasar a graceful stop
        self.graceful_sl: float | None = None     # cerrar si desde ahí sigue en contra este porcentaje
        long = self.b.long
        self.worse = (lambda x, p: x <= p) if long else (lambda x, p: x >= p)
        self.better = (lambda x, p: x >= p) if long else (lambda x, p: x <= p)

    @property
    def open(self) -> bool:
        return self.b.qty > 0

    def close_now(self, i: int, kind: str = "agent") -> None:
        if self.b.qty > 0:
            self.events.append((i, kind, self.b.flatten(self.o[i], TAKER)))
        self.graceful_ref = None

    def set_graceful(self, i: int, sl_after: float | None) -> None:
        """Fase 1: no abrir más ciclos; la posición sigue con sus ventas. Fase 2: si el precio sigue en
        contra ``sl_after`` desde este momento, se cierra."""
        if self.b.qty > 0 and self.graceful_ref is None:
            self.graceful_ref, self.graceful_sl = self.o[i], sl_after

    def step(self, i: int, allow: bool, scale: float = 1.0) -> float:
        """Procesa la vela i y devuelve el resultado acumulado (realizado + no realizado) en $."""
        b, cfg, o, h, lo, c = self.b, self.cfg, self.o, self.h, self.lo, self.c
        worse, better = self.worse, self.better
        if b.qty <= 0:
            self.graceful_ref = None
        if b.qty > 0 and self.graceful_ref is not None and self.graceful_sl:
            lim = self.graceful_ref * (1 - b.sgn * self.graceful_sl)
            x = lo[i] if b.long else h[i]
            if worse(x, lim):
                gap = worse(o[i], lim)
                self.events.append((i, "graceful_sl", b.flatten(o[i] if gap else lim, TAKER)))
                self.stops += 1
                self.graceful_ref = None
                self.cool_until = i + cfg.cooldown_h
        if b.qty <= 0 and allow and i >= self.cool_until and scale > 0:
            b.start(o[i], scale)
        if b.qty > 0:
            adverse_x, favour_x = (lo[i], h[i]) if b.long else (h[i], lo[i])
            up = c[i] >= o[i]
            path = ("adv", "fav") if (up if b.long else not up) else ("fav", "adv")
            for leg in path:
                if b.qty <= 0:
                    break
                if leg == "adv":
                    x = adverse_x
                    while b.entries and worse(x, b.entries[0][0]):
                        sl = b.sl_price()
                        if sl is not None and worse(b.entries[0][0], sl):
                            break                      # el stop está antes que la siguiente compra
                        p, notional = b.entries.pop(0)
                        b.buy(p, notional, MAKER)
                        if cfg.mode == "recursive":
                            b.next_recursive()
                        b.place_closes()
                    sl, liq = b.sl_price(), b.liq_price()
                    if sl is not None and worse(x, sl) and not worse(sl, liq):
                        gap = worse(o[i], sl)
                        self.events.append((i, "sl", b.flatten(o[i] if gap else sl, TAKER)))
                        self.stops += 1
                        self.cool_until = i + cfg.cooldown_h
                        break
                    if worse(x, liq):
                        self.events.append((i, "liq", b.flatten(liq, 0.0)))
                        self.liqs += 1
                        self.cool_until = i + cfg.cooldown_h
                        break
                else:
                    x = favour_x
                    rest = []
                    for j, (p, q) in enumerate(b.closes):
                        if b.qty > 0 and better(x, p):
                            b.sell(p, b.qty if j == len(b.closes) - 1 else q, MAKER)   # el último cierra todo
                        else:
                            rest.append((p, q))
                    b.closes = rest
                    if b.qty <= 0:
                        b.entries, b.closes = [], []
                        self.events.append((i, "tp", b.realized - b.cycle_start))
                        self.cycles += 1
                        break
            if self.funding is not None and self.funding[i] and b.qty > 0:
                pay = b.sgn * self.funding[i] * b.qty * c[i]
                b.funding += pay
                b.realized -= pay
        return b.realized + (b.sgn * (c[i] - b.avg) * b.qty if b.qty > 0 else 0.0)

    def exposure(self, i: int) -> float:
        return self.b.qty * self.c[i] / self.capital if self.b.qty > 0 else 0.0


def simulate(cfg: GridCfg, o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray,
             allow: np.ndarray, capital: float, funding: np.ndarray | None = None,
             force_close: np.ndarray | None = None) -> SimResult:
    """Simula un bot sobre una moneda.

    ``allow[i]``: se puede **empezar** un ciclo en la vela i (el agente tiene esta moneda y este modo).
    Si deja de estar permitido, la posición abierta se sigue gestionando hasta cerrarse por sus ventas o
    su stop («graceful stop»). ``force_close[i]``: cerrar a mercado al abrir la vela i.
    ``funding[i]``: tasa pagada al cierre de la vela i (0 si no hay pago).
    """
    n = len(c)
    r = CoinRunner(cfg, o, h, lo, c, capital, funding)
    pnl, expo = np.zeros(n), np.zeros(n)
    for i in range(n):
        if force_close is not None and force_close[i]:
            r.close_now(i)
        pnl[i] = r.step(i, bool(allow[i]))
        expo[i] = r.exposure(i)
    return SimResult(pnl, expo, r.cycles, r.stops, r.liqs, r.b.fees, r.b.funding, r.events)
