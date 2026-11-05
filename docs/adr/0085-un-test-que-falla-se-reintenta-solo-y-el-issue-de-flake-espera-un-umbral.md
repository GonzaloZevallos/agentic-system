---
status: accepted
date: 2026-11-04
---

# Un test que falla se reintenta solo en toda puerta, y el issue de flake espera un umbral

GH-464 (parte 1, GH-478). ADR-0066 reintentaba un chequeo rojo una sola vez, y la forma
"solo los tests que fallaron" (`rerun_checks`) solo corría en la puerta de entrega. El merge, el
chequeo completo y el aterrizaje repetían el comando entero, y el primer flake de un chequeo ya
abría un issue (`file_flakes`), aunque un solo caso intermitente no dice nada del chequeo.

El dueño decidió el 2026-11-03: un test que falla se reintenta una vez, solo; si pasa cuenta como
pasado y queda registrado como flaky; el issue se abre recién pasado un umbral.

## Decisión

- **Reintento por forma en las tres puertas.** `presubmit.rerun_command` ya no mira la puerta:
  la forma de `rerun_checks` aplica en `delivery`, `merge` y `full_checks`. Se elige solo cuando
  `failed_ids` encontró ids y `ids_complete` es verdadero (menos de `FAILED_IDS_MAX` ids y la salida
  dentro de `FAILED_IDS_TAIL_BYTES`); en cualquier otro caso, o sin forma, corre el comando entero.
  Un chequeo `timed_out`, `not_ready` o `rerun` nunca se reintenta.
- **Forma sin resolver.** `presubmit.rerun_resolved(form_row, ids, form)`: una fila roja de la
  forma solo está resuelta si `failed_ids` de su salida nombra uno de los ids que se pidió
  reintentar. Si no (nada seleccionado, un id truncado como `test_x[a`, un error de comando), el
  chequeo vuelve una sola vez al comando entero, el veredicto es `flake_verdict(primera, entero)` y
  la fila de la forma se descarta dentro de `_with_rerun`. Una forma sin resolver nunca es `fail`
  por sí sola.
- **Cupo de tiempo.** `presubmit.check_runs(has_form)` da el peor caso de corridas de un chequeo:
  3 con forma (primera, forma, entero) y 2 sin ella. Es la única fuente para `_timeout_s` en el
  camino serie, el paralelo y la rama de `timeouts`; un producto sin `rerun_checks` conserva el cupo
  de siempre.
- **Bloque de flakes del CI.** Un job de CI imprime una línea
  `agentic-flakes: {"v":1,"check":"<nombre>","tests":["<id>",...]}` con los tests que su propio
  reintento encontró flaky. `GhCiLogs.flake_block` lee `gh run view <run> --job <job> --log` (nunca
  `--log-failed`) de los jobs de `ci:`, solo si el reporte de CI es de la cabeza que se juzga
  (`rules.same_commit`), y se queda con las líneas que empiezan con el prefijo tras las columnas de
  `gh` y la marca de tiempo. `ci.parse_flake_blocks` gana el último bloque por `(job, check)`, descarta
  los chequeos que el stack **en el commit base** no declara (`declared`, nunca el de la cabeza
  bajo prueba), acota los ids (`FAILED_IDS_MAX`, 200 caracteres cada uno), descarta el id con salto de
  línea, comilla invertida, `<!--`, `-->` o `{{`, y descarta la línea malformada entera. Solo produce
  `FlakeRecord`; nunca un veredicto. `full_checks`, en su camino de CI, y la puerta de merge lo leen
  para la cabeza que juzgan (`ci_flakes.block_records`) y lo anotan con `add_flakes`.
- **Registros fechados.** `FlakeRecord.at` (ISO UTC, `NotRequired`) lo pone la aplicación, que lee
  el reloj una vez por llamada. Un documento anterior sin `at` sigue siendo válido y no cuenta para el
  umbral.
- **Umbral por clave.** `landing.flake_key(record)` da `(chequeo, test)` por cada test, o
  `(chequeo, "")` cuando `tests` está vacío; es la única función que alimenta el marcador, el umbral y la
  deduplicación. `landing.keys_over_threshold` marca una clave cuando al menos
  `Configuration.flake_issue_tasks` (3) tareas distintas tienen un registro con
  `at >= ahora - flake_issue_days` (14 días); una tarea cuenta una vez por clave y `ahora` lo pasa el
  llamador. El conteo suma `TaskDocuments.flakes_since(corte)` (que salta por mtime los documentos
  anteriores al corte antes de parsearlos) y los registros de la tarea en curso.
