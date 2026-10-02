"""Agentes diarios: configuración, decisiones por cuenta, vigilante de riesgo, universo y revisión con Claude.

Todo sin red: las funciones de red se sustituyen con monkeypatch.
"""
from __future__ import annotations

import json
import sys
import types
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from kriptty.agentes import lectura as lectura_mod
from kriptty.agentes import orquestador
from kriptty.agentes.agente import Contexto, decidir_cuenta, exposicion_del_dia
from kriptty.agentes.config import Config, Cuenta, cargar
from kriptty.agentes.informe import informe_diario, informe_vigilancia
from kriptty.agentes.kriptty_api import BotInfo, Posicion
from kriptty.agentes.lectura import Lectura, revisar_con_llm
from kriptty.agentes.mercado import Ticker
from kriptty.agentes.riesgo import vigilar

EJEMPLO = Path(__file__).resolve().parents[1] / "config" / "agentes.example.toml"


# ── utilidades ──

def bot(id_: int, coin: str, lm: str = "m", lwe: float = 0.07, sm: str = "m", swe: float = 0.07,
        running: bool = True, name: str | None = None) -> BotInfo:
    return BotInfo(id=id_, name=name if name is not None else coin, symbol=f"{coin}USDT", coin=coin, lm=lm,
                   lwe=lwe, sm=sm, swe=swe, leverage=7, running=running, grid_id=None)


def pos(coin: str, side: str = "long", size: float = 1.0, entry: float = 100.0) -> Posicion:
    return Posicion(f"{coin}USDT", side, size, entry, 0.0)


def ctx(regimen: str = "alcista", ranking: dict | None = None, riesgo: str = "normal",
        vetadas: dict | None = None) -> Contexto:
    lec = Lectura(fear_greed=50, riesgo=riesgo, vetadas=dict(vetadas or {}))
    return Contexto(date(2026, 10, 2), regimen, lec, ranking or {}, set())


def cuenta_larga(bots: list[int], **kw) -> Cuenta:
    base = dict(nombre="Recursive", estrategia="recursive_vasos", bots=bots, lado="long", criterio="lag_long",
                regimenes=["alcista"], incertidumbre=True)
    base.update(kw)
    return Cuenta(**base)


def cfg_con(*cuentas: Cuenta, permitir_normal: bool = False) -> Config:
    return Config(exchange_id=8, permitir_normal=permitir_normal, cuentas=list(cuentas))


# ── configuración ──

def test_ejemplo_carga_con_valores_de_la_estrategia():
    cfg = cargar(EJEMPLO)
    assert cfg.modo == "simulacion" and cfg.permitir_normal is False
    nombres = {c.estrategia: c for c in cfg.cuentas}
    cortos = nombres["cortos_vasos"]
    assert cortos.lado == "short" and cortos.criterio == "lag_short" and cortos.regimenes == ["bajista"]
    assert nombres["scalper_lateral"].graceful_sl == 0.05
    assert cfg.exchanges() == {101: [1001, 1002], 102: [1003, 1004], 103: [1005, 1006], 104: [1007], 105: [1008, 1009]}
    assert [c.estrategia for c in cfg.cuentas_memes()] == ["meme_pico"]


