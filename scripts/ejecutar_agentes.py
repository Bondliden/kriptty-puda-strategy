"""Lanzador de los agentes para el Programador de tareas (o cron): carga ``.env`` de la raíz del repositorio
(``KRIPTTY_ADMIN_TOKEN``, ``ANTHROPIC_API_KEY``, ``YOUTUBE_API_KEY``) y ejecuta ``kriptty-agentes``.

    python scripts/ejecutar_agentes.py diario -c config/agentes.toml

``.env`` está en ``.gitignore``: los secretos nunca entran en git. Lo que haga la orden lo decide
``general.modo`` de la configuración (``simulacion`` = solo informe).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def cargar_env(path: Path) -> None:
    if not path.exists():
        return
    for linea in path.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, v = linea.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


if __name__ == "__main__":
    os.chdir(RAIZ)
    cargar_env(RAIZ / ".env")
    sys.path.insert(0, str(RAIZ / "src"))
    from kriptty.agentes.cli import main

    log = RAIZ / "data" / "agentes_programados.log"
    log.parent.mkdir(exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')} {' '.join(sys.argv[1:])}\n")
    raise SystemExit(main(sys.argv[1:]))
