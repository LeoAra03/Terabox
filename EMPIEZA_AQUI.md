# TeraBox Downloader — empezá acá

> **PRIMERO: descomprimí el zip.**
> Si hacés doble clic en los `.bat` desde adentro del zip, Windows los corre
> en una carpeta temporal y no encuentra el resto de los archivos.
> Botón derecho → "Extraer todo" → y usá esa carpeta.

Baja dos shares completos: el curso de piano (20,6 GB) y los cursos de inglés
(96,4 GB). En paralelo, con reanudación, mucho más rápido que la app oficial.

---

## Los 3 pasos

### 1. `INSTALAR.bat` — doble clic
Instala Python y las librerías. Una sola vez.

### 2. Renovar la cookie
En el navegador: entrá a `1024tera.com`, **cerrá sesión y volvé a entrar**,
después `F12` → Application → Cookies → copiá el valor de **`ndus`**.

Corré **`RENOVAR_COOKIE.bat`** y pegala. El asistente la **verifica de verdad**
(no te dice "OK" sin probarla).

> La cookie que venía en `cookie.txt` **ya venció**. Sin este paso, la descarga
> larga falla con `need verify_v2` y perdés la noche.

### 3. Elegí qué bajar

| Doble clic en | Qué baja | Tamaño | Tiempo |
|---|---|---|---|
| **`PIANO.bat`** | Curso de piano | 20,6 GB | 2-3 h |
| **`INGLES.bat`** | 5 cursos de inglés | 96,4 GB | 7-14 h |

**No los corras al mismo tiempo**, uno después del otro.

---

## ¿Puedo frenar y seguir después?

Sí. `Ctrl+C` y volvés a correr el mismo `.bat`: sigue donde quedó.

## ¿Cómo sé que terminó bien?

La última línea dice `Fallos: []`. Si lista archivos, volvé a correr el `.bat`.

## ¿Y si va lento?

Subí `--workers-files` (al 6 u 8). Nunca bajes `-c` de 16.

## ¿Bajar de a partes?

Sí. Las líneas por carpeta (piano) y por curso (inglés) están listas adentro de
los `.bat`, comentadas: sacale el `REM` a la que quieras.

---

Detalle completo, problemas y números medidos: **`INSTRUCCIONES.md`**.
