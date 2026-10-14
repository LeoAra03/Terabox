#!/usr/bin/env python3
# verificar_integridad.py — verifica lo bajado contra el manifiesto (bytes + md5)
# y arma la lista de lo que hay que rehacer, lista para pasarle a --fsids.
#
#   python verificar_integridad.py --manifest inventarios/manifest_piano.csv \
#                                  --dir "C:\Users\Leo\desktop\Curso de piano\descargas"
#
#   python verificar_integridad.py --manifest inventarios/manifest_ingles.csv \
#                                  --dir C:\TB --solo-tamano        (ingles no tiene md5 todavia)
#
# Opciones:
#   --solo-tamano   compara bytes y existencia, sin leer los archivos (segundos)
#   --cuarentena    mueve los corruptos a _cuarentena/ para que el downloader los rebaje
#   --json FILE     informe detallado
#
# Salidas:
#   rehacer_fsids.txt -> "123,456,789"   usar:  python terabox_v2_fixed.py URL -o DEST --fsids 123,456
#   rehacer.txt       -> una linea por archivo con el motivo
#
# Codigos de salida: 0 = todo OK, 1 = hay faltantes/corruptos, 2 = error de invocacion

import argparse, csv, hashlib, io, json, os, shutil, sys, time
from pathlib import Path

MALOS = '<>:"|?*'


def consola_utf8():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def safe_rel(rel: str) -> Path:
    """Misma sanitizacion que usa terabox_v2_fixed.safe_rel(), para caer en el
    mismo archivo que escribio el downloader."""
    partes = []
    for pieza in (rel or "").replace("\\", "/").split("/"):
        pieza = "".join("" if (c in MALOS or ord(c) < 32) else c for c in pieza).rstrip(". ")
        partes.append(pieza or "_")
    return Path(*partes)


def candidatos(base: Path, ruta: str):
    """Devuelve las rutas donde puede haber quedado el archivo. El manifiesto
    guarda la ruta del API; el downloader la sanitiza al escribir."""
    r = (ruta or "").lstrip("/").replace("\\", "/")
    vistos, out = set(), []
    for p in (base / Path(*r.split("/")), base / safe_rel(r)):
        k = str(p)
        if k not in vistos:
            vistos.add(k)
            out.append(p)
    return out


