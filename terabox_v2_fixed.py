#!/usr/bin/env python3
# terabox_dl.py — v2, fusión: núcleo verificado tuyo + arquitectura mía
import argparse, json, logging, os, re, shutil, signal, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
import requests
from tqdm import tqdm
try:
    import psutil
except ImportError:
    psutil = None

LOG = logging.getLogger("tb")
MIRRORS = ["www.terabox.com", "www.1024tera.com", "1024terabox.com",
           "www.teraboxapp.com", "4funbox.com", "mirrobox.com", "teraboxshare.com"]

def build_session(cookie=None, proxy=None):
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                      "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"})
    if cookie:
        raw = cookie if "=" in cookie else f"ndus={cookie}"
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                s.cookies.set(k.strip(), v.strip())   # TARRO, no cabecera
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s

TOKENS = {}
TOKENS_LOCK = threading.RLock()
REFRESH_LOCK = threading.RLock()

def bootstrap(sess, base):
    with REFRESH_LOCK:
        for attempt in range(3):
            try:
                r = sess.get(f"https://{base}/wap/share/filelist", timeout=20)
                if r.ok: break
            except requests.RequestException:
                pass
            time.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError(f"no pude cargar la página de {base}")
        m = (re.search(r'fn%28%22([0-9A-Fa-f]+)%22', r.text)
             or re.search(r'jsToken["\':=\s]+([0-9A-Fa-f%]+)', r.text))
        if not m:
            raise RuntimeError("no encontré jsToken")
        TOKENS[base] = {"jsToken": m.group(1)}
    return TOKENS[base]

def ensure_tokens(sess, base):
    with TOKENS_LOCK:
        if base not in TOKENS:
            bootstrap(sess, base)
        return dict(TOKENS[base])

def shorturl_from(link):
    p = urlparse(link); q = parse_qs(p.query)
    if "surl" in q: surl = q["surl"][0]
    else:
        m = re.search(r'/s/([A-Za-z0-9_-]+)', p.path)
        if not m: raise ValueError(f"no entiendo este link: {link}")
        surl = m.group(1)
    # Un surl de TeraBox mide 22 caracteres. Algunos enlaces llegan precedidos de
    # un "1" (prefijo de shorturl), asi que solo se recorta si mide MAS de 22:
    # recortar sin esa condicion rompe los surl que empiezan por "1" de verdad
    # (p. ej. 1Mx1GP2rPLypBFF1RlVMog -> need verify_v2).
    if surl.startswith("1") and len(surl) > 22:
        surl = surl[1:]
    return surl

class ShareClient:
    def __init__(self, sess, mirrors):
        self.sess = sess; self.mirrors = list(mirrors); self.sign = self.ts = ""
    def _api(self, path, params):
        last = None
        for dom in list(self.mirrors):
            p = dict(params); p.setdefault("app_id", "250528"); p.setdefault("web", "1")
            p.update(ensure_tokens(self.sess, dom))
            try:
                r = self.sess.get(f"https://{dom}{path}", params=p, timeout=25)
                if r.status_code == 429:
                    wait = int(r.headers.get("Retry-After", 10))
                    LOG.warning("429: espero %ss", wait); time.sleep(wait); continue
                r.raise_for_status(); j = r.json()
                if j.get("errno") == 0:
                    if dom != self.mirrors[0]:
                        self.mirrors.remove(dom); self.mirrors.insert(0, dom)
                    return j
                last = j
                if j.get("errno") == -9:
                    pwd = input(f"Contraseña del share: ")
                    p["pwd"] = pwd; p["extra"] = quote(pwd)
                    r = self.sess.get(f"https://{dom}{path}", params=p, timeout=25)
                    j = r.json()
                    if j.get("errno") == 0: return j
            except (requests.RequestException, ValueError) as e:
                last = e
        raise RuntimeError(f"todos los mirrors fallaron: {last}")
    def list_all(self, surl):
        j = self._api("/api/shorturlinfo", {"shorturl": "1" + surl, "root": 1})
        info = j.get("data") or j
        shareid, uk = info["shareid"], info["uk"]
        self.sign, self.ts = info.get("sign", ""), info.get("timestamp", "")
        out, stack = [], [("", True)]
        while stack:
            d, root = stack.pop()
            p = {"shareid": shareid, "uk": uk, "shorturl": surl,
                 "sign": self.sign, "timestamp": self.ts}
            if root: p["root"] = 1
            else:    p["dir"] = d
            for it in self._api("/share/list", p).get("list", []):
                if it.get("isdir") in ("1", 1):
                    stack.append((it["path"], False))
                else:
                    out.append({"fs_id": it["fs_id"], "rel": it["path"].lstrip("/"),
                                "name": it["server_filename"], "size": int(it.get("size", 0))})
        return out, shareid, uk
    def dlink(self, surl, shareid, uk, fs_id):
        j = self._api("/share/download", {"shareid": shareid, "uk": uk, "shorturl": surl,
            "sign": self.sign, "timestamp": self.ts, "fid_list": f"[{fs_id}]"})
        if j.get("errno") != 0: raise RuntimeError(f"errno {j.get('errno')} pidiendo dlink")
        return j["dlink"]

