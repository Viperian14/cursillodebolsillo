#!/usr/bin/env python3
"""Descarga apertura y cierre de todos los valores de tickers.csv y genera
precios.json y precios.csv. Lista en errores.txt los que fallen.

Uso:  pip install yfinance pandas && python actualizar_precios.py

Campos por valor:
  apertura         precio de apertura de la última sesión
  cierre           último precio de esa sesión (es el cierre oficial cuando la bolsa ya cerró;
                   si la bolsa está abierta, es el último precio con ~15 min de retraso)
  cierre_anterior  cierre de la sesión anterior
  fecha            fecha de la sesión (AAAA-MM-DD, hora de la bolsa)
  precio_usd       cierre convertido a dólares (null si no hay tipo de cambio)
  precio_eur       cierre convertido a euros (null si no hay tipo de cambio)
  indices          todos los índices de la lista a los que pertenece la empresa

Además, precios.json lleva un bloque "fx" con los dólares por unidad de cada moneda.
Cada empresa aparece UNA sola vez (clave ticker_yahoo) con una sola etiqueta de índice:
  "Dow Jones" si está en el Dow Jones (todas están también en el S&P 500);
  si no, "S&P 500" si está en él; en el resto, su índice.
Los precios en peniques (moneda GBp) se dividen entre 100 antes de convertir.
"""
import json, os, sys
from datetime import datetime, timezone
import pandas as pd
import yfinance as yf

LOTE = 150  # tickers por petición (más pequeño = menos riesgo de bloqueo)

# Divisas: símbolo de Yahoo, y si cotiza como "dólares por unidad" (True) o "unidades por dólar" (False)
FX_SIMBOLOS = {"EUR": ("EURUSD=X", True), "GBP": ("GBPUSD=X", True),
               "JPY": ("JPY=X", False), "HKD": ("HKD=X", False)}
# Moneda de tickers.csv -> (código de divisa, divisor). GBp = peniques: se divide entre 100.
MONEDAS = {"USD": ("USD", 1), "EUR": ("EUR", 1), "GBP": ("GBP", 1), "GBp": ("GBP", 100),
           "JPY": ("JPY", 1), "HKD": ("HKD", 1)}
# Prioridad de etiqueta cuando una empresa está en varios índices (menor = gana)
PRIORIDAD_INDICE = {"Dow Jones 30": 0, "S&P 500": 1}
ETIQUETA_INDICE = {"Dow Jones 30": "Dow Jones"}


def campo(d, nombre, lote):
    x = d[nombre]
    return x.to_frame(lote[0]) if isinstance(x, pd.Series) else x


def descargar(tickers):
    """Devuelve {ticker_yahoo: dict(apertura, cierre, cierre_anterior, fecha)}."""
    out = {}
    for i in range(0, len(tickers), LOTE):
        lote = tickers[i:i + LOTE]
        try:
            d = yf.download(lote, period="10d", interval="1d", auto_adjust=False,
                            group_by="column", progress=False, threads=True)
        except Exception as e:
            print(f"Lote {i // LOTE + 1} falló: {e}", file=sys.stderr)
            continue
        if d is None or d.empty:
            continue
        close, abre = campo(d, "Close", lote), campo(d, "Open", lote)
        for t in close.columns:
            c = close[t].dropna()
            if c.empty:
                continue
            dia = c.index[-1]
            ap = abre[t].get(dia)
            out[t] = {
                "apertura": None if pd.isna(ap) else round(float(ap), 4),
                "cierre": round(float(c.iloc[-1]), 4),
                "cierre_anterior": round(float(c.iloc[-2]), 4) if len(c) > 1 else None,
                "fecha": dia.strftime("%Y-%m-%d"),
            }
    return out


def descargar_fx():
    """Devuelve {"fecha": AAAA-MM-DD, "usd_por": {USD:1, EUR:..., ...}} o None si falla del todo."""
    usd_por, fechas = {"USD": 1.0}, []
    try:
        d = yf.download([v[0] for v in FX_SIMBOLOS.values()], period="10d", interval="1d",
                        auto_adjust=False, group_by="column", progress=False, threads=True)
        close = d["Close"] if d is not None and not d.empty else None
    except Exception as e:
        print(f"Divisas: descarga fallida: {e}", file=sys.stderr)
        close = None
    if close is not None:
        for cod, (sim, directo) in FX_SIMBOLOS.items():
            if sim not in close.columns:
                continue
            c = close[sim].dropna()
            if c.empty or float(c.iloc[-1]) <= 0:
                continue
            v = float(c.iloc[-1])
            usd_por[cod] = round(v if directo else 1 / v, 8)
            fechas.append(c.index[-1].strftime("%Y-%m-%d"))
    if len(usd_por) < len(FX_SIMBOLOS) + 1:
        return usd_por, (max(fechas) if fechas else None)
    return usd_por, max(fechas)


