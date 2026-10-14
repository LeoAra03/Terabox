#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
elegir_carpetas.py - Elegi que carpetas bajar de un share.

Lee los inventarios de inventarios.csv (no toca la red), muestra el arbol
de carpetas con su tamano real y arma el comando de terabox_v2_fixed.py con
--only para bajar SOLO lo elegido.

Uso:  python elegir_carpetas.py      (o doble clic en ELEGIR.bat)
"""

import csv
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

AQUI = os.path.dirname(os.path.abspath(__file__))

# id: (nombre, manifiesto, url, destino, ajustes)
SHARES = {
    1: ("Curso de Piano", "inventarios/manifest_piano.csv",
          "https://www.1024tera.com/spanish/sharing/link?surl=4xnpHUw7xDHWwwuJVsU6dQ",
          r"C:\Users\Leo\desktop\Curso de piano\descargas",
          "--workers-files 8 -c 16"),
    2: ("Cursos de Ingles", "inventarios/manifest_ingles.csv",
          "https://www.1024tera.com/spanish/sharing/link?surl=1Mx1GP2rPLypBFF1RlVMog",
          r"C:\TB",
          "--workers-files 4 -c 24"),
}


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return "%.1f %s" % (n, u)
        n /= 1024
    return "%.1f TB" % n


def _nat(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def cargar_filas(manifiesto):
    ruta = os.path.join(AQUI, manifiesto)
    if not os.path.exists(ruta):
        sys.exit("No encuentro el inventario %s" % ruta)
    filas = []
    with open(ruta, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            rel = (r.get("ruta") or "").replace("\\", "/").strip()
            if not rel:
                continue
            try:
                filas.append((rel, int(r.get("bytes") or 0)))
            except ValueError:
                pass
    return filas


def construir_arbol(filas):
    raiz = {"hijos": {}, "size": 0, "n": 0, "path": ""}
    for rel, size in filas:
        partes = [p for p in rel.split("/") if p]
        if not partes:
            continue
        raiz["size"] += size
        raiz["n"] += 1
        nodo = raiz
        for p in partes[:-1]:
            nodo = nodo["hijos"].setdefault(
                p, {"hijos": {}, "size": 0, "n": 0, "path": ""})
            nodo["size"] += size
            nodo["n"] += 1

    def asignar(nodo, prefijo):
        for nombre, hijo in nodo["hijos"].items():
            hijo["path"] = prefijo + nombre + "/"
            asignar(hijo, hijo["path"])

    asignar(raiz, "")
    return raiz


def listar(nodo, contador, nivel=0):
    for nombre in sorted(nodo["hijos"], key=_nat):
        hijo = nodo["hijos"][nombre]
        contador[0] += 1
        hijo["_idx"] = contador[0]
        print("  [%3d]  %10s  %4d arch   %s%s" % (
            hijo["_idx"], human(hijo["size"]), hijo["n"],
            "    " * nivel, nombre))
        listar(hijo, contador, nivel + 1)


def indice(nodo, tabla):
    for hijo in nodo["hijos"].values():
        tabla[hijo["_idx"]] = hijo
        indice(hijo, tabla)


def parsear_seleccion(texto, maximo):
    elegidos = set()
    for tok in re.split(r"[,\s]+", texto.strip()):
        if not tok:
            continue
        m = re.fullmatch(r"(\d+)-(\d+)", tok)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            for i in range(min(a, b), max(a, b) + 1):
                if 1 <= i <= maximo:
                    elegidos.add(i)
        elif tok.isdigit():
            i = int(tok)
            if 1 <= i <= maximo:
                elegidos.add(i)
    return elegidos


def main():
    print("=" * 64)
    print("  ELEGIR CARPETAS - baja solo lo que necesitas")
    print("=" * 64)
    print()
    for k in sorted(SHARES):
        nombre, manifiesto, url, destino, ajustes = SHARES[k]
        filas = cargar_filas(manifiesto)
        print("  %s) %s   (%d archivos, %s)"
              % (k, nombre, len(filas), human(sum(s for _, s in filas))))
    print()
    try:
        op = int(input("  Cual [1/2]: ").strip() or "1")
    except ValueError:
        sys.exit("Opcion invalida.")
    if op not in SHARES:
        sys.exit("Opcion invalida.")
    nombre, manifiesto, url, destino, ajustes = SHARES[op]
    filas = cargar_filas(manifiesto)

    raiz = construir_arbol(filas)
    print()
    print("  %s  -  %s" % (nombre, destino))
    print("  Total del share: %s  (%d archivos)" % (human(raiz["size"]), raiz["n"]))
    print("-" * 64)
    contador = [0]
    listar(raiz, contador)
    tabla = {}
    indice(raiz, tabla)
    print("-" * 64)
    print("  Escribi los numeros de las carpetas:  3      3,5      7-9")
    print("  Elegir una carpeta baja TODO lo de adentro. 't' = el share entero.")
    print()
    texto = input("  Carpetas a bajar: ").strip()
    if not texto:
        print("  Nada elegido.")
        return

    if texto.lower() in ("t", "todo"):
        patron = None
        elegidas_paths = ["(todo el share)"]
    else:
        elegidos = parsear_seleccion(texto, contador[0])
        if not elegidos:
            print("  No reconoci ningun numero.")
            return
        rutas = sorted((tabla[i]["path"] for i in elegidos), key=len)
        podadas = []
        for p in rutas:
            if not any(p.startswith(q) for q in podadas):
                podadas.append(p)
        patron = "^(?:" + "|".join(re.escape(p) for p in podadas) + ")"
        elegidas_paths = podadas

    if patron:
        rx = re.compile(patron)
        sel = [(r, s) for r, s in filas if rx.search(r)]
    else:
        sel = filas
    total_sel = sum(s for _, s in sel)

    print()
    print("=" * 64)
    print("  Elegiste %d archivos  -  %s" % (len(sel), human(total_sel)))
    print("=" * 64)
    for p in elegidas_paths:
        print("   -", p)

    partes = [sys.executable, "terabox_v2_fixed.py", url, "-o", destino] + ajustes.split()
    if patron:
        partes += ["--only", patron]
    visible = " ".join("'%s'" % x if " " in x else x for x in partes)
    print()
    print("  Comando:")
    print("    " + visible)
    print()
    resp = input("  Lanzo la descarga ahora [S/N]: ").strip().lower()
    if resp in ("s", "si", "y"):
        subprocess.call(partes, cwd=AQUI)
    else:
        print("  Listo. Copia el comando de arriba cuando quieras.")


if __name__ == "__main__":
    main()