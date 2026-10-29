---
status: accepted
date: 2026-10-27
---

# El tablero del Proyecto espeja el estado y nunca se lee

GH-375. El dueño no tiene un lugar donde ver en qué está cada issue de ambos productos: el
estado vive en documentos de tarea, logs del poller y etiquetas. Existe un GitHub Project
(`GonzaloZevallos/projects/3`, privado) con los campos **Etapa** (el `Status` incorporado,
renombrado), **Bloque** y **Hito** (los pone una persona), **Bloqueo**, **Costo USD**,
**Inicio** y **Fin**. El poller lo mantiene al día.

## Decisión

- **El tablero es una salida, nunca una entrada.** Ninguna decisión del sistema lee el valor
  de un campo o de un ítem del Project. Lo único que se lee del Project son los ids de nodo
  del proyecto, de sus campos y de las opciones de Etapa: identidad, no estado. Una tarjeta
  movida a mano no cambia nada de lo que el sistema decide.
- **Un puerto propio, `Board`.** El Project es E/S que el dominio no ve. No es parte de
  `Tracker`: el tracker es issues, PRs, comentarios y etiquetas (adenda de ADR-0062 §2). El
  puerto lee issues (`open_issues`, `all_issues`, `issue`), resuelve la identidad
  (`project_id`) y escribe (`write`). La memoria de lo enviado es otro puerto, `BoardSent`.
- **Etapa y Bloqueo se calculan en un solo lugar:** `domain/board.py`, a partir de hechos que
  el poller ya calculó (el `BlockerReport` de un issue sin documento, el registro vivo, las
  acciones del plan, el `eval_set`) o de campos del documento. Ningún otro módulo recalcula
  bloqueos, merges, reanudaciones ni retomas para el tablero.
- **Precedencia (una interpretación de la tabla del pedido, supuesto):** Hecha (issue
  cerrado) > Corriendo (hay un proceso vivo; `En PR` sólo sale del mapa) > Necesita humano (la etiqueta `needs-human`, o
  lo que da el mapa) > Fuera del sistema (sin `agentic`, o `ready-for-human`) > Trabada >
  En cola.
- **Mapa cerrado de `Status`** para un issue abierto, con `agentic` y sin proceso vivo.
  Todo valor del `Literal` tiene fila y un valor nuevo sin fila rompe un test:

  | Situación | Etapa | Bloqueo |
  | --- | --- | --- |
  | sin documento, bloqueado | Trabada | `#N, #M` |
  | sin documento, sin bloqueo | En cola | — |
  | `open`, `defined`, `returned`, `definition_ready`, `planned`, `developing` | Trabada | sin proceso vivo: retomar o esperar cupo |
  | `waiting_on_human` | Necesita humano | primera línea del motivo, 200 caracteres |
  | `ready_for_review` | En PR | — |
  | `discarded` / `split` / `tracker_only` | Necesita humano | descartada / partida: cerrar el issue / lista de tracker pendiente |

  `En PR` es la opción que el dueño añadió al campo Etapa (decisión humana): una tarea
  aprobada que espera CI y merge. Un `ready_for_review` es `En PR` lo mergee el sistema o un
  humano, y aunque haya un merge rechazado en el documento; `Corriendo` queda para un
  proceso vivo. Por eso el espejo ya no distingue por `eval_set` ni por merge rechazado.
- **Límite conocido:** `Trabada` sin proceso vivo mezcla "murió" y "espera cupo", porque el
  espejo no sabe distinguirlos. Su Bloqueo es neutro y no afirma una causa que el espejo no
  calcula.
- **Se escribe sólo** Etapa, Bloqueo, Costo USD (`rules.task_spent`, a centavos), Inicio
  (el `started_at` más temprano de las corridas, día UTC) y Fin (el `closedAt` del issue), y
  se dan de alta ítems. Nunca se escribe Bloque ni Hito, nunca se borra un ítem. Un campo sin
  dato se limpia, y sólo si lo último enviado tenía valor.
- **Memoria de lo enviado.** Un archivo JSON por producto en el directorio de estado de la
  máquina, atado al id de nodo del proyecto configurado (una memoria de otro proyecto se
  descarta entera). Guarda por issue la huella de la tarjeta y los campos que tenían valor.
  Se escribe de forma atómica (temporal más `os.replace`); un archivo ilegible se lee vacío,
  con una línea de log. Se reinicia borrando el archivo o cambiando de proyecto. Una tarjeta
  editada a mano se repara en el próximo cambio de hechos de ese issue, no antes. Un issue
  creado y cerrado entre dos vueltas no entra al tablero hasta que se borre la memoria.
- **Reintento.** Un fallo entero de la llamada (identidad o lote) graba `failed_at` y el
  espejo no llama a GitHub durante 15 minutos (`BOARD_RETRY_AFTER`). Un fallo del tablero se
  registra y nunca detiene una vuelta ni una corrida.
