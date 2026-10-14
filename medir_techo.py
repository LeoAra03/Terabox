#!/usr/bin/env python3
# medir_techo.py — encuentra TU techo real de descarga TeraBox con UNA cuenta.
#
# Por que existe: no sabemos si tu limite es por conexion, por cuenta o por ruta
# de red. Las corridas anteriores cambiaron varias cosas a la vez (workers,
# conexiones por archivo, perfiles de archivo distintos), asi que no permiten
# concluir nada. Esto mide de a UNA variable por vez.
#
# Como mide: para cada configuracion (archivos en paralelo x conexiones por
# archivo) descarga durante N segundos y suma bytes. No escribe a disco (descarta)
# para no medir el disco por error. Reporta MB/s agregados y MB/s por conexion.
#
# REGLA: si aparece un 429 (o errno -6 / ip_blacklist), el experimento SE DETIENE.
# Eso no es un fallo del script: es TeraBox diciendote "hasta aca". Insistir es
# lo que marca la cuenta.
#
#   Uso real (tu cuenta, tu share):
#     python medir_techo.py --share "https://www.1024tera.com/spanish/sharing/link?surl=4xnpHUw7xDHWwwuJVsU6dQ"
#
#   Uso con dlinks directos (util para probar el medidor contra un server propio):
#     python medir_techo.py --dlink "http://127.0.0.1:8766/archivo" --tamano 20000000
#
#   Opciones:
#     --configs "1x1,1x4,2x8,4x8,8x8,8x16"   archivos x conexiones (default: rampa)
#     --segundos 40        duracion de cada configuracion
#     --pausa 8            espera entre configuraciones (deja respirar y ver 429)
#     --rapido             preset corto (25 s por config, 5 s de pausa)
#     --max-conexiones 128 tope duro de conexiones totales
#     --gb 103.47          para calcular el ETA al final
#     --json informe_techo.json
#     --cookie "ndus=..."  (si no, usa cookie.txt)
#
# Salida: tabla comparativa, veredicto (donde satura) y la configuracion que
# conviene usar en la descarga real.

import argparse, json, sys, threading, time

UA = "netdisk;P2SP;2.2.91.136"
RAMPA = "1x1,1x2,1x4,1x8,2x8,4x8,8x8,4x16,8x16"


def consola_utf8():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def parse_configs(txt, max_conn):
    out = []
    for parte in txt.replace(" ", "").split(","):
        if not parte:
            continue
        a, c = parte.lower().split("x")
        n = int(a) * int(c)
        if n > max_conn:
            print(f"  (salteo {parte}: {n} conexiones > --max-conexiones {max_conn})")
            continue
        out.append((int(a), int(c)))
    return out


def bajar_stream(sesion, url, tamano, idx, total_conn, stop, contador, lock, errores):
    """Un hilo = una conexion. Streamea sin guardar nada, desde un tramo distinto
    del archivo para que las conexiones no pidan el mismo rango."""
    inicio = (tamano * idx) // max(total_conn, 1)
    while not stop.is_set():
        try:
            cabeceras = {"Range": f"bytes={inicio}-", "User-Agent": UA}
            with sesion.get(url, headers=cabeceras, stream=True, timeout=(10, 30)) as r:
                if r.status_code == 429:
                    with lock:
                        errores["429"] = errores.get("429", 0) + 1
                    stop.set()                      # el servicio dijo basta
                    return
                if r.status_code in (403, 404):
                    with lock:
                        errores[f"http{r.status_code}"] = errores.get(f"http{r.status_code}", 0) + 1
                    return
                if r.status_code not in (200, 206):
                    with lock:
                        errores[f"http{r.status_code}"] = errores.get(f"http{r.status_code}", 0) + 1
                    return
                leido = 0
                for ch in r.iter_content(1 << 20):
                    if stop.is_set():
                        return
                    leido += len(ch)
                    with lock:
                        contador[0] += len(ch)
                    if inicio + leido >= tamano:
                        break
                inicio = 0                          # llegue al final: vuelvo a empezar
        except Exception as e:
            with lock:
                errores[type(e).__name__] = errores.get(type(e).__name__, 0) + 1
            if stop.is_set():
                return
            time.sleep(0.5)


