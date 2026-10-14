# Migración TeraBox → disco local: plan real para 125,6 GB

Este documento cierra el objetivo "103 GB en 30 minutos" con números medidos, un
método elegido (uno solo), scripts listos para correr, fallback y checklist de
verificación. Los datos de velocidad salen de **tu propio** `terabox_dl.log` y de
los manifiestos de `inventarios/`.

---

## 1. Diagnóstico

### Lo que hay que saber antes de tocar nada

1. **No sabemos dónde está tu techo, y las corridas previas no alcanzan para
   deducirlo.** En el log: 1 conexión ≈ 0,2 MB/s (documentado), piano
   (`8×16 = 128` conexiones) 3,0-3,6 MB/s, inglés (`4×24 = 96`) 1,8-2,1 MB/s. Esa
   comparación **cambia dos variables a la vez** (conexiones y perfil de archivo:
   media 84 MB vs 309 MB) y además piano usó 128 conexiones contra 96. Si algo
   sugiere, es que **más conexiones sí ayudan**. Conclusión honesta: hay que
   medirlo con un experimento controlado, y eso es lo que hace §2.
2. **El objetivo de 30 minutos son 460 Mbps sostenidos** para el share de inglés
   (103,47 GB = 827,8 Gbit ÷ 1800 s = 460 Mbps). Tu mejor corrida medida es
   3,6 MB/s = 28,8 Mbps, o sea **16× menos**. Si el techo está en la cuenta, no hay
   tuning que lo mueva; si está en las conexiones, el margen existe. §2 lo decide.
3. **Dos sesiones simultáneas empeoran todo.** Ya lo tenés documentado en
   `INSTRUCCIONES.md`: TeraBox contesta `need verify_v2` y te quedás sin sesión.
   Cualquier esquema de "rotación de sesiones" cae justo en ese control
   anti-abuso — y una vez que te marca, la migración se frena más, no menos.
4. **Los resolvers de terceros no son una alternativa técnica.** No tienen un
   bypass: bajan el archivo a *sus* servidores con *su* capacidad y te lo reenvían
   con *su* throttle. Cambiás un límite por otro, dependés de la sesión de un
   tercero, y el archivo pasa por una infraestructura que no controlás. Para una
   migración de datos reales eso no es aceptable ni más rápido.
5. **Los dlinks caducan (~10 min) y son lo que rompe a `aria2c`.** Por eso el
   método principal de acá **no** es `aria2c`: tu `terabox_v2_fixed.py` renegocia
   el dlink con presupuesto de 540 s por ronda, y además guarda progreso por
   segmento de 64 MB en `.part.json`. `aria2c` no tiene gancho para renegociar
   URLs: cuando el dlink muere a mitad de archivo, falla el archivo entero.
   Se usa `aria2c` y el bucle `curl` solo como fallback (§4 y §5), para archivos que
   entran en una sola ventana de 10 minutos.

### Qué control es y por qué TeraBox limita

TeraBox regala 1 TB de almacenamiento y cobra por **egress** (ancho de banda de
salida). El throttle del plan gratis es la cerca de precios: no protege el
servicio, separa al usuario que paga del que no. Concretamente:

| Control | Cómo se manifiesta | Cómo se ve en tu log |
|---|---|---|
| Throttle de bajada | ~0,2 MB/s por conexión; el agregado depende de cuántas conexiones y de qué archivo | `vel 1.8-2.1` (inglés) vs `3.0-3.6` (piano, más conexiones) |
| Renovación de token de sesión | `need verify_v2` con dos sesiones o cookie vieja | `INSTRUCCIONES.md` §1 |
| Cuota diaria | corte a cierta cantidad de datos/día | no visible todavía |
| Caducidad de dlink | 403/404 a ~10 min del token | `fetch_range()` lo maneja a los 540 s |

**Lo que sí se puede explotar con legitimidad técnica**: la parte por conexión
(0,2 MB/s → 3,6 MB/s es un x18 real, y lo estás aprovechando), la reanudación por
rangos HTTP, y la verificación de integridad. Qué parte del techo es de cuenta y
qué parte es de conexión **se mide** (§2): si es de conexión, la ganancia está al
alcance sin cambiar de tier; si es de cuenta, el único camino corto es premium.

