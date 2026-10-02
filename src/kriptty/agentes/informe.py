"""Informe diario en Markdown (para Obsidian o para leer en el servidor)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .agente import MODOS, Contexto, Decision
from .riesgo import Alerta


def _modo(m: str) -> str:
    return MODOS.get(m, m)


def _tabla(decisiones: list[Decision]) -> list[str]:
    lin = ["| Cuenta | Bot | Moneda | Modo | Exposición | Motivo | Aplicado | Pendiente de permiso |",
           "|---|---|---|---|---|---|---|---|"]
    for d in decisiones:
        moneda = d.moneda_actual if d.moneda == d.moneda_actual else f"{d.moneda_actual} → {d.moneda}"
        modo = _modo(d.modo_actual) if d.modo == d.modo_actual else f"{_modo(d.modo_actual)} → {_modo(d.modo)}"
        expo = f"{d.expo_actual:g}" if abs(d.expo - d.expo_actual) < 1e-9 else f"{d.expo_actual:g} → {d.expo:g}"
        apl = ", ".join([f"{k}={v}" for k, v in d.cambios.items()] + (["arrancar"] if d.arrancar else [])) or "—"
        pen = ", ".join(f"{k}={v}" for k, v in d.propuesta.items()) or "—"
        lin.append(f"| {d.cuenta} | {d.bot_id} | {moneda} | {modo} | {expo} | {d.motivo} | {apl} | {pen} |")
    return lin


def informe_diario(ctx: Contexto, decisiones: list[Decision], aplicado: bool, permitir_normal: bool,
                   errores: list[str] | None = None) -> str:
    lec = ctx.lectura
    lin = [f"# Agentes Kriptty · {ctx.fecha.isoformat()}", "",
           f"- **Régimen del mercado:** {ctx.regimen}" + (" · **bull run fuerte** (BTC +20% en 30 días)" if ctx.bull_extremo else ""),
           f"- **Miedo y codicia:** {lec.fear_greed if lec.fear_greed is not None else '—'} ({lec.fear_greed_texto or '—'})",
           f"- **Riesgo del día:** {lec.riesgo}" + (" (revisado con Claude)" if lec.revisada_por_llm else " (solo reglas)"),
           f"- **Modo:** {'APLICAR' if aplicado else 'simulación (no se ha cambiado nada)'}"
           + ("" if permitir_normal else " · sin permiso para activar bots: solo cambios que reducen riesgo"),
           ""]
    if lec.resumen:
        lin += ["## Lectura del mercado", "", lec.resumen, ""]
    if lec.vetadas:
        lin += ["## Monedas vetadas hoy", ""] + [f"- **{m}**: {motivo}" for m, motivo in lec.vetadas.items()] + [""]
    for criterio, lista in sorted(ctx.ranking.items()):
        lin.append(f"- Top {criterio}: {', '.join(lista[:8]) or '—'}")
    lin += ["", "## Decisiones por cuenta", ""] + _tabla(decisiones)
    if errores:
        lin += ["", "## Errores", ""] + [f"- {e}" for e in errores]
    if lec.titulares:
        lin += ["", "<details><summary>Titulares leídos</summary>", ""]
        lin += [f"- [{t.fuente}] {t.titulo}" for t in lec.titulares[:60]] + ["", "</details>"]
    return "\n".join(lin) + "\n"


def informe_memes(senales: dict, decisiones: list[Decision], aplicado: bool, errores: list[str] | None = None) -> str:
    lin = [f"### Memecoins · {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC "
           f"({'aplicado' if aplicado else 'simulación'})", ""]
    nombres = {"hype": "Hype (subida + volumen)", "pico": "Pico (ya cae desde el máximo: corto)"}
    for tipo, lista in senales.items():
        if not lista or tipo not in nombres:
            continue
        lin += [f"**{nombres.get(tipo, tipo)}**", "",
                "| Moneda | 24 h | Pico 24 h | Cae desde máx. | Volumen ×media | Volumen 24 h | Nueva | Tendencia |",
                "|---|---|---|---|---|---|---|---|"]
        for s in lista[:10]:
            vr = f"{s.vratio:.1f}" if s.vratio is not None else "—"
            lin.append(f"| {s.coin} | {s.ret24:+.0%} | {s.pico24:+.0%} | {s.caida:.0%} | {vr} | {s.vol24 / 1e6:,.1f} M$ | "
                       f"{'sí' if s.nueva else ''} | {s.tendencia} |")
        lin.append("")
    if not senales.get("hype") and not senales.get("pico"):
        lin += ["Sin señales de hype ni de pico.", ""]
    if senales.get("tendencia_bitget"):
        lin += ["En tendencia y con futuros en Bitget: " + ", ".join(senales["tendencia_bitget"]), ""]
    if senales.get("vigilar"):
        lin += ["En tendencia sin futuros en Bitget (solo para vigilar): " + ", ".join(senales["vigilar"]), ""]
    if decisiones:
        lin += _tabla(decisiones) + [""]
    if errores:
        lin += ["Errores:"] + [f"- {e}" for e in errores] + [""]
    return "\n".join(lin) + "\n"


def informe_vigilancia(alertas: list[Alerta], aplicado: bool) -> str:
    if not alertas:
        return ""
    lin = [f"### Vigilancia de riesgo ({'aplicado' if aplicado else 'simulación'})", ""]
    lin += [f"- {a.cuenta} · bot {a.bot_id} · {a.moneda} {a.lado}: **{a.accion}** — {a.motivo}" for a in alertas]
    return "\n".join(lin) + "\n"


def guardar(texto: str, carpeta: str | Path, nombre: str, anexar: bool = False) -> Path:
    p = Path(carpeta)
    p.mkdir(parents=True, exist_ok=True)
    f = p / nombre
    if anexar and f.exists():
        f.write_text(f.read_text(encoding="utf-8") + "\n" + texto, encoding="utf-8")
    else:
        f.write_text(texto, encoding="utf-8")
    return f