def test_config_rechaza_bot_en_dos_cuentas(tmp_path):
    p = tmp_path / "a.toml"
    p.write_text('[general]\nexchange_id = 8\n'
                 '[[cuentas]]\nnombre = "A"\nestrategia = "recursive_vasos"\nbots = [1, 2]\n'
                 '[[cuentas]]\nnombre = "B"\nestrategia = "cortos_vasos"\nbots = [2]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="más de una cuenta"):
        cargar(p)


def test_config_rechaza_modo_y_estrategia_desconocidos(tmp_path):
    p = tmp_path / "a.toml"
    p.write_text('[general]\nexchange_id = 8\nmodo = "real"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="modo"):
        cargar(p)
    p.write_text('[general]\nexchange_id = 8\n[[cuentas]]\nnombre = "A"\nestrategia = "inventada"\nbots = [1]\n',
                 encoding="utf-8")
    with pytest.raises(ValueError, match="estrategia desconocida"):
        cargar(p)


def test_config_exige_subcuenta(tmp_path):
    p = tmp_path / "a.toml"
    p.write_text('[[cuentas]]\nnombre = "A"\nestrategia = "recursive_vasos"\nbots = [1]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="exchange_id"):
        cargar(p)


# ── exposición y activación ──

def test_exposicion_por_regimen_y_riesgo_elevado():
    c = cuenta_larga([1])
    cfg = cfg_con(c)
    assert exposicion_del_dia(c, cfg, ctx("alcista")) == 0.07
    assert exposicion_del_dia(c, cfg, ctx("lateral")) == 0.08
    assert exposicion_del_dia(c, cfg, ctx("alcista", riesgo="elevado")) == 0.035


# ── decisiones ──

def test_bot_en_manual_recibe_moneda_y_queda_listo_sin_permiso():
    c = cuenta_larga([1])
    d, = decidir_cuenta(c, cfg_con(c), ctx(ranking={"lag_long": ["ZEC", "BCH"]}), {1: bot(1, "ALGO", lwe=0.05)}, {})
    assert d.moneda == "ZEC" and d.modo == "n"
    # sin permiso: moneda, nombre y exposición se aplican (el bot sigue en manual); Normal queda propuesto
    assert d.cambios == {"symbol": "ZECUSDT", "lwe": 0.07, "name": "ZEC"}
    assert d.propuesta == {"lm": "n"}
    assert d.reiniciar is True and d.arrancar is False


def test_con_permiso_se_aplica_todo_y_arranca_si_estaba_parado():
    c = cuenta_larga([1])
    cfg = cfg_con(c, permitir_normal=True)
    d, = decidir_cuenta(c, cfg, ctx(ranking={"lag_long": ["ZEC"]}), {1: bot(1, "ALGO", running=False)}, {})
    assert d.cambios["lm"] == "n" and d.cambios["symbol"] == "ZECUSDT" and not d.propuesta
    assert d.arrancar is True and d.reiniciar is False


def test_grid_de_la_cuenta_se_prepara_en_bots_parados_y_se_propone_en_los_que_operan():
    c = cuenta_larga([1, 2], grid_id=230)
    bots = {1: bot(1, "ZEC"), 2: bot(2, "BCH", lm="n")}
    bots[1].grid_id = bots[2].grid_id = 181
    ds = {d.bot_id: d for d in decidir_cuenta(c, cfg_con(c), ctx(ranking={"lag_long": ["ZEC", "BCH"]}), bots, {})}
    assert ds[1].cambios.get("grid_id") == 230                     # en manual: se deja preparado
    assert ds[2].propuesta.get("grid_id") == 230 and "grid_id" not in ds[2].cambios
    con_pos = decidir_cuenta(c, cfg_con(c, permitir_normal=True), ctx(ranking={"lag_long": ["ZEC", "BCH"]}),
                             bots, {("BCH", "long"): pos("BCH")})
    assert all("grid_id" not in d.cambios for d in con_pos if d.bot_id == 2)


def test_bot_con_posicion_nunca_cambia_de_moneda():
    c = cuenta_larga([1])
    d, = decidir_cuenta(c, cfg_con(c, permitir_normal=True), ctx(ranking={"lag_long": ["ZEC", "BCH"]}),
                        {1: bot(1, "ALGO", lm="n")}, {("ALGO", "long"): pos("ALGO")})
    assert d.moneda == "ALGO" and d.modo == "gs"
    assert "symbol" not in d.cambios and d.cambios == {"lm": "gs"}


def test_posicion_en_el_lado_contrario_tambien_bloquea_la_moneda():
    c = cuenta_larga([1])
    d, = decidir_cuenta(c, cfg_con(c, permitir_normal=True), ctx(ranking={"lag_long": ["ZEC"]}),
                        {1: bot(1, "ALGO", sm="n")}, {("ALGO", "short"): pos("ALGO", "short")})
    assert d.moneda == "ALGO" and "symbol" not in d.cambios
    assert d.cambios["sm"] == "gs"          # el lado contrario se cierra con calma


def test_histeresis_mantiene_moneda_entre_las_2n_mejores():
    c = cuenta_larga([1])
    d, = decidir_cuenta(c, cfg_con(c), ctx(ranking={"lag_long": ["ZEC", "BCH", "INJ"]}),
                        {1: bot(1, "BCH", lm="n")}, {})
    assert d.moneda == "BCH" and d.modo == "n" and not d.cambios


def test_dos_bots_no_reciben_la_misma_moneda_ni_la_de_otra_cuenta():
    a = cuenta_larga([1, 2], nombre="A")
    b = cuenta_larga([3], nombre="B", criterio="momentum")
    cfg = cfg_con(a, b)
    contexto = ctx(ranking={"lag_long": ["ZEC", "BCH", "INJ", "LTC", "ETC"], "momentum": ["ZEC", "BCH", "SUI"]})
    bots = {1: bot(1, "ALGO"), 2: bot(2, "LDO"), 3: bot(3, "ICP")}
    ocupadas = {"ALGO", "LDO", "ICP", "SUI"}          # SUI la tiene un bot que no gestionan los agentes
    da = decidir_cuenta(a, cfg, contexto, bots, {}, ocupadas)
    db = decidir_cuenta(b, cfg, contexto, bots, {}, ocupadas)
    assert sorted(d.moneda for d in da) == ["BCH", "ZEC"]
    assert db[0].moneda not in {"ZEC", "BCH", "SUI"} and db[0].modo == "m"     # no queda ninguna libre


def test_regimen_fuera_de_la_estrategia_cierra_con_calma():
    c = cuenta_larga([1, 2], incertidumbre=False)
    ds = decidir_cuenta(c, cfg_con(c), ctx("bajista"), {1: bot(1, "ALGO", lm="n"), 2: bot(2, "LDO", lm="n")},
                        {("ALGO", "long"): pos("ALGO")})
    modos = {d.moneda: d.cambios.get("lm") for d in ds}
    assert modos == {"ALGO": "gs", "LDO": "m"}


def test_incertidumbre_opera_btc_y_eth_con_exposicion_baja():
    c = cuenta_larga([1, 2])                       # recursive con incertidumbre = True
    ds = decidir_cuenta(c, cfg_con(c), ctx("incertidumbre", ranking={"lag_long": ["ZEC", "BCH"]}),
                        {1: bot(1, "ALGO"), 2: bot(2, "LDO")}, {})
    assert sorted(d.moneda for d in ds) == ["BTC", "ETH"]
    assert all(d.expo == 0.04 for d in ds)
    sin = cuenta_larga([1], incertidumbre=False)
    d, = decidir_cuenta(sin, cfg_con(sin), ctx("incertidumbre", ranking={"lag_long": ["ZEC"]}), {1: bot(1, "ALGO")}, {})
    assert d.moneda == "ALGO" and d.modo == "m"


def test_riesgo_extremo_apaga_todas_las_cuentas():
    c = cuenta_larga([1])
    d, = decidir_cuenta(c, cfg_con(c), ctx(ranking={"lag_long": ["ALGO"]}, riesgo="extremo"),
                        {1: bot(1, "ALGO", lm="n")}, {})
    assert d.cambios == {"lm": "m"}


def test_moneda_vetada_por_la_lectura():
    c = cuenta_larga([1])
    d, = decidir_cuenta(c, cfg_con(c), ctx(ranking={"lag_long": ["ALGO"]}, vetadas={"ALGO": "hackeo"}),
                        {1: bot(1, "ALGO", lm="n")}, {("ALGO", "long"): pos("ALGO")})
    assert d.cambios == {"lm": "gs"} and "vetada" in d.motivo


def test_panic_en_curso_no_se_toca():
    c = cuenta_larga([1])
    d, = decidir_cuenta(c, cfg_con(c, permitir_normal=True), ctx(ranking={"lag_long": ["ALGO"]}),
                        {1: bot(1, "ALGO", lm="p")}, {("ALGO", "long"): pos("ALGO")})
    assert "lm" not in d.cambios and "Panic" in d.motivo


def test_subir_exposicion_queda_como_propuesta_sin_permiso():
    c = cuenta_larga([1], regimenes=["lateral"])
    d, = decidir_cuenta(c, cfg_con(c), ctx("lateral", ranking={"lag_long": ["ALGO"]}),
                        {1: bot(1, "ALGO", lm="n", lwe=0.04)}, {})
    assert d.propuesta == {"lwe": 0.08} and not d.cambios


def test_cuenta_de_cortos_usa_sm_y_swe():
    c = Cuenta(nombre="Cortos", estrategia="cortos_vasos", bots=[1], lado="short", criterio="lag_short",
               regimenes=["bajista"])
    d, = decidir_cuenta(c, cfg_con(c), ctx("bajista", ranking={"lag_short": ["SUI"]}),
                        {1: bot(1, "XRP", swe=0.15)}, {})
    assert d.cambios == {"symbol": "SUIUSDT", "swe": 0.04, "name": "SUI"} and d.propuesta == {"sm": "n"}


# ── vigilante ──

def test_graceful_registra_referencia_y_luego_panic():
    c = cuenta_larga([1], graceful_sl=0.08)
    cfg = cfg_con(c)
    bots = {1: bot(1, "ALGO", lm="gs")}
    posiciones = {8: {("ALGO", "long"): pos("ALGO", entry=100)}}
    estado: dict = {}
    al = vigilar(cfg, bots, posiciones, {"ALGO": 95.0}, estado)
    assert [a.accion for a in al] == ["registrar_graceful"] and estado["graceful"]["1:long"]["precio"] == 95.0
    assert vigilar(cfg, bots, posiciones, {"ALGO": 90.0}, estado) == []          # −5,3%: aún no
    al = vigilar(cfg, bots, posiciones, {"ALGO": 87.0}, estado)                  # −8,4% desde 95
    assert al[-1].accion == "panic" and al[-1].cambios == {"lm": "p"}


def test_stop_de_catastrofe_desde_la_entrada_en_cortos():
    c = Cuenta(nombre="Cortos", estrategia="cortos_vasos", bots=[1], lado="short", criterio="lag_short",
               regimenes=["bajista"], stop_catastrofe=0.15)
    cfg = cfg_con(c)
    bots = {1: bot(1, "SUI", sm="n")}
    posiciones = {8: {("SUI", "short"): pos("SUI", "short", entry=2.0)}}
    assert vigilar(cfg, bots, posiciones, {"SUI": 2.2}, {}) == []
    al = vigilar(cfg, bots, posiciones, {"SUI": 2.31}, {})
    assert al[0].accion == "panic" and al[0].cambios == {"sm": "p"}


def test_panic_sin_posicion_vuelve_a_manual():
    c = cuenta_larga([1])
    al = vigilar(cfg_con(c), {1: bot(1, "ALGO", lm="p")}, {8: {}}, {"ALGO": 1.0}, {})
    assert al[0].accion == "manual" and al[0].cambios == {"lm": "m"}


# ── universo de monedas ──

def test_universo_filtra_memes_kraken_top_y_listas(monkeypatch):
    tick = {c: Ticker(f"{c}USDT", c, 1.0, 1e6, 0.0)
            for c in ["BTC", "ETH", "ZEC", "PEPE", "1000PEPE", "FARTCOIN", "NEWCOIN", "USDC", "INJ", "LUNA"]}
    monkeypatch.setattr(orquestador.Bitget, "tickers", lambda self: tick)
    monkeypatch.setattr(orquestador, "top_marketcap", lambda n: {c: 50 for c in
                                                                 ["ZEC", "PEPE", "FARTCOIN", "USDC", "INJ", "LUNA"]})
    monkeypatch.setattr(orquestador, "kraken_assets", lambda: {"ZEC", "PEPE", "FARTCOIN", "LUNA", "USDC"})
    monkeypatch.setattr(orquestador, "memes_coingecko", lambda: {"FARTCOIN"})
    cfg = Config(exchange_id=8, lista_negra=["LUNA"])
    disp, _ = orquestador.universo(cfg, orquestador.Bitget())
    assert disp == {"ZEC"}                     # INJ no está en Kraken (en este caso inventado)
    cfg = Config(exchange_id=8, exigir_kraken=False, excluir_memes=False)
    disp, _ = orquestador.universo(cfg, orquestador.Bitget(), {"ZECUSDT", "INJUSDT", "1000PEPEUSDT"})
    assert disp == {"ZEC", "INJ", "1000PEPE"}


def test_kraken_traduce_alias(monkeypatch):
    from kriptty.agentes import mercado
    monkeypatch.setattr(mercado, "_get", lambda url, *a, **k: {"result": {
        "XXBT": {"altname": "XBT"}, "XXDG": {"altname": "XDG"}, "DOT.S": {"altname": "DOT.S"}, "ZEC": {"altname": "ZEC"}}})
    assert mercado.kraken_assets() == {"BTC", "DOGE", "DOT", "ZEC"}


# ── revisión con Claude: solo endurece ──

class _Resp:
    def __init__(self, datos: dict, stop: str = "end_turn"):
        self.stop_reason = stop
        self.content = [types.SimpleNamespace(type="text", text=json.dumps(datos))]


def _anthropic_falso(monkeypatch, datos: dict, stop: str = "end_turn"):
    mod = types.ModuleType("anthropic")

    class _Err(Exception):
        status_code = 500

    for nombre in ("AuthenticationError", "RateLimitError", "APIStatusError", "APIConnectionError", "AnthropicError"):
        setattr(mod, nombre, type(nombre, (_Err,), {}))

    class _Mensajes:
        def create(self, **kw):
            assert kw["model"] == lectura_mod.MODELO
            assert kw["output_config"]["format"]["type"] == "json_schema"
            return _Resp(datos, stop)

    class Anthropic:
        def __init__(self):
            self.beta = types.SimpleNamespace(messages=_Mensajes())

    mod.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-de-prueba")


def test_llm_sube_riesgo_y_veta_solo_candidatas(monkeypatch):
    _anthropic_falso(monkeypatch, {"riesgo": "elevado", "resumen": "hackeo en un puente",
                                   "vetadas": [{"moneda": "ZECUSDT", "motivo": "exploit"},
                                               {"moneda": "XYZ", "motivo": "no es candidata"}]})
    lec = revisar_con_llm(Lectura(), "alcista", ["ZEC", "BCH"])
    assert lec.riesgo == "elevado" and lec.vetadas == {"ZEC": "exploit"} and lec.revisada_por_llm


def test_llm_no_puede_bajar_el_riesgo(monkeypatch):
    _anthropic_falso(monkeypatch, {"riesgo": "normal", "resumen": "", "vetadas": []})
    lec = revisar_con_llm(Lectura(riesgo="elevado"), "alcista", ["ZEC"])
    assert lec.riesgo == "elevado"


def test_llm_rechazo_deja_solo_reglas(monkeypatch):
    _anthropic_falso(monkeypatch, {"riesgo": "extremo", "resumen": "", "vetadas": []}, stop="refusal")
    lec = revisar_con_llm(Lectura(), "alcista", ["ZEC"])
    assert lec.riesgo == "normal" and not lec.revisada_por_llm


# ── informes ──

def test_sin_clave_de_anthropic_solo_reglas(monkeypatch):
    _anthropic_falso(monkeypatch, {"riesgo": "extremo", "resumen": "", "vetadas": []})
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    lec = revisar_con_llm(Lectura(), "alcista", ["ZEC"])
    assert lec.riesgo == "normal" and not lec.revisada_por_llm


def test_informes_se_generan():
    c = cuenta_larga([1])
    contexto = ctx(ranking={"lag_long": ["ZEC"]})
    ds = decidir_cuenta(c, cfg_con(c), contexto, {1: bot(1, "ALGO")}, {})
    texto = informe_diario(contexto, ds, False, False)
    assert "ALGO → ZEC" in texto and "lm=n" in texto
    al = vigilar(cfg_con(c), {1: bot(1, "ALGO", lm="p")}, {8: {}}, {}, {})
    assert "Manual" in informe_vigilancia(al, False)


# ── memecoins: detector, cuenta de cortos tras el pico y cierre por tiempo ──

def _velas(precios: list[float], volumen: list[float], fin: str = "2026-10-02 10:00") -> pd.DataFrame:
    idx = pd.date_range(end=pd.Timestamp(fin, tz="UTC"), periods=len(precios), freq="1h")
    c = pd.Series(precios, index=idx)
    return pd.DataFrame({"open": c.shift().fillna(c.iloc[0]), "high": c * 1.01, "low": c * 0.99, "close": c,
                         "base_vol": 0.0, "quote_vol": volumen}, index=idx)


def test_detector_hype_y_pico():
    from kriptty.agentes.memes import es_hype, es_pico, medir
    ahora = pd.Timestamp("2026-10-02 11:00", tz="UTC")
    # una semana tranquila y un día de +60% con 5 veces el volumen
    df = _velas([1.0] * 180 + [1.0 + 0.025 * i for i in range(1, 25)], [1e5] * 180 + [5e5] * 24)
    m = medir(df, ahora)
    assert m["ret24"] > 0.5 and m["vratio"] > 4 and not m["nueva"]
    assert es_hype(m, R=0.25, V=2.0) and not es_pico(m, R_pico=1.0, D=0.1)
    # dobla en 24 h y ya cae un 15% desde el máximo → pico para cortos
    subida = [1.0 + i / 12 for i in range(1, 13)]
    df = _velas([1.0] * 180 + subida + [2.0 - 0.025 * i for i in range(1, 13)], [1e5] * 180 + [6e5] * 24)
    m = medir(df, ahora)
    assert m["pico24"] >= 1.0 and m["caida"] >= 0.1 and es_pico(m, R_pico=1.0, D=0.1, D_max=0.3)
    assert not es_pico({**m, "caida": 0.76}, R_pico=1.0, D=0.1, D_max=0.3)        # llegaríamos tarde
    # recién listada: 30 horas, +80% desde la primera vela y 40 M$ → hype de moneda nueva
    df = _velas([1.0 + 0.8 * i / 29 for i in range(30)], [1.5e6] * 30)
    m = medir(df, ahora)
    assert m["nueva"] and es_hype(m, R=0.25, V=2.0, nuevas=True) and not es_hype(m, R=0.25, V=2.0, nuevas=False)


def test_cuenta_de_memes_va_aparte_y_opera_cortos_tras_el_pico(tmp_path):
    p = tmp_path / "a.toml"
    p.write_text('[general]\nexchange_id = 8\n'
                 '[[cuentas]]\nnombre = "Momentum"\nestrategia = "recursive_momentum"\nbots = [1]\n'
                 '[[cuentas]]\nnombre = "Memes"\nestrategia = "meme_pico"\nexchange_id = 23\nbots = [7]\n', encoding="utf-8")
    cfg = cargar(p)
    memes_c, = cfg.cuentas_memes()
    assert [c.nombre for c in cfg.cuentas_diarias()] == ["Momentum"]
    assert memes_c.lado == "short" and memes_c.max_horas == 24 and memes_c.stop_catastrofe == 0.20
    contexto = Contexto(date(2026, 10, 2), "alcista", Lectura(), {"pico": ["NEWMEME"]}, {"NEWMEME"})
    d, = decidir_cuenta(memes_c, cfg, contexto, {7: bot(7, "XRP", swe=0.15)}, {})
    assert d.cambios == {"symbol": "NEWMEMEUSDT", "name": "NEWMEME", "swe": 0.4}
    assert d.propuesta == {"sm": "n"}


def test_cierre_por_tiempo_a_las_24_horas():
    from datetime import UTC, datetime, timedelta
    c = Cuenta(nombre="Memes", estrategia="meme_pico", bots=[7], lado="short", criterio="pico",
               regimenes=["alcista"], max_horas=24, stop_catastrofe=0.20)
    cfg = cfg_con(c)
    bots = {7: bot(7, "NEWMEME", sm="n")}
    posiciones = {8: {("NEWMEME", "short"): pos("NEWMEME", "short", entry=1.0)}}
    estado: dict = {}
    t0 = datetime(2026, 10, 2, 10, tzinfo=UTC)
    assert vigilar(cfg, bots, posiciones, {"NEWMEME": 1.0}, estado, ahora=t0) == []
    assert vigilar(cfg, bots, posiciones, {"NEWMEME": 1.0}, estado, ahora=t0 + timedelta(hours=23)) == []
    al = vigilar(cfg, bots, posiciones, {"NEWMEME": 1.0}, estado, ahora=t0 + timedelta(hours=24))
    assert al[0].accion == "panic" and al[0].cambios == {"sm": "p"} and "tiempo" in al[0].motivo
