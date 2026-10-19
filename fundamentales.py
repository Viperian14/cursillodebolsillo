#!/usr/bin/env python3
"""Fundamentales por empresa (Yahoo Finance vía yfinance) y puntuación del modelo.

Genera fundamentales.json y fundamentales_estado.json.
Cada día se actualiza una séptima parte de las empresas (más las que no tengan datos);
tras cada tanda se recalculan medianas y puntuaciones de TODAS con los datos guardados.

Uso:  pip install yfinance pandas && python fundamentales.py          (tanda diaria)
      FUND_TODO=1 python fundamentales.py                              (carga completa)

Datos con retraso, solo informativos. No es una recomendación de inversión.
"""
import json, os, sys, time, statistics
from datetime import datetime, timezone, date, timedelta
import pandas as pd
import yfinance as yf

# --- Constantes ajustables (hipótesis, no datos validados) ---
MARGEN = 0.15          # a partir de ±15 % frente a la mediana del grupo: medio punto (±0,5)
MARGEN_FUERTE = 0.40   # a partir de ±40 %: punto entero (±1)
ESCALA = 10.0          # la puntuación final va de -10 a +10
MIN_SEÑALES = 3        # señales mínimas para dar puntuación
GRUPO_PEQUEÑO = 5      # menos empresas que esto = "grupo pequeño"
DIAS_ROTACION = 7      # una séptima parte cada día
YIELD_MAX = 25.0       # rentabilidad por dividendo imposible por encima de esto (%)
GUARDAR_CADA = 100     # guardar progreso cada N empresas
PRESUPUESTO_MIN = float(os.environ.get("FUND_MINUTOS", "200"))  # tiempo máximo del job

SECTORES = {
    "Technology": "Tecnología de la información", "Financial Services": "Finanzas",
    "Healthcare": "Salud", "Consumer Cyclical": "Consumo discrecional",
    "Consumer Defensive": "Consumo básico", "Energy": "Energía", "Industrials": "Industriales",
    "Communication Services": "Servicios de comunicación", "Utilities": "Servicios públicos",
    "Real Estate": "Bienes raíces", "Basic Materials": "Materiales",
}
SIN_CLASIFICAR = "Sin clasificar"
FINANZAS = "Finanzas"

F_DATOS, F_ESTADO = "fundamentales.json", "fundamentales_estado.json"


def hoy():
    return datetime.now(timezone.utc).date()


def cargar(ruta, defecto):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return defecto


def guardar(ruta, obj):
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ruta)


def num(x):
    try:
        v = float(x)
        return None if pd.isna(v) or v in (float("inf"), float("-inf")) else v
    except Exception:
        return None


def con_reintentos(fn, intentos=4):
    for i in range(intentos):
        try:
            return fn()
        except Exception as e:
            if i == intentos - 1:
                raise
            time.sleep(2 * (i + 1) ** 2)  # 2, 8, 18 s


def tendencia_de(cierres):
    """alcista / bajista / lateral, o None si hay menos de 200 sesiones."""
    c = pd.Series(cierres).dropna()
    if len(c) < 200:
        return None
    ult, m50, m200 = float(c.iloc[-1]), float(c.iloc[-50:].mean()), float(c.iloc[-200:].mean())
    if ult > m50 > m200:
        return "alcista"
    if ult < m50 < m200:
        return "bajista"
    return "lateral"


def tendencia_voto(cierres):
    """Voto -1 / -0,5 / 0 / +0,5 / +1 según el precio frente a sus medias de 50 y 200 sesiones."""
    c = pd.Series(cierres).dropna()
    if len(c) < 200:
        return None
    ult, m50, m200 = float(c.iloc[-1]), float(c.iloc[-50:].mean()), float(c.iloc[-200:].mean())
    if ult > m50 > m200:
        return 1.0
    if ult < m50 < m200:
        return -1.0
    return 0.5 if ult > m200 else (-0.5 if ult < m200 else 0.0)


