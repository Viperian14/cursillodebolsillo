#!/usr/bin/env python3
"""Rellena actuales.json con los datos reales de EE. UU. ya publicados (BLS y FRED).

Lee el calendario de index.html, busca los eventos de hoy o anteriores que aún no tienen
valor real ("act") y, si BLS/FRED ya tienen el dato del mes o la semana que toca, lo escribe
en el formato de la página. Solo usa la biblioteca estándar de Python.
Claves en variables de entorno: BLS_KEY y FRED_KEY.
"""
import json, os, re, sys, time, subprocess, datetime as dt
from zoneinfo import ZoneInfo
import urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLS_KEY = os.environ.get('BLS_KEY', '')
FRED_KEY = os.environ.get('FRED_KEY', '')
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


# ---------- utilidades de formato ----------
def pct(x, sign=True):
    s = ('%.1f' % abs(x)).replace('.', ',')
    if not sign:
        return s + '%'
    return ('+' if x >= 0 else '−') + s + '%'


def num_mil(x):
    s = '%d' % round(abs(x))
    return ('+' if x >= 0 else '−') + s + ' mil'


def parse_num(s):
    """Primer número de un texto en español ('+0,5% m/m', '−3,2 M', '195 mil')."""
    if not s:
        return None
    m = re.search(r'[+\-−]?\s?\d+(?:[.,]\d+)?', s.replace('−', '-'))
    if not m:
        return None
    t = m.group(0).replace(' ', '')
    if ',' in t:
        t = t.replace('.', '').replace(',', '.')
    try:
        return float(t)
    except ValueError:
        return None


def res_from(act, fc, higher_is_better, tol=0.0):
    if higher_is_better is None:   # sin dirección clara (p. ej. decisiones de tipos): no se valora
        return ''
    a, f = parse_num(act), parse_num(fc)
    if a is None or f is None or abs(a - f) <= tol:
        return ''
    better = a > f if higher_is_better else a < f
    return 'up' if better else 'down'


# ---------- descargas ----------
def http_json(url, data=None):
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json', 'User-Agent': 'cdb-actuales'})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode('utf-8'))


def bls_series(ids, year):
    """{serie: {(año, mes): valor}} con el año anterior y el actual."""
    body = {'seriesid': ids, 'startyear': str(year - 1), 'endyear': str(year)}
    if BLS_KEY:
        body['registrationkey'] = BLS_KEY
    j = http_json('https://api.bls.gov/publicAPI/v2/timeseries/data/', json.dumps(body).encode())
    if j.get('status') != 'REQUEST_SUCCEEDED':
        raise RuntimeError('BLS: ' + '; '.join(j.get('message') or [str(j.get('status'))]))
    out = {}
    for s in j['Results']['series']:
        d = {}
        for p in s['data']:
            if p['period'].startswith('M') and p['period'] != 'M13' and p['value'] not in ('-', ''):
                d[(int(p['year']), int(p['period'][1:]))] = float(p['value'])
        out[s['seriesID']] = d
    return out


def fred_obs(series, limit=26):
    q = urllib.parse.urlencode({'series_id': series, 'api_key': FRED_KEY, 'file_type': 'json', 'sort_order': 'desc', 'limit': limit})
    j = http_json('https://api.stlouisfed.org/fred/series/observations?' + q)
    return [(o['date'], float(o['value'])) for o in j['observations'] if o['value'] not in ('.', '')]


def latest_month(d):
    return max(d) if d else None


def prev_month(ym, back=1):
    y, m = ym
    m -= back
    while m <= 0:
        m += 12
        y -= 1
    return (y, m)


def mm(d, ym):
    a, b = d.get(ym), d.get(prev_month(ym))
    return None if a is None or b is None else (a / b - 1) * 100


def yy(d, ym):
    a, b = d.get(ym), d.get(prev_month(ym, 12))
    return None if a is None or b is None else (a / b - 1) * 100


