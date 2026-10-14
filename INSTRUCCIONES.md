# Descargador de TeraBox — Instrucciones

Descarga completa de dos shares de TeraBox, en paralelo, con reanudación.
Mucho más rápido que la app oficial.

---

## 1. Los dos shares

| | Curso de Piano | Cursos de Inglés |
|---|---|---|
| Tamaño | **20,63 GB** | **96,36 GB** |
| Archivos | 691 (19 carpetas) | 356 (5 cursos) |
| Ajustes | `--workers-files 8 -c 16` | `--workers-files 4 -c 24` |
| Destino | `...\Curso de piano\descargas` | `C:\TB` |
| Tiempo (a 3 MB/s) | ~2 h | ~9 h |
| Lanzador | `PIANO.bat` | `INGLES.bat` |

Los dos son **independientes**. Hacé uno y después el otro.

> **No los corras al mismo tiempo.** Comparten la cookie y la conexión, y
> TeraBox responde `need verify_v2` cuando detecta dos sesiones simultáneas.
> Uno tras otro, siempre.

---

## 2. Instalación (5 minutos, una sola vez)

1. **`INSTALAR.bat`** — doble clic.
   Instala Python (te abre la página si falta) y las 3 librerías.
   Al final te abre el asistente de cookie.

2. **Sacar el `ndus` nuevo.** En el navegador:
   - Entrá a `1024tera.com` y **cerrá sesión**
   - Volvé a entrar (esto genera un `ndus` nuevo)
   - `F12` → **Application** → **Cookies** → `https://www.1024tera.com`
   - Buscá la cookie llamada **`ndus`** y copiá su valor

3. **`RENOVAR_COOKIE.bat`** — pega el `ndus` (opción 2), o copialo antes
   y usá la opción 1 (lo lee del portapapeles).
   El asistente lo **prueba de verdad**: pide un enlace de descarga y baja
   1 KB del CDN. Si dice `[OK] Sesión OK`, listo.

**La cookie vence.** Si volvés en unos días, corré `RENOVAR_COOKIE.bat` otra vez.

---

## 3. Descargar

Doble clic en **`PIANO.bat`** o **`INGLES.bat`** y elegí:

- **Prueba de fuego (recomendado la primera vez)** — baja una muestra real
  (57 MB el piano, 596 MB el inglés, 2-4 minutos). Si termina en `Fallos: []`,
  la descarga larga también va a andar.
- **No** — arranca la descarga completa directamente.

Además, antes de preguntarte nada, los `.bat` hacen un **chequeo de 5 segundos**
de la cookie. Si no pasa, te avisan y te ofrecen parar.

### Cómo termina

```
RESUMEN: 691/691 OK en 148.3 min. Fallos: []
```

- `Fallos: []` → listo, quedó todo.
- `Fallos: [archivo1, archivo2]` → volvé a correr el mismo `.bat`.
  Retoma solo lo que falta.

Al final el `.bat` también cuenta los archivos `.part` que quedaron
(tienen que dar **0**).

---

## 4. Velocidad y tiempos

Medido en pruebas reales: **~2 MB/s** con `-c 16`. Tu resultado depende de la
conexión y de la hora.

| Velocidad | Piano (20,6 GB) | Inglés (96,4 GB) |
|---|---|---|
| 2 MB/s | 2,9 h | 13,7 h |
| 3 MB/s | 2,0 h | 9,1 h |
| 4 MB/s | 1,5 h | 6,9 h |
| 6 MB/s | 1,0 h | 4,6 h |
| 8 MB/s | 0,7 h | 3,4 h |

**Si va lento** (menos de 2 MB/s sostenido): subí `--workers-files` a 6-8
(más archivos en paralelo = más conexiones vivas). **No bajes `-c`** de 16:
una sola conexión da ~0,2 MB/s.

**Se degrada con el tiempo?** El script tiene un vigilante: cada 60 segundos
mide la velocidad; si los últimos 10 minutos promedian menos de 100 KB/s,
renueva los tokens y rota de servidor solo. Está en `terabox_dl.log`.

---

## 5. Frenar y retomar

**Ctrl+C** frena en cualquier momento. Lo bajado **no se pierde** (se guarda
en `.part` + `.part.json`).

Volvé a correr el mismo `.bat` y sigue donde quedó.

> Cada Ctrl+C cuesta hasta **64 MB de reanudación** por archivo que estaba
> en vuelo. No lo aprietes por gusto.

**Windows suspendido = descarga muerta.** Antes de dejarlo de noche:
Panel de control → Energía → "Nunca" suspender.

---

