"""Línea de comandos de los agentes (``kriptty-agentes``)."""
from __future__ import annotations

import argparse
import logging
import sys

from .config import Config, cargar
from .orquestador import diario, memes, solo_mercado, vigilancia


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kriptty-agentes", description="Agentes diarios de Kriptty")
    p.add_argument("orden", choices=["mercado", "plan", "diario", "vigilar", "memes"])
    p.add_argument("-c", "--config", default="agentes.toml")
    p.add_argument("--llm", action="store_true", help="en «mercado»: revisar noticias con Claude")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if a.orden in ("mercado", "memes"):
        try:
            cfg = cargar(a.config)
        except FileNotFoundError:
            cfg = Config()
        print(solo_mercado(cfg, usar_llm=a.llm) if a.orden == "mercado" else memes(cfg, aplicar=True))
        return 0
    cfg = cargar(a.config)
    if a.orden == "plan":
        texto, _ = diario(cfg, aplicar=False)
    elif a.orden == "diario":
        texto, _ = diario(cfg, aplicar=True)
    else:
        texto = vigilancia(cfg, aplicar=True) or "Vigilancia: sin alertas."
    print(texto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