# ---------- calculadores por tipo de evento (devuelven (act, titular_numerico, mejor_si_sube)) ----------
def calc_cpi(month, year):
    ids = ['CUSR0000SA0', 'CUSR0000SA0L1E', 'CUUR0000SA0', 'CUUR0000SA0L1E']
    s = bls_series(ids, year)
    ym = latest_month(s['CUSR0000SA0'])
    if not ym or ym[1] != month:
        return None
    g_m, c_m = mm(s['CUSR0000SA0'], ym), mm(s['CUSR0000SA0L1E'], ym)
    g_y, c_y = yy(s['CUUR0000SA0'], ym), yy(s['CUUR0000SA0L1E'], ym)
    act = 'General %s m/m · %s a/a\nSubyacente %s m/m · %s a/a' % (pct(g_m), pct(g_y, False), pct(c_m), pct(c_y, False))
    return act, False


def calc_ppi(month, year):
    ids = ['WPSFD4', 'WPSFD49104', 'WPUFD4', 'WPUFD49104']
    s = bls_series(ids, year)
    ym = latest_month(s['WPSFD4'])
    if not ym or ym[1] != month:
        return None
    act = 'General %s m/m · %s a/a\nSubyacente %s m/m · %s a/a' % (
        pct(mm(s['WPSFD4'], ym)), pct(yy(s['WPUFD4'], ym), False), pct(mm(s['WPSFD49104'], ym)), pct(yy(s['WPUFD49104'], ym), False))
    return act, False


def calc_import(month, year):
    s = bls_series(['EIUIR', 'EIUIQ'], year)
    ym = latest_month(s['EIUIR'])
    if not ym or ym[1] != month or latest_month(s['EIUIQ']) != ym:
        return None
    act = 'Importaciones %s m/m · %s a/a\nExportaciones %s m/m · %s a/a' % (
        pct(mm(s['EIUIR'], ym)), pct(yy(s['EIUIR'], ym), False), pct(mm(s['EIUIQ'], ym)), pct(yy(s['EIUIQ'], ym), False))
    return act, False


def eurostat_hicp(coicop, year=None):
    year = year or dt.datetime.now(dt.timezone.utc).year
    url = ('https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr'
           '?format=JSON&lang=EN&geo=EA&unit=RCH_A&coicop18=%s&sinceTimePeriod=%d-01' % (coicop, year))
    j = http_json(url)
    idx = j['dimension']['time']['category']['index']
    vals = j.get('value', {})
    return {t: vals.get(str(i)) for t, i in idx.items()}


def calc_hicp(month, year):
    key = '%d-%02d' % (year, month)
    g, c = eurostat_hicp('TOTAL', year), eurostat_hicp('TOT_X_NRG_FOOD', year)
    if g.get(key) is None or c.get(key) is None:
        return None
    return 'General %s a/a\nSubyacente %s a/a' % (pct(g[key], False), pct(c[key], False)), False


def calc_nfp(month, year):
    s = bls_series(['CES0000000001', 'LNS14000000'], year)
    emp, un = s['CES0000000001'], s['LNS14000000']
    ym = latest_month(emp)
    if not ym or ym[1] != month:
        return None
    diff = emp[ym] - emp[prev_month(ym)]
    return '%s · paro %s' % (num_mil(diff), ('%.1f' % un[ym]).replace('.', ',') + '%'), True


def fred_month_event(series):
    def f(month, year):
        o = fred_obs(series, 16)
        if not o:
            return None
        d, v = o[0]
        y, m = int(d[:4]), int(d[5:7])
        if m != month:
            return None
        prev = o[1][1]
        yago = next((val for dd, val in o if dd[:4] == str(y - 1) and int(dd[5:7]) == m), None)
        act = pct((v / prev - 1) * 100) + ' m/m'
        if yago:
            act += ' · ' + pct((v / yago - 1) * 100, False) + ' a/a'
        return act, True
    return f


def calc_claims(month, year, ev_date):
    o = fred_obs('ICSA', 6)
    if not o:
        return None
    d, v = o[0]
    obs = dt.date.fromisoformat(d)
    if not (ev_date - dt.timedelta(days=8) <= obs <= ev_date):
        return None
    return '%d mil' % round(v / 1000), False


NOTAS = {'2 años': ('Note', ('2-Year',)), '5 años': ('Note', ('5-Year',)), '7 años': ('Note', ('7-Year',)), '10 años': ('Note', ('10-Year', '9-Year')), '30 años': ('Bond', ('30-Year', '29-Year'))}
TERMINOS = {'6 semanas': '6-Week', '3 meses': '13-Week', '6 meses': '26-Week'}


