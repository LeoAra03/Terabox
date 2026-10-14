#!/usr/bin/env python3
# preflight.py — ¿puede tu equipo bajar 103 GB y a que velocidad real?
#
#   python preflight.py --destino C:\TB --manifest inventarios/manifest_ingles.csv
#   python preflight.py --destino C:\TB --manifest inventarios/manifest_ingles.csv --streams 8
#   python preflight.py --destino C:\TB --manifest inventarios/manifest_ingles.csv --url "<link>"
#
# Hace 4 cosas, en este orden:
#   1. Espacio en disco: alcanza para el share completo? (con margen del 5%)
#   2. Capacidad REAL de tu linea: baja de una CDN neutral (Cloudflare), no de
#      TeraBox, con N conexiones simultaneas. Mide Mbps SOSTENIDOS.
#   3. Cookie de TeraBox (si tenes terabox_v2_fixed.py al lado): la prueba.
#   4. Veredicto: ETA honesto a la velocidad medida y comparacion con 400 Mbps.
#
# El paso 2 mide tu LINEA, no TeraBox. Lo que da TeraBox lo dice terabox_dl.log.

import argparse, csv, io, shutil, ssl, sys, threading, time, urllib.request

FUENTES = "https://speed.cloudflare.com/__down?bytes=1000000000"
OBJETIVO_MBPS = 400.0
# velocidades para la tabla de ETA: incluye las TUSAS medidas (terabox_dl.log)
ESCALONES = (400, 200, 100, 50, 25, 10, 3.6, 2.0, 0.8)


def consola_utf8():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def fmt_eta(seg):
    if not seg or seg <= 0 or seg == float("inf"):
        return "--"
    if seg >= 3600:
        return f"{seg/3600:.1f} h"
    if seg >= 90:
        return f"{seg/60:.0f} min"
    return f"{seg:.0f} s"


def medir_stream(url, dur, acumula, errores):
    req = urllib.request.Request(url, headers={"User-Agent": "preflight/1.0",
                                               "Range": "bytes=0-1999999999"})
    ctx = ssl.create_default_context()
    t0 = time.time()
    n = 0
    while time.time() - t0 < dur:
        try:
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                while time.time() - t0 < dur:
                    b = r.read(1 << 20)
                    if not b:
                        break                      # se acabo el archivo: reintentar
                    n += len(b)
                    with acumula[1]:
                        acumula[0] += len(b)
        except Exception as e:                     # red, TLS, DNS, 4xx...
            with acumula[1]:
                errores.append(f"{type(e).__name__}: {e}")
            break
    return n


def prueba_velocidad(fuentes, streams, dur=12):
    acumula = [0, threading.Lock()]
    errores = []
    hilos = [threading.Thread(target=medir_stream,
                              args=(fuentes[i % len(fuentes)], dur, acumula, errores),
                              daemon=True) for i in range(streams)]
    t0 = time.time()
    for h in hilos:
        h.start()
    ultimo = 0
    while time.time() - t0 < dur:
        time.sleep(2)
        actual = acumula[0]
        inst = (actual - ultimo) / 2
        ultimo = actual
        print(f"      {inst*8/1e6:6.1f} Mbps  ({inst/1e6:5.2f} MB/s)", flush=True)
        if errores and actual == 0:
            break
    for h in hilos:
        h.join(timeout=3)
    dt = max(time.time() - t0, 1e-9)
    return acumula[0] / dt, errores, acumula[0]


def tabla_eta(bytes_total, medido_mbps):
    print()
    print("  ETA del share completo segun la velocidad SOSTENIDA:")
    print()
    print(f"    {'Mbps':>8} {'MB/s':>8}   {'tiempo':>9}")
    print("    " + "-" * 30)
    for mbps in ESCALONES:
        bps = mbps * 1e6 / 8
        marca = ""
        if medido_mbps and abs(mbps - medido_mbps) <= max(medido_mbps * 0.06, 0.3):
            marca = "   <-- tu linea"
        print(f"    {mbps:>8.1f} {bps/1e6:>8.2f}   "
              f"{fmt_eta(bytes_total/bps):>9}{marca}")
    print()
    print("    TeraBox free, medido por vos: 0,2 MB/s con 1 conexion;")
    print("    2,0-3,6 MB/s con 16-24 conexiones (terabox_dl.log).")
    print(f"    Objetivo: 400 Mbps = 50 MB/s -> {fmt_eta(bytes_total/50e6)} "
          f"para {bytes_total/1e9:.1f} GB.")


