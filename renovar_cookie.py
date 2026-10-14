#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Renueva y VERIFICA la cookie de UNA cuenta.

La verificacion es real: pide un enlace de descarga (/share/download) y baja
1 KB del CDN. Es la misma cadena que usa el descargador, asi que si esto pasa,
la descarga larga tambien va a andar.

Codigos que importan:
  400210 -> cookie invalida o ausente
  400310 -> la sesion existe pero pide re-verificacion (cookie vieja, o
            generada desde otra red/IP): hay que renovarla igual

Uso:
  RENOVAR_COOKIE.bat                       (menu interactivo, cookie.txt)
  python renovar_cookie.py --check         (silencio, exit 0 = OK / 1 = renovar)
  python renovar_cookie.py --cookie-file cookies\\acc2.txt --check
  python renovar_cookie.py --cookie-file cookies\\acc2.txt --ndus "YQQ-..."
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

AQUI = os.path.dirname(os.path.abspath(__file__))
COOKIE = os.path.join(AQUI, "cookie.txt")   # --cookie-file lo cambia

# (nombre, surl, manifest con fs_id de un archivo chico de ese share)
SHARES = [
    ("Curso de Piano", "4xnpHUw7xDHWwwuJVsU6dQ", "inventarios/manifest_piano.csv"),
    ("Cursos de Ingles", "1Mx1GP2rPLypBFF1RlVMog", "inventarios/manifest_ingles.csv"),
]


# --------------------------------------------------------------------------
# portapapeles
# --------------------------------------------------------------------------
def leer_portapapeles():
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                           capture_output=True, text=True, timeout=25)
        return r.stdout or ""
    except Exception:
        return ""


def extraer_ndus(texto):
    """Saca el valor de ndus de un texto cualquiera. None si no hay."""
    if not texto:
        return None
    t = texto.replace("\r", " ").replace("\n", " ").replace("\t", " ").strip()
    m = re.search(r"ndus\s*=\s*([A-Za-z0-9%._\-]{20,})", t)
    if m:
        return m.group(1)
    t2 = t.strip().strip('"\'')          # pegaron solo el valor
    if re.fullmatch(r"[A-Za-z0-9%._\-]{25,}", t2):
        return t2
    return None


def guardar(valor):
    os.makedirs(os.path.dirname(COOKIE) or ".", exist_ok=True)
    with open(COOKIE, "w", encoding="ascii", newline="\n") as fh:
        fh.write("ndus=%s\n" % valor)


def leer_actual():
    try:
        with open(COOKIE, encoding="utf-8", errors="replace") as fh:
            return extraer_ndus(fh.read())
    except FileNotFoundError:
        return None


# --------------------------------------------------------------------------
# verificacion de verdad
# --------------------------------------------------------------------------
def _fs_id_chico(manifest):
    """fs_id de un archivo pequeno del share, leido del inventario."""
    import csv
    ruta = os.path.join(AQUI, manifest)
    try:
        filas = [r for r in csv.DictReader(open(ruta, encoding="utf-8")) if int(r["bytes"]) > 1000]
        if not filas:
            return None
        filas.sort(key=lambda r: int(r["bytes"]))
        return int(filas[0]["fs_id"]), filas[0]["archivo"]
    except (OSError, KeyError, ValueError):
        return None


def _primer_archivo(cli, surl, shareid, uk):
    """Sin inventario: baja un nivel del arbol y devuelve el primer archivo."""
    p = {"shareid": shareid, "uk": uk, "shorturl": surl, "sign": cli.sign,
         "timestamp": cli.ts, "root": 1}
    for it in cli._api("/share/list", p).get("list", []):
        if it.get("isdir") in ("1", 1):
            p2 = dict(p); p2.pop("root"); p2["dir"] = it["path"]
            for f in cli._api("/share/list", p2).get("list", []):
                if f.get("isdir") not in ("1", 1) and int(f.get("size", 0) or 0) > 1000:
                    return int(f["fs_id"]), f["server_filename"]
    return None


