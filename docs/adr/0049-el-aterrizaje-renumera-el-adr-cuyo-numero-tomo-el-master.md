---
status: accepted
date: 2026-10-16
---

# El aterrizaje renumera el ADR cuyo número tomó el master

El 2026-10-08 cuatro corridas de apostrophe crearon cada una su ADR-0037 en paralelo (#160,
#159, #167, #170). Cada rama estaba verde sola; al mergear dos, `tests/test_adr_numbers.py`
puso el master en rojo y cada PR posterior falló hasta renumerar a mano. El número se elige
sobre la base de la tarea y la puerta de merge no notaba que el master ya lo había tomado.
Este ADR (GH-229) hace imposible que dos ADR aterrizados compartan número.

## Decisión

**Opción 1**: antes de mergear, el aterrizaje renumera el ADR de la rama cuyo número el
master ya tomó, y reescribe sus referencias dentro del trabajo de la propia rama. La
opción 2 (asignar el número al mergear) se rechaza: dejaría un número provisional en texto
ya commiteado y cada referencia habría que reescribirla igual al mergear.

1. `agentic merge` resuelve un solo sha M del master. Si la rama toca `docs/adr/`, lee los
   ADR que agregaron sus propios commits (los commits no-merge de `M..head`) y los compara
   con el `docs/adr` de M. Una colisión rechaza con `adr_number_taken`, justo después de
   `pr_not_open`, para no gastar revisión, pasada ni chequeos sobre un head que va a cambiar.
2. La tabla de aterrizaje manda ese rechazo al paso `renumber`: aplica el `Renaming` que
   calcula una función pura (`domain/adr_numbers.py`), commitea sólo las rutas del
   `Renaming` (las viejas incluidas), empuja con lease sobre el head que leyó y registra
   `adr_renumbered`. La ronda siguiente decide de nuevo sobre el head nuevo. Un lease
   perdido no registra nada.
3. Respaldo: `tests/test_adr_numbers.py` corre en toda rama de ADR (fila nueva de `TABLE`
   en el selector de pruebas afectadas), y la puerta de merge corre los chequeos completos
   sobre el head mezclado con el master.

## Qué cuenta como ADR propio y cómo se lee

Los ADR propios son los archivos que agregaron commits no-merge de `M..head`, nunca
inferidos de un merge-base ni de las rutas tocadas (sólo disparan la lectura). Las lecturas
son en dos fases: `added_adrs` siempre; `added_lines` y `tree_names` sólo para los números
que colisionan. El adaptador recibe los números como cadenas y no conoce la gramática.

## Gramática cerrada y postcondición

Una sola sustitución simultánea por texto (0049->0050 y 0050->0051 nunca se encadenan):
(a) el nombre completo `NNNN-<slug>.md`, con o sin prefijo `docs/adr/` o `adr/`; (b)
`ADR-NNNN` y `ADR NNNN`; (c) una lista tras un solo `ADR`/`ADRs` unida por `,`, `and`, `y`
o `/`. Los tokens sueltos se reescriben sólo en líneas que agregaron los commits propios, y
sólo si el commit que agregó la línea no tenía ya el archivo del master con el mismo número.
Un número renumerado que quede fuera de la gramática vuelve el resultado `Ambiguous`.

## Ambiguous va a diagnose

Tres casos: dos ADR propios con el mismo número; una línea de la rama cita el número
colisionado después de que llegó el archivo del master; un número renumerado fuera de la
gramática. `Ambiguous` va a `diagnose`, nunca a `fix`: ningún Developer elige un número de ADR.

## Presupuesto y por qué no es un `wait`

`LANDING_RENUMBERS_MAX = 3`, contado desde la lista `landing` y no cargado a `syncs` ni
`passes`; el valor es declarado, no medido. Agotado, el paso siguiente es `diagnose` sin más
pushes. No es un `wait`: esperar no cambia el número tomado; cada renumeración empuja un
head nuevo y cada uno vuelve a gastar chequeos.

## Qué carga la revisión

`renumber_carries` (en `domain/landing.py`, junto a `review_carries`) carga el veredicto
sólo si cada línea cambiada está bajo `docs/` o es un comentario `#`; docstrings y literales
no cargan. `landing_sync._carries` sigue siendo el único que escribe `reviewed_head` y
`reviewed_head_carried_from`. Si no carga, `reviewed_head` queda y la ronda siguiente rechaza
`head_not_reviewed` y va a `re_review`; los chequeos siempre corren de nuevo.

## Qué enmienda

Por nombre, sin editar esos ADR: ADR-0045 (la tabla de rechazos suma `adr_number_taken`
tras `pr_not_open`), ADR-0047 (la lista de ignorados y `TABLE` de las pruebas afectadas) y
ADR-0048 (la tabla de aterrizaje suma el paso `renumber`). Las tareas del conjunto de
evaluación también se renumeran. Para que el aterrizaje vea el rechazo, el sondeo
(`assess_merge`) lee la colisión con el mismo sha M, sólo para una tarea lista con PR
abierto cuyo diff toca `docs/adr/`.

## Observabilidad y seguridad

§7: sólo valores cerrados nuevos (`adr_number_taken` en `MergeRefusal`, `renumber` en
`LandingStepKind` y su atributo `agentic.landing.*`) y la clave opcional `adr_renumbered`
(se escribe sólo si existe y se lee con `.get`); ninguna métrica, etiqueta ni atributo se
renombra ni se quita; ningún prompt cambia. §8: el sistema ahora puede renombrar un ADR,
reescribir texto de archivos que cambiaron los commits propios y empujar ese commit a su
propia rama con lease (el derecho de push que `sync_master` ya tenía). Costo: cero en una
ronda cuyo diff no toca `docs/adr/`; si no, un `git log` acotado y, ante una colisión, un
`git log -G` por número, un `ls-tree` por commit con tales líneas y dos `show` por archivo;
a lo sumo 3 pushes por tarea, cada uno seguido de una corrida de chequeos. El sondeo suma
una lectura de diff por tarea lista y tick.

## Riesgos conocidos

1. El ADR de esta misma rama no queda protegido por el rechazo nuevo: mergea con el código
   viejo del stage0; lo protegen la prueba de unicidad y la revisión del número antes de
   entregar. Si se cuela una colisión, el aterrizaje viejo la manda a `rerun` y luego a un
   `fix` manual hasta reanclar el stage0.
2. Las líneas agregadas sólo dentro de un commit de merge (resoluciones de conflicto) son
   invisibles a `--no-merges`; sólo las cubre la prueba de unicidad.
3. Con cuatro corridas aterrizando ADR a la vez una rama puede perder tres carreras
   seguidas y terminar en `diagnose`; la salida es el reinicio acotado (`RESTART_DEPTH_MAX = 1`).
4. Los merges manuales fuera de `agentic merge` sólo los cubre la prueba de unicidad, y sólo
   cuando el CI la vuelva a correr (apagado por billing).
5. La subtarea `renumber` es grande a propósito: los archivos que chocaron en el PR #237
   pertenecen a la ola 2.

## Adenda (GH-412)

Enmienda la sección «Ambiguous va a diagnose»: ahí donde dice que `Ambiguous` va a `diagnose`,
ahora va a `escalate_adr`.

- **Una colisión `Ambiguous`, o un `Renaming` con `LANDING_RENUMBERS_MAX` renumeraciones
  gastadas, es `escalate_adr`**, nunca `diagnose` ni `restart`. Se pregunta una sola vez por
  (cabeza, rechazo) y en esa cabeza el paso siguiente es `none`. Ningún Developer elige un
  número de ADR, y un reinicio chocaría con el mismo número. La entrada cuenta como preguntada
  sólo cuando el documento se guardó y el comentario se publicó (`resolved_at`): un comentario
  que falla se vuelve a preguntar. El mensaje (`landing_fix.adr_collision_reason`) nombra los
  movimientos que eligió la regla, el motivo cerrado y la línea a corregir a mano; la salida es
  renumerar a mano y empujar (una cabeza nueva vuelve a la tabla) o decidir en el issue; una
  respuesta sin cabeza nueva no vuelve a preguntar. El caso GH-384 (la rama con 0054–0060 y un
  0054 propio del master) mueve sólo el 0054 al siguiente libre, no los siete.
- **Motivo nuevo `range`.** Un número movido en cualquiera de los extremos de un rango
  (`ADR-0054…ADR-0060`, `0054–0060`) vuelve el resultado `Ambiguous("range")`; antes se
  reescribía en silencio a `ADR-0061…ADR-0060`. Los separadores cerrados son `…`, `...`, `..`, `–`, `—`, un
  `-` pegado a un dígito, `to`, `through` y `a`. `Ambiguous` lleva los `moves` que la regla calculó.
- **Reservar los números al sincronizar con el master, rechazado.** Ahorraría una ronda de
  chequeos por colisión, pero obligaría a tocar `landing_sync`, el único que escribe
  `reviewed_head`, y a mezclar dos políticas con su propio lease y presupuesto.
- **§7.** Dos valores cerrados nuevos: `escalate_adr` (`LandingStepKind`, que ya lleva el
  atributo `agentic.landing.*`) y `range` (`AmbiguityReason`). Ninguna métrica, etiqueta,
  atributo ni clase de escalado nuevo: se usa `questions`, la de `escalate_ledger`.
- **Riesgos conocidos.** (1) Si GH-384 pasó por `checks_failed` y `poll._adr_collision` tragó un
  `GitError`, esta adenda no lo cubre: es un seguimiento. (2) Un rango implícito
  (`0052–0056` con el 0054 adentro sin nombrarse) no lo ve la regla. (3) Más escalados: las
  ramas llenas de tablas y rangos ahora preguntan en vez de reiniciar; ampliar la gramática a
  celdas de tabla es un seguimiento. (4) La guarda de `escalate_ledger` cuenta entradas sin
  mirar `resolved_at`; este cambio no lo corrige y queda como seguimiento.

## Adenda (GH-544)

Enmienda «una respuesta sin cabeza nueva no vuelve a preguntar»: una decisión del dueño en el
issue sobre una pregunta `escalate_adr` ahora se aplica (apostrophe GH-407, PR #618).

- **Decisión → `apply_adr_decision`.** Una colisión `Ambiguous` con `moves`, cuya pregunta
  `escalate_adr` el dueño liberó (`released_at` sobre esa entrada), da el paso nuevo
  `apply_adr_decision` (`land_model`). Una colisión `duplicate_own_number` (sin `moves`) sigue
  siendo a mano. Entradas de GH-412 sin `moves` son elegibles.
- **La regla elige los números; ningún Developer elige uno.** El ejecutor recalcula la colisión
  contra el sha del master de la ronda y `adr_numbers.renumber_decided` aplica todo lo que la
  gramática decide y lista cada línea propia que no (`unclassified_token`, `range`,
  `cites_master_after_sync`): los puntos (_spots_).
- **El Developer edita sólo los puntos.** El sistema verifica los límites y que no quede
  colisión antes de empujar con lease; la cabeza nueva vuelve a la tabla.
- **Tope.** `LANDING_ADR_DECISIONS_MAX = 2`, contado sobre todas las entradas con un `outcome` cerrado
  (`applied | out_of_bounds | role_failed | checks_red | still_colliding`); `stale_moves` no usa
  cupo y es ilimitado a propósito, y una restauración no devuelve cupo. El paso no gasta
  `LANDING_PASSES_MAX` ni llega a `exhausted()`. Todo `outcome` que no sea `applied` vuelve a
  preguntar con `escalate_adr`. `adr_asked` es el único lector de «pregunta pendiente»: cuenta
  sólo las `escalate_adr` resueltas después del último `apply_adr_decision` de la cabeza.
- **La primera revisión tras una decisión aplicada se exime del presupuesto una vez**, incluso
  con un `sync_master` en medio (`adr_review_owed`: la última decisión `applied` no tiene
  `re_review` posterior, en ninguna cabeza). Cuenta como pase; las siguientes pueden terminar
  en `exhausted()`.
- **§7.** Valor cerrado nuevo `apply_adr_decision` (`LandingStepKind`, que ya lleva el atributo
  `agentic.landing.*`) y el `outcome` cerrado. Claves opcionales nuevas de la entrada, leídas con
  `.get`: `moves`, `decision_at`, `outcome`, `head_after`. Ninguna métrica, etiqueta ni clase de
  escalado nuevo: volver a preguntar usa `questions`.
- **§8.** Un comentario del dueño tras un `Escalated:` deja que un Developer edite como mucho
  los puntos (líneas propias de la rama que citan un número movido), verificado antes de un push
  con lease, a lo sumo dos veces por tarea.
- **Riesgos conocidos.** La corrección de la referencia reescrita la juzga sólo la
  `re_review` pendiente; `stale_moves` puede repetirse mientras el master siga moviéndose.

## Adenda (GH-570)

Enmienda la viñeta «Tope» de GH-544: el tope era por tarea y una tarea larga puede chocar dos
veces con el master en cabezas distintas; el segundo choque quedaba sin cupo tras un solo
intento fallido (apostrophe GH-407, PR #618).

- **Tope por colisión.** `LANDING_ADR_DECISIONS_MAX = 2` (mismo valor) se cuenta por la clave
  (cabeza, `moves`), no por tarea. Un `apply_adr_decision` pertenece a una clave cuando su
  `head` es esa cabeza y su `decision_at` es el `released_at` de una `escalate_adr` de la misma
  cabeza cuyos `moves` registrados, normalizados a tuplas de pares `(old, new)`, igualan los de
  la clave (así una lista de YAML y un `Renaming` recalculado comparan igual).
- **Lo que no se puede emparejar cuenta contra todas las claves de su cabeza.** Un apply sin
  `decision_at`, sin pregunta que lo explique o cuya pregunta no registró `moves` gasta cupo en
  cada clave de su cabeza; uno sin `head`, en cada clave. Un documento viejo nunca queda más
  suelto que con el conteo por tarea.
- **Sin cambios.** `stale_moves` sigue sin usar cupo, y una restauración sigue sin devolverlo
  (el tope se lee sobre todas las entradas; la idempotencia, sobre las vivas).
- **Una sola función cuenta.** `landing.adr_decisions_spent(entries, head, moves)` la leen la
  tabla de pasos (`adr_decision_due`), `landing_plan.release_asked` y la pregunta de
  `escalate_adr`; `LandingSpent.adr_decisions` desaparece.
- **Texto con el tope gastado.** Cuando los dos cupos de esa colisión en esa cabeza están
  gastados, la pregunta deja de ofrecer «decide on this issue»: dice que no se aplicará otra
  decisión a esa colisión y da la salida a mano (`WAY_OUT_ADR`). Una cabeza nueva vuelve a
  pasar por la tabla.
- **Qué chequeo estaba en rojo.** Cuando un `apply_adr_decision` termina `checks_red`, la
  entrada guarda dos claves opcionales (se leen con `.get`; un documento anterior se lee como
  antes): `red_checks`, una lista de `{name, tail, recovered}`, y `red_more`, cuántos chequeos
  rojos quedaron fuera (solo si es mayor que 0). Las construye y sanea una sola función,
  `presubmit.red_checks`, sobre las mismas filas de la puerta que lee el predicado `checks_red`
  (que no cambia): primero los que siguen rojos, luego los que se recuperaron, cada grupo por
  orden de primer fallo, hasta `RED_CHECKS_MAX = 3`. Un chequeo rojo lleva la cola de su
  última fila fallida (forma `_tail_of`), con cada línea que contiene un token de
  `ID_FORBIDDEN` reemplazada por `[line dropped]`; uno recuperado lleva `recovered: true` y
  ninguna cola. Nunca se guarda el log crudo ni nadie vuelve a sanear. `escalate_adr` los
  agrega al comentario que ya publica (nunca un segundo comentario): cada chequeo rojo con sus
  últimas líneas, los recuperados como «falló y luego pasó al repetir», y «and N more checks
  were red»; si `checks_red` llegó sin detalle, una línea fija lo dice. La pregunta entera se
  recorta bajo `MERGE_COMMENT_MAX` con el mismo recorte de la evidencia de merge
  (`presubmit.fit_comment`).
- **§8.** Desde aquí la cola acotada (a lo sumo `TAIL_LINES` líneas y `MERGE_TAIL_CHARS`
  caracteres por chequeo, 3 chequeos) de la salida de un chequeo que corrió el stack llega al
  documento de la tarea y a quien pueda leer el issue; es lo que pide el pedido, y el saneo es
  en el origen. Un flake sigue terminando en `checks_red` (el predicado lee todas las filas):
  el comentario ahora dice que el chequeo se recuperó.
- **§7.** Ninguna métrica, etiqueta, atributo de span ni clase de escalado nuevo: volver a
  preguntar sigue usando `questions`.

## Adenda (GH-576)

Enmienda GH-544 y GH-570: en apostrophe GH-407 (PR #618) la fila del índice de ADR entró por
la resolución de un merge, no por un commit propio. La regla no la reescribía y no era un punto:
si el pase del modelo no la tocaba, el chequeo `docs` quedaba en rojo (`checks_red`); si la
tocaba, el resultado era `out_of_bounds`. Los dos cupos de la colisión se gastaron así.

- **Lectura ampliada, solo con decisión.** `adr_decided` (no `adr_collision`) pide además a
  `_inputs` que lea todo archivo de la cabeza que nombra un archivo movido, de quien sea la
  línea (`Workspace.paths_naming`, un `git grep -l -F` por archivo movido; ninguna ruta de
  índice se escribe en el sistema). `renumber` y `adr_collision` dan el mismo resultado para toda
  entrada.
- **Regla (d), solo en `renumber_decided`.** La etiqueta numérica `[NNNN]` de un enlace
  Markdown se reescribe cuando el destino del mismo enlace es un archivo movido y la etiqueta es
  el número viejo de ese archivo; nunca por un número suelto en la línea.
- **Líneas `bound`.** `DecidedRenaming.bound` son las líneas que la regla (a) o (d) reescribió
  y el primer título de cada ADR movido. El pase puede moverlas dentro de su archivo (para
  mantener ordenado un índice) pero no cambiarles el texto; el `brief` se lo dice.
- **Comprobación por contenido.** `adr_numbers.outside_bounds` compara multiconjuntos de líneas
  por archivo y reemplaza a `within_spots`. Es el único lugar donde se calcula qué líneas puede
  editar el pase; `landing_adr` solo lo lee.
- **Cupos versionados.** `ADR_EDIT_BOUNDS = 2` se escribe en cada entrada de apply como
  `bounds`; una entrada sin el campo vale 1 y `adr_decisions_spent` (el único lector) cuenta
  solo las de la versión vigente. Ampliar o reducir el conjunto de líneas editables sube la
  constante en el mismo diff, y subirla es la única manera de devolver los cupos gastados con
  otro conjunto, sin cambiar qué mide el tope.
- **Informe de `out_of_bounds`.** La entrada guarda `out_of_bounds_lines` (a lo sumo
  `OUT_OF_BOUNDS_SHOWN_MAX` filas `{path, line}`) y `out_of_bounds_more`. Toda ruta pasa por la
  limpieza del texto del comentario (sin salto de línea, comilla invertida, `{{`, `<!--`/`-->`
  ni enlace); la que no pasa solo se cuenta en `out_of_bounds_more`. Nunca se publica el texto de
  una línea.
- **Costo (§8).** Un `git grep -l -F` por archivo movido, solo con una colisión con decisión del
  dueño; escala con el tamaño del árbol de la cabeza y solo se leen enteros los archivos que
  nombran el archivo movido.
- **§7.** Ninguna métrica, etiqueta ni atributo de span nuevo.
- **Riesgo.** La regla (a) reescribe como texto los archivos generados; si el generador ordena
  por número, el pase debe mover la fila (por eso las líneas `bound` pueden reordenarse). Si el
  chequeo `docs` sigue en rojo, el informe de `checks_red` (GH-570) lo muestra.
