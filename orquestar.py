#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""orquestar.py — reparte un share entre N cuentas y levanta N supervisores.

Sin capa de abstraccion rara: N procesos independientes, cada uno con SU cookie,
SU particion, SU log y SU config. Si una cuenta se cae, las otras siguen.

Preparacion (una sola vez):
    mkdir cookies
    copia el ndus de cada cuenta a cookies\\acc1.txt, cookies\\acc2.txt, ...
    (opcional pero recomendado: python renovar_cookie.py --cookie-file cookies\\acc1.txt --check)

Uso:
    python orquestar.py --url "<link>" -o C:\\TB
    python orquestar.py --url "<link>" -o C:\\TB --limite 2 --techo-json informe_techo.json
    python orquestar.py --url "<link>" -o C:\\TB --por fsids --esperar
"""
import argparse, os, re, subprocess, sys, time
from pathlib import Path

AQUI = Path(__file__).resolve().parent


def consola_utf8():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def descubrir_cookies(limite=0):
    carpeta = AQUI / "cookies"
    if not carpeta.is_dir():
        return []
    archivos = [p for p in carpeta.glob("*.txt") if p.is_file()]
    archivos.sort(key=lambda p: (len(p.stem), p.stem))
    if limite:
        archivos = archivos[:limite]
    return archivos


def leer_share(url, cookie_file, profundidad):
    """Devuelve {clave_grupo: (bytes, n_archivos, [fs_id, ...])}."""
    import terabox_v2_fixed as tb
    ck = Path(cookie_file).read_text(encoding="utf-8").strip()
    sess = tb.build_session(ck)
    cli = tb.ShareClient(sess, tb.MIRRORS)
    surl = tb.shorturl_from(url)
    print(f"  listando {surl} ...")
    items, _, _ = cli.list_all(surl)
    grupos = {}
    for it in items:
        partes = it["rel"].split("/")
        if len(partes) > profundidad:
            clave = "/".join(partes[:profundidad])
        elif len(partes) > 1:
            clave = "/".join(partes[:-1])
        else:
            clave = "(raiz)"
        g = grupos.setdefault(clave, {"bytes": 0, "n": 0, "fsids": []})
        g["bytes"] += it["size"]
        g["n"] += 1
        g["fsids"].append(str(it["fs_id"]))
    return grupos


def repartir(grupos, n):
    """Greedy: el grupo mas grande va siempre al que menos carga tiene."""
    cargas = [0] * n
    asignacion = [[] for _ in range(n)]
    for clave, g in sorted(grupos.items(), key=lambda kv: -kv[1]["bytes"]):
        i = min(range(n), key=lambda k: cargas[k])
        asignacion[i].append((clave, g))
        cargas[i] += g["bytes"]
    return asignacion, cargas


def main():
    consola_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="link del share")
    ap.add_argument("-o", "--destino", required=True, help="carpeta destino")
    ap.add_argument("--cookies", nargs="*", default=None,
                    help="rutas explicitas a las cookies (si no, cookies\\*.txt)")
    ap.add_argument("--limite", type=int, default=0,
                    help="usar solo las primeras N cuentas (0 = todas)")
    ap.add_argument("--por", choices=["carpeta", "fsids"], default="carpeta",
                    help="granularidad del reparto (default: carpeta)")
    ap.add_argument("--profundidad", type=int, default=2,
                    help="niveles de carpeta que forman un grupo (default: 2)")
    ap.add_argument("--techo-json", default=None,
                    help="config medida por medir_techo.py (se pasa a cada supervisor)")
    ap.add_argument("--conexiones", "-c", type=int, default=0,
                    help="override de conexiones por archivo (si no, lo decide --techo-json)")
    ap.add_argument("--workers-files", type=int, default=0, help="override")
    ap.add_argument("--esperar", action="store_true", help="esperar a que terminen todas")
    ap.add_argument("--verificar", action="store_true",
                    help="probar cada cookie antes de lanzar")
    args = ap.parse_args()

    cookies = [Path(c) for c in args.cookies] if args.cookies else descubrir_cookies(args.limite)
    if args.cookies and args.limite:
        cookies = cookies[:args.limite]
    if not cookies:
        print("[X] no encontre cookies. Creá cookies\\acc1.txt, cookies\\acc2.txt, ...")
        return 2
    faltan = [c for c in cookies if not c.exists()]
    if faltan:
        print("[X] no existen: " + ", ".join(str(c) for c in faltan))
        return 2
    print(f"  {len(cookies)} cuenta(s): " + ", ".join(c.stem for c in cookies))

    if args.verificar:
        for c in cookies:
            r = subprocess.run([sys.executable, str(AQUI / "renovar_cookie.py"),
                                "--check", "--cookie-file", str(c)],
                               cwd=str(AQUI), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=180)
            marca = "OK" if r.returncode == 0 else "FALLA"
            print(f"  [{marca}] {c.stem}: {(r.stdout or '').strip()[:110]}")
            if r.returncode != 0:
                print("         corregí esa cuenta o sacala de cookies\\ antes de seguir.")
                return 2

    grupos = leer_share(args.url, cookies[0], args.profundidad)
    total = sum(g["bytes"] for g in grupos.values())
    print(f"  {len(grupos)} grupo(s), {len(grupos and [x for g in grupos.values() for x in g['fsids']])} "
          f"archivos, {total/1e9:.2f} GB")

    asignacion, cargas = repartir(grupos, len(cookies))
    parte_dir = AQUI / "particiones"
    logs_dir = AQUI / "logs"
    parte_dir.mkdir(exist_ok=True)
    logs_dir.mkdir(exist_ok=True)

    print()
    print(f"  {'cuenta':<10} {'grupos':>7} {'GB':>9}   muestra")
    print("  " + "-" * 62)

    procs = []
    for i, (c, (grupos_i, carga)) in enumerate(zip(cookies, zip(asignacion, cargas)), 1):
        claves = [cl for cl, _ in grupos_i]
        n_arch = sum(g["n"] for _, g in grupos_i)
        muestra = ", ".join(claves[:2]) + (" ..." if len(claves) > 2 else "")
        print(f"  {c.stem:<10} {len(claves):>7} {carga/1e9:>9.2f}   {muestra[:46]}")
        if not claves:
            continue

        arch_part = parte_dir / f"{c.stem}.txt"
        if args.por == "carpeta":
            arch_part.write_text(
                "\n".join("^" + re.escape(cl) for cl in claves) + "\n", encoding="utf-8")
            extra = ["--only-file", str(arch_part)]
        else:
            fsids = [f for _, g in grupos_i for f in g["fsids"]]
            arch_part.write_text("\n".join(fsids) + "\n", encoding="utf-8")
            extra = ["--fsids", ",".join(fsids)]

        cmd = [sys.executable, "-u", str(AQUI / "supervisor.py"),
               "--url", args.url, "-o", str(args.destino),
               "--cookie-file", str(c),
               "--log-descarga", str(logs_dir / f"{c.stem}.log"),
               "--log", str(logs_dir / f"supervisor_{c.stem}.log"),
               "--etiqueta", c.stem] + extra
        if args.techo_json:
            cmd += ["--techo-json", args.techo_json]
        if args.conexiones:
            cmd += ["-c", str(args.conexiones)]
        if args.workers_files:
            cmd += ["--workers-files", str(args.workers_files)]

        print(f"    -> {c.stem}: {n_arch} archivos, log en logs\\{c.stem}.log")
        procs.append((c.stem, subprocess.Popen(cmd, cwd=str(AQUI))))

    print()
    print(f"  {len(procs)} instancia(s) lanzada(s). Logs en logs\\")
    if not args.esperar:
        print("  (sin --esperar: podés cerrar esta ventana, los procesos siguen)")
        return 0

    print("  esperando a que terminen todas ...")
    codigos = {}
    while any(p.poll() is None for _, p in procs):
        time.sleep(5)
    for nombre, p in procs:
        codigos[nombre] = p.returncode
    print()
    for nombre, cod in codigos.items():
        estado = {0: "LISTO", 1: "con pendientes", 2: "FRENADO (ban)",
                  3: "sesion rota", 130: "interrumpido"}.get(cod, f"exit {cod}")
        print(f"  {nombre}: {estado}")
    malas = [n for n, c in codigos.items() if c in (2, 3)]
    if malas:
        print(f"\n  [!] {', '.join(malas)} necesita(n) atencion: "
              f"revisá logs\\supervisor_<cuenta>.log")
    return 0 if not malas else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[Ctrl+C] orquestador interrumpido.")
        sys.exit(130)