def cargar_fx_guardado():
    try:
        with open("precios.json", encoding="utf-8") as f:
            return json.load(f).get("fx")
    except Exception:
        return None


def construir_fx():
    """Tasas nuevas; las que falten se rellenan con las últimas guardadas (marcando su fecha)."""
    nuevas, fecha = descargar_fx()
    previo = cargar_fx_guardado() or {}
    antiguas = previo.get("usd_por", {})
    antiguas_f = previo.get("fechas", {})
    usd_por, fechas = {"USD": 1.0}, {"USD": None}
    for cod in FX_SIMBOLOS:
        if cod in nuevas:
            usd_por[cod], fechas[cod] = nuevas[cod], fecha
        elif cod in antiguas:
            usd_por[cod], fechas[cod] = antiguas[cod], antiguas_f.get(cod, previo.get("fecha"))
            print(f"Divisas: sin tasa nueva para {cod}; se mantiene la guardada ({fechas[cod]})", file=sys.stderr)
    return {"fecha": max([f for f in fechas.values() if f] or [None]) if any(fechas.values()) else None,
            "usd_por": usd_por, "fechas": fechas,
            "fuente": "Yahoo Finance (con retraso, solo orientativo)"}


def convertir(cierre, moneda, fx):
    """(precio_usd, precio_eur) o (None, None). Peniques: /100 antes de convertir."""
    if cierre is None or pd.isna(cierre) or moneda not in MONEDAS:
        return None, None
    cod, div = MONEDAS[moneda]
    tasa, eur = fx["usd_por"].get(cod), fx["usd_por"].get("EUR")
    if not tasa:
        return None, None
    usd = float(cierre) / div * tasa
    return round(usd, 4), (round(usd / eur, 4) if eur else None)


def unificar(df):
    """Una fila por ticker_yahoo, con una sola etiqueta de índice y la lista de todos sus índices."""
    df = df.copy()
    df["_prio"] = df.indice.map(lambda i: PRIORIDAD_INDICE.get(i, 9))
    miembros = df.groupby("ticker_yahoo").indice.apply(lambda s: sorted(set(s)))
    df = df.sort_values("_prio", kind="stable").drop_duplicates("ticker_yahoo", keep="first")
    df["indices"] = df.ticker_yahoo.map(lambda t: [ETIQUETA_INDICE.get(i, i) for i in miembros[t]])
    df["indice"] = df.indice.map(lambda i: ETIQUETA_INDICE.get(i, i))
    return df.drop(columns="_prio").reset_index(drop=True)


def main():
    df = unificar(pd.read_csv("tickers.csv", dtype=str))
    datos = descargar(sorted(df.ticker_yahoo.unique()))
    fx = construir_fx()

    for k in ("apertura", "cierre", "cierre_anterior", "fecha"):
        df[k] = df.ticker_yahoo.map(lambda t: datos.get(t, {}).get(k))

    conv = [convertir(c, m, fx) for c, m in zip(df.cierre, df.moneda)]
    df["precio_usd"] = [c[0] for c in conv]
    df["precio_eur"] = [c[1] for c in conv]

    fallidos = df[df.cierre.isna()]
    with open("errores.txt", "w", encoding="utf-8") as f:
        for r in fallidos.itertuples():
            f.write(f"{r.indice}\t{r.nombre}\t{r.ticker_google}\t{r.ticker_yahoo}\n")

    df.assign(indices=df.indices.map(" | ".join)).to_csv("precios.csv", index=False, encoding="utf-8")
    registros = json.loads(df.to_json(orient="records", force_ascii=False))
    with open("precios.json", "w", encoding="utf-8") as f:
        json.dump({"actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "fx": fx, "precios": registros}, f, ensure_ascii=False, indent=1)

    print("fx:", json.dumps(fx["usd_por"]), "fecha", fx["fecha"])
    for moneda in ("GBp", "JPY", "HKD", "EUR", "USD"):
        m = df[(df.moneda == moneda) & df.cierre.notna()].head(1)
        for r in m.itertuples():
            print(f"  muestra {moneda}: {r.nombre} cierre={r.cierre} -> USD {r.precio_usd} / EUR {r.precio_eur}")
    print(f"{len(df) - len(fallidos)}/{len(df)} con precio. Fallidos: {len(fallidos)} (ver errores.txt)")


if __name__ == "__main__":
    main()
