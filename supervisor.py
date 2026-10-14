#!/usr/bin/env python3
# supervisor.py — vigila la descarga, arregla la sesion cuando TeraBox pide
# re-verificacion, y relanza conservando lo bajado. UNA CUENTA POR INSTANCIA.
#
# Para N cuentas: levantá N instancias con --cookie-file distinto, --log-descarga
# distinto, --log distinto y una particion distinta (--only-file / --only / --fsids).
# Si una cuenta recibe verify_v2, cae solo esa instancia; las otras siguen.
# Para automatizarlo: orquestar.py
#
# Que hace, en concreto:
#   * Lee la configuracion optima medida por medir_techo.py (--techo-json) y
#     arranca el descargador con ella. En cada relanzamiento usa LA MISMA config.
#   * Vigila el log del descargador en vivo. Si aparece `need verify_v2`,
#     400310 o 400210 (sesion vencida / re-verificacion pedida):
#       - corre el refresco de cookie DE SU CUENTA (renovar_cookie.py --check)
#       - si el refresco no arregla nada, ESPERA a que pegues el ndus nuevo
#       - relanza. Lo bajado NO se pierde (.part.json).
#   * Si aparece errno -6 / ip_blacklist: PARA TODO. Eso no se reintenta.
#   * Lleva un log central: [FECHA] [INTENTO n] [ESTADO] mensaje.
#
# Uso tipico (una cuenta):
#   python supervisor.py --url "<link>" -o C:\TB --techo-json informe_techo.json
# Uso multi-cuenta (una instancia por cuenta):
#   python supervisor.py --url "<link>" -o C:\TB --cookie-file cookies\acc2.txt ^
#          --only-file particiones\acc2.txt --log-descarga logs\acc2.log ^
#          --log logs\supervisor_acc2.log --etiqueta acc2
#
# Codigos de salida:
#   0 = termino sin fallos    1 = termino con archivos pendientes
#   2 = frenado (ban/blacklist)  3 = sesion no se pudo arreglar  130 = Ctrl+C

import argparse, json, os, re, subprocess, sys, threading, time
from datetime import datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
LOG_CENTRAL = os.path.join(AQUI, "supervisor.log")

# --- patrones que buscamos en la salida del descargador -------------------
RE_SESION = re.compile(r"need verify_v2|400310|400210", re.I)
RE_BAN = re.compile(r"ip_blacklist|errno\s*-6\b", re.I)
RE_PROGRESO = re.compile(
    r"vel\s+([\d.]+)\s+MB/s\s*\|\s*eta\s+(\d+)\s*min\s*\|\s*pend\s+(\d+)\s*\|\s*total\s+(\d+)/(\d+)")
RE_RESUMEN = re.compile(r"RESUMEN:\s*(\d+)/(\d+)\s*OK")
RE_FALLO = re.compile(r"\bFALLO\b")
RE_429 = re.compile(r"\b429\b")


def consola_utf8():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


class Log:
    def __init__(self, ruta, etiqueta=None):
        self.ruta = ruta
        self.etiqueta = etiqueta
        self.lock = threading.Lock()

    def __call__(self, estado, msg, intento=None):
        t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cab = f"[{t}]"
        if self.etiqueta:
            cab += f" [{self.etiqueta}]"
        if intento is not None:
            cab += f" [INTENTO {intento}]"
        linea = f"{cab} [{estado}] {msg}"
        with self.lock:
            print(linea, flush=True)
            try:
                os.makedirs(os.path.dirname(self.ruta) or ".", exist_ok=True)
                with open(self.ruta, "a", encoding="utf-8") as f:
                    f.write(linea + "\n")
            except OSError:
                pass


def construir_comando(args):
    if args.comando:
        return args.comando
    if not (args.url and args.destino):
        raise SystemExit("[X] pasá --url <link> y -o <destino> (o --comando para probar)")
    w, c = args.workers_files, args.conexiones
    if args.techo_json and os.path.exists(args.techo_json):
        try:
            with open(args.techo_json, encoding="utf-8") as f:
                mejor = json.load(f)["mejor"]
            w, c = max(1, int(mejor["archivos"])), max(1, int(mejor["conexiones"]))
            print(f"  config optima leida de {args.techo_json}: "
                  f"workers-files {w} x {c} conexiones = {w*c} conexiones activas")
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as e:
            print(f"  [!] no pude leer {args.techo_json} ({e}); uso {w}x{c}")
    cmd = [sys.executable, "-u", os.path.join(AQUI, "terabox_v2_fixed.py"),
           args.url, "-o", args.destino,
           "--workers-files", str(w), "-c", str(c),
           "--log", args.log_descarga]
    # --- NUEVO: la identidad y la particion de ESTA instancia -------------
    if args.cookie_file:
        cmd += ["--cookie-file", args.cookie_file]
    if args.only:
        cmd += ["--only", args.only]
    if args.only_file:
        cmd += ["--only-file", args.only_file]
    if args.fsids:
        cmd += ["--fsids", args.fsids]
    if args.etiqueta:
        cmd += ["--etiqueta", args.etiqueta]
    return cmd


