"""Inserta el informe del test de estrés en la presentación interactiva (ES y EN).

    kriptty-stress --report antes=data/stress_antes despues=data/stress_despues --out data/stress_report.json
    python scripts/embed_stress.py data/stress_report.json
"""
import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
PAGES = ["presentacion-interactiva.html", "presentacion-interactiva.en.html"]

report = Path(sys.argv[1]).read_text()
for name in PAGES:
    page = DOCS / name
    if not page.exists():
        continue
    html = page.read_text()
    new, n = re.subn(r'<script id="stress-data">.*?</script>',
                     lambda _: f'<script id="stress-data">window.STRESS = {report};</script>', html, flags=re.S)
    if n != 1:
        sys.exit(f"{name}: no se encontró el bloque stress-data")
    page.write_text(new)
    print(f"{name}: informe insertado ({len(report) / 1024:.0f} KB)")