---

## 2. Medición controlada: dónde está tu techo, de verdad

Antes de decidir entre ~8 horas de espera y pagar premium, corré el experimento.
`medir_techo.py` cambia **una variable por vez**: para cada configuración
(archivos en paralelo × conexiones por archivo) descarga durante N segundos, suma
bytes y reporta MB/s agregados y MB/s por conexión. No escribe a disco, así que no
estás midiendo el disco por error.

```bat
REM rampa completa (~7 min): 1, 2, 4, 8, 16, 32, 64, 128 conexiones
python medir_techo.py --share "https://www.1024tera.com/spanish/sharing/link?surl=4xnpHUw7xDHWwwuJVsU6dQ"
REM para ir directo a la zona alta (donde ya sabés que estás):
python medir_techo.py --share "<link>" --configs "2x8,4x8,8x8,4x16,8x16" --segundos 40
```

Regla de oro del script: **si aparece un 429, el experimento se detiene y marca esa
configuración como el borde**. No es un fallo: es el servicio diciendo "hasta acá",
y la configuración recomendada es la anterior. Insistir después de un 429 es
exactamente lo que marca la cuenta.

Cómo leer el resultado:

| Lo que muestra la tabla | Qué significa | Qué hacer |
|---|---|---|
| MB/s sube ~proporcional al total de conexiones (`MB/s/conn` plano) | límite **por conexión** | usar la config alta: tu velocidad es cuestión de hilos |
| MB/s se estanca aunque dupliques conexiones (`MB/s/conn` cae) | techo **agregado** (cuenta o ruta) | no subas más; ese es el número y con él se decide |
| Aparece 429 / `errno -6` | ahí está el borde | quedarte una config por debajo, siempre |
| Da igual desde dos redes distintas | el techo es **de cuenta** | solo premium lo mueve |
| Desde el hotspot del celular da bastante más | el techo es **de tu ruta/ISP** | hablá con el ISP antes que con TeraBox |

El último par es la prueba que atribuye el techo. Se hace a mano y es decisiva:
misma cuenta, misma configuración, **otra red** (compartí internet desde el
celular). Cinco minutos que te dicen si el problema es TeraBox o tu conexión.

Con el resultado, la corrida real usa esa configuración:

```bat
REM si la mejor fue 8x8 -> 8 archivos en paralelo, 8 conexiones cada uno
python terabox_v2_fixed.py "<link>" -o C:\TB --workers-files 8 -c 8
```

Y el veredicto queda en `informe_techo.json` para comparar contra corridas futuras.

El medidor está validado contra un CDN simulado, en tres escenarios: con cota
agregada (detecta la saturación y avisa "el techo es de cuenta o ruta"), sin cota
agregada (detecta que escala con las conexiones y recomienda la config alta) y con
429 (frena en seco y marca el borde). O sea: lo que reporte contra TeraBox es
interpretable, no un número suelto.

## 3. Método principal ELEGIDO

**Un mes de premium + tu descargador actual, tuneado, con verificación md5** — o,
si §2 muestra que el techo es por conexión, **tu descargador con la configuración
que midió §2, sin pagar nada**.

Por qué este y no otro:

- Es el **único** cambio que mueve el cubo por cuenta, o sea el único que convierte
  el objetivo de imposible en alcanzable. Todo lo demás (más hilos, más sesiones,
  proxies, resolvers) pelea contra el mismo límite.
- **No hay nada que reescribir**: `terabox_v2_fixed.py` ya tiene reanudación por
  segmentos, renegociación de dlink, rotación de mirrors, chequeo de disco y
  frenado con Ctrl+C. Ya bajó 691/691 archivos en tus pruebas.
- Es una migración **única**: un mes alcanza y sobra. Es el costo total de la
  operación, no una suscripción nueva de por vida.

### Paso 0 — Preflight (30 s, antes de comprar nada)

```bat
PREFLIGHT.bat
```