def calc_subasta(ev, d):
    """Rendimiento adjudicado de las subastas del Tesoro de EE. UU. (fiscaldata.treasury.gov, sin clave).
    Letras: tipo de descuento. Notas y bonos: rendimiento (high_yield)."""
    letra = next((v for k, v in TERMINOS.items() if k in ev), None) if 'letras' in ev else None
    nota = None
    if not letra:
        for k, (tipo, pref) in NOTAS.items():
            if k in ev:
                nota = (tipo, pref)
    if not letra and not nota:
        return None
    tipo = 'Bill' if letra else nota[0]
    campo = 'high_discnt_rate' if letra else 'high_yield'
    q = urllib.parse.urlencode({'filter': 'security_type:eq:%s,auction_date:eq:%s' % (tipo, d.isoformat()), 'fields': 'security_term,%s' % campo, 'page[size]': '20'}, safe=':,[]')
    j = http_json('https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query?' + q)
    for row in j.get('data') or []:
        term = row.get('security_term') or ''
        ok = term == letra if letra else any(term.startswith(x) for x in nota[1])
        v = row.get(campo)
        if ok and v not in (None, '', 'null'):
            return ('%s%%' % v.rstrip('0').rstrip('.')).replace('.', ','), False
    # Respaldo: TreasuryDirect publica los resultados al momento (fiscaldata puede tardar horas o un día)
    try:
        q2 = urllib.parse.urlencode({'format': 'json', 'type': tipo, 'dateFieldName': 'auctionDate', 'startDate': d.isoformat(), 'endDate': d.isoformat()})
        j2 = http_json('https://www.treasurydirect.gov/TA_WS/securities/auctioned?' + q2)
        campo2 = 'highRate' if letra else 'highYield'
        for row in (j2 if isinstance(j2, list) else []):
            if (row.get('competitiveAuctionCloseDate') or '')[:10] != d.isoformat():
                continue
            term = row.get('securityTerm') or ''
            ok = term == letra if letra else any(term.startswith(x) for x in nota[1])
            v = str(row.get(campo2) or '').strip()
            if ok and v not in ('', 'null', 'None'):
                return ('%s%%' % v.rstrip('0').rstrip('.')).replace('.', ','), False
    except Exception as ex:
        print('Respaldo TreasuryDirect sin datos: %s' % ex, file=sys.stderr)
    return None


def calc_fed(d):
    """Rango objetivo de los fondos federales tras la reunión (FRED: DFEDTARL/DFEDTARU)."""
    lo, hi = fred_obs('DFEDTARL', 1), fred_obs('DFEDTARU', 1)
    if not lo or not hi or dt.date.fromisoformat(lo[0][0]) < d:
        return None
    # Sin valoración automática: la previsión suele ser un texto ("pausa descontada…") y no una cifra comparable.
    return 'Tipos %s–%s%%' % (('%.2f' % lo[0][1]).replace('.', ','), ('%.2f' % hi[0][1]).replace('.', ',')), None


# ---------- más series de EE. UU. (FRED, misma clave) ----------
def es_num(x, dec=1):
    return ('%.*f' % (dec, x)).replace('.', ',')


def millones(miles):
    """1221 (miles) -> '1,221 M'."""
    return es_num(miles / 1000.0, 3) + ' M'


def mil_m_dolares(millones_usd):
    """-59600 (millones de $) -> '−59,6 mil M$'."""
    v = millones_usd / 1000.0
    s = es_num(abs(v))
    if s.endswith(',0'):
        s = s[:-2]
    return ('−' if v < 0 else '') + s + ' mil M$'


def obs_mes(series, month, year, n=16):
    """Devuelve (valor, anterior, hace_un_año) de la observación mensual del mes pedido, o None."""
    o = fred_obs(series, n)
    for i, (d, v) in enumerate(o):
        if int(d[:4]) == year and int(d[5:7]) == month:
            prev = o[i + 1][1] if i + 1 < len(o) else None
            yago = next((val for dd, val in o if int(dd[:4]) == year - 1 and int(dd[5:7]) == month), None)
            return v, prev, yago
    return None


def anio_de_mes(month, ev_date):
    """El dato de diciembre publicado en enero es del año anterior."""
    return ev_date.year - 1 if month > ev_date.month else ev_date.year