CHUNK = 64 * 1024 * 1024
SPEED_LOCK = threading.Lock(); SPEED_WINDOW = []
def add_speed(n):
    with SPEED_LOCK:
        now = time.time(); SPEED_WINDOW.append((now, n))
        while SPEED_WINDOW and SPEED_WINDOW[0][0] < now - 10: SPEED_WINDOW.pop(0)
def speed():
    with SPEED_LOCK:
        if len(SPEED_WINDOW) < 2: return 0.0
        dt = SPEED_WINDOW[-1][0] - SPEED_WINDOW[0][0]
        return sum(b for _, b in SPEED_WINDOW) / max(dt, 1e-9)

BAD = '<>:"|?*'
def safe_rel(rel):
    parts = []
    for piece in rel.split("/"):
        piece = piece.replace("/", "_")
        piece = "".join("" if c in BAD or ord(c) < 32 else c for c in piece).rstrip(". ")
        parts.append(piece or "_")
    return Path(*parts)

def fetch_range(sess, url, part_path, seg_start, seg_end, prog, state):
    """Descarga [seg_start, seg_end]. Presupuesto de 540 s por ronda
    (renegociar dlink antes de los 600 s de caducidad). Devuelve:
      True      -> segmento completo
      False     -> fallo transitorio (429/red), reintentar con el mismo url
      "expired" -> dlink caducado/invalido, el llamador debe renegociar"""
    deadline = time.time() + 540
    while time.time() < deadline and not state["abort_fn"]():
        offset = seg_start + prog.get(seg_start, 0)
        if offset > seg_end:
            return True
        h = {"Range": f"bytes={offset}-{seg_end}",
             "User-Agent": "netdisk;P2SP;2.2.91.136"}
        try:
            with sess.get(url, headers=h, stream=True, timeout=(10, 60)) as r:
                if r.status_code == 429:
                    time.sleep(int(r.headers.get("Retry-After", 15)))
                    continue
                if r.status_code in (403, 404) or r.status_code >= 500:
                    return "expired"          # este dlink ya no sirve
                r.raise_for_status()
                with open(part_path, "r+b") as f:
                    f.seek(offset)
                    for ch in r.iter_content(1 << 20):
                        if state["abort_fn"]():
                            return False
                        f.write(ch)
                        prog[seg_start] = prog.get(seg_start, 0) + len(ch)
                        state["written"] += len(ch)
                        add_speed(len(ch))
                return True
        except requests.RequestException as e:
            LOG.debug("range retry: %s", e)
            time.sleep(min(30, 2 ** state.setdefault("fails", 0)))
            state["fails"] += 1
    return "expired"   # se acabo el presupuesto: renegociar dlink