def lanzar(cmd, log, intento, estado):
    """Lanza el descargador y devuelve (codigo, resumen, estado_run)."""
    log(estado, f"lanzando: {' '.join(cmd)}" if isinstance(cmd, list) else f"lanzando: {cmd}",
        intento)
    # el log del descargador se APPENDEA entre corridas: si no marcamos el offset,
    # un RESUMEN viejo se puede leer como si fuera de esta corrida
    ruta_log = os.path.join(AQUI, args_global.log_descarga)
    try:
        offset_previo = os.path.getsize(ruta_log)
    except OSError:
        offset_previo = 0
    entorno = dict(os.environ, PYTHONUNBUFFERED="1")
    proceso = subprocess.Popen(
        cmd, cwd=AQUI, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1, env=entorno,
        shell=isinstance(cmd, str))

    estado_run = {"sesion": False, "ban": False, "n429": 0, "fallos": 0, "progreso": None}
    lock = threading.Lock()

    def leer():
        assert proceso.stdout is not None
        for linea in proceso.stdout:
            linea = linea.rstrip("\n")
            if linea:
                print(f"    {linea}", flush=True)
            if RE_SESION.search(linea):
                estado_run["sesion"] = True
            if RE_BAN.search(linea):
                estado_run["ban"] = True
            if RE_429.search(linea):
                estado_run["n429"] += 1
            if RE_FALLO.search(linea):
                estado_run["fallos"] += 1
            m = RE_PROGRESO.search(linea)
            if m:
                estado_run["progreso"] = tuple(float(x) if "." in x else int(x)
                                               for x in m.groups())

    h = threading.Thread(target=leer, daemon=True)
    h.start()
    codigo = proceso.wait()
    h.join(timeout=5)

    # leer SOLO lo que escribio esta corrida (desde offset_previo)
    resumen = None
    try:
        with open(ruta_log, encoding="utf-8", errors="replace") as f:
            if os.path.getsize(ruta_log) >= offset_previo:
                f.seek(offset_previo)
            for linea in f:
                m = RE_RESUMEN.search(linea)
                if m:
                    resumen = (int(m.group(1)), int(m.group(2)))
    except OSError:
        pass
    return codigo, resumen, estado_run


