#!/usr/bin/env python3
"""Publicar cursillodebolsillo.com en Cloudflare (R2 + Worker), mientras GitHub esté suspendido.

La web se guarda TAL CUAL (index.html con las imágenes dentro) en el almacén R2 «cursillodebolsillo-web»
y la sirve el Worker «cursillodebolsillo» en cursillodebolsillo.com.

Uso:
  python3 publicar_cloudflare.py descargar CARPETA --token-file cloudflare_token.md   # baja la web publicada
  python3 publicar_cloudflare.py publicar CARPETA --token-file cloudflare_token.md # sube solo lo que ha cambiado
  python3 publicar_cloudflare.py lista --token-file cloudflare_token.md            # lista lo publicado

El token se lee de CLOUDFLARE_API_TOKEN o del archivo --token-file (línea que empieza por «cfat_»).
"""
import hashlib, json, mimetypes, os, sys, time, urllib.parse, urllib.request

ACCOUNT = "b489064d781ca92e25731d8255c771cd"
BUCKET = "cursillodebolsillo-web"
API = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/r2/buckets/{BUCKET}"
TIPOS = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
         ".json": "application/json; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".csv": "text/csv; charset=utf-8", ".txt": "text/plain; charset=utf-8",
         ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml", ".png": "image/png",
         ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".ico": "image/x-icon",
         ".pdf": "application/pdf", ".xml": "application/xml"}
NO_SUBIR = (".py", ".md", ".git", ".github", "scripts/", "README", "CNAME", "errores.txt")


def token():
    t = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if "--token-file" in sys.argv:
        for l in open(sys.argv[sys.argv.index("--token-file") + 1], encoding="utf-8"):
            if l.strip().startswith("cfat_"):
                t = l.strip()
    if not t:
        sys.exit("Falta el token de Cloudflare (CLOUDFLARE_API_TOKEN o --token-file).")
    return t


def req(url, metodo="GET", datos=None, tipo=None, crudo=False):
    r = urllib.request.Request(url, data=datos, method=metodo)
    r.add_header("Authorization", "Bearer " + token())
    if tipo:
        r.add_header("Content-Type", tipo)
    for intento in range(4):
        try:
            with urllib.request.urlopen(r, timeout=600) as resp:
                b = resp.read()
                return b if crudo else json.loads(b.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code < 500 or intento == 3:
                raise
            time.sleep(3)


def lista():
    objs, cursor = [], None
    while True:
        u = API + "/objects?per_page=1000" + (f"&cursor={cursor}" if cursor else "")
        d = req(u)
        objs += d["result"]
        cursor = (d.get("result_info") or {}).get("cursor")
        if not (d.get("result_info") or {}).get("is_truncated"):
            return objs


def descargar(carpeta):
    n = 0
    for o in lista():
        k = o["key"]
        destino = os.path.join(carpeta, k)
        os.makedirs(os.path.dirname(destino) or carpeta, exist_ok=True)
        with open(destino, "wb") as f:
            f.write(req(API + "/objects/" + urllib.parse.quote(k), crudo=True))
        n += 1
    print(f"Descargados {n} archivos en {carpeta}")


def publicar(carpeta):
    if not os.path.isfile(os.path.join(carpeta, "index.html")):
        sys.exit("La carpeta no tiene index.html: no publico.")
    remotos = {o["key"]: o.get("etag", "").strip('"') for o in lista()}
    subidos = iguales = 0
    for raiz, dirs, fs in os.walk(carpeta):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in fs:
            p = os.path.join(raiz, f)
            k = os.path.relpath(p, carpeta).replace(os.sep, "/")
            if k.startswith(".") or any(k.endswith(x) or k.startswith(x) for x in NO_SUBIR):
                continue
            datos = open(p, "rb").read()
            if remotos.get(k) == hashlib.md5(datos).hexdigest():
                iguales += 1
                continue
            tipo = TIPOS.get(os.path.splitext(k)[1].lower()) or mimetypes.guess_type(k)[0] or "application/octet-stream"
            req(API + "/objects/" + urllib.parse.quote(k), "PUT", datos, tipo)
            subidos += 1
            print("  subido", k, f"{len(datos)/1e6:.1f} MB")
    print(f"Publicado: {subidos} archivos subidos, {iguales} sin cambios. https://cursillodebolsillo.com")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("descargar", "publicar", "lista"):
        sys.exit(__doc__)
    if sys.argv[1] == "lista":
        for o in lista():
            print(o["key"], o.get("size"))
    else:
        (descargar if sys.argv[1] == "descargar" else publicar)(sys.argv[2])