def medir_config(sesion, cfg, tareas, segundos):
    archivos, conn = cfg
    total_conn = archivos * conn
    contador, lock, errores = [0], threading.Lock(), {}
    stop = threading.Event()
    hilos = []
    for w in range(archivos):
        url, tam = tareas[w % len(tareas)]
        for c in range(conn):
            hilos.append(threading.Thread(
                target=bajar_stream,
                args=(sesion, url, tam, w * conn + c, total_conn, stop, contador, lock, errores),
                daemon=True))
    t0 = time.time()
    for h in hilos:
        h.start()
    while time.time() - t0 < segundos and not stop.is_set():
        time.sleep(0.25)
    stop.set()
    for h in hilos:
        h.join(timeout=5)
    dt = max(time.time() - t0, 1e-9)
    return contador[0] / dt, total_conn, dict(errores)


def preparar_tareas_desde_share(args):
    import terabox_v2_fixed as tb
    from pathlib import Path
    cookie = args.cookie
    if not cookie:
        ck = Path("cookie.txt")
        if ck.exists():
            cookie = ck.read_text(encoding="utf-8").strip()
    if not cookie:
        print("[X] falta la cookie (cookie.txt o --cookie). Corre RENOVAR_COOKIE.bat.")
        return None, None
    sess = tb.build_session(cookie)
    cli = tb.ShareClient(sess, tb.MIRRORS)
    surl = tb.shorturl_from(args.share)
    print(f"Listando {surl} ...")
    items, shareid, uk = cli.list_all(surl)
    items = [i for i in items if i["size"] > 8 << 20]          # > 8 MB: alcanza para medir
    items.sort(key=lambda i: -i["size"])
    n = max(a for a, _ in CONFIGS)
    elegidos = items[:max(n, 4)]
    tareas = []
    for it in elegidos:
        url = cli.dlink(surl, shareid, uk, it["fs_id"])
        tareas.append((url, it["size"], it["rel"]))
    print(f"{len(tareas)} archivos elegidos (los mas grandes), "
          f"{sum(t[1] for t in tareas)/1e9:.2f} GB en total\n")
    return sess, tareas


def preparar_tareas_dlink(args):
    import requests
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})
    tareas = [(u, args.tamano, f"dlink{i}") for i, u in enumerate(args.dlink)]
    return sess, tareas