def calc_nivel_millones(series):
    """Inicios de viviendas o permisos: nivel en millones (anualizado)."""
    def f(month, year, d):
        r = obs_mes(series, month, anio_de_mes(month, d))
        return (millones(r[0]), True) if r else None
    return f


def calc_var_mensual(series):
    def f(month, year, d):
        r = obs_mes(series, month, anio_de_mes(month, d))
        if not r or not r[1]:
            return None
        return pct((r[0] / r[1] - 1) * 100), True
    return f


def calc_jolts(month, year, d):
    r = obs_mes('JTSJOL', month, anio_de_mes(month, d))
    return (millones(r[0]), True) if r else None


def calc_balanza(month, year, d):
    r = obs_mes('BOPGSTB', month, anio_de_mes(month, d))
    return (mil_m_dolares(r[0]), True) if r else None


def calc_presupuesto(month, year, d):
    r = obs_mes('MTSDS133FMS', month, anio_de_mes(month, d))
    return (mil_m_dolares(r[0]), True) if r else None


def trimestre_in(ev):
    m = re.search(r'(\d)\s*\.?\s*(?:er|º|o|ᵉʳ)?\s*trimestre', ev)
    return int(m.group(1)) if m else None


def obs_trimestre(series, q, year, n=8):
    """Valor y anterior de una serie trimestral de FRED (la fecha es el primer día del trimestre)."""
    o = fred_obs(series, n)
    mes = 3 * (q - 1) + 1
    for i, (dd, v) in enumerate(o):
        if int(dd[:4]) == year and int(dd[5:7]) == mes:
            return v, (o[i + 1][1] if i + 1 < len(o) else None)
    return None


def calc_pib(ev, d):
    """PIB real (tasa trimestral anualizada) y, si el evento lo nombra, el PCE del mes."""
    q = trimestre_in(ev)
    if not q:
        return None
    year = d.year - 1 if q == 4 and d.month <= 3 else d.year
    g = obs_trimestre('A191RL1Q225SBEA', q, year)
    if not g:
        return None
    act = 'PIB %s anualizado' % pct(g[0])
    mes = month_in(ev)
    if mes and 'PCE' in ev:
        try:
            yy_ = anio_de_mes(mes, d)
            p, c = obs_mes('PCEPI', mes, yy_), obs_mes('PCEPILFE', mes, yy_)
            if p and c and p[1] and c[1] and p[2] and c[2]:
                act += '\nPCE %s: %s m/m · %s a/a' % (MESES[mes - 1], pct((p[0] / p[1] - 1) * 100), pct((p[0] / p[2] - 1) * 100, False))
                act += '\nPCE subyacente: %s m/m · %s a/a' % (pct((c[0] / c[1] - 1) * 100), pct((c[0] / c[2] - 1) * 100, False))
        except Exception as ex:
            print('PCE sin datos: %s' % ex, file=sys.stderr)
    return act, True


def calc_eci(ev, d):
    q = trimestre_in(ev)
    if not q:
        return None
    r = obs_trimestre('ECIALLCIV', q, d.year)
    if not r or not r[1]:
        return None
    return pct((r[0] / r[1] - 1) * 100) + ' t/t', False


# ---------- EIA (inventarios semanales; clave EIA_KEY) ----------
EIA_KEY = os.environ.get('EIA_KEY', '')


def eia_semanal(serie, ev_date):
    """[(fecha_semana, valor)] más recientes de una serie semanal de la EIA (API v2, ruta seriesid)."""
    if not EIA_KEY:
        print('Sin EIA_KEY: no se pueden leer los inventarios de la EIA', file=sys.stderr)
        return []
    desde = (ev_date - dt.timedelta(days=40)).isoformat()
    q = urllib.parse.urlencode({'api_key': EIA_KEY, 'start': desde})
    j = http_json('https://api.eia.gov/v2/seriesid/%s?%s' % (serie, q))
    datos = [(r['period'][:10], float(r['value'])) for r in (j.get('response') or {}).get('data', []) if r.get('value') not in (None, '')]
    return sorted(datos, reverse=True)


