"""Inserta el informe del test de estrés en docs/presentacion-interactiva.html.

    kriptty-stress --report antes=data/stress_antes despues=data/stress_despues --out data/stress_report.json
    python scripts/embed_stress.py data/stress_report.json
"""
import re
import sys
from pathlib import Path

HTML = Path(__file__).resolve().parent.parent / "docs" / "presentacion-interactiva.html"
report = Path(sys.argv[1]).read_text()
html = HTML.read_text()
new, n = re.subn(r'<script id="stress-data">.*?</script>',
                 lambda _: f'<script id="stress-data">window.STRESS = {report};</script>', html, flags=re.S)
if n != 1:
    sys.exit("No se encontró el bloque stress-data en el HTML")
HTML.write_text(new)
print(f"{HTML.name}: informe insertado ({len(report) / 1024:.0f} KB)")
