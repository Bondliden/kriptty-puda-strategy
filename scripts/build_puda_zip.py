"""Regenera PUDA.zip con el paquete para inversores en inglés y en español y el backtest real.

    python scripts/build_puda_zip.py

Nombres sin acentos ni emojis dentro del zip (Expand-Archive de Windows los rompe). El índice
(INDICE.html) se toma del zip anterior y se le añade la entrada del paquete en español.
"""
from __future__ import annotations

import io
import re
import subprocess
import unicodedata
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "PUDA.zip"
E = ROOT / "estrategia"
EN, ES = "0 - Investor pack (English)", "0 - Paquete para inversores (espanol)"
PLAN = "1 - Plan PUDA (empieza aqui)"

FILES = {
    f"{EN}/1 - PUDA Investor presentation.pdf": E / "investor-pack" / "1 - PUDA Investor presentation.pdf",
    f"{EN}/2 - PUDA Investor update (October 2026).pdf": E / "investor-pack" / "2 - PUDA Investor update (October 2026).pdf",
    f"{EN}/3 - PUDA One-page summary.pdf": E / "investor-pack" / "3 - PUDA One-page summary.pdf",
    f"{EN}/PUDA Investor presentation (web version).html": E / "investor-pack" / "PUDA Investor presentation (web version).html",
    f"{EN}/README.txt": E / "investor-pack" / "README.txt",
    f"{ES}/1 - PUDA Presentacion para inversores.pdf": E / "paquete-inversor" / "1 - PUDA Presentacion para inversores.pdf",
    f"{ES}/2 - PUDA Carta de actualizacion (octubre 2026).pdf": E / "paquete-inversor" / "2 - PUDA Carta de actualizacion (octubre 2026).pdf",
    f"{ES}/3 - PUDA Resumen en una pagina.pdf": E / "paquete-inversor" / "3 - PUDA Resumen en una pagina.pdf",
    f"{ES}/LEEME.txt": E / "paquete-inversor" / "LEEME.txt",
    f"{PLAN}/LEEME.md": E / "LEEME.md",
    f"{PLAN}/Plan PUDA.html": E / "estrategia.html",
    f"{PLAN}/Plan PUDA (English).html": E / "estrategia.en.html",
    f"{PLAN}/Presentacion para inversores.html": E / "Presentacion para inversores.html",
    f"{PLAN}/Presentacion para inversores.pdf": E / "Presentacion para inversores.pdf",
    f"{PLAN}/datos/backtest_real.json": E / "datos" / "backtest_real.json",
    f"{PLAN}/datos/backtest_real_resumen.json": E / "datos" / "backtest_real_resumen.json",
    f"{PLAN}/datos/cobertura_short.json": E / "datos" / "cobertura_short.json",
    f"{PLAN}/datos/ejecuciones.csv": E / "datos" / "ejecuciones.csv",
    f"{PLAN}/datos/mi_estrategia.json": E / "datos" / "mi_estrategia.json",
    f"{PLAN}/datos/rentabilidad_mensual.json": E / "datos" / "rentabilidad_mensual.json",
    "2 - Estrategias del bot/REVISION.md": ROOT / "docs" / "REVISION.md",
    "2 - Estrategias del bot/estrategias-puda.html": ROOT / "docs" / "estrategias-puda.html",
    "2 - Estrategias del bot/presentacion-estrategias.html": ROOT / "docs" / "presentacion-estrategias.html",
    "2 - Estrategias del bot/presentacion-interactiva.html": ROOT / "docs" / "presentacion-interactiva.html",
    "2 - Estrategias del bot/presentacion-interactiva.en.html": ROOT / "docs" / "presentacion-interactiva.en.html",
    "2 - Estrategias del bot/BACKTEST_REAL.md": ROOT / "docs" / "BACKTEST_REAL.md",
    "3 - Lanzamiento del token/estrategia-lanzamiento.html": ROOT / "docs" / "estrategia-lanzamiento.html",
    "3 - Lanzamiento del token/estrategia-lanzamiento.en.html": ROOT / "docs" / "estrategia-lanzamiento.en.html",
    "3 - Lanzamiento del token/lanzamiento-el-salvador.html": ROOT / "docs" / "lanzamiento-el-salvador.html",
    "3 - Lanzamiento del token/lanzamiento-el-salvador.en.html": ROOT / "docs" / "lanzamiento-el-salvador.en.html",
}

LEEME = """PUDA - todo lo preparado a 1 de octubre de 2026
================================================

Abre INDICE.html (doble clic) para ver todos los documentos.

0 - Investor pack (English)          Presentation, investor update and one-page summary, with the 6-year real backtest.
0 - Paquete para inversores (espanol) Lo mismo en español: presentación, carta de actualización y resumen de una página.
1 - Plan PUDA (empieza aqui)          Plan completo en pestañas (ES/EN), presentación para inversores y datos (test de estrés y backtest real).
2 - Estrategias del bot               Presentación interactiva de las estrategias, revisión técnica y cómo se hizo el backtest real.
3 - Lanzamiento del token             Pasos en El Salvador y estrategia de lanzamiento (ES/EN).
4 - Codigo del bot                    Copia del código de GitHub (Bondliden/kriptty-puda-strategy, master).
"""

ES_BLOCK = (
    '<section class="hl"><h2><small>0</small>Paquete para inversores (español)</h2><ul>\n'
    f'<li><a href="{ES}/1 - PUDA Presentacion para inversores.pdf">Presentación para inversores</a> · '
    f'<a href="{ES}/2 - PUDA Carta de actualizacion (octubre 2026).pdf">Carta de actualización (octubre 2026)</a> · '
    f'<a href="{ES}/3 - PUDA Resumen en una pagina.pdf">Resumen en una página</a></li>\n'
    "</ul></section>\n")


def ascii_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip()


def index_html() -> str:
    with zipfile.ZipFile(io.BytesIO(OUT.read_bytes())) as z:
        html = z.read("PUDA/INDICE.html").decode("utf-8")
    html = re.sub(r'<section class="hl"><h2><small>0</small>Paquete para inversores \(español\)</h2>.*?</section>\n', "",
                  html, flags=re.S)
    html = html.replace("Todo lo preparado el 1 de octubre de 2026.",
                        "Todo lo preparado a 1 de octubre de 2026, con el backtest real de 6 años.")
    i = html.index('<section><h2><small>1</small>')
    return html[:i] + ES_BLOCK + html[i:]


def code_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8")
    out = [f for f in out.split("\0") if f]
    skip = ("historico/", "PUDA.zip", "estrategia/investor-pack/src/")
    return [f for f in out if not f.startswith(skip)]


def main() -> None:
    index = index_html()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("PUDA/INDICE.html", index)
        z.writestr("PUDA/LEEME.txt", LEEME)
        for arc, src in FILES.items():
            z.write(src, "PUDA/" + ascii_name(arc))
        for f in code_files():
            z.write(ROOT / f, "PUDA/4 - Codigo del bot/" + ascii_name(f.replace("🤖 ", "")))
    OUT.write_bytes(buf.getvalue())
    with zipfile.ZipFile(OUT) as z:
        names = z.namelist()
    assert all(n.isascii() for n in names), [n for n in names if not n.isascii()]
    print(f"PUDA.zip: {len(names)} archivos, {OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