def download_file(client, surl, shareid, uk, item, outdir, connections, STOP):
    rel = safe_rel(item["rel"]); dest = Path(outdir) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part"); meta = dest.with_suffix(dest.suffix + ".part.json")
    size = item["size"]
    if dest.exists() and dest.stat().st_size == size: return "ok", "ya estaba"
    if size == 0:
        dest.touch(); return "ok", "vacío"
    prog = {}
    if meta.exists() and part.exists():
        try:
            m = json.loads(meta.read_text())
            if m.get("size") == size: prog = {int(k): v for k, v in m["segments"].items()}
        except Exception: prog = {}
    if not part.exists():
        with open(part, "wb") as f: f.truncate(size)
        prog = {}
    segs = [(a, min(a + CHUNK, size) - 1) for a in range(0, size, CHUNK)]
    pending = [s for s in segs if prog.get(s[0], 0) < (s[1] - s[0] + 1)]
    state = {"abort_fn": STOP.is_set, "written": 0}
    url = client.dlink(surl, shareid, uk, item["fs_id"])
    MAX_ATTEMPTS = 8          # tope de renegociaciones de dlink por archivo
    BACKOFF = 5               # segundos base, crece x2 por intento

    with ThreadPoolExecutor(max_workers=connections) as ex:
        attempts = 0
        while pending and not STOP.is_set():
            futs = {ex.submit(fetch_range, client.sess, url, part, a, b, prog, state): (a, b)
                    for a, b in pending}
            done, expired, transient = [], [], []
            for fu in as_completed(futs):
                a, b = futs[fu]
                r = fu.result()
                if r is True:
                    done.append((a, b))
                elif r == "expired":
                    expired.append((a, b))
                else:
                    transient.append((a, b))
                meta.write_text(json.dumps(
                    {"size": size, "segments": {str(k): v for k, v in prog.items()}}))
            pending = [s for s in pending if s not in done]
            attempts += 1

            if pending and attempts >= MAX_ATTEMPTS:
                LOG.error("%s: %d intentos sin completar %d segmentos; lo dejo "
                          "para reanudar despues", item["rel"], attempts, len(pending))
                return "fail", f"agoto {attempts} intentos, {sum(prog.values())}/{size} bytes"

            if pending and (expired or transient):
                # backoff SIEMPRE que hubo fallo — cierra el bucle cerrado
                wait = min(120, BACKOFF * 2 ** attempts)
                LOG.warning("%s: %d segmentos pendientes, espero %ds antes de "
                            "renegociar dlink (intento %d/%d)",
                            item["rel"], len(pending), wait, attempts, MAX_ATTEMPTS)
                STOP.wait(wait)   # espera interrumpible por Ctrl+C
                try:
                    url = client.dlink(surl, shareid, uk, item["fs_id"])
                except RuntimeError as e:
                    LOG.error("%s: %s", item["rel"], e)
            elif pending:
                time.sleep(1)
    meta.write_text(json.dumps({"size": size, "segments": {str(k): v for k, v in prog.items()}}))
    if all(prog.get(a, 0) >= (b - a + 1) for a, b in segs):
        part.rename(dest); meta.unlink(missing_ok=True); return "ok", "descargado"
    return "fail", f"{sum(prog.values())}/{size} bytes"