def voto_analistas(rec, n):
    """Sentimiento de mercado a partir de la recomendación media de Yahoo (1 = compra fuerte ... 5 = venta)."""
    if rec is None or n is None or n < 3:
        return None
    return 1.0 if rec <= 1.8 else 0.5 if rec <= 2.3 else 0.0 if rec <= 3.2 else -0.5 if rec <= 3.7 else -1.0


def frecuencia_dividendo(divs):
    """(pagos en 12 meses, texto). 0 = no paga."""
    if divs is None or len(divs) == 0:
        return 0, "no paga"
    idx = pd.DatetimeIndex(divs.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    corte = pd.Timestamp(hoy()) - pd.Timedelta(days=365)
    n = int((idx > corte).sum())
    nombres = {0: "no paga", 1: "anual", 2: "semestral", 4: "trimestral", 12: "mensual"}
    return n, nombres.get(n, "irregular")


def resultados(t):
    """Fechas de resultados y cambio de los últimos datos publicados (todo opcional; None si Yahoo no lo da)."""
    r = {"proxima": None, "ultima": None, "bpa_real": None, "bpa_est": None, "sorpresa": None,
         "periodo": None, "ingresos": None, "beneficio": None}
    try:
        ed = con_reintentos(lambda: t.get_earnings_dates(limit=12))
        if ed is not None and len(ed):
            idx = pd.DatetimeIndex(ed.index)
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            ahora = pd.Timestamp(hoy())
            fut = [d for d in idx if d >= ahora]
            if fut:
                r["proxima"] = min(fut).strftime("%Y-%m-%d")
            rep = ed.copy()
            rep.index = idx
            rep = rep[rep["Reported EPS"].notna()] if "Reported EPS" in rep else rep.iloc[0:0]
            rep = rep[rep.index <= ahora + pd.Timedelta(days=1)]
            if len(rep):
                f = rep.sort_index().iloc[-1]
                r["ultima"] = rep.sort_index().index[-1].strftime("%Y-%m-%d")
                r["bpa_real"] = num(f.get("Reported EPS"))
                r["bpa_est"] = num(f.get("EPS Estimate"))
                r["sorpresa"] = num(f.get("Surprise(%)"))
    except Exception:
        pass
    try:
        q = con_reintentos(lambda: t.quarterly_income_stmt)
        if q is not None and len(q.columns):
            q = q[sorted(q.columns, reverse=True)]
            r["periodo"] = pd.Timestamp(q.columns[0]).strftime("%Y-%m-%d")
            for clave, fila in (("ingresos", "Total Revenue"), ("beneficio", "Net Income")):
                if fila in q.index:
                    v = [num(x) for x in q.loc[fila].tolist()]
                    # [último trimestre, trimestre anterior, mismo trimestre del año anterior]
                    r[clave] = {"ultimo": v[0] if len(v) > 0 else None,
                                "anterior": v[1] if len(v) > 1 else None,
                                "año_anterior": v[4] if len(v) > 4 else None}
    except Exception:
        pass
    return r


def analistas(info, ultimo):
    rec, n = num(info.get("recommendationMean")), num(info.get("numberOfAnalystOpinions"))
    obj = num(info.get("targetMeanPrice"))
    pot = None
    if obj and ultimo:
        r = obj / ultimo
        if 0.2 < r < 5:          # descarta monedas distintas (p. ej. peniques frente a libras)
            pot = round((r - 1) * 100, 1)
    return {"recomendacion": rec, "n": None if n is None else int(n), "objetivo": obj, "potencial": pot,
            "voto": voto_analistas(rec, n)}


def obtener(ticker, cierres):
    """Descarga los datos de una empresa. Devuelve dict (campos brutos normalizados)."""
    t = yf.Ticker(ticker)
    info = con_reintentos(lambda: t.info) or {}
    try:
        divs = con_reintentos(lambda: t.dividends)
    except Exception:
        divs = None
    pagos, freq = frecuencia_dividendo(divs)
    ultimo = num(cierres.iloc[-1]) if cierres is not None and len(cierres.dropna()) else None
    rend = None
    if divs is not None and len(divs) and ultimo:
        idx = pd.DatetimeIndex(divs.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        suma = float(divs[idx > pd.Timestamp(hoy()) - pd.Timedelta(days=365)].sum())
        rend = suma / ultimo * 100 if suma > 0 else 0.0
        if rend > YIELD_MAX:
            rend = None
    roe = num(info.get("returnOnEquity"))
    de = num(info.get("debtToEquity"))
    mrq = info.get("mostRecentQuarter")
    if isinstance(mrq, (int, float)) and mrq > 0:
        mrq = datetime.fromtimestamp(mrq, timezone.utc).strftime("%Y-%m-%d")
    web = info.get("website") or ""
    dominio = None
    if isinstance(web, str) and web.strip():
        dominio = web.strip().lower().replace("https://", "").replace("http://", "").split("/")[0]
        dominio = dominio[4:] if dominio.startswith("www.") else dominio
        dominio = dominio or None
    return {
        "dominio": dominio,
        "sector_yahoo": info.get("sector"),
        "per": num(info.get("trailingPE")),
        "eps": num(info.get("trailingEps")),
        "roe": None if roe is None else round(roe * 100, 2),            # fracción -> %
        "deuda": None if de is None else round(de / 100, 3),            # % -> ratio
        "dividendo": None if rend is None else round(rend, 3),          # % calculado con los pagos de 12 meses
        "dividendo_yahoo": num(info.get("dividendYield")),              # tal cual, solo diagnóstico
        "pagos_12m": pagos,
        "frecuencia": freq,
        "trimestre": mrq if isinstance(mrq, str) else None,
        "moneda_yahoo": info.get("currency"),
        "tendencia": tendencia_de(cierres) if cierres is not None else None,
        "tendencia_voto": tendencia_voto(cierres) if cierres is not None else None,
        "analistas": analistas(info, ultimo),
        "volumen_rel": None,
        "resultados": resultados(t),
        "fecha": hoy().isoformat(),
    }


def historicos(tickers):
    """{ticker: serie de cierres} (≈ 14 meses) en lotes."""
    out = {}
    for i in range(0, len(tickers), 100):
        lote = tickers[i:i + 100]
        try:
            d = con_reintentos(lambda: yf.download(lote, period="14mo", interval="1d", auto_adjust=False,
                                                   group_by="column", progress=False, threads=True))
        except Exception as e:
            print(f"Históricos del lote {i // 100 + 1} fallaron: {e}", file=sys.stderr)
            continue
        if d is None or d.empty:
            continue
        close = d["Close"]
        if isinstance(close, pd.Series):
            close = close.to_frame(lote[0])
        for t in close.columns:
            out[t] = close[t].dropna()
        time.sleep(1)
    return out


# ---------------- cálculo de señales ----------------

def mediana(vals):
    v = [x for x in vals if x is not None]
    return statistics.median(v) if v else None


def voto_relativo(valor, med, mas_es_mejor):
    """-1 / -0,5 / 0 / +0,5 / +1 comparando con la mediana. None si no se puede calcular."""
    if valor is None or med is None or med <= 0:
        return None
    r = valor / med
    alto = 1.0 if r > 1 + MARGEN_FUERTE else 0.5 if r > 1 + MARGEN else 0.0   # por encima de la mediana
    bajo = 1.0 if r < 1 - MARGEN_FUERTE else 0.5 if r < 1 - MARGEN else 0.0   # por debajo de la mediana
    return alto - bajo if mas_es_mejor else bajo - alto


def calcular(empresas, meta):
    """Recalcula señales y puntuación de todas. meta: {ticker: (pais, sector_es)}."""
    grupos = {}
    for t, e in empresas.items():
        if t in meta:
            grupos.setdefault(meta[t], []).append(t)
    for clave, miembros in grupos.items():
        datos = [empresas[t] for t in miembros]
        med_per = mediana([d["per"] for d in datos if d.get("per") and d["per"] > 0 and (d.get("eps") or 0) > 0])
        med_roe = mediana([d["roe"] for d in datos if d.get("roe") is not None])
        med_de = mediana([d["deuda"] for d in datos if d.get("deuda") is not None and d["deuda"] > 0])
        n = len(miembros)
        for t in miembros:
            e = empresas[t]
            sen = {}
            # PER
            if e.get("eps") is not None and e["eps"] <= 0:
                sen["per"] = {"valor": e.get("per"), "voto": -1, "nota": "pérdidas"}
            elif e.get("per") and e["per"] > 0:
                sen["per"] = {"valor": e["per"], "voto": voto_relativo(e["per"], med_per, False)}
            # ROE
            if e.get("roe") is not None:
                v = -1 if e["roe"] <= 0 else voto_relativo(e["roe"], med_roe, True)
                sen["roe"] = {"valor": e["roe"], "voto": v, **({"nota": "ROE negativo o nulo"} if e["roe"] <= 0 else {})}
            # Deuda
            if clave[1] == FINANZAS:
                sen["deuda"] = {"valor": e.get("deuda"), "voto": None, "nota": "n/a"}
            elif e.get("deuda") is not None:
                if e["deuda"] < 0:
                    sen["deuda"] = {"valor": e["deuda"], "voto": -1, "nota": "patrimonio negativo"}
                elif e["deuda"] == 0:
                    sen["deuda"] = {"valor": 0.0, "voto": 1 if med_de else None, "nota": "sin deuda"}
                else:
                    sen["deuda"] = {"valor": e["deuda"], "voto": voto_relativo(e["deuda"], med_de, False)}
            # Tendencia
            if e.get("tendencia"):
                tv = e.get("tendencia_voto")
                sen["tendencia"] = {"valor": e["tendencia"], "voto": tv if tv is not None else {"alcista": 1.0, "lateral": 0.0, "bajista": -1.0}[e["tendencia"]]}
            # Sentimiento de mercado (analistas)
            an = e.get("analistas") or {}
            if an.get("voto") is not None:
                sen["sentimiento"] = {"valor": an.get("recomendacion"), "voto": an["voto"], "nota": f"{an.get('n')} analistas"}
            for k, m in (("per", med_per), ("roe", med_roe), ("deuda", med_de)):
                if k in sen:
                    sen[k]["mediana"] = None if m is None else round(m, 3)
            votos = [s["voto"] for s in sen.values() if s.get("voto") is not None]
            e["señales"] = sen
            e["grupo"] = {"pais": clave[0], "sector": clave[1], "n": n, "pequeno": n < GRUPO_PEQUEÑO}
            if len(votos) >= MIN_SEÑALES:
                p = round(ESCALA * sum(votos) / len(votos), 1)   # media de los votos disponibles, sobre ±10
                e["puntuacion"] = p
                e["etiqueta"] = ("Muy favorable" if p >= 5 else "Favorable" if p >= 2 else
                                 "Neutral" if p > -2 else "Desfavorable" if p > -5 else "Muy desfavorable")
            else:
                e["puntuacion"] = None
                e["etiqueta"] = "Datos insuficientes"
                e["faltan"] = [k for k in ("per", "roe", "deuda", "tendencia", "sentimiento")
                               if not (k in sen and sen[k].get("voto") is not None)
                               and not (k == "deuda" and clave[1] == FINANZAS)]
            d = sen.get("deuda", {})
            e["deuda_etiqueta"] = ("n/a" if clave[1] == FINANZAS else
                                   ("bajo" if d["voto"] > 0 else "alto" if d["voto"] < 0 else "medio") if d and d.get("voto") is not None else None)
    return empresas


def main():
    ini = time.time()
    # Importa la unificación de tickers del script de precios (misma lógica de una fila por empresa)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from actualizar_precios import unificar
    df = unificar(pd.read_csv("tickers.csv", dtype=str))
    pais = dict(zip(df.ticker_yahoo, df.pais))
    nombre = dict(zip(df.ticker_yahoo, df.nombre))
    tickers = sorted(df.ticker_yahoo.unique())

    datos = cargar(F_DATOS, {"empresas": {}})
    emp = datos.get("empresas", {})
    emp = {t: e for t, e in emp.items() if t in set(tickers)}
    estado = cargar(F_ESTADO, {})

    todo = os.environ.get("FUND_TODO", "").lower() in ("1", "true", "yes")
    dia = hoy().toordinal()
    tanda = [t for i, t in enumerate(tickers) if todo or i % DIAS_ROTACION == dia % DIAS_ROTACION or t not in emp or "resultados" not in emp[t] or "dominio" not in emp[t] or "analistas" not in emp[t]]
    # Primero las que no tienen datos, para que una ejecución cortada continúe donde quedó
    tanda.sort(key=lambda t: (t in emp and "resultados" in emp[t] and "dominio" in emp[t] and "analistas" in emp[t], t))
    print(f"Tanda: {len(tanda)} empresas de {len(tickers)} (todo={todo})")

    errores, hechas = [], 0
    muestra_diag = {}
    for i in range(0, len(tanda), 100):
        bloque = tanda[i:i + 100]
        if (time.time() - ini) / 60 > PRESUPUESTO_MIN:
            print("Presupuesto de tiempo agotado; el siguiente día continúa donde quedó")
            break
        hist = historicos(bloque)
        for t in bloque:
            if (time.time() - ini) / 60 > PRESUPUESTO_MIN:
                break
            try:
                nuevo = obtener(t, hist.get(t))
            except Exception as e:
                errores.append(f"{t}: {str(e)[:80]}")
                continue
            previo = emp.get(t, {})
            nuevo["actualizada"] = previo.get("actualizada")
            if previo.get("trimestre") and nuevo.get("trimestre") and nuevo["trimestre"] != previo["trimestre"]:
                nuevo["actualizada"] = hoy().isoformat()
            emp[t] = nuevo
            hechas += 1
            if len(muestra_diag) < 12 and t in tickers[:: max(1, len(tickers) // 12)]:
                muestra_diag[t] = {k: nuevo.get(k) for k in ("sector_yahoo", "per", "eps", "roe", "deuda", "dividendo",
                                                              "dividendo_yahoo", "frecuencia", "trimestre", "moneda_yahoo", "tendencia")}
            time.sleep(0.4)
            if hechas % GUARDAR_CADA == 0:
                guardar_todo(emp, nombre, pais, estado, hechas, errores, ini, muestra_diag, parcial=True)
    guardar_todo(emp, nombre, pais, estado, hechas, errores, ini, muestra_diag, parcial=False)


def guardar_todo(emp, nombre, pais, estado, hechas, errores, ini, muestra, parcial):
    meta = {}
    for t, e in emp.items():
        sec = SECTORES.get(e.get("sector_yahoo")) or SIN_CLASIFICAR
        e["sector"] = sec
        meta[t] = (pais.get(t, ""), sec)
    calcular(emp, meta)
    guardar(F_DATOS, {"actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "parametros": {"margen": MARGEN, "min_señales": MIN_SEÑALES, "grupo_pequeño": GRUPO_PEQUEÑO},
                      "empresas": emp})
    suf = sum(1 for e in emp.values() if e.get("etiqueta") == "Datos insuficientes")
    estado.update({"ultima_ejecucion": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "parcial": parcial, "empresas_con_datos": len(emp), "actualizadas_en_esta_ejecucion": hechas,
                   "errores": errores[-60:], "n_errores": len(errores), "datos_insuficientes": suf,
                   "minutos": round((time.time() - ini) / 60, 1), "muestra_diagnostico": muestra})
    guardar(F_ESTADO, estado)
    print(f"Guardado ({'parcial' if parcial else 'final'}): {len(emp)} empresas, {hechas} nuevas, {len(errores)} errores")


if __name__ == "__main__":
    main()