Te dice si tu disco alcanza (necesitás ~110 GB libres para el share de inglés:
el manifiesto real dice **103,47 GB**, no los 96,36 GB de la doc) y cuál es el
techo real de tu línea. Si tu línea no da 50 MB/s, ningún tier de TeraBox te va a
dar 103 GB en 30 minutos — eso se resuelve con el ISP, no con el script.

### Paso 1 — Activar premium en la cuenta

En la web/app de TeraBox, sobre la cuenta cuyo `ndus` usás. Verificá en la propia
página de planes el precio de tu país.

### Paso 2 — Regenerar la cookie desde la misma red

Cerrá sesión y volvé a entrar en `1024tera.com`, copiá el `ndus` nuevo, corré
`RENOVAR_COOKIE.bat`. Sin este paso, la descarga larga falla con `need verify_v2`.
Y una sola sesión por vez: no uses la web en paralelo mientras baja.

### Paso 3 — Descargar, con rampa de concurrencia

Arrancá conservador y subí midiendo. La regla es simple y está en tu log:

```bat
REM ingles: 4 archivos x 24 conexiones = 96 conexiones vivas
python terabox_v2_fixed.py "https://www.1024tera.com/spanish/sharing/link?surl=1Mx1GP2rPLypBFF1RlVMog" ^
       -o C:\TB --workers-files 4 -c 24
```

A los 10 minutos, mirá la línea `vel X MB/s` del log y probá **+8 conexiones**
(`--workers-files 6 -c 24`). Volvé a medir a los 10 minutos:

- Si la velocidad subió **más del 5%**, seguí subiendo.
- Si subió menos del 5% (o bajó), **ese es el techo**: volvé al valor anterior y
  dejalo correr. Subir más solo agrega reconexiones, 429 y riesgo de marca.
- Con premium el techo del CDN suele estar muy por encima de tu línea; el
  binding constraint debería pasar a ser tu ISP. Lo vas a ver como `vel` plana
  cerca del resultado del preflight.

Números de referencia (medidos en tus runs, y proyección honesta):

| Escenario | Velocidad | Inglés (103,47 GB) | Los dos shares (125,62 GB) |
|---|---|---|---|
| Free, 16 conn | 2,0 MB/s | 14,4 h | 17,5 h |
| Free, 24 conn (tu mejor run) | 3,6 MB/s | 8,0 h | 9,7 h |
| Premium a 25 MB/s | 25 MB/s | 69 min | 84 min |
| Premium a 50 MB/s (= 400 Mbps) | 50 MB/s | **34,5 min** | 41,9 min |
| Tu línea, lo que mida PREFLIGHT | ? | `103470 / (MB/s)` s | `125620 / (MB/s)` s |

El objetivo de "90 GB en 30 minutos" **= 400 Mbps sostenidos**, y es alcanzable
solo si las dos condiciones se cumplen a la vez: tier que entregue ≥50 MB/s **y**
línea que aguante ≥400 Mbps. El preflight te dice la segunda; el log te dice la
primera.

### Paso 4 — Verificar y reencolar

```bat
VERIFICAR.bat
```

Compara cada archivo contra el manifiesto (bytes y md5), mueve los corruptos a
`_cuarentena/` si le decís que sí, y escribe `rehacer_fsids.txt` con la lista
exacta para relanzar:

```bat
python terabox_v2_fixed.py "URL" -o C:\TB --workers-files 4 -c 24 --fsids 123,456,789
```

Detalle importante: `inventarios/manifest_ingles.csv` **no tiene columna md5**, así
que esos 103,47 GB hoy solo se pueden verificar por tamaño. Para tener md5 real:

```bat
python inventario_md5.py "https://www.1024tera.com/spanish/sharing/link?surl=1Mx1GP2rPLypBFF1RlVMog" ^
       -o inventarios\manifest_ingles_md5.csv
```

Es de solo lectura (usa `/share/list`, que sí devuelve el md5), tarda un minuto, y
después `VERIFICAR.bat` pasa a chequear md5 en los 103 GB. Corrélo **antes** de la
descarga grande: el manifiesto no cambia, y así verificás todo al final de una.

---

## 4. Script listo para copiar

