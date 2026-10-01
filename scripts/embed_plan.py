"""Inserta los datos de estrés, rentabilidad mensual y recomendación en docs/plan-puda(.en).html.

    python scripts/embed_plan.py data/stress_report.json data/monthly_report.json data/recomendacion.json
"""
import json
import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
stress = json.loads(Path(sys.argv[1]).read_text())
monthly = json.loads(Path(sys.argv[2]).read_text())
rec = json.loads(Path(sys.argv[3]).read_text()) if len(sys.argv) > 3 else {}
stress_small = {"scenarios": stress["scenarios"],
                "portfolio": [p for p in stress["portfolio"] if p["cost_mult"] == 1]}
monthly_small = {"summary": monthly["summary"],
                 "paths": [{k: p[k] for k in ("set", "portfolio", "scenario", "seed", "monthly")}
                           for p in monthly["paths"]]}
for name, lang in (("plan-puda.html", "es"), ("plan-puda.en.html", "en")):
    page = DOCS / name
    if not page.exists():
        continue
    data = {"stress": stress_small, "monthly": monthly_small, "rec": rec.get(lang, {})}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    html, n = re.subn(r'<script id="plan-data">.*?</script>',
                      lambda _: f'<script id="plan-data">window.PLAN = {payload};</script>', page.read_text(), flags=re.S)
    if n != 1:
        sys.exit(f"{name}: no se encontró el bloque plan-data")
    page.write_text(html)
    print(f"{name}: datos insertados ({len(payload) / 1024:.0f} KB)")