def probar(valor, detallado=False):
    """
    Devuelve (estado, mensaje).  estado: "ok" | "renovar" | "otro"
    """
    try:
        import terabox_v2_fixed as tv
    except Exception as e:
        return "otro", "No pude importar terabox_v2_fixed.py: %s" % e

    errores = []
    for nombre, surl, manifest in SHARES:
        try:
            sess = tv.build_session(valor, None)
            cli = tv.ShareClient(sess, tv.MIRRORS)
            j = cli._api("/api/shorturlinfo", {"shorturl": "1" + surl, "root": 1})
            info = j.get("data") or j
            shareid, uk = info["shareid"], info["uk"]
            cli.sign, cli.ts = info.get("sign", ""), info.get("timestamp", "")

            par = _fs_id_chico(manifest) or _primer_archivo(cli, surl, shareid, uk)
            if not par:
                errores.append("%s: no encontre archivo de prueba" % nombre)
                continue
            fs_id, fname = par

            dl = cli.dlink(surl, shareid, uk, fs_id)
            r = sess.get(dl, headers={"Range": "bytes=0-1023"}, timeout=30, stream=True)
            trozo = r.raw.read(1024)
            if r.status_code in (200, 206) and trozo:
                return "ok", "Sesion OK. Baje 1 KB de '%s' (share %s)." % (fname[:40], nombre)
            errores.append("%s: el CDN respondio HTTP %s" % (nombre, r.status_code))
        except Exception as e:
            msg = str(e)
            if "400210" in msg:
                return "renovar", ("COOKIE INVALIDA O VENCIDA (400210).\n"
                                   "     Cerra sesion en 1024tera.com, volve a entrar y copia el ndus nuevo.")
            if "400310" in msg:
                return "renovar", ("COOKIE VIEJA: el servidor pide re-verificacion (400310).\n"
                                   "     Pasa cuando la cookie se genero desde otra red/IP o ya expiro.\n"
                                   "     Renovala: cerra sesion, volve a entrar y copia el ndus nuevo.")
            errores.append("%s: %s" % (nombre, msg[:120]))
    detalle = "\n     ".join(errores) if errores else "sin detalle"
    return "otro", "No pude confirmar la sesion:\n     %s" % detalle


# --------------------------------------------------------------------------
def main():
    global COOKIE
    args = sys.argv[1:]

    # --- NUEVO: apuntar a la cookie de OTRA cuenta ------------------------
    if "--cookie-file" in args:
        i = args.index("--cookie-file")
        if i + 1 >= len(args):
            print("  [X] --cookie-file necesita una ruta")
            return 2
        COOKIE = os.path.abspath(args[i + 1])
    # --- NUEVO: modo no interactivo ---------------------------------------
    ndus_arg = None
    if "--ndus" in args:
        j = args.index("--ndus")
        if j + 1 < len(args):
            ndus_arg = extraer_ndus(args[j + 1])
        if not ndus_arg:
            print("  [X] --ndus necesita un valor valido")
            return 2

    if "--check" in args:
        valor = leer_actual()
        if not valor:
            print("  [!] No hay %s. Corre RENOVAR_COOKIE.bat" % os.path.basename(COOKIE))
            return 1
        estado, msg = probar(valor)
        print("  [%s] %s" % ("OK" if estado == "ok" else "X", msg))
        return 0 if estado == "ok" else 1

    # --- NUEVO: guardar sin menu ------------------------------------------
    if ndus_arg:
        guardar(ndus_arg)
        print("  Guardado en %s  (...%s)" % (os.path.basename(COOKIE), ndus_arg[-8:]))
        print("  Verificando contra TeraBox (pide un enlace y baja 1 KB)...")
        estado, msg = probar(ndus_arg)
        print("  [%s] %s" % ("OK" if estado == "ok" else "X", msg))
        return 0 if estado == "ok" else 1

    print("=" * 60)
    print("  COOKIE DE TERABOX  -  renovar y verificar")
    print("  archivo: %s" % COOKIE)
    print("=" * 60)
    print()
    print("  ANTES DE EMPEZAR: en el navegador, cerra sesion en 1024tera.com")
    print("  y volve a entrar. Eso genera un ndus nuevo.")
    print("  Despues: F12 - Application - Cookies - 1024tera.com - copia 'ndus'")
    print()
    print("  1) Usar el ndus del PORTAPAPELES  (copialo antes de venir)")
    print("  2) Pegarlo a mano aca")
    print("  3) Solo VERIFICAR el archivo de cookie que ya tengo")
    print()
    try:
        op = (input("  Elegi [1/2/3]: ").strip() or "1")
    except EOFError:
        op = "3"

    actual = leer_actual()
    valor = None
    if op == "1":
        valor = extraer_ndus(leer_portapapeles())
        if valor:
            print("  Portapapeles: ndus encontrado (...%s)" % valor[-8:])
        else:
            print("  No encontre un ndus en el portapapeles.")
            try:
                valor = extraer_ndus(input("  Pegalo aca: "))
            except EOFError:
                valor = None
    elif op == "2":
        try:
            valor = extraer_ndus(input("  ndus: "))
        except EOFError:
            valor = None
    else:
        valor = actual

    if not valor:
        print()
        print("  [X] No consegui un ndus. No toque nada.")
        return 1

    if op in ("1", "2"):
        if valor == actual:
            print("  Es el mismo que ya tenias. Ojo: puede estar vencido igual.")
        else:
            guardar(valor)
            print("  Guardado en %s  (...%s)" % (os.path.basename(COOKIE), valor[-8:]))
    print()
    print("  Verificando contra TeraBox (pide un enlace y baja 1 KB)...")
    estado, msg = probar(valor)
    print()
    if estado == "ok":
        print("  [OK] %s" % msg)
        print()
        print("  Ya podes correr PIANO.bat o INGLES.bat.")
        return 0
    if estado == "renovar":
        print("  [X] %s" % msg)
        return 1
    print("  [!] %s" % msg)
    print("      (La cookie quedo guardada, pero no pude confirmarla.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
