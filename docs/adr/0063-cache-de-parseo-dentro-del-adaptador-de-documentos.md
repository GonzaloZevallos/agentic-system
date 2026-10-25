---
status: accepted
date: 2026-10-24
---

# Un cache de parseo dentro del adaptador de documentos, para que una ronda ociosa no reparsee la historia

GH-338. El poller ocioso quemaba un núcleo entero: cada ronda reparseaba con `yaml.safe_load`
todos los documentos de tarea, cinco veces (`gather_closes`, `gather_resumes`,
`awaiting_merge` desde `cli._round_eval_set` y desde `run_once`, `reflect_run._documents`).
Los logs no se leen por ronda, `_recent_spend` solo hace `stat` y `registry.spend` lee JSON
chicos: el costo era el parseo.

## Qué se decide

1. **Un cache de parseo dentro de `FilesystemTaskDocuments`**, en memoria del proceso. El
   puerto `TaskDocuments`, todos los llamadores y `MemoryDocuments` no cambian. Los puertos se
   construyen una vez por `agentic poll`, así que el cache dura entre rondas.
2. **La clave sale del descriptor abierto.** `load` hace `os.open`, `os.fstat(fd)` y compara
   `(st_ino, st_size, st_mtime_ns, st_ctime_ns)` con la entrada. Si coincide, devuelve una
   copia sin leer. Si no, lee desde el mismo descriptor, hace `fstat` otra vez y guarda solo
   si las dos claves coinciden. Es la regla de git para archivos "racily clean".
3. **Ventana de carrera de 2 s.** Solo se cachea un documento cuyo ctime es al menos 2 s más
   viejo que el reloj del adaptador (`clock`, ns, argumento solo por palabra; `composition`
   nunca lo pasa). Un ctime futuro, un archivo joven o un `open`/`fstat` fallido no se
   cachean: falla del lado seguro, que es el camino de siempre. La corrección depende de que
   el sistema de archivos local mueva el ctime en cada escritura; en un montaje remoto o con
   reloj roto ese supuesto puede no valer.
4. **Copia al leer.** `load` devuelve una copia que el llamador puede mutar sin cambiar lo que
   devuelve el siguiente `load`. Ver la medición: `copy.deepcopy` era todo el costo de un
   acierto, así que la copia es `_clone`, que reconstruye dicts y listas y comparte los
   escalares inmutables. Mantiene la garantía completa sin auditar a los ~50 llamadores y sin
   imponerles una regla.
5. **Presupuesto de admisión, sin desalojo.** 64 MB de bytes de archivo (`BUDGET`). Un
   documento entra solo si `admitidos + tamaño <= BUDGET`; si no cabe se lee y parsea como
   hoy y no desaloja a otro. No hay LRU: todos los escaneos recorren el mismo orden, y un LRU
   menor que N tendría 0 % de aciertos.
6. **Un `threading.Lock`** protege el dict y el contador de bytes, nada más: ningún `open`,
   `read` ni `fstat` corre con el lock tomado. La poda toma una foto de las claves bajo el
   lock, decide fuera de él y borra bajo el lock saltando las entradas cuya clave cambió.
7. **Poda.** `ids()` suelta las entradas de los ids que ya no lista; `load` de un archivo que
   no existe suelta la suya. `save`, `record_closure` y `write_into` no tocan el cache: el
   siguiente `fstat` decide, porque toda escritura mueve el ctime.
8. **Los errores no cambian.** Un archivo ausente da `None`; un `ValueError` de `parse` nunca
   se cachea y se vuelve a lanzar en cada `load`.

## Medición

Copia de `.agentic/tasks` de este repo: 138 documentos, 16 364 947 bytes. Una ronda ociosa de
`poll.run_once` (`gh` y git de los fakes), CPU de `time.process_time`, reloj del adaptador
adelantado una hora:

| | ronda 1 | ronda 2 |
|---|---|---|
| antes (`BUDGET = 0`: nada se admite, el camino de hoy) | 38,2 s | 41,1 s |
| después | 10,4 s | **0,12 s** |

- Antes, bajo `cProfile`: 552 `load` (4 pasadas de 138), 97,6 % del tiempo en `parse`, y casi
  todo en `yaml.safe_load`.
- Con `copy.deepcopy` como copia, la ronda 2 bajaba a ~0,55 s y `deepcopy` era el 97 % del
  perfil (835 052 llamadas): por eso `_clone`. Con `_clone`, bajo `cProfile` (que infla): 0,70 s
  en total, `_clone` 65 %, `ids()` 8 %, cada escaneo ~0,17 s.
- Memoria (`tracemalloc`) con todo el corpus cacheado: 36,5 MB residentes para 16,4 MB de
  archivo, **2,23 bytes por byte de archivo**: ~21,0 MB son strings de secciones y ~15,5 MB
  objetos del frontmatter. 64 MB de archivo implican ~143 MB de RAM, bajo el límite de 400 MB:
  `BUDGET` queda en 64 MB y el corpus entero cabe (100 % servido desde el cache).

La ronda 1 sigue pagando un parseo por documento (10 s); ocurre una vez por proceso.

## Forma del costo

Por ronda, el parseo pasa de O(N) por escaneo a O(documentos cambiados en los últimos 2 s más
los no admitidos). `stat` y la copia siguen siendo lineales en N: el punto 3 del issue (costo
plano al crecer la historia) se cumple para el parseo y no para el `stat` ni la copia. Memoria:
O(min(bytes del corpus, presupuesto) × 2,23), retenida durante la vida del poller.

## Lo que queda fuera del adaptador

En la ronda 2 del perfil (0,70 s inflado por el perfilador), cada ítem es un seguimiento con
alcance `outside`: `reflect_run.gather` (`gh pr list` y dos `git show`: no se midió, los
fakes no lo ejercitan), `decision_count` (0,002 s), `_closed_escalations` (≈0 s) y `lock.read` en
`gather_retakes` (≈0 s). Ninguno es hoy significativo frente a la copia y el `stat`; el
siguiente costo real sería memoizar `ids()` (8 %) o dar a los escaneos un resumen en vez del
documento completo.

## Consecuencias

- El guardián de regresión cuenta llamadas a `document.parse` (nunca segundos):
  `tests/core/process/application/test_poll_idle_parses.py`, para N = 50, 150 y 200 y por
  escaneo.
- Solo los tests que envejecen sus archivos con el reloj inyectado toman el camino de acierto;
  el resto de la suite escribe archivos frescos y sigue probando el camino de hoy.