def chequeo_cookie(url):
    try:
        import terabox_v2_fixed as tb
    except Exception as e:
        return None, f"no pude importar terabox_v2_fixed.py ({e})"
    try:
        from pathlib import Path
        ck = Path("cookie.txt")
        if not ck.exists():
            return None, "no hay cookie.txt en esta carpeta"
        sess = tb.build_session(ck.read_text(encoding="utf-8").strip())
        cli = tb.ShareClient(sess, tb.MIRRORS)
        surl = tb.shorturl_from(url)
        cli._api("/api/shorturlinfo", {"shorturl": "1" + surl, "root": 1})
        return True, f"surl {surl}: la sesion responde"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main():
    consola_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--destino", default=".")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--url", default=None, help="link del share, para probar la cookie")
    ap.add_argument("--streams", type=int, default=6)
    ap.add_argument("--segundos", type=int, default=12)
    ap.add_argument("--fuentes", default=FUENTES,
                    help="URLs separadas por coma para medir la linea")
    ap.add_argument("--solo-linea", action="store_true",
                    help="saltea el chequeo de disco")
    args = ap.parse_args()

    print("=" * 62)
    print("  PREFLIGHT - capacidad real antes de largar ~100 GB")
    print("=" * 62)

    # ---- 1. disco
    total_manif = None
    if args.manifest:
        try:
            with io.open(args.manifest, encoding="utf-8-sig", newline="") as fh:
                filas = list(csv.DictReader(fh))
            total_manif = sum(int(r.get("bytes") or 0) for r in filas)
            print("\n[1/4] Disco")
            print(f"      manifiesto : {args.manifest}")
            print(f"      contenido  : {len(filas)} archivos, {total_manif/1e9:.2f} GB")
        except Exception as e:
            print(f"\n[1/4] Disco: no pude leer el manifiesto ({e})")
    else:
        print("\n[1/4] Disco: salteado (pasá --manifest)")

    if total_manif:
        try:
            uso = shutil.disk_usage(args.destino)
            print(f"      destino    : {args.destino}")
            print(f"      libre      : {uso.free/1e9:.1f} GB de {uso.total/1e9:.1f} GB")
            necesita = total_manif * 1.05
            if uso.free >= necesita:
                print(f"      [OK] alcanza (necesita {necesita/1e9:.1f} GB con margen)")
            else:
                print(f"      [X] NO alcanza: faltan {(necesita-uso.free)/1e9:.1f} GB")
        except Exception as e:
            print(f"      [!] no pude medir el disco: {e}")

    if args.solo_linea:
        return 0

    # ---- 2. capacidad de linea
    print(f"\n[2/4] Capacidad de tu linea (CDN neutral, {args.streams} conexiones, "
          f"{args.segundos} s)")
    print("      (esto NO es TeraBox: mide el techo de tu conexion)")
    fuentes = [u.strip() for u in args.fuentes.split(",") if u.strip()]
    dur = max(2, args.segundos)
    bps, errores, total_b = prueba_velocidad(fuentes, max(1, args.streams), dur)
    mbps = bps * 8 / 1e6
    if total_b:
        print(f"      -> {bps/1e6:.1f} MB/s = {mbps:.0f} Mbps "
              f"({total_b/1e9:.2f} GB en {dur} s)")
    else:
        print("      [X] no bajo nada.")
        if errores:
            print(f"          primer error: {errores[0][:120]}")
        print("          Si estas detras de proxy/firewall corporativo, saltá este paso")
        print("          y usá: speedtest-cli --simple   (o fast.com en el navegador)")

    # ---- 3. cookie
    if args.url:
        print("\n[3/4] Cookie de TeraBox")
        ok, msg = chequeo_cookie(args.url)
        if ok is True:
            print(f"      [OK] {msg}")
        elif ok is False:
            print(f"      [X] {msg}  -> corre RENOVAR_COOKIE.bat")
        else:
            print(f"      [!] {msg}")
    else:
        print("\n[3/4] Cookie: salteado (pasá --url para probarla)")

    # ---- 4. veredicto
    print("\n[4/4] Veredicto")
    if total_manif and bps:
        seg = total_manif / bps
        print(f"      A la velocidad medida ({bps/1e6:.1f} MB/s), "
              f"{total_manif/1e9:.1f} GB tardan {fmt_eta(seg)}.")
        if mbps >= OBJETIVO_MBPS:
            print(f"      [OK] Tu linea pasa los 400 Mbps: el objetivo es viable "
                  f"A NIVEL DE RED.")
            print("      Ahora mira terabox_dl.log para ver cuanto te da TeraBox de verdad.")
        else:
            print(f"      [X] Tu linea da {mbps:.0f} Mbps, no 400. Ni con infinitas "
                  f"conexiones: el cuello es tu ISP, no TeraBox.")
            print("          (400 Mbps = 50 MB/s; una linea de 100 Mbps tope da 12,5 MB/s)")
    elif total_manif:
        print("      Sin medicion de linea: solo el disco esta chequeado.")
    else:
        print("      (pasá --manifest para el ETA con tus numeros)")

    if total_manif:
        tabla_eta(total_manif, mbps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