El descargador es el que ya tenés. Los tres archivos nuevos de esta tanda, con los
comandos exactos:

```bat
REM --- 0) medir donde esta el techo (una sola cuenta, ojo con los 429)
python medir_techo.py --share "<link>" --rapido --json informe_techo.json

REM --- 1) antes de descargar: capacidad real de disco y linea
PREFLIGHT.bat
REM    o a mano:
python preflight.py --destino C:\TB --manifest inventarios\manifest_ingles.csv --url "<link>"
python preflight.py --streams 8 --segundos 15 --fuentes "https://speed.cloudflare.com/__down?bytes=1000000000"

REM --- 2) una vez: manifiesto con md5 verificado por el propio TeraBox
python inventario_md5.py "<link>" -o inventarios\manifest_ingles_md5.csv

REM --- 3) al final: verificar + cuarentena + lista de rehacer
python verificar_integridad.py --manifest inventarios\manifest_ingles_md5.csv --dir C:\TB --cuarentena --json informe_ingles.json
python verificar_integridad.py --manifest inventarios\manifest_piano.csv --dir "C:\Users\Leo\desktop\Curso de piano\descargas" --json informe_piano.json
```

`medir_techo.py`, `preflight.py`, `verificar_integridad.py` e `inventario_md5.py`
funcionan con la librería estándar + `requests` (el último importa `terabox_v2_fixed` para reusar la sesión, los
mirrors y el bootstrap de `jsToken`). Nada de lo nuevo modifica el descargador:
`terabox_v2_fixed.py` quedó intacto.

Si querés el `aria2c` clásico para un archivo suelto cuyo dlink ya tenés:

```bash
# -x16 -s16 -k1M = 16 conexiones por servidor, 16 partes, bloques de 1 MiB.
# Dos correcciones sobre el -x16 -s16 -k1M -z que circula por ahí:
#   * -z NO verifica nada: es --http-accept-gzip (manda Accept-Encoding: deflate,gzip
#     e infla la respuesta). Con requests por Range es contraproducente y para media
#     no aporta: no lo pongas.
#   * -V (--check-integrity) solo verifica si le das el hash con --checksum=md5=...
#     TeraBox te da el md5 por archivo: sacalo del manifiesto y usalo de verdad.
md5=$(awk -F, -v f="archivo.mp4" '$6 ~ ("/" f "$") {print $7}' \
      inventarios/manifest_piano.csv | tr -d '\r')

aria2c -x16 -s16 -k1M --continue=true --max-tries=5 --retry-wait=3 \
       --lowest-speed-limit=64K --file-allocation=none --summary-interval=10 \
       --checksum=md5="$md5" -V \
       -d "/c/TB" -o "archivo.mp4" "<dlink>"
```

Sirve mientras el dlink viva (~10 minutos). Si el archivo tarda más que eso, `aria2c`
va a fallar y vas a tener que pedir dlink nuevo: por eso el método principal es el
script de §3.

### `supervisor.py` — la parte de tu spec que sí se puede hacer bien

```bat
SUPERVISOR.bat
REM o a mano:
python supervisor.py --url "<link>" -o C:\TB --techo-json informe_techo.json --esperar-cookie 900
```

Qué hace exactamente:

- **Arranca con la config que midió §2** (`--techo-json` lee `informe_techo.json`) y
  relanza **siempre con la misma**, así el número de conexiones activas se mantiene
  en el punto óptimo medido en vez de bailar.
- **Detecta `need verify_v2` / `400310` / `400210`** en la salida del descargador y,
  en orden: (1) prueba refrescar la cookie de tu cuenta; (2) si no alcanza, te pide
  el re-login y **espera** hasta 15 min (revisa cada 30 s); (3) si no aparece sesión
  nueva, **para y lo deja escrito** en `supervisor.log`, en vez de quemar la noche
  reintentando.
- **Reanuda sin perder nada**: el descargador ya guarda progreso por segmentos de
  64 MB en `.part.json` y pide con `Range`, así que el relanzamiento continúa donde
  quedó, no de cero.
