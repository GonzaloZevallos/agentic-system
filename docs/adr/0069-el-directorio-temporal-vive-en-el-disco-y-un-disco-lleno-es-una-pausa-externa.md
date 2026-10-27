---
status: accepted
date: 2026-10-27
---

# El directorio temporal de cada chequeo y de cada intento vive en el disco, y un disco lleno es una pausa externa

GH-391. El 2026-10-27 `/tmp`, un tmpfs de 19 GB en RAM, se llenó dos veces con 4 a 6 corridas
vivas. Los chequeos y los roles escribían ahí su scratch de pruebas y de compilación
(`pytest-of-<usuario>`, `node-compile-cache`, `playwright-transform-cache`, carpetas por
tarea) y nadie lo borraba. Todas las corridas fallaron igual (el Developer no podía
commitear) y tres tareas escalaron a un humano por un problema que no era de ellas.

## Decisión

**Cada chequeo, cada setup y cada intento de rol tiene su directorio temporal en el disco**,
bajo el producto: `<raíz del producto>/.agentic/tmp/<tarea raíz>/<tipo>-<8 caracteres del
token>/`, exportado como `TMPDIR`. Lo único que lo compone es `scratch.path_for` (dominio):
ningún adaptador arma un camino `.agentic/tmp` a mano, y `<tarea raíz>` es la clave de la
tarea raíz, `_untasked` si no hay. La parte bajo la raíz del producto mide a lo sumo 40
caracteres para una clave de hasta 16, porque un socket unix bajo el `tmp_path` de pytest
tiene que caber en 108 bytes.

**`TMPDIR`, no `--basetemp`.** pytest, la caché de compilación de Node y la de transformación
de Playwright derivan su ubicación de `TMPDIR`, así que se mueven con él. Agregar `--basetemp`
obligaría al sistema a escribir en una línea de comando que declara el stack del producto, y
rompe el contrato de que el stack declara y el sistema impone prácticas.

**La vida de un directorio.** (1) El sistema escribe primero el registro de dueño
`<dueño>.json` (a un nombre temporal y renombrado) con el pid, el id de arranque y el slug.
(2) Crea el directorio. (3) Corre el comando o el intento. (4) Al salir, en este orden: quien
llama reapea su marca, se lee el espacio libre (solo si el intento falló), se borra el
directorio y por último el registro. Así un registro sin directorio es un resto inofensivo y
nunca queda un directorio sin registro por culpa del orden.

**La evidencia.** Las capturas que una tarea cita viven en `.agentic/kept/<tarea>/`
(`TASK_EVIDENCE_DIR`), con el prefijo de la clave de la subtarea. Nada se mueve ahí
después y nada se lee del documento de tarea para decidirlo: el rol las escribe ahí. Se
conservan 14 días desde que la tarea cierra.

**El barrido.** En cada ronda el poller borra el scratch de las tareas cerradas, el de los
dueños que no están vivos (pid más id de arranque; un directorio sin registro, cuando su
mtime tiene más de 10 minutos) y la carpeta de evidencia vencida.

**El piso de espacio libre.** Después del barrido, el poller lee el espacio libre del
volumen del scratch y de `/tmp`. Si cualquiera está bajo el piso
(`AGENTIC_SCRATCH_FLOOR_MB`, 2048 por defecto), la ronda no arranca nada salvo cierres, y la
línea de log dice qué volumen y con qué cifras.

**`disk_full`.** Un intento que falla con el disco lleno es un motivo externo nuevo del
cierre de `FailureReason`. Se deriva solo de hechos medidos, nunca del texto del error: el
espacio libre en bytes o en inodos (`INODE_FLOOR` = 10 000) leído por el runner después de
reapear y antes de borrar el scratch, o un `OSError(errno.ENOSPC)` levantado en el proceso
del propio sistema durante el intento, capturado como dato. `rules.space_narrowed` lo
decide sobre esa lectura estampada en el fallo, nunca sobre una lectura nueva. `limit` (el
tope por intento, una configuración nuestra) nunca se angosta. El reintento dentro de la
corrida se corta en el primer intento, como el de `account_limit`: un disco lleno no se
vacía entre dos intentos. La pausa es una escalada de clase `failure` que el poller retoma
por `RETAKE_SCHEDULE`, pero el piso frena todo arranque, así que la retoma ocurre cuando
hay espacio. Conserva `needs-human`, y el scorecard lo excluye de los fallos propios
(copia propia de la lista de motivos externos).

**La guarda.** Los roles que ejecutan pueden escribir, fuera de su checkout, solo en el
directorio temporal de su propio intento y en la carpeta `kept/` de su propia tarea. La
negación del almacén de resultados de chequeo sigue ganando adentro de ambos, y un camino
que escapa por un symlink o `..` sigue negado. Con la lista de permitidos vacía la guarda es
byte por byte la de antes.

## Consecuencias y riesgos conocidos

- Un rol que escribe a mano un `/tmp/...` sigue escribiendo ahí: la cifra de `/tmp` del
  poller es la única evidencia y nada lo niega.
- `/tmp` está en el piso, como pidió el issue: el poller no arranca nada hasta que una
  persona limpia una vez lo que ya hay (`/tmp/pytest-of-<usuario>`, las cachés de Node y
  Playwright, las carpetas `/tmp/gh*`).
- El piso se mira solo al arrancar: una corrida puede gastarlo entre dos rondas.
- Un ENOSPC en un tercer volumen, o un límite por directorio que ninguno de los dos
  volúmenes medidos muestre, conserva el motivo de hoy. Una escritura del documento o del
  checkpointer que choque con ENOSPC fuera de un intento sigue siendo una Corrida
  interrumpida.
- El camino del scratch es más largo que el de `/tmp`: un socket unix bajo `tmp_path` puede
  pasar de 108 bytes.
- Con el barrido a los 14 días, una captura citada en un documento viejo puede dejar de
  existir.
- Node y Playwright reconstruyen sus cachés en cada chequeo: un costo de tiempo por chequeo,
  constante en el tamaño de los datos. El barrido cuesta un listado lineal en la cantidad de
  directorios de dueños, por ronda.
- `perf_runner.py` (su propio `mkdtemp`) y `sample_app.py` siguen usando el `TMPDIR` heredado;
  pasan al mismo puerto en otro issue.
- El prompt base de los roles que ejecutan cambia en una línea, y con él el hash de prompt del
  Developer y del integrador: las corridas posteriores no se comparan con las anteriores por
  ese hash.