- **Una sola ruta de archivado.** `landing_flakes.file_task_flakes` absorbe a `file_flakes`; el paso
  `rerun` del aterrizaje pasa a ser un registro con clave `(chequeo, "")`. El orden es fijo: (1) el
  conteo; (2) solo si alguna clave lo supera, el lock `RunRegistry.flake_lock` (archivo
  `flakes/<producto>.lock`, `flock` no bloqueante sondeado hasta `FLAKE_LOCK_WAIT_S` = 10 s), luego
  `Inbox.open_issue_refs` (una sola llamada `gh`, `--limit 1000`, `ValueError` si llega justo al
  límite) y recién entonces abrir o comentar, nombrando los tests sobre el umbral. Bajo el umbral no
  hay ninguna llamada al tracker. `landing.flake_issue_for` encuentra el issue por el marcador
  `flake_marker(chequeo)` o por el título exacto `Flaky check: <chequeo>`. `flake_comments` sigue
  siendo la idempotencia por (issue, tarea). `Inbox.open_issues` y `open_issue_bodies` no cambian.
- **Un lock perdido no frena nada.** Un lock que no se toma en el plazo o un directorio sin permiso
  de escritura da "no adquirido" (nunca una excepción), una lista saturada se registra
  (`list_saturated`) y el archivado se salta; el merge sigue.

## Modelo de amenaza

El bloque es evidencia de la cabeza bajo prueba, no un veredicto: cualquiera con permiso de push a
una rama que corre CI puede imprimirlo. Lo peor que un bloque falsificado puede hacer es registrar
flakes de chequeos declarados en el base (con ids acotados y limpios, así que no rompen un marcador ni
un comentario) y, si superan el umbral, abrir o comentar **un** issue por chequeo declarado. Nunca
cambia un veredicto, un merge ni el cupo de tiempo: las puertas deciden pass, flake o fail solo con
`flake_verdict` y `outcomes`. Un bloque ausente o malformado no registra nada y no cambia ningún
veredicto. El conjunto `declared` sale del stack del commit base precisamente para que la cabeza no
pueda ampliarlo.

## Rechazado

- Abrir el issue en el primer flake: un caso suelto no dice nada del chequeo.
- Contar por chequeo y no por `(chequeo, test)`: un chequeo grande con muchos tests sin relación
  alcanzaría el umbral sin que ningún test sea flaky.
- Listar los issues siempre y filtrar después: una llamada al tracker por merge sin necesidad.
- `--log-failed` para leer el bloque: un job verde, que es el caso de un flake, no tiene log fallido.

## Residuos aceptados

1. **Falla en la suite que pasa sola.** Un test que falla dentro de la suite por interacción con otro y
   pasa corrido solo queda registrado como flake. El umbral lo acota: hacen falta 3 tareas en 14 días.
2. **Falla que el patrón no nombra.** Si la salida tiene una falla que `test_id` no reconoce junto con un
   id que sí, la forma reintenta solo ese id. También lo acota el umbral, y la corrida completa en la
   cabeza integrada cubre lo que la forma omitió (ADR-0064).
3. **Ids distintos bajo el umbral.** Un chequeo que flakea con ids distintos, cada uno bajo el umbral,
   no abre issue; la clave `(chequeo, "")` cuenta aparte.
4. **Conteo local.** `flakes_since` lee los documentos de esta máquina y no ve las corridas archivadas
   (ADR-0078) ni otras máquinas: subcuenta, del lado seguro.
5. **Lock perdido.** Si el lock no se toma, el issue se demora hasta el próximo flake dentro de la
   ventana.
6. **Tope de 4 MiB.** `CI_FLAKE_LOG_BYTES` (`adapters/github/ci_logs.py`) retiene la cola del log, donde
   el job imprime el bloque, sin pasar de 4 MiB de memoria; la cifra está declarada, no medida. La
   revisión de integración deja un hallazgo `outside` para medir un log verde real de `ci.yml`. Un log
   cortado por el tope no es un fallo; un tiempo agotado (`CI_LOG_TIMEOUT_S`), una salida distinta de
   cero o una lectura imposible es `CiUnreadable`, que no registra nada.

## Adenda (2026-11-04, GH-479)

El lado de CI del bloque (`ci.yml` y `scripts/ci_flakes.py`) y la forma `rerun_checks` de este repo
fijaron tres puntos que el texto original dejaba abiertos:

1. **La forma es la caché de pytest, no ids.** El sistema no sustituye ningún token `{ids}` (`rerun_command`
   devuelve `command` tal cual y solo rellena `{base}` y `{workers}`), así que la forma de `tests` es
   `uv run pytest -m "not llm" --last-failed --last-failed-no-failures none`. Con la caché vacía no corre
   nada y sale distinto de cero, de modo que nunca reintenta la suite entera; `rerun_resolved` cae
   entonces al comando completo, como antes.
2. **El junit es `xunit1`.** Su atributo `file` reconstruye el id de nodo que la puerta lee de las líneas
   `FAILED`. El script cuenta solo `<failure>`, nunca `<error>` (el patrón `FAILED` no ve las líneas
   `ERROR`), y un id ausente del segundo archivo, o un archivo ilegible, es falta de evidencia: no es un
   flake.
3. **Un id con ` - ` se corta.** `test_id` termina en ` - ` o en el fin de línea, y un id parametrizado que
   contenga ` - ` queda truncado; el reintento por forma no encuentra ese test y la puerta cae al comando
   completo (residuo acotado por el umbral).