- **Frena en seco ante `errno -6` / `ip_blacklist`**: no reintenta. Eso no se
  reintenta, se espera.
- **No da por bueno un cierre dudoso**: si el proceso sale con código 0 pero sin
  `RESUMEN` de *esa* corrida, lo marca `DUDOSO` y te manda a verificar. (Esto era un
  bug real que encontré probándolo: leía un `RESUMEN` viejo del log y cantaba
  "listo" aunque la corrida hubiera fallado.)

Validado con un descargador falso que emula los fallos reales, 6 escenarios:

| Escenario probado | Resultado |
|---|---|
| `verify_v2` las 2 primeras corridas, el refresco funciona | refresca, relanza, `LISTO` 5/5 OK en 3 intentos (exit 0) |
| `errno -6` / `ip_blacklist` | `STOP` inmediato, sin reintentar (exit 2) |
| La cookie nunca se arregla | 3 intentos, para y explica (exit 3) |
| `RESUMEN` viejo en el log + corrida nueva falla | **no** dice listo; frena por ban (exit 2) |
| Exit 0 pero sin `RESUMEN` de esta corrida | `DUDOSO`, exit 1 |
| `RESUMEN 4/5` | `PENDIENTE`, exit 1, manda a `VERIFICAR.bat` |

### Tus requisitos de orquestación: dónde está cada uno

| Lo que pediste | Dónde está | Estado |
|---|---|---|
| Leer la lista de archivos | manifiestos en `inventarios/` + `elegir_carpetas.py` (por carpeta) y `--only`/`--fsids` | ya en tu repo |
| Detectar cuentas disponibles en `/cookies/` y rotarlas round-robin | — | **no se hace, a propósito** (§7) |
| Concurrencia real y acotada | `--workers-files` × `-c` (tope explícito); el pool es de hilos, no de procesos hijos: mismo paralelismo sin el costo de arrancar un intérprete por archivo | ya en tu repo |
| Health-check de la cookie antes de un archivo grande | `renovar_cookie.py --check` (lo llaman `PIANO.bat`/`INGLES.bat` antes de arrancar) y `preflight.py --url` | ya en tu repo |
| Retry con backoff en 403/429 | `fetch_range()` reintenta y `_api()` respeta `Retry-After` y rota mirror | ya en tu repo |
| Log central `[TIEMPO] [ARCHIVO] [ESTADO]` | `terabox_dl.log`: una línea por archivo + `vel X MB/s` cada 60 s | ya en tu repo |
| Reporte final OK/FAIL | `RESUMEN: n/N OK ... Fallos: [...]` en el log, e `informe_*.json` de `verificar_integridad.py` | ya en tu repo + nuevo |
| MD5 si está disponible | `inventario_md5.py` (lo trae del API) + `verificar_integridad.py` (lo comprueba) | nuevo |
| Medir el techo y elegir configuración con datos | `medir_techo.py` | nuevo |
| Reintento automático cuando `verify_v2` corta la sesión | `supervisor.py` (una sola cuenta) | nuevo |
| Mantener el nº de conexiones activas en el punto óptimo medido | `supervisor.py --techo-json` (relanza siempre con la misma config) | nuevo |
| Log central con estado por intento | `supervisor.log` + `terabox_dl.log` | ya en tu repo + nuevo |

O sea: de tu especificación, lo que faltaba era **medición y verificación** — eso es
lo que se agregó en esta tanda. La orquestación multi-cuenta es lo único que no está,
y es lo único que no va a estar.

Y para verificación de integridad con lo que **sí** devuelve el API:

```bash
md5sum archivo.mp4          # Linux/macOS/Git Bash
certutil -hashfile "archivo.mp4" MD5   # Windows, si no tenés Python a mano
```

---

## 5. Método alternativo (solo como fallback)

Si `aria2c` no está y necesitás bajar un archivo puntual **cuyo dlink ya tenés**,
y el archivo entra en una ventana de ~10 minutos a la velocidad disponible
(≲6 MB/s → ≲3,5 GB; a 50 MB/s → 30 GB):

