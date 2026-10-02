"""Configuración de los agentes (``agentes.toml``): una entrada por cuenta, con su estrategia y sus bots.

Las estrategias tienen valores por defecto sacados del backtest de 6 años (``docs/SISTEMA_GRIDS.md``);
cada cuenta puede sobrescribirlos.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .senales import CRITERIOS, REGIMENES

# Valores por defecto de cada estrategia (los que mejor aguantaron 2024–2026 en el backtest)
ESTRATEGIAS: dict[str, dict] = {
    "recursive_vasos": {"lado": "long", "criterio": "lag_long", "regimenes": ["alcista"], "incertidumbre": True,
                        "graceful_sl": 0.08, "modo_grid": "recursive"},
    "recursive_momentum": {"lado": "long", "criterio": "momentum", "regimenes": ["alcista"], "incertidumbre": True,
                           "graceful_sl": 0.08, "modo_grid": "recursive"},
    "cortos_vasos": {"lado": "short", "criterio": "lag_short", "regimenes": ["bajista"], "incertidumbre": False,
                     "graceful_sl": 0.08, "modo_grid": "neat"},
    # el scalper neat siempre encendido pierde en altcoins: solo en mercado lateral y con stop escalonado
    "scalper_lateral": {"lado": "long", "criterio": "scalper", "regimenes": ["lateral"], "incertidumbre": False,
                        "graceful_sl": 0.05, "modo_grid": "neat"},
}


@dataclass
class Cuenta:
    nombre: str
    estrategia: str
    bots: list[int]
    lado: str = "long"
    criterio: str = "lag_long"
    regimenes: list[str] = field(default_factory=lambda: ["alcista"])
    incertidumbre: bool = False
    graceful_sl: float = 0.08          # en graceful stop: si el precio sigue en contra este %, Panic
    stop_catastrofe: float = 0.15      # siempre: si el precio va este % en contra del precio de entrada, Panic
    grid_id: int | None = None         # configuración de Kriptty para esta estrategia (opcional)
    exposicion: dict[str, float] | None = None   # por régimen; si falta, la general
    exchange_id: int | None = None     # subcuenta de Kriptty; si falta, la general

    def validar(self) -> None:
        if self.lado not in ("long", "short"):
            raise ValueError(f"{self.nombre}: lado debe ser long o short")
        if self.criterio not in CRITERIOS:
            raise ValueError(f"{self.nombre}: criterio desconocido {self.criterio}")
        bad = [r for r in self.regimenes if r not in REGIMENES]
        if bad:
            raise ValueError(f"{self.nombre}: regímenes desconocidos {bad}")
        if not self.bots:
            raise ValueError(f"{self.nombre}: sin bots asignados")


@dataclass
class Config:
    kriptty_url: str = "https://kripttygold.com"
    exchange_id: int = 0
    modo: str = "simulacion"           # simulacion | aplicar
    permitir_normal: bool = False      # el agente puede poner bots en Normal (dinero real). Lo decide una persona.
    usar_llm: bool = True
    top_marketcap: int = 200
    exigir_kraken: bool = True         # solo monedas listadas en Kraken
    excluir_memes: bool = True         # fuera las memecoins (sin proyecto detrás)
    lista_blanca: list[str] = field(default_factory=list)
    lista_negra: list[str] = field(default_factory=list)
    youtube: dict[str, str] = field(default_factory=dict)
    exposicion: dict[str, float] = field(default_factory=lambda: {
        "lateral": 0.08, "alcista": 0.07, "incertidumbre": 0.04, "bajista": 0.04})
    informe_dir: str = "informes"
    estado: str = "estado_agentes.json"
    cuentas: list[Cuenta] = field(default_factory=list)

    @property
    def aplicar(self) -> bool:
        return self.modo == "aplicar"

    def exchange_de(self, cuenta: Cuenta) -> int:
        return cuenta.exchange_id if cuenta.exchange_id is not None else self.exchange_id

    def exchanges(self) -> dict[int, list[int]]:
        """Subcuenta → bots de las cuentas que operan en ella."""
        out: dict[int, list[int]] = {}
        for c in self.cuentas:
            out.setdefault(self.exchange_de(c), []).extend(c.bots)
        return out


def cargar(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    g = raw.get("general", {})
    cfg = Config(
        kriptty_url=g.get("kriptty_url", Config.kriptty_url).rstrip("/"),
        exchange_id=int(g.get("exchange_id", 0)),
        modo=g.get("modo", "simulacion"),
        permitir_normal=bool(g.get("permitir_normal", False)),
        usar_llm=bool(g.get("usar_llm", True)),
        top_marketcap=int(g.get("top_marketcap", 200)),
        exigir_kraken=bool(g.get("exigir_kraken", True)),
        excluir_memes=bool(g.get("excluir_memes", True)),
        lista_blanca=[s.upper() for s in g.get("lista_blanca", [])],
        lista_negra=[s.upper() for s in g.get("lista_negra", [])],
        youtube=dict(g.get("youtube", {})),
        informe_dir=g.get("informe_dir", "informes"),
        estado=g.get("estado", "estado_agentes.json"),
    )
    if "exposicion" in raw:
        cfg.exposicion.update({k: float(v) for k, v in raw["exposicion"].items()})
    if cfg.modo not in ("simulacion", "aplicar"):
        raise ValueError("general.modo debe ser 'simulacion' o 'aplicar'")
    usados: set[int] = set()
    for c in raw.get("cuentas", []):
        base = dict(ESTRATEGIAS.get(c["estrategia"], {}))
        base.pop("modo_grid", None)
        base.update({k: v for k, v in c.items() if k not in ("estrategia", "nombre", "bots")})
        if c["estrategia"] not in ESTRATEGIAS and "criterio" not in c:
            raise ValueError(f"{c['nombre']}: estrategia desconocida {c['estrategia']} (y sin criterio propio)")
        cuenta = Cuenta(nombre=c["nombre"], estrategia=c["estrategia"], bots=[int(b) for b in c["bots"]], **base)
        cuenta.validar()
        if cfg.exchange_de(cuenta) <= 0:
            raise ValueError(f"{cuenta.nombre}: falta exchange_id (en [general] o en la cuenta)")
        repetidos = usados & set(cuenta.bots)
        if repetidos:
            raise ValueError(f"Bots asignados a más de una cuenta: {sorted(repetidos)}")
        usados |= set(cuenta.bots)
        cfg.cuentas.append(cuenta)
    return cfg
