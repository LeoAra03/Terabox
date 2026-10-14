#!/usr/bin/env python3
# medir_techo.py — encuentra TU techo real de descarga TeraBox.
#
# MULTI-CUENTA: acepta varias cookies y reparte los hilos entre ellas, para
# poder medir si sumar cuentas suma ancho o si el techo es de IP.
#
#   Brazo A (base, 1 cuenta):   --configs "1x16" --usar-cuentas 1
#   Brazo B (2 archivos, 1 cta):--configs "2x8"  --usar-cuentas 1
#   Brazo C (2 archivos, 2 ctas):--configs "2x8" --usar-cuentas 2
#
#   Lectura: C >> B  -> limite por cuenta, sumar sesiones sirve.
#            C ~= B  -> limite por IP, sumar cuentas NO sirve.
#
# Como mide: para cada configuracion descarga durante N segundos y suma bytes.
# No escribe a disco (descarta) para no medir el disco por error.
#
# REGLA: si aparece un 429, por defecto la conexion que lo recibio muere y la
# medicion sigue (asi ves QUE cuenta se queja). Con --parar-en-429 se corta todo.
#
#   Uso:
#     python medir_techo.py --share "<link>" --cookie-file cookies\acc1.txt
#     python medir_techo.py --share "<link>" \
#            --cookie-file cookies\acc1.txt --cookie-file cookies\acc2.txt \
#            --configs "2x8" --usar-cuentas 2 --segundos 90

import argparse, json, sys, threading, time

UA = "netdisk;P2SP;2.2.91.136"
RAMPA = "1x1,1x2,1x4,1x8,2x8,4x8,8x8,4x16,8x16"
CONFIGS = []


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


def bajar_stream(sesion, url, tamano, idx, total_conn, stop, contador, lock,
                 errores, por_cuenta, acc, parar_en_429):
    """Un hilo = una conexion, atada a UNA cuenta. Streamea sin guardar nada,
    desde un tramo distinto del archivo para que las conexiones no pidan el
    mismo rango."""
    inicio = (tamano * idx) // max(total_conn, 1)
    while not stop.is_set():
        try:
            cabeceras = {"Range": f"bytes={inicio}-", "User-Agent": UA}
            with sesion.get(url, headers=cabeceras, stream=True, timeout=(10, 30)) as r:
                if r.status_code == 429:
                    with lock:
                        errores[f"429/{acc}"] = errores.get(f"429/{acc}", 0) + 1
                    if parar_en_429:
                        stop.set()
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
                        por_cuenta[acc] += len(ch)
                    if inicio + leido >= tamano:
                        break
                inicio = 0                          # llegue al final: vuelvo a empezar
        except Exception as e:
            with lock:
                errores[type(e).__name__] = errores.get(type(e).__name__, 0) + 1
            if stop.is_set():
                return
            time.sleep(0.5)