```bash
# Bucle paralelo con curl, reanudable por rangos. Corta si el dlink muere (403/404).
dlink="https://.../archivo"; out="archivo.mp4"; N=16

# Tamano real + soporte de Range, en un solo request de 1 byte.
# (No uses HEAD: muchos CDN no lo implementan y te devuelven cualquier cosa.)
eval "$(curl -sSL -r 0-0 -D - -o /dev/null "$dlink" | tr -d '\r' | awk '
  tolower($1) ~ /^http\//{code=$2}                       # HTTP/1.1 206 ...
  tolower($1) == "content-range:"{split($3,a,"/"); size=a[2]}   # Content-Range: bytes 0-0/20000000
  END{printf "code=%s; size=%s\n", code, size}')"
echo "sondeo: HTTP $code, tamano $size bytes"
[ "$code" = "206" ] && [ -n "$size" ] && [ "$size" != "0" ] || {
  echo "[X] el CDN no acepta Range (HTTP $code): usa wget -c o el script de §3." >&2
  exit 3; }

part=$(( size / N ))
for i in $(seq 0 $((N-1))); do (
  a=$(( i * part )); b=$(( a + part - 1 ))
  [ "$i" -eq $((N-1)) ] && b=$(( size - 1 ))
  f="$out.$i"
  for intento in 1 2 3 4 5; do
    have=$( [ -f "$f" ] && stat -c%s "$f" || echo 0 )   # macOS: stat -f%z
    [ "$have" -ge $(( b - a + 1 )) ] && break
    code=$(curl -fsS -w '%{http_code}' -r "$(( a + have ))-$b" "$dlink" -o "$f.tmp") || {
      sleep 3; continue; }        # red caida o 4xx: el dlink murio o hay 429
    [ "$code" = "206" ] || {
      echo "[X] el CDN respondio $code y no 206: no respeta Range." >&2
      echo "    => este archivo va secuencial (wget -c) o por el script de §3." >&2
      exit 3; }
    cat "$f.tmp" >> "$f"; rm -f "$f.tmp"
  done
) & done
wait

# concatenar en orden de indice (no de llegada) y verificar
cat $(seq 0 $((N-1)) | sed "s|^|$out.|") > "$out"
ls -l "$out"    # tiene que pesar exactamente $size
```

Equivalente con `wget` (`-c` reanuda, `--waitretry` respeta backoff):

```bash
wget -c --tries=20 --waitretry=5 --read-timeout=30 -O "$out" "$dlink"
```

Limitaciones reales, para que no te sorprendan a mitad de camino: no renegocian el
dlink, no llevan progreso por segmento en un JSON reanudable, y una caída de
`aria2c`/`curl` no te dice qué 64 MB faltan. Por eso son fallback y no método
principal. Usalos solo cuando el archivo es chico o el dlink es fresco.

---

## 6. Checklist de verificación de 400 Mbps + troubleshooting

### a) Aritmética, para tenerla a mano

```
400 Mbps = 50 MB/s sostenidos
Inglés     : 103,47 GB = 827,8 Gbit  → 30 min exigen 460 Mbps
Los dos    : 125,62 GB = 1005  Gbit  → 30 min exigen 558 Mbps
A 400 Mbps : inglés 34,5 min | los dos 41,9 min
A 3,6 MB/s : inglés 8,0 h    (tu mejor run gratis)
Regla      : segundos = GB x 1000 / (MB/s)      — ojo: MB/s, no Mbps
```

### b) Medir la línea (no TeraBox)

```bash
pip install speedtest-cli && speedtest-cli --simple     # Mbps del ISP
python preflight.py --streams 8 --segundos 15           # MB/s sostenidos, 8 flujos
```

Chequeo de un endpoint puntual con `curl -w` (sostenido, no pico):

```bash
curl -sL -o /dev/null -w 'bajado: %{size_download} bytes en %{time_total} s
velocidad: %{speed_download} B/s
codigo: %{http_code}  conexion: %{time_connect}s  tls: %{time_appconnect}s
' --max-time 20 "https://speed.cloudflare.com/__down?bytes=1000000000"
# 50 MB/s = 400 Mbps. Si speed_download < 6250000 (6,25 MB/s), tu techo son 50 Mbps.
```

