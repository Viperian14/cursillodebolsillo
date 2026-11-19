#!/usr/bin/env python3
"""Prueba de fuentes nuevas para el bot de datos reales (se lanza con el modo sonda).
Guarda en sonda_fuentes.json lo que devuelve cada API (recortado) o el error. No usa datos del calendario."""
import json, os, urllib.request, urllib.parse, datetime as dt

R = {'hecho': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
UA = {'User-Agent': 'Mozilla/5.0 (compatible; cdb-actuales/1.0)', 'Accept': 'application/json'}


def get(url, data=None, headers=None):
    h = dict(UA)
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode('utf-8'))


def probar(nombre, fn):
    try:
        R[nombre] = fn()
    except Exception as ex:
        msg = str(ex)
        for k in ('EIA_KEY', 'ALPHAVANTAGE_KEY'):
            if os.environ.get(k):
                msg = msg.replace(os.environ[k], '***')
        R[nombre] = 'ERROR: ' + msg[:300]


def eurostat(ds, params):
    q = urllib.parse.urlencode(dict(params, format='JSON', lang='EN'))
    j = get('https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/%s?%s' % (ds, q))
    idx = j['dimension']['time']['category']['index']
    v = j.get('value', {})
    return {t: v.get(str(i)) for t, i in sorted(idx.items(), key=lambda x: x[1])}


probar('eurostat_pib', lambda: {g: eurostat('namq_10_gdp', {'geo': g, 'unit': 'CLV_PCH_PRE', 's_adj': 'SCA', 'na_item': 'B1GQ', 'sinceTimePeriod': '2026-Q1'}) for g in ('EA20', 'DE', 'FR', 'ES')})
probar('eurostat_pib_aa', lambda: eurostat('namq_10_gdp', {'geo': 'EA20', 'unit': 'CLV_PCH_SM', 's_adj': 'SCA', 'na_item': 'B1GQ', 'sinceTimePeriod': '2026-Q1'}))
probar('eurostat_paro', lambda: {g: eurostat('une_rt_m', {'geo': g, 's_adj': 'SA', 'age': 'TOTAL', 'sex': 'T', 'unit': 'PC_ACT', 'sinceTimePeriod': '2026-06'}) for g in ('EA20', 'EA', 'EA21', 'EU27_2020')})
probar('eurostat_hicp_flash', lambda: eurostat('prc_hicp_manr', {'geo': 'EA', 'coicop': 'CP00', 'sinceTimePeriod': '2026-08'}))
probar('eurostat_hicp_paises', lambda: {g: eurostat('prc_hicp_manr', {'geo': g, 'coicop': 'CP00', 'sinceTimePeriod': '2026-08'}) for g in ('DE', 'FR', 'ES')})


ONS_RUTA = {
    'd7g7': 'economy/inflationandpriceindices/timeseries/d7g7/mm23',
    'dko8': 'economy/inflationandpriceindices/timeseries/dko8/mm23',
    'mgsx': 'employmentandlabourmarket/peoplenotinwork/unemployment/timeseries/mgsx/lms',
    'ihyq': 'economy/grossdomesticproductgdp/timeseries/ihyq/qna',
    'ecy2': 'economy/grossdomesticproductgdp/timeseries/ecy2/mgdp',
    'j5ek': 'businessindustryandtrade/retailindustry/timeseries/j5ek/drsi',
}


def ons(serie):
    j = get('https://www.ons.gov.uk/%s/data' % ONS_RUTA[serie])
    out = {'titulo': j.get('description', {}).get('title'), 'publicado': j.get('description', {}).get('releaseDate')}
    for per in ('months', 'quarters'):
        if j.get(per):
            out[per] = [(p['date'], p['value']) for p in j[per][-3:]]
    return out


for s in ONS_RUTA:
    probar('ons_' + s, lambda s=s: ons(s))


def statcan(vecs):
    body = json.dumps([{'vectorId': v, 'latestN': 3} for v in vecs]).encode()
    j = get('https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods', body, {'Content-Type': 'application/json'})
    out = {}
    for it in j:
        o = it.get('object') or {}
        out[str(o.get('vectorId'))] = [(p.get('refPer'), p.get('value'), p.get('releaseTime')) for p in o.get('vectorDataPoint', [])]
    return out


probar('statcan', lambda: statcan([41690973, 41690914, 2062811, 2062815, 65201210]))


def abs_(flow, key):
    j = get('https://data.api.abs.gov.au/rest/data/%s/%s?lastNObservations=3&dimensionAtObservation=AllDimensions&format=jsondata' % (flow, key),
            headers={'Accept': 'application/vnd.sdmx.data+json'})
    data = j.get('data', j)
    st = data.get('structure') or (data.get('structures') or [{}])[0] or j.get('structure')
    if not st:
        return {'claves': list(j.keys()), 'claves_data': list(data.keys()) if isinstance(data, dict) else None}
    ds = data['dataSets'][0]
    obs = ds.get('observations', {})
    tdim = [d for d in st['dimensions']['observation'] if d['id'] == 'TIME_PERIOD'][0]
    return {'n': len(obs), 'tiempos': [v['id'] for v in tdim['values']][-4:], 'muestra': list(obs.items())[-4:]}


probar('abs_paro', lambda: abs_('ABS,LF,1.0.0', 'M13.3.1599.20.AUS.M'))
probar('abs_empleo', lambda: abs_('ABS,LF,1.0.0', 'M3.3.1599.20.AUS.M'))
probar('abs_flujos_cpi', lambda: [f['id'] for f in get('https://data.api.abs.gov.au/rest/dataflow/ABS', headers={'Accept': 'application/vnd.sdmx.structure+json'})['data']['dataflows'] if 'CPI' in f['id']])


def av(sym):
    k = os.environ.get('ALPHAVANTAGE_KEY', '')
    if not k:
        return 'Sin ALPHAVANTAGE_KEY'
    j = get('https://www.alphavantage.co/query?function=EARNINGS&symbol=%s&apikey=%s' % (sym, k))
    if 'quarterlyEarnings' not in j:
        return {kk: str(vv)[:200] for kk, vv in j.items()}
    return j['quarterlyEarnings'][:2]


probar('alphavantage_HD', lambda: av('HD'))
import time
time.sleep(2)
probar('alphavantage_NVDA', lambda: av('NVDA'))


def eia(serie):
    k = os.environ.get('EIA_KEY', '')
    if not k:
        return 'Sin EIA_KEY'
    q = urllib.parse.urlencode({'api_key': k, 'start': (dt.date.today() - dt.timedelta(days=30)).isoformat()})
    j = get('https://api.eia.gov/v2/seriesid/%s?%s' % (serie, q))
    d = (j.get('response') or {}).get('data', [])
    return sorted([(r.get('period'), r.get('value')) for r in d], reverse=True)[:3] or {kk: str(vv)[:200] for kk, vv in j.items()}


probar('eia_crudo', lambda: eia('PET.WCESTUS1.W'))
probar('eia_gas', lambda: eia('NG.NW2_EPG0_SWO_R48_BCF.W'))

json.dump(R, open('sonda_fuentes.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(json.dumps(R, ensure_ascii=False)[:3000])