def cambio_semanal(serie, ev_date):
    o = eia_semanal(serie, ev_date)
    if len(o) < 2:
        return None
    sem = dt.date.fromisoformat(o[0][0])
    if not (ev_date - dt.timedelta(days=9) <= sem < ev_date):   # la semana que toca (acaba el viernes anterior)
        return None
    return o[0][1] - o[1][1], o[0][1]


def calc_crudo(ev, d):
    r = cambio_semanal('PET.WCESTUS1.W', d)       # miles de barriles, sin la reserva estratégica
    if not r:
        return None
    ch = r[0] / 1000.0
    # Un descenso de inventarios es lo "mejor" para el precio del crudo (criterio que ya usa la página).
    return '%s%s M barriles' % ('+' if ch >= 0 else '−', es_num(abs(ch))), False


def calc_gas(ev, d):
    r = cambio_semanal('NG.NW2_EPG0_SWO_R48_BCF.W', d)   # Bcf, 48 estados
    if not r:
        return None
    return '%s%d Bcf' % ('+' if r[0] >= 0 else '−', round(abs(r[0]))), False


def gate():
    """Código 0 solo si hoy hay subasta de letras de EE. UU. o decisión de la Fed en el calendario."""
    html = open(os.path.join(ROOT, 'index.html'), encoding='utf-8').read()
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    for line in html.splitlines():
        if line.lstrip().startswith("{ d: '%s'" % today) and ', p:' not in line and ("k: 'auction'" in line or "k: 'fed'" in line):
            return 0
    return 1


# (texto que debe contener el evento, función(mes, año, fecha_evento), necesita_mes, tolerancia para "igual")
HANDLERS = [
    ('Precios de producción (PPI)', lambda m, y, d: calc_ppi(m, y), True, 0.05),
    ('IPC de EE. UU.', lambda m, y, d: calc_cpi(m, y), True, 0.05),
    ('Empleo de EE. UU. (NFP)', lambda m, y, d: calc_nfp(m, y), True, 0.5),
    ('Ventas minoristas de EE. UU.', lambda m, y, d: fred_month_event('RSAFS')(m, y), True, 0.05),
    ('Producción industrial de EE. UU.', lambda m, y, d: fred_month_event('INDPRO')(m, y), True, 0.05),
    ('Peticiones iniciales de subsidio de desempleo', calc_claims, False, 0.5),
    ('Precios de importación y exportación de EE. UU.', lambda m, y, d: calc_import(m, y), True, 0.05),
    ('Subasta de letras del Tesoro', lambda m, y, d: None, False, 0.05),
    ('Subasta de notas y bonos del Tesoro', lambda m, y, d: None, False, 0.05),
    ('Decisión de tipos de la Fed', lambda m, y, d: calc_fed(d), False, 0.0),
    ('IPC final de la zona euro', lambda m, y, d: calc_hicp(m, y), True, 0.05),
    # Series de FRED añadidas el 30-10-2026. 'prev' = si el nombre no trae el mes, se usa el mes anterior al del evento.
    # 'ev' = la función recibe el texto del evento (trimestre, semana…). Los más concretos van antes.
    ('Inicios de viviendas, variación mensual', lambda m, y, d: calc_var_mensual('HOUST')(m, y, d), 'prev', 0.05),
    ('Inicios de viviendas', lambda m, y, d: calc_nivel_millones('HOUST')(m, y, d), 'prev', 0.0005),
    ('Permisos de construcción, variación mensual', lambda m, y, d: calc_var_mensual('PERMIT')(m, y, d), 'prev', 0.05),
    ('Permisos de construcción', lambda m, y, d: calc_nivel_millones('PERMIT')(m, y, d), 'prev', 0.0005),
    ('Ofertas de empleo JOLTS', calc_jolts, 'prev', 0.005),
    ('Balanza comercial de EE. UU.', calc_balanza, 'prev', 0.05),
    ('Estado mensual del presupuesto de EE. UU.', calc_presupuesto, 'prev', 0.5),
    ('PIB de EE. UU.', calc_pib, 'ev', 0.05),
    ('PIB del 3.er trimestre', calc_pib, 'ev', 0.05),
    ('Índice de costes laborales (ECI)', calc_eci, 'ev', 0.05),
    # EIA: necesitan el secreto EIA_KEY (si falta, se saltan sin error).
    ('Inventarios de crudo de la EIA', calc_crudo, 'ev', 0.05),
    ('Almacenamientos de gas natural de la EIA', calc_gas, 'ev', 0.5),
]