def md5_de(path: Path, buf=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(buf)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main():
    consola_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--solo-tamano", action="store_true")
    ap.add_argument("--cuarentena", action="store_true")
    ap.add_argument("--json")
    args = ap.parse_args()

    manif = Path(args.manifest)
    base = Path(args.dir)
    if not manif.exists():
        print(f"[X] no existe el manifiesto: {manif}")
        return 2
    if not base.exists():
        print(f"[X] no existe el destino: {base}")
        return 2

    filas = []
    with io.open(manif, encoding="utf-8-sig", newline="") as fh:
        lector = csv.DictReader(fh)
        cols = set(lector.fieldnames or [])
        for r in lector:
            filas.append(r)
    tiene_md5 = "md5" in cols
    total = sum(int(r.get("bytes") or 0) for r in filas)

    print(f"Manifiesto : {manif}  ({len(filas)} archivos, {total/1e9:.2f} GB)")
    print(f"Destino    : {base}")
    print(f"Chequeo    : {'bytes + md5' if (tiene_md5 and not args.solo_tamano) else 'solo bytes'}")

    ok = faltan = tamanio_mal = md5_mal = sin_md5 = 0
    pendientes = []          # (fs_id, motivo, ruta_rel)
    detalle = []
    t0 = time.time()
    leidos = 0
    prox_aviso = time.time() + 15

    for i, r in enumerate(filas, 1):
        rel = r.get("ruta") or r.get("archivo") or ""
        esperado = int(r.get("bytes") or 0)
        md5_api = (r.get("md5") or "").strip().lower()
        fs_id = (r.get("fs_id") or "").strip()
        destino = None
        for c in candidatos(base, rel):
            if c.exists():
                destino = c
                break

        if destino is None:
            faltan += 1
            pendientes.append((fs_id, "FALTANTE", rel))
            detalle.append({"ruta": rel, "estado": "FALTANTE", "bytes_ok": None})
            continue

        real = destino.stat().st_size
        if real != esperado:
            tamanio_mal += 1
            pendientes.append((fs_id, f"TAMANO {real}/{esperado}", rel))
            detalle.append({"ruta": str(destino), "estado": "TAMANO",
                            "bytes_ok": real, "bytes_api": esperado})
            if args.cuarentena:
                _cuarentena(base, destino)
            continue

        if not (tiene_md5 and not args.solo_tamano):
            ok += 1
            detalle.append({"ruta": str(destino), "estado": "OK", "bytes_ok": real})
            continue

        if not md5_api:
            sin_md5 += 1
            ok += 1
            detalle.append({"ruta": str(destino), "estado": "OK-SIN-MD5", "bytes_ok": real})
            continue

        h = md5_de(destino)
        leidos += esperado
        if h == md5_api:
            ok += 1
            detalle.append({"ruta": str(destino), "estado": "OK", "bytes_ok": real})
        else:
            md5_mal += 1
            pendientes.append((fs_id, "MD5-MAL", rel))
            detalle.append({"ruta": str(destino), "estado": "MD5-MAL",
                            "md5_local": h, "md5_api": md5_api, "bytes_ok": real})
            if args.cuarentena:
                _cuarentena(base, destino)

        if time.time() > prox_aviso:
            prox_aviso = time.time() + 15
            vel = leidos / max(time.time() - t0, 1e-9)
            print(f"  [{i}/{len(filas)}] {leidos/1e9:.1f} GB leidos, {vel/1e6:.0f} MB/s", flush=True)

    dt = time.time() - t0
    print()
    print("=" * 62)
    print(f"  OK           : {ok}")
    print(f"  FALTANTES    : {faltan}")
    print(f"  TAMANO MAL   : {tamanio_mal}")
    print(f"  MD5 MAL      : {md5_mal}")
    if tiene_md5 and not args.solo_tamano:
        print(f"  SIN MD5 API  : {sin_md5}")
    print(f"  Tiempo       : {dt/60:.1f} min ({leidos/1e9:.1f} GB leidos"
          f"{'' if dt < 1 else f', {leidos/max(dt,1e-9)/1e6:.0f} MB/s'})")
    print("=" * 62)

    with open("rehacer.txt", "w", encoding="utf-8") as f:
        for fs_id, motivo, rel in pendientes:
            f.write(f"{motivo}\t{fs_id}\t{rel}\n")
    ids = [p[0] for p in pendientes if p[0]]
    with open("rehacer_fsids.txt", "w", encoding="utf-8") as f:
        f.write(",".join(ids))

    if pendientes:
        print(f"\n  {len(pendientes)} archivo(s) a rehacer -> rehacer.txt")
        if ids:
            print(f"  Volve a bajarlos asi (una sola corrida):\n")
            print(f'    python terabox_v2_fixed.py "<URL del share>" -o "{base}" '
                  f'--workers-files 4 -c 24 --fsids {",".join(ids[:6])}'
                  f'{",..." if len(ids) > 6 else ""}\n')
            print(f"  (o usá el comando que arma el .bat; la lista completa esta en rehacer_fsids.txt)")
        else:
            print("  [!] No pude armar --fsids: el manifiesto no tiene la columna fs_id.")
        if not args.cuarentena and (tamanio_mal or md5_mal):
            print("  [!] Los corruptos NO se movieron: borra o renombra esos archivos "
                  "antes de relanzar, o el downloader los va a dar por completos.")
    else:
        print("\n  Todo verificado. Nada que rehacer.")

    if args.json:
        with io.open(args.json, "w", encoding="utf-8") as f:
            json.dump({"manifiesto": str(manif), "destino": str(base), "total_bytes": total,
                       "ok": ok, "faltan": faltan, "tamano_mal": tamanio_mal,
                       "md5_mal": md5_mal, "detalle": detalle}, f, ensure_ascii=False, indent=2)
        print(f"  informe: {args.json}")

    return 0 if not pendientes else 1


def _cuarentena(base: Path, archivo: Path):
    try:
        rel = archivo.relative_to(base)
    except ValueError:
        rel = Path(archivo.name)
    dst = base / "_cuarentena" / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(archivo), str(dst))
    print(f"  -> cuarentena: {dst}")


if __name__ == "__main__":
    sys.exit(main())
