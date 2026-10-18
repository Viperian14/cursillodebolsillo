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
"""
import json, sys
from datetime import datetime, timezone
import pandas as pd
import yfinance as yf

LOTE = 150  # tickers por petición (más pequeño = menos riesgo de bloqueo)


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


def main():
    df = pd.read_csv("tickers.csv", dtype=str)
    datos = descargar(sorted(df.ticker_yahoo.unique()))

    for k in ("apertura", "cierre", "cierre_anterior", "fecha"):
        df[k] = df.ticker_yahoo.map(lambda t: datos.get(t, {}).get(k))

    fallidos = df[df.cierre.isna()]
    with open("errores.txt", "w", encoding="utf-8") as f:
        for r in fallidos.itertuples():
            f.write(f"{r.indice}\t{r.nombre}\t{r.ticker_google}\t{r.ticker_yahoo}\n")

    df.to_csv("precios.csv", index=False, encoding="utf-8")
    registros = json.loads(df.to_json(orient="records", force_ascii=False))
    with open("precios.json", "w", encoding="utf-8") as f:
        json.dump({"actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "precios": registros}, f, ensure_ascii=False, indent=1)

    print(f"{len(df) - len(fallidos)}/{len(df)} con precio. Fallidos: {len(fallidos)} (ver errores.txt)")


if __name__ == "__main__":
    main()