def month_in(ev):
    low = ev.lower()
    for i, m in enumerate(MESES):
        if re.search(r'\b' + m + r'\b', low):
            return i + 1
    return None


def pending_events(html, today):
    out = []
    for line in html.splitlines():
        m = re.match(r"\s*\{ d: '(\d{4}-\d{2}-\d{2})', h: '([^']*)'.*? ev: '((?:[^'\\]|\\.)*)'", line)
        if not m or ', co:' in line:
            continue
        d, h, ev = m.groups()
        if d > today or ', act:' in line:
            continue
        # Ignora lo que lleva más de 3 días sin dato: no gasta consultas del BLS en eventos que no se van a rellenar.
        if (dt.date.fromisoformat(today) - dt.date.fromisoformat(d)).days > 3:
            continue
        fc = re.search(r" fc: '((?:[^'\\]|\\.)*)'", line)
        out.append({'d': d, 'h': h, 'ev': ev.replace("\\'", "'"), 'fc': (fc.group(1) if fc else '').replace('\\n', '\n')})
    return out


def sonda():
    """Modo de prueba: lee el último dato de cada serie y lo guarda en sonda.json (sin claves)."""
    r = {'hecho': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'bls': {}, 'fred': {}, 'calculos': {}}
    year = dt.datetime.now(dt.timezone.utc).year
    ids = ['CUSR0000SA0', 'CUSR0000SA0L1E', 'CUUR0000SA0', 'CUUR0000SA0L1E', 'WPSFD4', 'WPSFD49104', 'WPUFD4', 'WPUFD49104', 'CES0000000001', 'LNS14000000']
    try:
        s = bls_series(ids, year)
        for k, d in s.items():
            ym = latest_month(d)
            r['bls'][k] = {'ultimo': '%s-%02d' % ym if ym else None, 'valor': d.get(ym) if ym else None}
        for nombre, fn in (('IPC', calc_cpi), ('PPI', calc_ppi), ('NFP', calc_nfp)):
            ym = latest_month(s['CUSR0000SA0' if nombre == 'IPC' else 'WPSFD4' if nombre == 'PPI' else 'CES0000000001'])
            try:
                r['calculos'][nombre] = fn(ym[1], year) if ym else None
            except Exception as ex:
                r['calculos'][nombre] = 'ERROR: %s' % ex
    except Exception as ex:
        r['bls']['ERROR'] = str(ex)
    try:
        cand = ['EIUIR', 'EIUIQ', 'EIUSIR', 'EIUSIQ', 'EIUIR10', 'EIUIREXFUELS', 'EIUIR10']
        s2 = bls_series(cand, year)
        r['importacion'] = {}
        for k, d in s2.items():
            ym = latest_month(d)
            if not ym:
                r['importacion'][k] = None
                continue
            r['importacion'][k] = {'ultimo': '%s-%02d' % ym, 'valor': d.get(ym), 'mm': mm(d, ym), 'yy': yy(d, ym), 'mm_previo': mm(d, prev_month(ym)), 'yy_previo': yy(d, prev_month(ym))}
    except Exception as ex:
        r['importacion'] = 'ERROR: %s' % ex
    try:
        r['eurostat'] = {'TOTAL': eurostat_hicp('TOTAL'), 'TOT_X_NRG_FOOD': eurostat_hicp('TOT_X_NRG_FOOD')}
    except Exception as ex:
        r['eurostat'] = 'ERROR: %s' % ex
    try:
        j = http_json('https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr?format=JSON&lang=EN&geo=EA&sinceTimePeriod=2026-08&unit=RCH_A')
        dims = {}
        for dk, dv in j['dimension'].items():
            cat = dv['category']
            dims[dk] = {k: (cat.get('label', {}).get(k, '')[:60]) for k in list(cat['index'])[:12]} if dk not in ('time',) else list(cat['index'])
        cat18 = j['dimension']['coicop18']['category']
        r['eurostat_especiales'] = {k: cat18.get('label', {}).get(k, '')[:80] for k in cat18['index'] if not k.startswith('CP')}
        r['eurostat_minr'] = {'id': j.get('id'), 'size': j.get('size'), 'dims': dims, 'n_valores': len(j.get('value', {}))}
    except Exception as ex:
        r['eurostat_minr'] = 'ERROR: %s' % ex
    try:
        q = urllib.parse.urlencode({'filter': 'security_type:eq:Bill,auction_date:gte:2026-10-12', 'fields': 'auction_date,security_term,high_discnt_rate', 'sort': '-auction_date', 'page[size]': '12'}, safe=':,[]-')
        r['subastas_fiscaldata'] = (http_json('https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query?' + q) or {}).get('data')
    except Exception as ex:
        r['subastas_fiscaldata'] = 'ERROR: %s' % ex
    try:
        q = urllib.parse.urlencode({'format': 'json', 'type': 'Bill', 'dateFieldName': 'auctionDate', 'startDate': '2026-10-12', 'endDate': '2026-10-20'})
        j = http_json('https://www.treasurydirect.gov/TA_WS/securities/auctioned?' + q)
        r['subastas_treasurydirect'] = {'n': len(j), 'claves': sorted(j[0].keys()), 'ej': [{k: v for k, v in x.items() if v not in (None, '')} for x in j[-3:]]} if isinstance(j, list) and j else j
    except Exception as ex:
        r['subastas_treasurydirect'] = 'ERROR: %s' % ex
    try:
        r['eia'] = {s: eia_semanal(s, dt.date.today())[:2] for s in ('PET.WCESTUS1.W', 'NG.NW2_EPG0_SWO_R48_BCF.W')}
    except Exception as ex:
        r['eia'] = 'ERROR: %s' % str(ex).replace(EIA_KEY or 'x', '***')
    for sid in ('ICSA', 'RSAFS', 'INDPRO', 'HOUST', 'PERMIT', 'JTSJOL', 'BOPGSTB', 'MTSDS133FMS',
                'A191RL1Q225SBEA', 'PCEPI', 'PCEPILFE', 'ECIALLCIV', 'DFEDTARU'):
        try:
            o = fred_obs(sid, 3)
            r['fred'][sid] = [list(x) for x in o[:2]]
        except Exception as ex:
            r['fred'][sid] = 'ERROR: %s' % str(ex).replace(FRED_KEY or 'x', '***')
    with open(os.path.join(ROOT, 'sonda.json'), 'w', encoding='utf-8') as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
        f.write('\n')
    print(json.dumps(r, ensure_ascii=False, indent=1))