Cuidado con el error clásico: **Mbps ≠ MB/s**. Una línea de "400 Mbps" del ISP
entrega 50 MB/s como máximo teórico, ~45 MB/s real.

### c) Medir lo que da TeraBox (y sostenerlo en el tiempo)

```bash
grep "vel " terabox_dl.log | tail -20          # tu propio instrumento, cada 60 s
grep -oE "vel [0-9.]+ MB/s" terabox_dl.log | awk '{s+=$2; n++} END {printf "promedio %.2f MB/s en %d muestras\n", s/n, n}'
```

Si los últimos 10 minutos promedian menos de 100 KB/s, el propio script renueva
tokens y rota mirror (ya está implementado). Si el promedio se estanca siempre en
el mismo valor con distinta concurrencia, ese es el cubo: no insistas con hilos.

### d) Troubleshooting

| Síntoma | Causa | Qué hacer |
|---|---|---|
| `need verify_v2` | cookie vieja o dos sesiones a la vez | cerrar sesión y reentrar, `RENOVAR_COOKIE.bat`, una sola sesión |
| 429 repetidos | demasiadas conexiones o cuota | bajar `--workers-files` a 2-3; el script ya respeta `Retry-After` |
| `errno -6` / `ip_blacklist` | IP marcada por abuso | parar 6-24 h, bajar concurrencia, una sola sesión |
| 403/404 a mitad de archivo | dlink caducado | normal: el script renegocia a los 540 s; si se repite, subir el backoff |
| `vel` plana y baja con premium | cuello en tu línea, no en TeraBox | correr el preflight, hablar con el ISP |
| `.part` que no desaparecen | archivos incompletos | `VERIFICAR.bat` y relanzar con `--fsids` |
| Windows se suspende | energía | Panel de control → Energía → "Nunca" suspender |
| rutas largas fallan | límite 260 chars de Windows | usar destino corto (`C:\TB`), nunca "Curso de piano\descargas" para inglés |
| velocidad pico al inicio y después cae | ráfaga inicial del bucket | es el cubo: mirar el promedio de 10 min, no el pico |

### e) Cómo cerrar la migración

1. `PREFLIGHT.bat` → disco OK, línea medida.
2. `SUPERVISOR.bat` (medición §2 + descarga §3, con reintento automático de sesión)
   hasta `Fallos: []`.
3. `VERIFICAR.bat` con `--cuarentena` → tiene que dar `FALTANTES: 0`, `TAMANO MAL: 0`,
   `MD5 MAL: 0`.
4. Si quedó algo: relanzar con `--fsids $(cat rehacer_fsids.txt)` y repetir el paso 3.
5. Guardar `informe_ingles.json` e `informe_piano.json` como constancia de la migración.

---

## 7. Alcance: lo que este plan no hace

**No incluye repartir un mismo share entre varias cuentas gratuitas.** Ni rotación
de `ndus`/cookies para multiplicar el cupo, ni proxies para repartirlo entre IPs,
ni reventa de sesiones. Motivos concretos, uno técnico y uno práctico:

- **Técnico:** la premisa "el límite es por cuenta, no por IP" no está verificada.
  §2 la mide. Si TeraBox también limita por IP —y el comportamiento que ves
  (agregado que no crece con las conexiones en algunas corridas) es compatible con
  eso—, sumar cuentas desde la misma IP no suma ancho de banda: suma cuentas
  marcadas.
- **Práctico:** el multi-cuenta es justo lo que activa el control que ya te frenó
  (`need verify_v2`), y en servicios de esta familia el patrón termina en cuentas
  y IPs bloqueadas. En una migración de 103 GB que tenés que terminar, quedarte sin
  sesión a mitad de camino es el peor resultado posible.

Lo que **sí** incluye, y es donde está la ganancia real: medir dónde está tu techo
(§2), explotar la dimensión de conexiones si el techo resulta ser por conexión, y
verificar cada byte al final (§4). Si el techo resulta ser de cuenta, el único
camino corto es premium — y con premium el mismo código ya escrito llega a 400 Mbps
sin tocar nada del resto.
