#!/usr/bin/env python3
# inventario_md5.py — arma el manifiesto CON md5 de un share.
#
# Para que sirve: inventarios/manifest_ingles.csv NO tiene columna md5, asi que
# los 103,47 GB de ingles no se pueden verificar hoy. TeraBox SI devuelve el md5
# en /share/list; este script lo captura y escribe el manifiesto completo.
# Es de solo lectura: no descarga ni modifica nada.
#
#   python inventario_md5.py "<link del share>" -o inventarios/manifest_ingles_md5.csv
#
# Despues:
#   python verificar_integridad.py --manifest inventarios/manifest_ingles_md5.csv --dir C:\TB

import argparse, csv, io, sys, time


def consola_utf8():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def listar(client, surl):
    """Igual recorrido que ShareClient.list_all() de terabox_v2_fixed.py, pero
    conservando md5 y la carpeta de cada archivo."""
    j = client._api("/api/shorturlinfo", {"shorturl": "1" + surl, "root": 1})
    info = j.get("data") or j
    shareid, uk = info["shareid"], info["uk"]
    client.sign, client.ts = info.get("sign", ""), info.get("timestamp", "")
    salida, pila = [], [("", True)]
    while pila:
        d, es_raiz = pila.pop()
        p = {"shareid": shareid, "uk": uk, "shorturl": surl,
             "sign": client.sign, "timestamp": client.ts}
        if es_raiz:
            p["root"] = 1
        else:
            p["dir"] = d
        for it in client._api("/share/list", p).get("list", []):
            if it.get("isdir") in ("1", 1):
                pila.append((it["path"], False))
            else:
                rel = it["path"].lstrip("/")
                salida.append({
                    "carpeta": "/".join(rel.split("/")[:-1]) or "/",
                    "archivo": it.get("server_filename", ""),
                    "bytes": int(it.get("size", 0)),
                    "fs_id": it.get("fs_id", ""),
                    "ruta": it["path"],
                    "md5": (it.get("md5") or "").lower(),
                })
        print(f"  ... {len(salida)} archivos", flush=True)
    return salida


def main():
    consola_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--cookie", default=None)
    args = ap.parse_args()

    try:
        import terabox_v2_fixed as tb
    except Exception as e:
        print(f"[X] no pude importar terabox_v2_fixed.py: {type(e).__name__}: {e}")
        print("    Corre INSTALAR.bat (instala requests) y ejecutá esto desde la "
              "carpeta del descargador.")
        return 2
    from pathlib import Path

    cookie = args.cookie
    if not cookie:
        ck = Path("cookie.txt")
        if ck.exists():
            cookie = ck.read_text(encoding="utf-8").strip()
    if not cookie:
        print("[X] falta la cookie (cookie.txt o --cookie). Corre RENOVAR_COOKIE.bat.")
        return 1

    sess = tb.build_session(cookie)
    client = tb.ShareClient(sess, tb.MIRRORS)
    surl = tb.shorturl_from(args.url)
    print(f"Listando {surl} ...")
    t0 = time.time()
    filas = listar(client, surl)
    total = sum(f["bytes"] for f in filas)
    con_md5 = sum(1 for f in filas if f["md5"])

    with io.open(args.out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["carpeta", "archivo", "bytes", "MB",
                                           "fs_id", "ruta", "md5"])
        w.writeheader()
        for f in filas:
            w.writerow({"carpeta": f["carpeta"], "archivo": f["archivo"],
                        "bytes": f["bytes"], "MB": round(f["bytes"] / 1e6, 2),
                        "fs_id": f["fs_id"], "ruta": f["ruta"], "md5": f["md5"]})

    print(f"\n{len(filas)} archivos, {total/1e9:.2f} GB, {con_md5} con md5 "
          f"({time.time()-t0:.0f} s)")
    print(f"manifiesto: {args.out}")
    if con_md5 < len(filas):
        print(f"[!] {len(filas)-con_md5} archivos vinieron sin md5 (carpetas grandes "
              f"o archivos vacios): esos solo se pueden verificar por tamano.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