TZ_ES = ZoneInfo('Europe/Madrid')
DEMORA_MIN = 3        # se empieza a buscar el dato 3 minutos después de la hora del evento
VENTANA_MIN = 20      # y se sigue intentando hasta 20 minutos después
ANTELACION_MIN = 12   # el trabajo arranca como mucho 12 minutos antes (el reloj de GitHub se retrasa a veces)


def hora_evento(e):
    """Instante UTC del evento (las horas del calendario son de España) o None si no tiene hora."""
    if not re.match(r'^\d{1,2}:\d{2}$', e.get('h') or ''):
        return None
    y, mo, d = map(int, e['d'].split('-'))
    hh, mm_ = map(int, e['h'].split(':'))
    return dt.datetime(y, mo, d, hh, mm_, tzinfo=TZ_ES).astimezone(dt.timezone.utc)


def manejador(ev):
    for h in HANDLERS:
        if h[0] in ev:
            return h
    return None


def intentar(e):
    """Busca el dato de un evento. Devuelve el item {d, match, act, res} o None."""
    h = manejador(e['ev'])
    if not h:
        return None
    key, fn, needs_month, tol = h
    month = month_in(e['ev'])
    ev_date = dt.date.fromisoformat(e['d'])
    if needs_month == 'prev' and not month:
        month = 12 if ev_date.month == 1 else ev_date.month - 1
    if needs_month is True and not month:
        return None
    try:
        if key.startswith('Subasta'):
            res = calc_subasta(e['ev'], ev_date)
        elif needs_month == 'ev':
            res = fn(e['ev'], ev_date)
        else:
            res = fn(month, ev_date.year, ev_date)
    except Exception as ex:
        print('ERROR en %s: %s' % (e['ev'], ex), file=sys.stderr)
        return None
    if not res:
        print('Sin dato todavía:', e['d'], e['ev'])
        return None
    act, higher_better = res
    return {'d': e['d'], 'match': e['ev'], 'act': act, 'res': res_from(act, e['fc'], higher_better, tol)}