def refrescar_cookie(args, log, intento):
    """Corre el refresco de sesion de LA MISMA cuenta. 0 = quedo OK."""
    if args.sin_refresco:
        return 1
    cmd = args.refrescar
    log("SESION", f"refrescando cookie: {cmd}", intento)
    try:
        r = subprocess.run(cmd, cwd=AQUI, shell=isinstance(cmd, str),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        for linea in (r.stdout or "").splitlines():
            if linea.strip():
                print(f"    {linea}")
        if r.returncode == 0:
            log("SESION", "la cookie quedo OK", intento)
            return 0
        log("SESION", f"el refresco no alcanzo (exit {r.returncode})", intento)
    except subprocess.TimeoutExpired:
        log("SESION", "el refresco se colgo (>180 s)", intento)
    return 1


def esperar_cookie_manual(args, log, intento):
    """El refresco automatico no alcanzo: hace falta re-login. Esperamos."""
    if args.esperar_cookie <= 0:
        return 1
    log("SESION",
        f"hace falta un ndus nuevo: cerrá sesión en 1024tera.com, volvé a entrar, "
        f"copiá el ndus y corré RENOVAR_COOKIE.bat. Espero hasta "
        f"{args.esperar_cookie//60} min (reviso cada 30 s).", intento)
    limite = time.time() + args.esperar_cookie
    print()
    print("  " + "=" * 66)
    print("   El descargador se detuvo porque TeraBox pide re-verificacion.")
    print("   En otra ventana:")
    print("     1) cerrá sesión en 1024tera.com y volvé a entrar")
    print("     2) F12 - Application - Cookies - 1024tera.com - copiá 'ndus'")
    print("     3) guardala en SU archivo de cookie:")
    print(f"        python renovar_cookie.py --cookie-file \"{args.cookie_file or 'cookie.txt'}\"")
    print("   Lo bajado NO se pierde: al relanzar, sigue desde el segmento donde quedó.")
    print("  " + "=" * 66)
    print()
    while time.time() < limite:
        time.sleep(30)
        if refrescar_cookie(args, log, intento) == 0:
            return 0
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url"), ap.add_argument("-o", "--destino")
    ap.add_argument("--techo-json", help="informe de medir_techo.py; usa su config optima")
    ap.add_argument("--workers-files", type=int, default=4)
    ap.add_argument("--conexiones", "-c", type=int, default=24)
    # --- NUEVO: identidad y particion por instancia -----------------------
    ap.add_argument("--cookie-file", default=None,
                    help="archivo con el ndus de ESTA instancia (una cuenta por instancia)")
    ap.add_argument("--only", default=None, help="patron regex de archivos a bajar")
    ap.add_argument("--only-file", default=None,
                    help="archivo con un patron por linea (se combinan con OR)")
    ap.add_argument("--fsids", default=None, help="fs_id separados por coma")
    ap.add_argument("--etiqueta", default=None, help="prefijo de instancia (acc1, acc2, ...)")
    ap.add_argument("--log-descarga", default="terabox_dl.log",
                    help="log del descargador; USÁ UNO DISTINTO POR INSTANCIA")
    ap.add_argument("--log", dest="log_central", default=LOG_CENTRAL,
                    help="log central del supervisor; USÁ UNO DISTINTO POR INSTANCIA")
    ap.add_argument("--max-reintentos", type=int, default=5)
    ap.add_argument("--esperar-cookie", type=int, default=900,
                    help="segundos a esperar un ndus nuevo (0 = no esperar)")
    ap.add_argument("--refrescar", default=None,
                    help="comando de refresco de sesion (por defecto: la cookie de "
                         "esta instancia)")
    ap.add_argument("--sin-refresco", action="store_true")
    ap.add_argument("--comando", help="comando completo del descargador (para pruebas)")
    ap.add_argument("--max-horas", type=float, default=0, help="0 = sin tope")
    global args_global
    args = ap.parse_args()
    args_global = args
    consola_utf8()

    # --- NUEVO: el refresco apunta a LA CUENTA de esta instancia ----------
    if args.refrescar is None:
        args.refrescar = f'"{sys.executable}" renovar_cookie.py --check'
        if args.cookie_file:
            args.refrescar += f' --cookie-file "{args.cookie_file}"'

    log = Log(args.log_central, args.etiqueta)
    t0 = time.time()
    print("=" * 70)
    print(f"  SUPERVISOR DE DESCARGA — instancia {args.etiqueta or 'unica'}")
    if args.cookie_file:
        print(f"  cookie: {args.cookie_file}")
    print("=" * 70)
    log("INICIO", f"objetivo: {args.url or args.comando}")

    try:
        cmd = construir_comando(args)
    except SystemExit as e:
        print(e)
        return 2

    intentos, total_ok, total_arch = 0, None, None
    while True:
        intentos += 1
        codigo, resumen, run = lanzar(cmd, log, intentos, "RUN")
        if resumen:
            total_ok, total_arch = resumen
        prog = run["progreso"]
        detalle = f"exit {codigo}"
        if resumen:
            detalle += f", {resumen[0]}/{resumen[1]} OK"
        if prog:
            detalle += f", ultimo total {prog[3]:.0f}/{prog[4]:.0f} bytes a {prog[0]:.1f} MB/s"
        if run["n429"]:
            detalle += f", {run['n429']} lineas con 429"
        log("FIN-INTENTO", detalle, intentos)

        # --- freno duro ---------------------------------------------------
        if run["ban"]:
            log("STOP", "TeraBox respondio errno -6 / ip_blacklist. NO se reintenta: "
                        "eso escala a bloqueo de IP/cuenta. Cortá aca, esperá 6-24 h y "
                        "bajá la concurrencia.", intentos)
            return 2

        # --- sesion ------------------------------------------------------
        if run["sesion"]:
            if refrescar_cookie(args, log, intentos) != 0:
                if esperar_cookie_manual(args, log, intentos) != 0:
                    log("STOP", "no se pudo restablecer la sesion. Corré "
                                "RENOVAR_COOKIE.bat y volvé a lanzar el supervisor: "
                                "retoma donde quedó.", intentos)
                    return 3
            if intentos >= args.max_reintentos:
                log("STOP", f"agoté {args.max_reintentos} intentos", intentos)
                return 3
        elif codigo == 0 and resumen and resumen[0] == resumen[1]:
            log("LISTO", f"terminado en {intentos} intentos, "
                         f"{(time.time()-t0)/60:.1f} min. {resumen[0]}/{resumen[1]} OK",
                intentos)
            print()
            print("  Siguiente paso: VERIFICAR.bat (md5 + lista de rehacer)")
            return 0
        elif codigo == 0 and resumen and resumen[0] != resumen[1]:
            log("PENDIENTE", f"{resumen[1]-resumen[0]} archivo(s) sin bajar "
                             f"({resumen[0]}/{resumen[1]} OK). Corré VERIFICAR.bat y "
                             f"relanzá con --fsids.", intentos)
            return 1
        elif codigo == 0 and not resumen:
            log("DUDOSO", "salio con codigo 0 pero no encontre el RESUMEN de esta "
                          "corrida en el log. No lo doy por bueno: corré VERIFICAR.bat "
                          "para saber que falta.", intentos)
            return 1
        else:
            if intentos >= args.max_reintentos:
                log("STOP", f"agoté {args.max_reintentos} intentos", intentos)
                return 1
            log("REINTENTO", "relanzando (reanuda desde .part.json, no pierde nada)",
                intentos)
            time.sleep(5)

        if args.max_horas and (time.time() - t0) / 3600 > args.max_horas:
            log("STOP", f"pase el tope de {args.max_horas} h", intentos)
            return 1


args_global = None
if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[Ctrl+C] supervisor interrumpido. Lo bajado queda en los .part.json:")
        print("        volvé a lanzarlo y sigue donde quedó.")
        sys.exit(130)