- **Alcance del permiso.** El proceso del poller, y ningún rol, usa el token `gh` del dueño
  con el scope `project`. Escribe los cinco campos y da de alta ítems sólo en el proyecto
  configurado y sólo para issues de su propio repo. Bloqueo replica texto de un rol que ya es
  público en el issue.
- **Valores como variables.** Todo valor enviado al Project viaja como variable tipada de
  GraphQL, en un cuerpo JSON (`gh api graphql --input`); en el texto de la consulta sólo se
  interpolan alias de la forma `i<número>_<campo fijo>`.
- **Configuración:** `AGENTIC_BOARD_PROJECT=<dueño>/<número>`. Sin ella el poller no hace nada
  aquí y la vuelta es idéntica a la de antes.

## Consecuencias

- Una vuelta ociosa cuesta una lectura de issues abiertos y ninguna llamada al Project.
- La primera vuelta con unos 450 issues hace unos 36 pedidos (lotes de 25, dos pedidos cada
  uno).
- Si un mapeo es incorrecto se reporta como bug y, tras el arreglo, se borra el archivo de
  memoria del producto para que el espejo reenvíe todo.

## Adenda (GH-421, 2026-10-28)

Se observó que `updateProjectV2Field(name: "Etapa")` sobre el campo `Status` incorporado
contesta `name: "Status"` sin cambio. Por eso el adaptador toma como Etapa el SINGLE_SELECT
`Etapa` o, si no existe, el `Status` con las siete opciones.

## Adenda (GH-438, 2026-10-29)

Una primera sincronización completa (25 issues × 5 campos, unas 125 mutaciones con alias en un
solo documento) recibía un 502 de la pasarela de GitHub, y como el primer lote fallaba y el
bucle cortaba, nada quedaba registrado como enviado: cada ronda repetía la misma sincronización.

- **Tamaño por mutaciones.** `MUTATIONS_PER_REQUEST = 20` acota las mutaciones con alias de una
  petición, altas y escrituras de campo por igual (`BATCH` desaparece). Un issue nunca se parte
  entre dos peticiones; un `assert` de módulo exige que el límite alcance para los campos de un
  issue. Cada trozo de altas va seguido de los trozos de campos de los issues que añadió. El
  número es el orden de magnitud que pasó, no un límite publicado por GitHub.
- **Política solo en escrituras.** `GhBoard(..., retry, clock)`: la composición construye el
  `GhRetry` (2 intentos, espera de 5 s, tope de 30 s, con la bandera de parada) y el adaptador lo
  pasa a `run_gh` solo en peticiones de escritura; la identidad y las lecturas hacen las mismas
  llamadas de antes. El plazo `BOARD_WRITE_DEADLINE_S = 120` se mide con un reloj monotónico
  inyectado antes de cada petición; lo que queda se difiere a la ronda siguiente (una petición en
  vuelo puede añadir unos 65 s).
- **Veredicto de un trozo fallido** (`_verdict`, función pura sobre la excepción):

  | Veredicto | Cuándo |
  |---|---|
  | `stop` | `TrackerUnreachable`, `TrackerRateLimited`, estado 403 o 429, `gh` sin sesión (salida 4), sin `gh`, `OSError`, error GraphQL `RATE_LIMITED` (llega con HTTP 200) |
  | `split` | `TrackerTransient`, `TrackerTimedOut`, cuerpo ilegible (`_UnreadableBody`) |
  | `skip` | cualquier otro `ValueError` |

  `split` espera el `backoff` y parte el trozo por issue hasta uno solo; un issue que vuelve a
  fallar se abandona en esta ronda. `skip` lo registra una vez y sigue con el siguiente trozo.
  Sin política no hay división ni plazo: un 502 es un `skip` con `(HTTP 502)` en el mensaje.
- **`BoardWritten(written, complete)`.** `Board.write` devuelve lo enviado y si una causa de
  transporte cortó la ronda (plazo, división agotada, parada, cuerpo ilegible). Un error GraphQL
  que nombra un issue no baja `complete`: ese issue solo queda fuera de `written`. `write` no
  lanza una vez enviado algún issue: registra la causa y devuelve; sin nada enviado, una parada
  lanza su causa.
- **Descanso en fallo parcial.** `board_sync.mirror` guarda `written` como antes y deja
  `failed_at = now` mientras `complete` sea falso, de modo que el progreso parcial descansa 15
  minutos en vez de golpear a GitHub en cada ronda.
- **Registro.** Una línea de registro lleva lo que imprimió `gh` solo como el estado de tres
  dígitos, `(HTTP nnn)`; el estado lo lee `run.py` (`http_status`, adenda de ADR-0050).