## 6. Bajar por partes

Todos estos van **en la misma carpeta**: los archivos ya bajados se saltean
solos, así que podés combinar sin miedo.

**Por curso (inglés)** — sacale el `REM` a la línea que quieras al final de `INGLES.bat`:

```bat
python terabox_v2_fixed.py "%URL%" -o "%DEST%" %OPT% --only "Inglés C1 para Hispanohablantes"
```

**Por carpeta (piano)** — editá `PIANO.bat` y reemplazá la línea principal:

```bat
python terabox_v2_fixed.py "%URL%" -o "%DEST%" %OPT% --only "17\. Improvisación"
```

**Carpeta por carpeta, estricto** (una cerrada antes de la siguiente):

```bat
python terabox_v2_fixed.py "%URL%" -o "%DEST%" %OPT% --por-carpeta
```

> Cuesta **15-30% más de tiempo**: en cada límite el grupo de descargas se vacía
> y las últimas conexiones trabajan solas. Si no necesitás mirar carpeta por
> carpeta, no lo uses.

**Con las dos primeras formas**, por defecto los archivos se ordenan por carpeta
igual: las 19 carpetas del piano se completan en orden **sin perder velocidad**.

---

## 7. Problemas

| Qué ves | Qué pasa | Solución |
|---|---|---|
| `need verify_v2` / `400210` / `400310` | Cookie vencida o de otra red | `RENOVAR_COOKIE.bat` |
| `403` bajando archivos | El CDN rechazó la cookie | Renovar cookie |
| Un archivo nunca arranca, `eta` raro | Normal los primeros 2-3 min | Esperar |
| `No such file or directory` | Ruta muy larga | Usar `C:\TB` (ver abajo) |
| `Espacio insuficiente` | Falta disco | El script avisa antes de empezar |
| Docenas de `.srt` de **0 bytes** | Están vacíos en el origen | No es un error |

### ⚠️ Rutas largas (límite de 260 de Windows)

El share de inglés tiene una ruta interna de **208 caracteres**. Si le sumás
`C:\Users\Leo\desktop\Curso de piano\descargas\` (47), llegás a **255**: cinco
caracteres de margen. Por eso `INGLES.bat` usa **`C:\TB`** (214 en total,
46 de margen). No lo cambies por la carpeta del piano.

El share de piano sí puede ir a `descargas` sin problema.

### No llegás a la velocidad esperada

1. Verificá que estás usando los ajustes correctos para cada share (arriba).
2. Si estás bajando el inglés con `--workers-files 8`, el archivo de 1,28 GB
   baja con 3 conexiones y te frena todo. Usá `4`.
3. Cerrá la app oficial de TeraBox y el navegador con TeraBox abierto:
   compiten por la misma cuenta.

---

## 8. Qué hay adentro

| Archivo | Para qué |
|---|---|
| `terabox_v2_fixed.py` | El programa. v3 + parches 1-9. |
| `renovar_cookie.py` | Renueva y verifica la cookie. |
| `cookie.txt` | Tu `ndus`. |
| `INSTALAR.bat` | Dependencias + cookie. Una vez. |
| `RENOVAR_COOKIE.bat` | Cuando la cookie vence. |
| `PIANO.bat` / `INGLES.bat` | Los lanzadores. |
| `inventarios/manifest_*.csv` | Los 691 + 356 archivos, para verificar. |
| `extras/terabox_dl.py` | Motor alternativo (v3, verificado 441/441). |
| `extras/Plan_Descarga_TeraBox.xlsx` | Plan original en Excel. |
| `terabox_dl.log` | Se crea al correr. Ahí está la velocidad minuto a minuto. |

### Detalles técnicos (para curiosos)

- **Tuning distinto por share.** Piano: mp4 de 84 MB promedio, ~447 segmentos →
  muchos archivos, 2 conexiones cada uno. Inglés: mp4 de 309 MB promedio,
  **1.753 segmentos** → menos archivos, 6 conexiones cada uno.
- **Un `fs_id` por llamada** a `/share/download`: con varios devuelve un `.zip`
  con `size -1` y falla en silencio.
- **Cookie en el tarro** (`s.cookies.set`), nunca en cabecera estática: si no,
  `errno 400210`.
- **Los enlaces del CDN vencen a los 600 segundos**: se piden por segmento.
- **`surl` = 22 caracteres exactos.** Recortar un `1` inicial de más rompe
  todo con `need verify_v2` (bug encontrado y arreglado en el parche 9).
- **`--only` es una búsqueda, no un ancla**: poné el nombre de la carpeta sin
  `^` adelante.