def medir_config(sesiones, cfg, tareas_por_cuenta, segundos, parar_en_429=False):
    archivos, conn = cfg
    n_acc = len(sesiones)
    total_conn = archivos * conn
    contador, lock, errores = [0], threading.Lock(), {}
    por_cuenta = [0] * n_acc
    stop = threading.Event()
    hilos = []
    for w in range(archivos):
        acc = w % n_acc
        tareas = tareas_por_cuenta[acc]
        if not tareas:
            continue
        url, tam, _ = tareas[(w // n_acc) % len(tareas)]
        for c in range(conn):
            hilos.append(threading.Thread(
                target=bajar_stream,
                args=(sesiones[acc][1], url, tam, w * conn + c, total_conn, stop,
                      contador, lock, errores, por_cuenta, acc, parar_en_429),
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
    return contador[0] / dt, total_conn, dict(errores), [b / dt / 1e6 for b in por_cuenta]


def cargar_sesiones(args, permitir_sin_cookie=False):
    """Devuelve [(etiqueta, session), ...] o None."""
    from pathlib import Path
    try:
        import terabox_v2_fixed as tb
    except Exception as e:
        print(f"[X] no pude importar terabox_v2_fixed.py: {e}")
        return None

    crudas = list(args.cookie)
    for f in args.cookie_file:
        p = Path(f)
        if not p.exists():
            print(f"[X] no existe {f}")
            return None
        crudas.append(p.read_text(encoding="utf-8").strip())
    if not crudas:
        for cand in (Path("cookie.txt"), Path(__file__).resolve().parent / "cookie.txt"):
            if cand.exists():
                crudas.append(cand.read_text(encoding="utf-8").strip())
                break
    crudas = [c for c in crudas if c]

    if not crudas:
        if not permitir_sin_cookie:
            print("[X] falta la cookie (--cookie, --cookie-file o cookie.txt). "
                  "Corre RENOVAR_COOKIE.bat.")
            return None
        return [("acc1", None)]

    if args.usar_cuentas:
        crudas = crudas[:args.usar_cuentas]

    sesiones = []
    for i, c in enumerate(crudas, 1):
        try:
            sesiones.append((f"acc{i}", tb.build_session(c)))
        except Exception as e:
            print(f"[X] cuenta {i}: {e}")
            return None
    print(f"  {len(sesiones)} cuenta(s): " + ", ".join(n for n, _ in sesiones))
    return sesiones


def preparar_tareas_desde_share(args, sesiones):
    import terabox_v2_fixed as tb
    surl = tb.shorturl_from(args.share)
    n_arch = max(max(a for a, _ in CONFIGS), 2)
    tareas_por_cuenta = []
    for i, (nombre, sess) in enumerate(sesiones):
        cli = tb.ShareClient(sess, tb.MIRRORS)
        print(f"  [{nombre}] listando {surl} ...")
        items, shareid, uk = cli.list_all(surl)
        items = [x for x in items if x["size"] > 8 << 20]          # > 8 MB
        items.sort(key=lambda x: -x["size"])
        # cada cuenta toma archivos DISTINTOS (rebanada intercalada) para que
        # las cuentas no midan el mismo objeto, pero el brazo B y el C usen
        # exactamente los mismos dos archivos
        elegidos = items[i::len(sesiones)][:n_arch]
        tareas = []
        for it in elegidos:
            try:
                url = cli.dlink(surl, shareid, uk, it["fs_id"])
                tareas.append((url, it["size"], it["rel"]))
            except Exception as e:
                print(f"  [{nombre}] dlink fallo en {it['rel']}: {e}")
        if not tareas:
            print(f"[X] {nombre} no tiene archivos utilizables")
            return None
        print(f"  [{nombre}] {len(tareas)} archivos, "
              f"{sum(t[1] for t in tareas)/1e9:.2f} GB")
        tareas_por_cuenta.append(tareas)
    return tareas_por_cuenta


def preparar_tareas_dlink(args, sesiones):
    import requests
    n_acc = len(sesiones)
    tareas_por_cuenta = [[] for _ in range(n_acc)]
    for k, u in enumerate(args.dlink):
        tareas_por_cuenta[k % n_acc].append((u, args.tamano, f"dlink{k}"))
    return tareas_por_cuenta


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
    ap.add_argument("--cookie", action="append", default=[],
                    help="ndus=... (repetible: una por cuenta)")
    ap.add_argument("--cookie-file", action="append", default=[],
                    help="archivo con el ndus (repetible: uno por cuenta)")
    ap.add_argument("--usar-cuentas", type=int, default=0,
                    help="usar solo las primeras N cuentas dadas (0 = todas)")
    ap.add_argument("--parar-en-429", action="store_true",
                    help="cortar la medicion entera al primer 429 (comportamiento viejo)")
    ap.add_argument("--json")
    args = ap.parse_args()

    if args.rapido:
        args.segundos, args.pausa = 25, 5
    if not args.share and not args.dlink:
        ap.error("pasá --share <link> o --dlink <url>")
    if args.dlink and not args.tamano:
        ap.error("en modo --dlink hace falta --tamano <bytes>")

    print("=" * 74)
    print("  MEDIR TECHO — TeraBox, multi-cuenta")
    print("=" * 74)
    CONFIGS = parse_configs(args.configs, args.max_conexiones)
    if not CONFIGS:
        print("[X] ninguna configuracion valida")
        return 2

    sesiones = cargar_sesiones(args, permitir_sin_cookie=bool(args.dlink))
    if not sesiones:
        return 2

    if args.share:
        tareas_por_cuenta = preparar_tareas_desde_share(args, sesiones)
    else:
        tareas_por_cuenta = preparar_tareas_dlink(args, sesiones)
    if not tareas_por_cuenta:
        return 2

    max_arch = max(a for a, _ in CONFIGS)
    if max_arch < len(sesiones):
        print(f"  [!] la config mas ancha usa {max_arch} archivo(s) y hay "
              f"{len(sesiones)} cuentas: {len(sesiones)-max_arch} quedaran sin usar. "
              f"Subi el primer numero de --configs (p. ej. {len(sesiones)}x8).")

    print(f"Configuraciones: {len(CONFIGS)}  |  {args.segundos} s cada una  |  "
          f"pausa {args.pausa} s")
    print(f"Total estimado: {(args.segundos+args.pausa)*len(CONFIGS)/60:.1f} min\n")
    etiquetas = [n for n, _ in sesiones]
    print(f"  {'config':>10} {'conex':>6} {'MB/s':>9} {'MB/s/conn':>10}  {'delta':>7}  "
          f"{'por cuenta':<26}  errores")
    print("  " + "-" * 78)

    filas, anterior, detenido = [], None, None
    for cfg in CONFIGS:
        bps, total_conn, errores, por_acc = medir_config(
            sesiones, cfg, tareas_por_cuenta, args.segundos, args.parar_en_429)
        mbps = bps / 1e6
        por_conn = mbps / total_conn
        delta = ""
        if anterior:
            crec = (mbps / anterior - 1) * 100 if anterior else 0
            delta = f"{crec:+.0f}%"
        marca = "  ".join(f"{k}x{v}" for k, v in errores.items()) or "-"
        detalle_acc = "  ".join(f"{etiquetas[i]}={v:.1f}" for i, v in enumerate(por_acc)
                                if tareas_por_cuenta[i])
        print(f"  {cfg[0]}x{cfg[1]:<8} {total_conn:>6} {mbps:>9.2f} {por_conn:>10.3f}  "
              f"{delta:>7}  {detalle_acc:<26}  {marca}")
        filas.append({"archivos": cfg[0], "conexiones": cfg[1], "total_conex": total_conn,
                      "MBps": round(mbps, 3), "MBps_por_conexion": round(por_conn, 4),
                      "MBps_por_cuenta": {etiquetas[i]: round(v, 3)
                                          for i, v in enumerate(por_acc)},
                      "bytes": int(bps * args.segundos), "errores": errores})
        anterior = mbps
        if any(str(k).startswith("429") for k in errores):
            detenido = (cfg, errores)
            print("\n  [STOP] TeraBox respondio 429 en esta configuracion.")
            print("         Ese es el limite real: el experimento se detiene aca.")
            if args.parar_en_429:
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
    print("=" * 74)
    print("  VEREDICTO")
    print("=" * 74)
    ultimas = [f for f in filas if not detenido or f != filas[-1]] or filas
    pico = max(f["MBps"] for f in filas)
    satura = [f for f in ultimas if f["MBps"] >= pico * 0.9]
    menor_conn_saturada = min(satura, key=lambda f: f["total_conex"]) if satura else mejor

    print(f"  Mejor configuracion: {mejor['archivos']}x{mejor['conexiones']} "
          f"({mejor['total_conex']} conexiones) -> {mejor['MBps']:.2f} MB/s")
    print(f"  Satura a partir de ~{menor_conn_saturada['total_conex']} conexiones "
          f"({menor_conn_saturada['MBps']:.2f} MB/s)")

    # --- NUEVO: diagnostico por cuenta -----------------------------------
    if len(sesiones) > 1:
        pa = mejor["MBps_por_cuenta"]
        vivas = {k: v for k, v in pa.items() if v > 0}
        if len(vivas) >= 2:
            mejor_acc = max(vivas.values())
            flojas = [k for k, v in vivas.items() if v < mejor_acc * 0.35]
            print(f"  Reparto por cuenta en la mejor config: "
                  + "  ".join(f"{k}={v:.2f} MB/s" for k, v in pa.items()))
            if flojas:
                print(f"  [!] {', '.join(flojas)} no aporta casi nada al agregado:")
                print("      el techo es POR IP / por ruta. Sumar cuentas no suma ancho.")
            else:
                print("  Todas las cuentas aportan parecido: el techo es POR CUENTA.")
                print("  Sumar sesiones independientes SI suma ancho.")
        elif len(vivas) == 1:
            print(f"  Solo una cuenta movio bytes ({list(vivas)[0]}): revisá las otras "
                  f"(sesion vencida, verify_v2 o 429).")

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
    print("  Protocolo de 3 brazos para atribuir el techo (mismos archivos, misma duracion):")
    print("    A) --configs \"1x16\" --usar-cuentas 1        (base, 1 cuenta)")
    print("    B) --configs \"2x8\"  --usar-cuentas 1        (2 archivos, 1 cuenta)")
    print("    C) --configs \"2x8\"  --usar-cuentas 2        (2 archivos, 2 cuentas)")
    print("    C >> B -> por cuenta.  C ~= B -> por IP.  B < A -> ojo con partir archivos.")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"configs": filas, "mejor": mejor, "detenido_por_429": bool(detenido),
                       "cuentas": etiquetas, "gb_objetivo": args.gb}, f,
                      ensure_ascii=False, indent=2)
        print(f"\n  informe: {args.json}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[Ctrl+C] medicion interrumpida.")
        sys.exit(130)