def cargar_actuales():
    path = os.path.join(ROOT, 'actuales.json')
    try:
        return path, json.load(open(path, encoding='utf-8'))
    except Exception:
        return path, {'updated': '', 'items': []}


def guardar_actuales(path, cur):
    cur['updated'] = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
        f.write('\n')


def subir():
    """Publica ya lo que haya cambiado (la página se actualiza al momento)."""
    sh = os.path.join(ROOT, 'scripts', 'guardar.sh')
    if os.path.exists(sh) and os.environ.get('GITHUB_ACTIONS'):
        subprocess.run(['bash', sh], cwd=ROOT, check=False)


def eventos_pendientes(cur, hoy=None):
    html = open(os.path.join(ROOT, 'index.html'), encoding='utf-8').read()
    today = hoy or dt.datetime.now(dt.timezone.utc).date().isoformat()
    have = {(i['d'], i['match']) for i in cur.get('items', [])}
    return [e for e in pending_events(html, today) if (e['d'], e['ev']) not in have]


def debidos_en(eventos, ahora):
    """Eventos con dato automático cuya hora (menos ANTELACION) ya ha llegado.
    No tiene límite por detrás: GitHub retrasa o se salta muchas ejecuciones programadas (en la práctica corre
    una vez por hora, a minutos variables), así que cada ejecución recoge también lo que se quedó atrás.
    El límite de antigüedad lo pone eventos_pendientes (3 días)."""
    out = []
    for e in eventos:
        t = hora_evento(e)
        if t is None or not manejador(e['ev']):
            continue
        if t - dt.timedelta(minutes=ANTELACION_MIN) <= ahora:
            out.append((e, t))
    return out


def ventana():
    """Modo por franjas (cada ejecución programada del día):
    1) intenta una vez todos los eventos con dato automático cuya hora ya pasó (recuperación);
    2) si hay alguno a punto de salir o recién salido (hasta VENTANA_MIN después), espera y reintenta cada 45 s."""
    t0 = time.time()
    path, cur = cargar_actuales()
    intentados = set()
    while True:
        ahora = dt.datetime.now(dt.timezone.utc)
        debidos = debidos_en(eventos_pendientes(cur), ahora)
        if not debidos:
            print('Nada que buscar en esta franja.')
            return
        nuevos = 0
        for e, t in debidos:
            if ahora < t + dt.timedelta(minutes=DEMORA_MIN):
                continue                     # aún no ha salido
            reciente = ahora <= t + dt.timedelta(minutes=VENTANA_MIN)
            clave = (e['d'], e['ev'])
            if clave in intentados and not reciente:
                continue                     # lo atrasado se intenta una sola vez por ejecución
            intentados.add(clave)
            item = intentar(e)
            if item:
                cur.setdefault('items', []).append(item)
                guardar_actuales(path, cur)
                nuevos += 1
                print('NUEVO:', json.dumps(item, ensure_ascii=False))
        if nuevos:
            subir()
            continue            # recalcula qué falta
        # ¿Merece la pena esperar? Solo si hay algo que sale en breve o acaba de salir.
        espera = [1 for e, t in debidos if ahora <= t + dt.timedelta(minutes=VENTANA_MIN)]
        if not espera:
            print('Recuperación hecha; nada reciente que esperar.')
            return
        if time.time() - t0 > 28 * 60:
            print('Tiempo máximo de la franja alcanzado.')
            return
        time.sleep(45)


def main():
    if '--gate' in sys.argv:
        sys.exit(gate())
    if '--sonda' in sys.argv:
        return sonda()
    if '--ventana' in sys.argv:
        return ventana()
    # Barrido: prueba todos los eventos pendientes de los últimos días (una o dos veces al día)
    path, cur = cargar_actuales()
    nuevos = 0
    for e in eventos_pendientes(cur):
        item = intentar(e)
        if item:
            cur.setdefault('items', []).append(item)
            nuevos += 1
            print('NUEVO:', json.dumps(item, ensure_ascii=False))
    if nuevos:
        guardar_actuales(path, cur)
    print('Eventos nuevos:', nuevos)


if __name__ == '__main__':
    main()