def main():
    global CONFIGS
    consola_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--share", help="link del share de TeraBox")
    ap.add_argument("--dlink", action="append", default=[], help="URL directa (modo prueba)")
    ap.add_argument("--tamano", type=int, default=0, help="bytes del archivo en modo --dlink")
    ap.add_argument("--configs", default=RAMPA)
    ap.add_argument("--segundos", type=int, default=40)
    ap.add_argument("--pausa", type=int, default=8)
    ap.add_argument("--rapido", action="store_true")
    ap.add_argument("--max-conexiones", type=int, default=128)
    ap.add_argument("--gb", type=float, default=103.47)
    ap.add_argument("--cookie")
    ap.add_argument("--json")
    args = ap.parse_args()

    if args.rapido:
        args.segundos, args.pausa = 25, 5
    if not args.share and not args.dlink:
        ap.error("pasá --share <link> o --dlink <url>")
    if args.dlink and not args.tamano:
        ap.error("en modo --dlink hace falta --tamano <bytes>")

    # CONFIGS global para preparar_tareas_desde_share
    print("=" * 70)
    print("  MEDIR TECHO — TeraBox, una sola cuenta")
    print("=" * 70)
    CONFIGS = parse_configs(args.configs, args.max_conexiones)
    if not CONFIGS:
        print("[X] ninguna configuracion valida")
        return 2

    if args.share:
        sess, tareas = preparar_tareas_desde_share(args)
    else:
        sess, tareas = preparar_tareas_dlink(args)
    if not tareas:
        return 2
    # en modo dlink, completar el nombre si falta
    tareas = [(t[0], t[1]) for t in tareas]

    print(f"Configuraciones: {len(CONFIGS)}  |  {args.segundos} s cada una  |  "
          f"pausa {args.pausa} s")
    print(f"Total estimado: {(args.segundos+args.pausa)*len(CONFIGS)/60:.1f} min\n")
    print(f"  {'config':>12} {'conex':>6} {'MB/s':>9} {'MB/s/conn':>10}  {'delta':>8}  errores")
    print("  " + "-" * 62)

    filas, anterior, detenido = [], None, None
    for cfg in CONFIGS:
        bps, total_conn, errores = medir_config(sess, cfg, tareas, args.segundos)
        mbps = bps / 1e6
        por_conn = mbps / total_conn
        delta = ""
        if anterior:
            crec = (mbps / anterior - 1) * 100 if anterior else 0
            delta = f"{crec:+.0f}%"
        marca = "  ".join(f"{k}x{v}" for k, v in errores.items()) or "-"
        print(f"  {cfg[0]}x{cfg[1]:<10} {total_conn:>6} {mbps:>9.2f} {por_conn:>10.3f}  "
              f"{delta:>8}  {marca}")
        filas.append({"archivos": cfg[0], "conexiones": cfg[1], "total_conex": total_conn,
                      "MBps": round(mbps, 3), "MBps_por_conexion": round(por_conn, 4),
                      "bytes": int(bps * args.segundos), "errores": errores})
        anterior = mbps
        if errores.get("429"):
            detenido = (cfg, errores)
            print("\n  [STOP] TeraBox respondio 429 en esta configuracion.")
            print("         Ese es el limite real: el experimento se detiene aca.")
            print("         La configuracion recomendada es la anterior a esta.")
            break
        if errores.get("http403") and mbps < 0.05:
            print("  [!] dlinks vencidos: en modo --share hay que re-resolver (no afecta la medida).")
        if cfg != CONFIGS[-1]:
            time.sleep(args.pausa)

    if not filas:
        print("[X] ninguna configuracion midio nada")
        return 2

    mejor = max(filas, key=lambda f: f["MBps"])
    print()
    print("=" * 70)
    print("  VEREDICTO")
    print("=" * 70)
    ultimas = [f for f in filas if not detenido or f != filas[-1]] or filas
    pico = max(f["MBps"] for f in filas)
    satura = [f for f in ultimas if f["MBps"] >= pico * 0.9]
    menor_conn_saturada = min(satura, key=lambda f: f["total_conex"]) if satura else mejor

    print(f"  Mejor configuracion: {mejor['archivos']}x{mejor['conexiones']} "
          f"({mejor['total_conex']} conexiones) -> {mejor['MBps']:.2f} MB/s")
    print(f"  Satura a partir de ~{menor_conn_saturada['total_conex']} conexiones "
          f"({menor_conn_saturada['MBps']:.2f} MB/s)")
    crecimiento = filas[-1]["MBps"] / filas[0]["MBps"] if filas[0]["MBps"] else 0
    if crecimiento < 1.15:
        print("  Lectura: NO escala con conexiones. El techo es de cuenta o de ruta,")
        print("           no de conexion. Subir hilos no va a servir.")
    else:
        print(f"  Lectura: x{crecimiento:.1f} desde la config minima. Escala con conexiones")
        print("           (limite por conexion, no por cuenta) — usa la mejor configuracion.")
    if detenido:
        print(f"  [!] Hubo 429 en {detenido[0][0]}x{detenido[0][1]}: ese es el borde. "
              f"No lo cruces en la corrida real.")
    seg = args.gb * 1e9 / (mejor["MBps"] * 1e6)
    print(f"\n  A {mejor['MBps']:.2f} MB/s, {args.gb:.2f} GB tardan "
          f"{seg/3600:.1f} h ({seg/60:.0f} min).")
    print(f"  Para 30 minutos harian falta {args.gb*1e9/1800/1e6:.1f} MB/s "
          f"({args.gb*1e9*8/1800/1e6:.0f} Mbps).")
    print()
    print("  Para atribuir el techo (cuenta vs red): repetí la mejor configuracion desde")
    print("  OTRA red (hotspot del celular, misma cuenta). Si da parecido -> es la cuenta.")
    print("  Si da bastante mas -> el cuello esta en tu ruta/ISP.")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"configs": filas, "mejor": mejor, "detenido_por_429": bool(detenido),
                       "gb_objetivo": args.gb}, f, ensure_ascii=False, indent=2)
        print(f"\n  informe: {args.json}")
    return 0


CONFIGS = []
if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[Ctrl+C] medicion interrumpida.")
        sys.exit(130)
