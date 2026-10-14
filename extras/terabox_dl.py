#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
terabox_dl.py — Descargador paralelo y reanudable para enlaces compartidos de TeraBox.
Versión 2: enlaces generados bajo demanda (caducan a los 10 min), cookie enviada al CDN,
detección de sesión corregida y manejo de las respuestas reales del servidor.

  * N archivos en paralelo            (--workers)
  * M conexiones HTTP Range por archivo (--connections)
  * Reanudación real: si se corta, sigue donde quedó
  * Reintentos con backoff y regeneración automática del enlace

Requisitos: Python 3.9+ y `pip install requests`
"""

from __future__ import annotations
import argparse, csv, json, math, os, re, sys, threading, time, random
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, parse_qs

try:
    import requests
except ImportError:
    sys.exit("Falta la librería 'requests'. Instálala con:  pip install requests")

# --- que Windows no rompa con los acentos ----------------------------------- #
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MIRRORS = [
    "https://www.1024tera.com",
    "https://www.terabox.com",
    "https://www.terabox.app",
    "https://www.freeterabox.com",
    "https://www.1024terabox.com",
    "https://www.mirrobox.com",
    "https://www.nephobox.com",
]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
APP_ID = "250528"
CHUNK = 1024 * 512
MIN_SPLIT = 8 * 1024 * 1024      # no partir en rangos los archivos de menos de 8 MB
LINK_TTL = 600                   # los dlink de TeraBox caducan a los 10 minutos

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def safe_name(name: str) -> str:
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name)
    name = name.replace("/", "_").replace("\\", "_").rstrip(" .")
    return (name or "sin_nombre")[:180]


def first_url(v):
    """El servidor devuelve 'dlink' como str, como lista de str o como lista de dicts."""
    if isinstance(v, str):
        return v
    if isinstance(v, list) and v:
        x = v[0]
        if isinstance(x, str):
            return x
        if isinstance(x, dict):
            return x.get("dlink") or x.get("url")
    if isinstance(v, dict):
        return v.get("dlink") or v.get("url")
    return None


# --------------------------------------------------------------------------- #
def extract_surl(url: str) -> str:
    url = (url or "").strip().replace("&amp;", "&")
    if not url:
        return ""
    m = re.search(r"/s/1([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    q = parse_qs(urlparse(url).query)
    if "surl" in q and q["surl"][0]:
        return q["surl"][0].lstrip("1")
    m = re.search(r"surl=1?([A-Za-z0-9_-]{6,})", url)
    if m:
        return m.group(1)
    raise SystemExit(f"No pude extraer el 'surl' de: {url}")


def parse_cookie(raw: str) -> dict:
    """Acepta 'ndus=...', un header Cookie completo y también el prefijo 'Cookie:'."""
    raw = (raw or "").strip().strip('"').strip("'")
    raw = re.sub(r'^\s*Cookie\s*:\s*', '', raw, flags=re.I)   # tolera "Cookie: ndus=..."
    jar = {}
    for part in re.split(r";\s*", raw):
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip()
            if k:
                jar[k] = v.strip()
    return jar


# --------------------------------------------------------------------------- #
class TeraBox:
    def __init__(self, surl, cookie, base=MIRRORS[0], proxy=None, timeout=30, verbose=False):
        self.surl = surl
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.verbose = verbose
        self._proxy = proxy
        self._cookie = dict(cookie or {})
        self._hdr_tpl = {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.base}/sharing/link?surl={surl}",
            "Origin": self.base,
        }
        self._local = threading.local()          # una sesion por hilo (Session no es thread-safe)
        self.js_token = ""
        self.pcftoken = ""
        self.share = {}
        self._sig = {}
        self._lock = threading.Lock()
        self._fails = 0

    # --- sesion por hilo -------------------------------------------------- #
    def _new_session(self):
        s = requests.Session()
        s.headers.update(self._hdr_tpl)
        s.cookies.update(self._cookie)
        if self._proxy:
            s.proxies.update({"http": self._proxy, "https": self._proxy})
        return s

    @property
    def s(self):
        s_ = getattr(self._local, "s", None)
        if s_ is None:
            s_ = self._local.s = self._new_session()
        return s_

    # ---------------- tokens ---------------- #
    def bootstrap(self):
        self._hdr_tpl["Referer"] = self.base + "/"
        self.s.headers["Referer"] = self.base + "/"
        r = self.s.get(self.base + "/main", timeout=self.timeout)
        m = re.search(r'fn%28%22([0-9A-Fa-f]+)%22', r.text)
        if not m:
            raise RuntimeError("No encontré el jsToken en /main. Prueba con --base y otro mirror.")
        self.js_token = m.group(1)
        ref = f"{self.base}/sharing/link?surl={self.surl}"
        self._hdr_tpl["Referer"] = ref
        self.s.headers["Referer"] = ref
        h = self.s.get(f"{self.base}/sharing/link?surl={self.surl}", timeout=self.timeout).text
        m2 = re.search(r'pcftoken["\\:=\s]+([0-9a-f]{32})', h)
        self.pcftoken = m2.group(1) if m2 else ""
        # propagar las cookies de sesion (TSID, browserid...) a los hilos nuevos
        try:
            self._cookie.update(self.s.cookies.get_dict())
        except Exception:
            pass
        if self.verbose:
            log(f"    jsToken  = {self.js_token[:18]}…")
            log(f"    pcftoken = {self.pcftoken}")

    def refresh_token(self):
        try:
            r = self.s.get(self.base + "/main", timeout=self.timeout)
            m = re.search(r'fn%28%22([0-9A-Fa-f]+)%22', r.text)
            if m:
                self.js_token = m.group(1)
        except Exception:
            pass

    # ---------------- sesión ---------------- #
    def check_auth(self) -> bool:
        """La respuesta real es {"errno":0,"uk":...} — no trae data.login_status."""
        try:
            d = self.s.get(self.base + "/api/check/login", timeout=self.timeout).json()
            if d.get("errno") == 0 and d.get("uk"):
                return True
        except Exception:
            pass
        return False

    # ---------------- metadatos ---------------- #
    def shorturl_info(self):
        p = {"clientfrom": "h5", "psign": "0", "pcftoken": self.pcftoken, "clienttype": "0",
             "channel": "dubox", "shorturl": "1" + self.surl, "root": "1", "scene": "",
             "app_id": APP_ID, "web": "1", "jsToken": self.js_token}
        d = self.s.get(self.base + "/api/shorturlinfo", params=p, timeout=self.timeout).json()
        if d.get("errno") not in (0, None):
            raise RuntimeError(f"shorturlinfo errno={d.get('errno')} {d.get('errmsg')}")
        self.share = d
        self._sig = {k: d.get(k) for k in ("sign", "timestamp", "shareid", "uk") if d.get(k)}
        if d.get("randsk"):
            self._sig["randsk"] = d["randsk"]
        return d

    # ---------------- listado ---------------- #
    def list_dir(self, path: str = "") -> list:
        p = {"clientfrom": "h5", "psign": "0", "pcftoken": self.pcftoken, "clienttype": "0",
             "channel": "dubox", "page": "1", "num": "1000", "web": "1", "scene": "",
             "shorturl": self.surl, "by": "time", "order": "desc", "app_id": APP_ID,
             "jsToken": self.js_token}
        p["root"] = "1" if not path else "0"
        if path:
            p["dir"] = path
        last = None
        for attempt in range(4):
            d = self.s.get(self.base + "/share/list", params=p, timeout=self.timeout).json()
            if d.get("errno") == 0:
                return d.get("list", []) or []
            last = d
            time.sleep(1.2 * (attempt + 1))
            self.refresh_token()
            p["jsToken"] = self.js_token
        log(f"    ! errno={last.get('errno')} al listar {path or '/'} — se omite")
        return []

    def walk(self) -> list:
        files, ndirs, seen, queue = [], 1, set(), [""]
        while queue:
            path = queue.pop(0)
            if path in seen:
                continue
            seen.add(path)
            for it in self.list_dir(path):
                if str(it.get("isdir")) == "1":
                    ndirs += 1
                    queue.append(it["path"])
                else:
                    files.append(it)
            time.sleep(0.12)
        log(f"  Carpetas recorridas: {ndirs}   Archivos encontrados: {len(files)}")
        return files

    # ---------------- enlace directo (UN archivo) ---------------- #
    def dlink_one(self, fs_id, tries: int = 4):
        """Pide el dlink de UN solo archivo.

        Ojo: si se piden varios fs_id a la vez, TeraBox devuelve un .zip, así que
        siempre llamamos de uno en uno. El enlace caduca a los 10 min, por eso se
        genera justo antes de descargar.
        """
        last = None
        for attempt in range(tries):
            p = {"clientfrom": "h5", "psign": "0", "pcftoken": self.pcftoken, "clienttype": "0",
                 "channel": "dubox", "web": "1", "scene": "", "shorturl": self.surl,
                 "app_id": APP_ID, "jsToken": self.js_token, **self._sig,
                 "fid_list": f"[{fs_id}]", "type": "1"}
            try:
                d = self.s.get(self.base + "/share/download", params=p, timeout=self.timeout).json()
            except Exception as e:
                d = {"errno": -1, "errmsg": str(e)[:80]}
            last = d
            if d.get("errno") == 0:
                u = first_url(d.get("dlink"))
                if u:
                    with self._lock:
                        self._fails = 0
                    return u
            # escalera de recuperacion: el fallo sostenido suele ser ficha caducada
            if attempt == 1:
                self.refresh_token()
            elif attempt == 2:
                self.refresh_share()
            elif attempt >= 3:
                self.refresh_token()
                self.refresh_share()
            with self._lock:
                n_fails = self._fails
            # si llevamos muchos fallos seguidos, pausa larga y ficha nueva
            if n_fails >= 5:
                log(f"      [dlink] {n_fails} fallos seguidos: pausa de 30 s y ficha nueva")
                time.sleep(30)
                self.refresh_share()
            elif n_fails >= 2:
                time.sleep(3 * n_fails)
            if attempt < tries - 1:
                time.sleep(1.0 * (attempt + 1) + random.uniform(0, .5))
        with self._lock:
            self._fails += 1
            n = self._fails
        log(f"      [dlink] errno={last.get('errno')} errmsg={last.get('errmsg')} "
            f"fs_id={fs_id} (fallos seguidos: {n})")
        return None

    def refresh_share(self):
        """Regenera pcftoken + sign/timestamp: la ficha entera, no solo el jsToken."""
        try:
            self.bootstrap()
            self.shorturl_info()
        except Exception as e:
            log(f"      [refresh_share] {e}")


# --------------------------------------------------------------------------- #
class FileDownloader:
    def __init__(self, proto_sess, timeout, retries, quiet=True):
        self._proto = proto_sess
        self._local = threading.local()
        self.timeout = timeout
        self.retries = retries
        self.quiet = quiet

    @property
    def s(self):
        """Una sesion distinta por hilo: requests.Session NO es thread-safe."""
        s_ = getattr(self._local, "s", None)
        if s_ is None:
            s_ = requests.Session()
            s_.headers.update({"User-Agent": UA,
                               "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"})
            try:
                s_.cookies.update(self._proto.cookies.get_dict())
                s_.proxies.update(self._proto.proxies)
            except Exception:
                pass
            s_ = self._local.s = s_
        return s_

    def _fetch(self, url, start, end, sink, cookie_hdr):
        headers = {"User-Agent": UA, "Range": f"bytes={start}-{end}"}
        if cookie_hdr:
            headers["Cookie"] = cookie_hdr        # el CDN da 403 sin la cookie
        last = None
        for attempt in range(self.retries + 1):
            try:
                with self.s.get(url, headers=headers, stream=True, timeout=self.timeout) as r:
                    if r.status_code in (403, 404, 410):
                        raise RuntimeError(f"HTTP {r.status_code} (enlace caducado)")
                    if r.status_code == 416:
                        return 0
                    if r.status_code >= 400:
                        raise RuntimeError(f"HTTP {r.status_code}")
                    for chunk in r.iter_content(CHUNK):
                        if chunk:
                            sink.write(chunk)
                            sink.flush()
                return end - start + 1
            except Exception as e:
                last = e
                if attempt < self.retries:
                    time.sleep(1.5 * (2 ** attempt) + random.uniform(0, .6))
        raise last

    def _stream_whole(self, url, dest, cookie_hdr):
        """TeraBox reporta tamaño 0 en 43 .srt: sin rangos ni tamaño que validar."""
        tmp = dest + ".part"
        headers = {"User-Agent": UA}
        if cookie_hdr:
            headers["Cookie"] = cookie_hdr
        last = None
        for attempt in range(self.retries + 1):
            try:
                with self.s.get(url, headers=headers, stream=True, timeout=self.timeout) as r:
                    if r.status_code >= 400:
                        raise RuntimeError(f"HTTP {r.status_code}")
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(CHUNK):
                            if chunk:
                                f.write(chunk)
                if os.path.getsize(tmp) == 0 and r.headers.get("Content-Length") not in (None, "0"):
                    raise RuntimeError("descarga vacia")
                if os.path.exists(dest):
                    os.remove(dest)
                os.rename(tmp, dest)
                return "OK"
            except Exception as e:
                last = e
                if attempt < self.retries:
                    time.sleep(1.5 * (2 ** attempt))
        return f"ERROR {last}"

    def run(self, provider, dest, size, conns, cookie_hdr, max_rounds=4):
        """Descarga `dest` usando rangos paralelos.

        `provider()` devuelve un enlace fresco. Si algo falla se pide otro enlace
        y se reanuda desde el punto guardado en el .part.json.
        """
        tmp = dest + ".part"
        state_f = dest + ".part.json"
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)

        if os.path.exists(dest) and ((size and os.path.getsize(dest) == size)
                                     or (not size and os.path.getsize(dest) > 0)):
            return "ya descargado"

        # tamaño desconocido (TeraBox lo reporta como 0): descarga directa
        if not size:
            url = provider()
            if not url:
                return "ERROR: no se pudo obtener el enlace de descarga"
            return self._stream_whole(url, dest, cookie_hdr)

        n_conn = 1 if size < MIN_SPLIT else max(1, conns)
        seg = math.ceil(size / n_conn) if size else 0
        segs = [(i * seg, min(size, (i + 1) * seg) - 1) for i in range(n_conn) if i * seg < size]

        if not os.path.exists(tmp):
            with open(tmp, "wb") as f:
                if size:
                    f.truncate(size)
        done = [0] * len(segs)
        if os.path.exists(state_f):
            try:
                st = json.load(open(state_f))
                if st.get("size") == size and st.get("seg") == seg and len(st.get("done", [])) == len(segs):
                    done = st["done"]
                else:
                    os.remove(state_f)
            except Exception:
                pass

        last_err = None
        for rnd in range(max_rounds):
            url = provider()
            if not url:
                return "ERROR: no se pudo obtener el enlace de descarga"

            def worker(idx):
                s0, s1 = segs[idx]
                start = s0 + done[idx]
                if start > s1:
                    return
                with open(tmp, "r+b") as f:
                    f.seek(start)
                    got = self._fetch(url, start, s1, f, cookie_hdr)
                done[idx] += got
                with _print_lock:
                    try:
                        json.dump({"size": size, "seg": seg, "done": done}, open(state_f, "w"))
                    except Exception:
                        pass

            try:
                with ThreadPoolExecutor(max_workers=max(1, len(segs))) as ex:
                    list(ex.map(worker, range(len(segs))))
            except Exception as e:
                last_err = e
                continue                      # enlace nuevo en la siguiente ronda

            final = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            if size and final != size:
                last_err = RuntimeError(f"incompleto {human(final)}/{human(size)}")
                continue
            if os.path.exists(dest):
                os.remove(dest)
            os.rename(tmp, dest)
            if os.path.exists(state_f):
                os.remove(state_f)
            return "OK"

        return f"ERROR {last_err}"


# --------------------------------------------------------------------------- #
def build_tasks(files, out, only, exts, min_mb, max_mb):
    tasks = []
    for f in files:
        name = safe_name(f["server_filename"])
        path = f["path"]
        size = int(f.get("size", 0))
        mb = size / 1048576
        if only and not any(o.lower() in path.lower() for o in only):
            continue
        if exts and os.path.splitext(name)[1].lower() not in exts:
            continue
        if min_mb and mb < min_mb:
            continue
        if max_mb and mb > max_mb:
            continue
        rel = path.lstrip("/")
        dest = os.path.join(out, *[safe_name(p) for p in rel.split("/")])
        tasks.append({"fs_id": str(f["fs_id"]), "name": name, "path": path,
                      "size": size, "dest": dest, "md5": f.get("md5", "")})
    return tasks


def write_aria2(tasks, links, path):
    with open(path, "w", encoding="utf-8") as fh:
        for t in tasks:
            u = links.get(t["fs_id"])
            if not u:
                continue
            d = os.path.dirname(t["dest"])
            fh.write(f"{u}\n  dir={d}\n  out={t['name']}\n  continue=true\n"
                     f"  max-connection-per-server=16\n  split=16\n"
                     f"  header=Cookie: {links.get('_cookie','')}\n\n")
    log(f"  Archivo para aria2 escrito: {path}")


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(
        description="Descargador paralelo y reanudable de enlaces compartidos de TeraBox.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--url", help="Enlace compartido de TeraBox (o usa --surl)")
    ap.add_argument("--surl", help="Solo el código surl")
    ap.add_argument("--cookie", default=os.environ.get("TERABOX_COOKIE", ""),
                    help='Cookie de sesión: "ndus=XXXX" (o el header Cookie completo)')
    ap.add_argument("--cookie-file", help="Archivo de texto con la cookie")
    ap.add_argument("--out", default="./descargas", help="Carpeta de destino")
    ap.add_argument("--workers", type=int, default=4, help="Archivos en paralelo (4)")
    ap.add_argument("--connections", type=int, default=12, help="Conexiones por archivo (12)")
    ap.add_argument("--only", action="append", default=[], help="Solo rutas que contengan este texto (repetible)")
    ap.add_argument("--ext", default="", help="Solo estas extensiones, p.ej. .mp4,.pdf")
    ap.add_argument("--min-mb", type=float, default=0)
    ap.add_argument("--max-mb", type=float, default=0)
    ap.add_argument("--list-only", action="store_true", help="Solo listar y generar manifiesto")
    ap.add_argument("--aria2", metavar="ARCHIVO", help="Exportar enlaces para aria2")
    ap.add_argument("--manifest", metavar="CSV")
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--proxy", default="")
    ap.add_argument("--base", default=MIRRORS[0], choices=MIRRORS)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    surl = a.surl or extract_surl(a.url or "")
    if not surl:
        ap.error("Indica --url o --surl")

    # cookie: --cookie > --cookie-file > cookie.txt junto al script > entorno
    cookie_raw = a.cookie
    cf = a.cookie_file
    if not cookie_raw and not cf:
        auto = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookie.txt")
        if os.path.exists(auto):
            cf = auto
    if cf:
        if not os.path.exists(cf):
            ap.error(f"No existe el archivo de cookie: {cf}")
        cookie_raw = open(cf, encoding="utf-8").read()
    cookie = parse_cookie(cookie_raw)
    cookie_hdr = "; ".join(f"{k}={v}" for k, v in cookie.items())
    masked = (list(cookie.values())[0][:6] + "…" + list(cookie.values())[0][-4:]) if cookie else ""

    print("=" * 68)
    print("  TeraBox - descargador paralelo  v2")
    print("=" * 68)
    print(f"  surl    : {surl}")
    print(f"  destino : {os.path.abspath(a.out)}")
    print(f"  cookie  : {masked or 'NINGUNA (solo listado)'}")

    tb = TeraBox(surl, cookie, base=a.base, proxy=a.proxy or None,
                 timeout=a.timeout, verbose=a.verbose)
    log("\n[1/4] Obteniendo tokens de la sesion...")
    tb.bootstrap()
    logged = tb.check_auth()
    log(f"      Estado de sesion: {'AUTENTICADO' if logged else 'ANONIMO (sin login)'}")
    if not logged and not a.list_only:
        log("\n" + "!" * 68)
        log("  AVISO: TeraBox no reconoce la cookie. Sin sesion no hay enlaces de")
        log("  descarga. Revisa cookie.txt (la ndus caduca si cierras sesion en")
        log("  otro dispositivo). Solo funcionara el listado.")
        log("!" * 68)

    log("\n[2/4] Resolviendo el recurso compartido...")
    info = tb.shorturl_info()
    log(f"      shareid={info.get('shareid')}  uk={info.get('uk')}")

    log("\n[3/4] Recorriendo carpetas...")
    files = tb.walk()
    if not files:
        sys.exit("No se encontraron archivos. El enlace puede haber caducado.")
    total = sum(int(f.get("size", 0)) for f in files)
    log(f"      TOTAL: {len(files)} archivos - {human(total)}")

    man = a.manifest or os.path.join(a.out, "manifest.csv")
    os.makedirs(os.path.dirname(os.path.abspath(man)) or ".", exist_ok=True)
    with open(man, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["carpeta", "archivo", "bytes", "MB", "fs_id", "ruta", "md5"])
        for f in sorted(files, key=lambda x: x["path"]):
            w.writerow([os.path.dirname(f["path"]), f["server_filename"], f.get("size", 0),
                        round(int(f.get("size", 0)) / 1048576, 2), f["fs_id"],
                        f["path"], f.get("md5", "")])
    log(f"      Manifiesto: {man}")

    exts = [e.strip().lower() if e.startswith(".") else "." + e.strip().lower()
            for e in a.ext.split(",") if e.strip()]
    tasks = build_tasks(files, a.out, a.only, exts, a.min_mb, a.max_mb)
    sel = sum(t["size"] for t in tasks)
    log(f"\n      Seleccionados: {len(tasks)} archivos - {human(sel)}")

    if a.list_only:
        log("\nModo --list-only: no se descarga nada.")
        return

    if not cookie:
        log("\nSin cookie no se puede descargar. Pasa --cookie \"ndus=...\" o crea cookie.txt.")
        return

    os.makedirs(a.out, exist_ok=True)

    if a.aria2:
        log("\n[4/4] Generando enlaces para aria2...")
        links, fail = {}, 0
        for i, t in enumerate(tasks, 1):
            u = tb.dlink_one(t["fs_id"])
            if u:
                links[t["fs_id"]] = u
            else:
                fail += 1
            if i % 25 == 0:
                log(f"      {i}/{len(tasks)}...")
        links["_cookie"] = cookie_hdr
        log(f"      Enlaces: {len(links)-1}/{len(tasks)}  (fallidos: {fail})")
        write_aria2(tasks, links, a.aria2)
        log("\n  ATENCION: los enlaces caducan a los 10 min. Arranca aria2 YA:")
        log(f'  aria2c -c -i "{a.aria2}" -j {a.workers} -s {a.connections} -x {a.connections}')
        return

    # ---- descarga ---- #
    fd = FileDownloader(tb.s, timeout=(a.timeout, 180), retries=a.retries, quiet=not a.verbose)
    ok = err = skip = 0
    bytes_done = 0
    t0 = time.time()
    lock = threading.Lock()

    def do(t):
        nonlocal ok, err, skip, bytes_done
        # enlace fresco justo antes de descargar (caduca a los 10 min)
        def provider():
            return tb.dlink_one(t["fs_id"])
        r = fd.run(provider, t["dest"], t["size"], a.connections, cookie_hdr)
        with lock:
            if r == "OK":
                ok += 1
                bytes_done += t["size"]
                log(f"  OK  {t['name']} ({human(t['size'])})")
            elif r == "ya descargado":
                skip += 1
                bytes_done += t["size"]
            else:
                err += 1
                log(f"  ERR {t['name']}: {r}")

    log(f"\n[4/4] Descargando {len(tasks)} archivos ({human(sel)}) "
        f"con {a.workers} hilos x {a.connections} conexiones...\n")
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(do, t) for t in tasks]
        for _ in as_completed(futs):
            pass

    dt = max(time.time() - t0, 1)
    print("\n" + "=" * 68)
    print(f"  Completados: {ok}   Ya existian: {skip}   Con error: {err}")
    print(f"  Descargado : {human(bytes_done)} en {dt/60:.1f} min  ({human(bytes_done/dt)}/s)")
    print(f"  Destino    : {os.path.abspath(a.out)}")
    print("=" * 68)
    if err:
        log("\nVuelve a ejecutar el mismo comando: reanuda solo lo que falta.")


if __name__ == "__main__":
    main()