STOP = threading.Event()
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url"); ap.add_argument("-o", "--out", default="terabox_out")
    ap.add_argument("-c", "--connections", type=int, default=8)
    ap.add_argument("--cookie", default=None,
                    help="linea ndus=... (si se omite: variable TERABOX_COOKIE "
                         "o cookie.txt junto al script)")
    ap.add_argument("--por-carpeta", action="store_true",
                    help="una carpeta completa antes de empezar la siguiente "
                         "(coste: el pool se vacia en cada frontera)")
    ap.add_argument("--only"); ap.add_argument("--proxy"); ap.add_argument("--fsids")
    ap.add_argument("--workers-files", type=int, default=4)
    ap.add_argument("--log", default="terabox_dl.log")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(args.log, encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    signal.signal(signal.SIGINT, lambda *_: (LOG.info("Ctrl+C: cerrando con gracia..."), STOP.set()))
    cookie = args.cookie or os.environ.get("TERABOX_COOKIE")
    if not cookie:
        cand = Path(__file__).resolve().parent / "cookie.txt"
        if cand.exists():
            cookie = cand.read_text(encoding="utf-8").strip()
            LOG.info("cookie leida de %s", cand)
    if not cookie:
        ap.error("Falta la cookie: usa --cookie, TERABOX_COOKIE o un cookie.txt "
                 "junto al script")
    sess = build_session(cookie, args.proxy)
    client = ShareClient(sess, MIRRORS)
    surl = shorturl_from(args.url)
    LOG.info("Listando %s ...", surl)
    files, shareid, uk = client.list_all(surl)
    if args.only: files = [f for f in files if re.search(args.only, f["rel"])]
    if args.fsids:
        want = {x.strip() for x in args.fsids.split(",")}
        files = [f for f in files if str(f["fs_id"]) in want]
    def _carpeta(r):
        p = (r or "").replace("\\", "/")
        m = re.search(r"Aprende a tocar el Piano/([^/]+)", p)
        return m.group(1) if m else "(raiz)"

    def _orden(r):
        k = _carpeta(r)
        m = re.match(r"\s*(\d+)", k)
        return (int(m.group(1)) if m else 999, k, r)

    files = sorted(files, key=lambda f: _orden(f.get("rel") or f.get("name") or ""))
    if args.por_carpeta:
        grupos = {}
        for f in files:
            grupos.setdefault(_carpeta(f.get("rel") or f.get("name") or ""), []).append(f)
        tandas = list(grupos.items())
        LOG.info("Modo por-carpeta: %d carpetas", len(tandas))
    else:
        tandas = [("TODO (ordenado por carpeta)", files)]

    total = sum(f["size"] for f in files)
    LOG.info("%d archivos, %.2f GB", len(files), total / 1e9)
    if psutil:
        free = shutil.disk_usage(args.out if os.path.exists(args.out) else ".").free
        if free < total * 1.02:
            LOG.error("Espacio insuficiente"); sys.exit(1)
    os.makedirs(args.out, exist_ok=True)
    bar = tqdm(total=total, unit="B", unit_scale=True, dynamic_ncols=True)
    results, t0 = {}, time.time()
    last_vel_log = time.time()
    vel_history = []          # velocidades instantaneas cada 60 s
    STALL_MIN = 10            # minutos sin progreso real antes de actuar
    per_file = max(1, args.connections // args.workers_files)
    for nombre, tanda in tandas:
        LOG.info("=== %s ===", nombre)
        with ThreadPoolExecutor(max_workers=args.workers_files) as file_pool:
            futs = {fu: f for f in tanda for fu in [file_pool.submit(download_file, client, surl, shareid, uk, f, args.out, per_file, STOP)]}
            while futs:
                time.sleep(2)
                if STOP.is_set():
                    for fu in futs: fu.cancel()
                    LOG.info("Interrumpido"); break
                for fu in [f for f in futs if f.done()]:
                    item = futs.pop(fu)
                    try: st = fu.result()
                    except Exception as e: st = ("fail", str(e)); LOG.exception("fallo %s", item["name"])
                    results[item["rel"]] = st
                    if st[0] == "ok": bar.update(item["size"])
                    else: LOG.error("FALLO %s: %s", item["rel"], st[1])
                sp = speed(); eta = (total - bar.n) / sp if sp > 0 else 0
                bar.set_postfix(vel=f"{sp/1e6:.1f}MB/s", eta=f"{eta/60:.0f}min", pend=len(futs))

                if time.time() - last_vel_log > 60:
                    last_vel_log = time.time()
                    vel_history.append(sp)
                    vel_history = vel_history[-30:]  # ultimos 30 min
                    LOG.info("vel %.1f MB/s | eta %.0f min | pend %d | total %d/%d",
                             sp / 1e6, eta / 60, len(futs), bar.n, total)
                    if len(vel_history) >= STALL_MIN and \
                       sum(vel_history[-STALL_MIN:]) / STALL_MIN < 100_000:
                        avg = sum(vel_history[-STALL_MIN:]) / STALL_MIN
                        LOG.warning("Degradacion sostenida %.1f KB/s durante %d min "
                                    "- reinicio de tokens y giro de mirror",
                                    avg / 1e3, STALL_MIN)
                        with REFRESH_LOCK:
                            TOKENS.clear()
                        vel_history.clear()
                        client.mirrors.append(client.mirrors.pop(0))
    bar.close()
    ok = sum(1 for s in results.values() if s[0] == "ok")
    LOG.info("RESUMEN: %d/%d OK en %.1f min. Fallos: %s", ok, len(results), (time.time()-t0)/60,
             [k for k, v in results.items() if v[0] == "fail"][:20])
if __name__ == "__main__":
    main()
