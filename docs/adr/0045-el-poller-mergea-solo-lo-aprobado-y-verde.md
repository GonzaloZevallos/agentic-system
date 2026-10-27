---
status: accepted
date: 2026-10-06
---

# El poller mergea solo lo que Tech Lead aprobó y los chequeos dejan verde

Hasta hoy el merge del pull request era del humano, y era también el criterio de éxito:
"el diff pasa los tests y el humano lo mergea sin tocar" (README sección 9), "un pull
request que el humano mergea o no" (ADR-0011), "el merge es el cierre" (ADR-0011), "No
mergea" (ADR-0039) y la autonomía como "entregas `merged` limpias sobre tareas
terminadas" (ADR-0028, punto 3). En las últimas 17 tareas (2026-10-04 → 2026-10-05) las
esperas del humano sumaron unas 9,7 h; #146 y #147 esperaron 7 h un merge, cuando la regla de
trabajo de la sesión orquestadora ya era "PR aprobado + chequeos verdes → mergear y avisar".

Este ADR (GH-178) lo cambia. Zeta decidió en
https://github.com/GonzaloZevallos/agentic-system/issues/178#issuecomment-5999971527 las
tres preguntas abiertas: el merge automático rige para **todas las tareas**; el resultado es
**un valor propio**, `auto_merged`, que no es un veredicto del humano; las tareas del set de
evaluación **nunca** se mergean solas; y **no hay ventana de veto** antes del merge.

## Qué reemplaza

Reemplaza por nombre, sin editar ninguna de estas frases (el historial queda donde se
escribió y la sección 13 del README apunta acá):

- README sección 9: "el humano lo mergea **sin tocar**" como único éxito. El puntaje
  binario cuenta ahora entregas **juzgadas por un humano**; una `auto_merged` no puntúa.
- ADR-0011: "que el humano mergea o no" y "el merge es el cierre". El merge sigue siendo el
  cierre; lo hace el humano o, si pasa la decisión de abajo, el poller.
- ADR-0039, "Lo que el poller no hace": "No mergea". Mergea, y sólo por el camino de este
  ADR. Todo lo demás de esa sección sigue: no corre ningún rol en su propio proceso, y sobre
  el checkout del producto no corre `fetch`, `pull`, `checkout`, `merge` ni `reset`.
- ADR-0028 punto 3, la definición de autonomía: ahora se lee sobre las entregas juzgadas
  por un humano (ver "El resultado" y "El Scorecard").
- README sección 8, "al revisar el diff antes de mergear": el humano ve el conocimiento de
  producto al revisar el diff de lo que le queda; lo que el poller mergea lo ve después.

## El resultado de entrega

`DeliveryOutcome` suma `auto_merged`. `OUTCOME_CLASS` (en `metrics.py`, junto al literal)
clasifica cada valor en exactamente una de tres clases:

| Valor | Clase | Qué dice |
|---|---|---|
| `merged`, `merged_with_changes` | `human_kept` | el humano mergeó: es su veredicto |
| `auto_merged` | `landed_unjudged` | aterrizó sin veredicto humano |
| `closed` | `closed` | nunca aterrizó |

Dos conjuntos salen de ahí y ningún consumidor decide por un literal propio:

- `KEPT` (el playbook, qué entregas cuentan como "se quedó") tiene sólo los `human_kept`:
  el sistema no se acredita lo que el humano no juzgó.
- `MERGED_OUTCOMES` (las señales del stack, qué entregas aterrizaron) tiene todos menos
  `closed`: un `auto_merged` sí aterrizó.

`agentic close` clasifica el merge como `auto_merged` cuando la clave `auto_merge_head` del
documento es igual al `head_oid` final del pull request mergeado (`PullRequest.head_oid`).
`""` y la clave ausente son lo mismo: sin intención, y el documento cierra exactamente como
antes de este ADR. Un documento sin las claves nuevas no cambia.

## El Scorecard y el puntaje

- **Autonomía** es `merged` limpias sobre las entregas **juzgadas** (las cerradas cuyo
  resultado no es `landed_unjudged`, o sea `human_kept` y `closed`). `auto_merged` queda fuera del numerador y del denominador, y fuera del costo
  por entrega. Sigue dentro del conjunto de tareas terminadas que miden fallos propios y
  retrabajo: esas dos métricas no dependen de quién mergeó.
- **El piso es `AUTONOMY_MIN_JUDGED = 5`**, el tamaño del set de evaluación. La autonomía se
  imprime `n/m` y sólo muestra una tasa cuando `m >= 5`; debajo, `agentic eval compare` no
  muestra tasa y no compara autonomías.
- **`evaluation.score`** devuelve **sin puntaje** para `auto_merged`, nunca `False`: un
  merge que el sistema se dio a sí mismo no es un cero ni un uno. Como las tareas del set no
  se mergean solas, es una red de seguridad y no un caso esperado.

## La decisión

`rules.auto_merge_decision(facts: MergeFacts) -> MergeDecision` es pura: la única que
conoce las condiciones. Ni un adaptador, ni la CLI, ni la aplicación las vuelve a derivar.
Devuelve un `kind` (`merge`, `run_checks` o `refuse`) con un `reason` cerrado
(`MergeRefusal`), y rechaza en la primera condición que falla, en este orden:

| `reason` | Se rechaza cuando |
|---|---|
| `not_ready` | la tarea no está en `ready_for_review` |
| `not_approved` | la unidad revisada (la subtarea `integration`, o la única) no está aprobada |
| `head_not_reviewed` | el head del PR no es el que Tech Lead revisó (`reviewed_head`), o alguno se desconoce |
| `open_findings` | queda un hallazgo `blocking` o `important` abierto (el pliegue `rules.open_findings`) |
| `pr_not_open` | el PR no está `OPEN` o no se pudo leer |
| `eval_set_unreadable` | no se pudo leer el set de evaluación: nada prueba que el issue esté fuera |
| `eval_set` | el issue está en el set de evaluación |
| `guarded_path` | el diff toca un camino guardado |
| `attempts_exhausted` | los chequeos ya corrieron `MERGE_ATTEMPTS_MAX` veces sobre este head y el master volvió a moverse |
| `checks_failed` | los chequeos corrieron sobre este head mergeado con el master actual y fallaron |
| `master_moved` | lo da sólo el verbo, tras su último fetch: el master se movió entre los chequeos y el merge |

`master_moved` pertenece al conjunto cerrado pero la decisión no lo devuelve: el hecho no
existe cuando ella decide. Si los chequeos grabados (`merge_checks`) no cubren este head en
el master actual y quedan intentos, devuelve `run_checks`; si cubren y pasaron, `merge`.

**Caminos guardados** (`rules.guarded_path`): `.agentic/**` salvo `.agentic/tasks/<task_id>.md`
(el documento propio de la tarea, que toda entrega escribe), `CODING_STANDARDS.md` y
`.github/**`. Son la maquinaria del sistema, sus estándares y sus flujos: un cambio ahí lo
mira un humano.

**`MERGE_ATTEMPTS_MAX = 3`** corridas completas de los chequeos por head del PR, aplicado
dentro de `auto_merge_decision` y en ningún otro lugar. Un head nuevo es un push, y reinicia
la cuenta.

### De dónde sale cada hecho

Todo se lee por `ports.documents` en el checkout del producto del poller, del tracker, o del
sha de upstream; **nada** se lee de la copia del documento de tarea que lleva el PR: esa la
escribe la propia rama, y una decisión que se apoye en ella se la puede dar la rama a sí misma.

- Estado, aprobación de la unidad revisada, `reviewed_head`, hallazgos abiertos,
  `merge_checks` y `merge_attempts`: el documento en `ports.documents`.
- Estado del PR, `head_oid` y los caminos del diff: el tracker.
- El set de evaluación: `.agentic/eval-set.yaml` **al sha de upstream**, no el del
  checkout ni el del PR. `cli.py`, la raíz de composición, es el único que importa
  `core.measurement.evaluation` para leerlo y le pasa a `poll.run_once` un `frozenset[int]`;
  un set ilegible rechaza (`eval_set_unreadable`).

## El poller y el verbo `agentic merge`

El poller reúne candidatos (`gather_merges`): una tarea sin cerrar con PR, sin corrida viva
en su lock, que la decisión no rechace con los hechos que el poller tiene (sin lista de
caminos todavía: el verbo decide de nuevo, con ella). Cada rechazo es una línea de log del
poller con el motivo. Sin el sha de upstream legible no se juzga ninguna tarea, porque los
chequeos grabados no se distinguirían de unos viejos. `poll_plan` coloca **como
máximo un merge por vuelta, al final**: después de los cierres, las retomas, las reanudaciones
y el lanzamiento, y sólo con un slot que sobró. Un merge nunca le saca el lugar a trabajo de
roles. `execute` tiene una rama `merge` explícita que lanza `agentic merge --issue N`.

`agentic merge` es un hijo que no gasta plata de modelo y no abre span, como `close`. En orden:

1. toma el lock de corrida de la tarea y se registra en el registro de la máquina;
2. poda los worktrees que sobraron y borra un `merge-<task>` huérfano;
3. agrega un worktree descartable bajo `.agentic/worktrees/` en el head del PR y le mergea
   el master de upstream recién traído;
4. corre `setup` y los chequeos declarados, leídos en el base de la tarea, por
   `CheckoutPresubmit`/`ShellCommands` y respetando `parallel_checks` (ADR-0044);
5. escribe `merge_checks` (`head`, `master`, `passed`, `duration_s`) y `merge_attempts`
   (`head`, `count`);
6. consulta la decisión, ahora con los caminos del diff;
7. vuelve a traer upstream y rechaza (`master_moved`) si el master se movió;
8. **escribe la intención** (`auto_merge_head`, `auto_merge_master`, `auto_merge_at`,
   `auto_merge_method`) en el documento: sólo después de pasar la guarda del master de
   upstream, y justo antes de la llamada;
9. llama `Tracker.merge(number, head_sha)`, que pasa el sha exacto sobre el que se decidió
   (`gh pr merge --match-head-commit`) y corre sólo después de que el subproceso de los
   chequeos terminó;
10. si funcionó, escribe `auto_merged_at` y corre `_close_document` en el mismo proceso: el
    camino de `close` de siempre, que clasifica `auto_merged`;
11. si falló, vuelve a leer el PR; **sólo si sigue `OPEN`** limpia `auto_merge_head` a `""`
    (nunca la quita: "" y ausente clasifican igual). Si ya no está abierto, la intención se
    queda: alguien lo mergeó.

Los chequeos sólo corren por el camino existente `CheckoutPresubmit`/`ShellCommands`, en un
worktree descartable bajo `.agentic/worktrees/`. Nada del camino escribe en el repositorio de
`stage0` ni corre `checkout`, `merge`, `reset` o `pull` en el árbol de trabajo del checkout
del producto: el merge de upstream vive en el worktree descartable.

**El método de merge** es el primero que el repositorio permite, en el orden `merge`,
`squash`, `rebase` (`gh repo view --json mergeCommitAllowed,squashMergeAllowed,rebaseMergeAllowed`;
ninguno permitido es `no_merge_method`, y se comenta). Se prefiere el commit de merge
(`gh pr merge --merge`): conserva los commits de la rama, que `delivery_outcome` lee
para separar los del sistema de los del humano, y no reescribe historia que una squash
perdería. `Tracker.merge` devuelve el método usado y el verbo lo escribe en
`auto_merge_method`.

### Comentarios

Todo comentario del camino empieza por una constante de `poll.py`: `AUTO_MERGE_REFUSED`
(`"Auto-merge refused: "`) o `AUTO_MERGED` (`"Auto-merged: "`). Las dos están en
`resume.SYSTEM_COMMENTS`, así que ninguna se lee como una decisión del humano en la próxima
`agentic resume`. `poll.py` lee `SYSTEM_COMMENTS` con un import local a la función y `resume`
importa de `poll`, el mismo patrón de `AUTO_RESUMED` y `RETAKEN`: sin ciclo de imports de
módulo. El verbo comenta un rechazo que el humano puede accionar **una vez por (head,
motivo)**; las líneas de log del poller no son comentarios.

## Medición y seguridad

**Observabilidad (sección 7 de los estándares).** No se agrega, mueve ni quita ninguna
métrica, label, span ni atributo de span. El hijo no abre span. Las claves nuevas del
documento de tarea, leídas siempre con `.get`, son `auto_merge_head`, `auto_merge_master`,
`auto_merge_at`, `auto_merge_method`, `auto_merged_at`, `merge_checks` (`head`, `master`,
`passed`, `duration_s`) y `merge_attempts` (`head`, `count`). La fila `delivery_outcome` de
`observability/README.md` gana el valor `auto_merged`. Ningún prompt cambia, así que
`prompts.fingerprint` no se mueve.

**Seguridad (sección 8).** El sistema ahora llama `gh pr merge` sobre el repositorio del
producto con las credenciales de `gh` del usuario, sólo para un PR que pasa la decisión. Los
chequeos corren el `setup` y los chequeos de la rama con las credenciales del usuario, igual
que la puerta de entrega ya hace en cada entrega: el merge automático no ensancha lo que ese
código alcanza. La llamada de merge corre después de que terminó el subproceso de los
chequeos.

**Costo (sección 8).** Como máximo `MERGE_ATTEMPTS_MAX = 3` corridas completas de los
chequeos por head. Crece con los PR aprobados abiertos por el tráfico del master, acotado por
head; un hijo de merge por vuelta, en un slot sobrante. El prefiltro del poller trae upstream
y puede escribir una línea de rechazo por vuelta para cada tarea sin cerrar con PR: es costo
de lectura y de log, sin llamadas de modelo.

## Riesgos conocidos

- **Crash ambiguo.** El proceso muere entre la intención y la llamada de merge, y el humano
  mergea después ese mismo head sin tocarlo. Se clasifica `auto_merged` a propósito: ante la
  duda el sistema nunca se acredita el veredicto del humano.
- **Ventana entre el fetch y el merge.** Los segundos entre el último fetch de upstream y
  `gh pr merge` no se pueden cerrar: `gh` no tiene guarda de sha de base. Sólo el head del PR
  va fijado.
- **Tests borrados.** La decisión no detecta un test borrado dentro de caminos de código
  comunes; la única guarda ahí es el `spec-missing-test` de la revisión de Tech Lead.
- **La muestra de autonomía se achica.** Cuanto más mergea el sistema, menos entregas
  juzgadas hay. Debajo de cinco, `eval compare` no muestra tasa. Es el costo declarado de la
  decisión de Zeta, no un defecto.
- **Camino guardado.** El poller no ve el diff, así que sólo el verbo descubre un
  `guarded_path`. Para que no se lance un hijo por vuelta para siempre, el verbo gasta
  entonces los intentos del head (`merge_attempts.count = MERGE_ATTEMPTS_MAX`) y la decisión
  misma lo deja de proponer (`attempts_exhausted`, sólo una línea de log) hasta que un push
  dé un head nuevo. Un diff ilegible o un head inalcanzable no gastan intentos: el verbo se
  detiene sin escribir y la vuelta siguiente lo reintenta.
- **Master ocupado.** Un PR aprobado puede quedar sin mergear tras tres corridas de los
  chequeos (`attempts_exhausted`). Queda para el humano, que es el comportamiento de hoy.
- **Credenciales.** El código de la rama corre con las credenciales del usuario, como en la
  puerta de entrega.
- **Opción descartada.** Volver a correr los chequeos sólo si el diff del master toca los
  archivos del PR. Se descartó: es una heurística sobre qué rompe qué, y la cuenta por head
  ya acota el costo sin depender de ella.

## Consecuencias

- README secciones 2, 8, 9 y 11 dicen que el poller mergea lo que pasa este ADR y el humano
  el resto; la sección 13 gana la fila "el merge del humano como único cierre" → ADR-0045.
- El glosario de Medición gana **Fusionado por el sistema** y precisa **Scorecard** y
  **Resultado de entrega**.
- Los nombres compartidos entre código y este documento: `auto_merge_decision`, `MergeFacts`,
  `MERGE_ATTEMPTS_MAX`, `AUTO_MERGE_REFUSED`, `AUTO_MERGED`, `OUTCOME_CLASS`,
  `AUTONOMY_MIN_JUDGED` y `agentic merge`.

## Adenda (2026-10-06): el método se registra al éxito, no con la intención

El paso 8 de «El poller y el verbo `agentic merge`» lista `auto_merge_method` dentro de la
intención que se escribe antes de la llamada. El código nunca lo hizo así (issue #195), y esta
adenda precisa el texto, no el código.

- **La intención previa al merge** son `auto_merge_head`, `auto_merge_master` y
  `auto_merge_at`. Se escriben en un solo `record_closure`, justo antes de llamar a
  `Tracker.merge`.
- **El método** se escribe junto con `auto_merged_at`, en un solo `record_closure`,
  inmediatamente después de que `Tracker.merge` vuelve sin lanzar, y antes del comentario
  `Auto-merged:` y de `_close_document`. Si `Tracker.merge` lanza, ninguna de las dos claves
  se escribe, ni cuando el PR sigue `OPEN` ni cuando quedó `MERGED`.
- Esto precisa el paso 8 y concuerda con el párrafo «El método de merge», que ya decía que
  `Tracker.merge` devuelve el método y el verbo lo escribe en `auto_merge_method`.

**Por qué no se adelanta el método.**

- Nadie lo lee antes del merge: `close` y `delivery_outcome` sólo comparan `auto_merge_head`
  con el `head_oid` del PR.
- `GhTracker.merge` elige el método y mergea en un mismo verbo. Conocerlo antes obliga a
  partir el port `Tracker.merge`, su adaptador y sus dobles, para registrar antes un dato que
  nadie lee.
- El paso 11 limpia sólo `auto_merge_head` cuando el PR sigue `OPEN`. Un método escrito con
  la intención quedaría huérfano, con el PR todavía sin mergear.

**Riesgo aceptado.** Si el proceso muere entre un `gh pr merge` exitoso y esa escritura, el
documento queda con la intención y sin método. `close` lo clasifica `auto_merged` igual, por
el riesgo «Crash ambiguo», y el método se lee del PR en GitHub. Un lector del método antes del
merge pediría un ADR nuevo.

**Qué no cambia.** Ninguna señal, label, atributo de span ni clave de documento se agrega,
mueve o quita, y ningún prompt cambia: `prompts.fingerprint` no se mueve.

## Adenda (2026-10-06): un `gh pr merge` que falla se cuenta por head (GH-196)

Si `gh pr merge` fallaba y el PR seguía `OPEN`, nada de lo que lee `auto_merge_decision`
cambiaba: `merge_checks` seguía cubriendo el head con `passed=True` y `merge_attempts` sólo
se mueve al correr los chequeos. Cada vuelta del poller lanzaba `agentic merge` y llamaba a
`gh pr merge` otra vez, sin límite. Esta adenda lo acota.

- **Presupuesto propio.** `MERGE_CALLS_MAX = 3` acota las llamadas de merge que fallaron por
  head; `MERGE_ATTEMPTS_MAX` sigue acotando las corridas de chequeos. Son dos costos
  distintos con dos causas distintas y no comparten cuenta. El límite se compara sólo en
  `auto_merge_decision` (`merge_failed`), y el chequeo va justo después de `guarded_path` y
  **antes** de `covers`: un head gastado no vuelve a correr chequeos cuando el master se
  mueve, porque la decisión no pregunta por los chequeos antes de rendirse.
- **Conjunto cerrado.** `MergeFailure` = `no_merge_method`, `gh_unavailable`,
  `gh_unreadable`, `gh_rejected`. `GhTracker.merge` levanta sólo `MergeCallFailed`, con la
  clase elegida en el paso que falló (ADR-0029): `gh` ausente en `repo view` o en `pr merge`
  es `gh_unavailable`; cualquier otro fallo de `repo view`, o un JSON ilegible, es
  `gh_unreadable`; ningún método permitido es `no_merge_method`; un `pr merge` que sale con
  error es `gh_rejected`. No se lee stderr para elegir la clase. Un `ValueError` común que
  llegue al verbo cuenta como `gh_unreadable`.
- **La clase terminal cuesta una llamada.** Repetir no cambia `no_merge_method`, así que el
  verbo gasta todo el presupuesto de una vez (`TERMINAL_MERGE_FAILURES`).
- **La clave `merge_failures`.** `{head, count, failure}` en el documento de la tarea. El
  verbo la escribe tras cada llamada fallida y **antes** de volver a leer el PR; el poller la
  lee como `merge_attempts`: falta, un `count` que no es entero o otro head leen como 0 y sin
  clase. Un documento anterior a este cambio decide exactamente como antes. En un comentario
  o en la clave sólo aparece un miembro de `MergeFailure` (o `unknown` en el comentario); el
  texto crudo de `gh` queda en las líneas de log del verbo.
- **El comentario.** Al agotarse el presupuesto el verbo comenta una vez por (head, razón)
  `Auto-merge refused: <head> gh pr merge failed: <clase>`, una frase fija de consejo por
  clase (`MERGE_FAILURE_ADVICE`) y `Left for the human to merge.`. Antes de eso sólo loguea
  `merge failed (<clase>), N of 3`.
- **Cambia un "se comenta".** `no_merge_method` ya no llega al humano como razón propia
  sino como la clase dentro de un comentario `merge_failed`. Esto modifica lo que este ADR
  decía en "El poller y el verbo" ("ninguno permitido es `no_merge_method`, y se comenta"):
  el comentario sigue existiendo, con otro texto.

Riesgos conocidos de esta adenda:

- **Fallos transitorios.** Tres fallos transitorios de `gh` seguidos sobre un head entregan
  un PR mergeable al humano. El costo está acotado y no hay bucle.
- **Verbo muerto en `pr merge`.** Si matan al verbo durante `pr merge`, esa llamada no se
  cuenta: cae en el camino ambiguo que ya cubre este ADR. El conteo no se escribe antes de la
  llamada porque un merge exitoso contaría como fallo.
- **Comentario perdido.** Si el comentario se pierde tras el último fallo, el poller no lo
  reintenta: sólo loguea `merge_failed` cada vuelta. Correr `agentic merge --issue N` a mano
  lo publica.
- **Reinicio.** Un head gastado se reinicia sólo con un push, como `attempts_exhausted` y
  `guarded_path`.
- **`gh_rejected` es grueso.** No distingue protección de rama de permisos. Agregar
  `mergeStateStatus` a la consulta del PR queda como seguimiento.

No se agrega ni se mueve ninguna métrica, etiqueta, span ni prompt; `prompts.fingerprint` no
cambia.

## Adenda (2026-10-06): el head revisado se compara por prefijo, y se guarda completo (GH-199)

`auto_merge_decision` comparaba `reviewed_head` con el head del PR por igualdad de texto, pero
`reviewed_head` se guardaba abreviado (`git rev-parse --short`) y el head del PR llega
completo desde el tracker: ningún PR aprobado pasaba y todos se rechazaban con
`head_not_reviewed`. Ahora la comparación vive en un solo lugar del dominio
(`rules.same_commit`) y `review` guarda el id completo (`Workspace.full_head`).

- **Ensanchamiento de §8.** Un `reviewed_head` abreviado guardado cuenta como el head del PR
  cuando es un prefijo hexadecimal de al menos `MIN_SHA_PREFIX` (7) caracteres. Dos ids
  completos se comparan estrictamente. Dos commits distintos que compartan ese prefijo
  podrían pasar esta sola condición; el tope real es `Tracker.merge`, que sigue fijando el
  head exacto con `--match-head-commit`.
- **La puerta.** El head del PR debe ser un id completo, de 40 o 64 hex (`FULL_SHA_LENGTHS`);
  si no lo es, la decisión es `head_not_reviewed` sin llegar a comparar.
- **Riesgo conocido.** Un documento en vuelo con `reviewed_head` abreviado sigue siendo la
  base de la próxima revisión incremental. Si esa abreviatura se volvió ambigua, el diff del
  revisor falla o vuelve vacío. Termina tras una revisión más de cada tarea así; no se
  corrige.
- **Sin señales nuevas.** No se agrega ni se mueve ninguna métrica, etiqueta, span, clave del
  documento ni prompt; `prompts.fingerprint` no cambia. Los rechazos anteriores por
  `head_not_reviewed` de heads sin mover venían del sha abreviado (apostrophe GH-103 / PR
  #108, probablemente también #198).

## Adenda (2026-10-07): un PR cerrado sin mergear no es candidato, y `pr_not_open` va segundo (GH-209)

El poller imprimía cada vuelta `auto-merge #N -> left to the human: open_findings` para una
tarea entregada cuyo PR se cerró sin mergear. La tarea nunca recibe `closed_at` (el barrido
de cierres sólo lo escribe para un PR `MERGED`), así que seguía siendo candidata, y la razón
impresa era engañosa: un PR que no está abierto no dice nada de revisión, aprobación ni
hallazgos. Esta adenda corrige la razón y corta el bucle.

**Tabla de rechazos.** Reemplaza **sólo en el orden** a la de "La decisión": cada condición
queda igual. Es el orden real del cuerpo de `auto_merge_decision`, que rechaza en la primera
que falla:

| `reason` | Se rechaza cuando |
|---|---|
| `not_ready` | la tarea no está en `ready_for_review` |
| `pr_not_open` | el PR no está `OPEN` o no se pudo leer |
| `not_approved` | la unidad revisada (la subtarea `integration`, o la única) no está aprobada |
| `head_not_reviewed` | el head del PR no es el que Tech Lead revisó (`reviewed_head`), o alguno se desconoce |
| `open_findings` | queda un hallazgo `blocking` o `important` abierto (el pliegue `rules.open_findings`) |
| `eval_set_unreadable` | no se pudo leer el set de evaluación: nada prueba que el issue esté fuera |
| `eval_set` | el issue está en el set de evaluación |
| `guarded_path` | el diff toca un camino guardado |
| `merge_failed` | `Tracker.merge` ya falló `MERGE_CALLS_MAX` veces sobre este head |
| `checks_failed` | los chequeos grabados cubren este head en este master y fallaron (si no lo cubren, se pasa a la condición siguiente) |
| `attempts_exhausted` | los chequeos ya corrieron `MERGE_ATTEMPTS_MAX` veces sobre este head y el master volvió a moverse |
| `master_moved` | lo da sólo el verbo, tras su último fetch: el master se movió entre los chequeos y el merge |

No se agrega `pr_closed`: `pr_not_open` ya cubre mergeado, cerrado e ilegible. El literal
`MergeRefusal` y su docstring sólo mueven `pr_not_open` a continuación de `not_ready`; el
resto de su orden (`eval_set` antes de `eval_set_unreadable`, `merge_failed` al final) ya
discrepaba del cuerpo antes de este cambio y no se toca.

**El salto.** `rules.pr_abandoned(state)` es un predicado puro, verdadero sólo para `"CLOSED"`.
`poll.gather_merges` lo llama tras leer el PR: una tarea cuyo PR lee `CLOSED` no da candidata
ni línea, **cualquiera sea su `status`**. El poller **no escribe nada** en el documento: ni
`closed_at`, ni `delivery_outcome`, ni otra clave. Se rechazó que el barrido de cierres
escriba `closed_at`: haría que el poller escribiera resultados juzgados, y hoy sólo lo hace
`agentic close`, que corre un humano. `pr_abandoned` decide sólo la candidatura a merge, nunca
si una tarea está cerrada. `auto_merge_decision` sigue siendo el único lugar que decide un
merge automático: el salto sólo puede descartar una tarea, nunca volverla candidata.

Sigue igual: un PR ilegible (`None`, o `ValueError`/`OSError` al leerlo) llega a la decisión
y se rechaza como `pr_not_open`, con su línea cada vuelta (antes salía `head_not_reviewed`);
un PR `MERGED` imprime una línea `pr_not_open` en la vuelta anterior a que el hijo de cierre
escriba `closed_at`; un PR reabierto, o con un head nuevo, vuelve a ser candidato en la
lectura siguiente, porque no se recuerda nada entre vueltas.

**Segundo cambio de comportamiento.** Una tarea en un estado distinto de `ready_for_review`
cuyo PR está `CLOSED` ya no imprime `not_ready` cada vuelta. Y para un PR no abierto con una
falla anterior (aprobación, head, hallazgos) la razón impresa pasa a ser `pr_not_open`.

**Costo que queda por vuelta.** Cada documento cerrado sin mergear sigue costando una llamada
a `gh` por vuelta, la misma que ya hace `gather_closes`, y `awaiting_merge` sigue forzando un
fetch del upstream por vuelta. Ambos escalan con la cantidad de esos documentos, no con el
tamaño de los datos, y terminan cuando un humano corre `agentic close`. El verbo
(`agentic merge --issue N` a mano) no cambia sus lecturas previas a la decisión (prune,
quitar el checkout huérfano, `fetch_commit`, `changed_paths`): el nuevo orden sólo cambia la
razón que informa, nunca su costo.

Riesgos conocidos de esta adenda:

- **Se pierde la última señal automática.** Condición: el PR lee `CLOSED`, el documento tiene
  `pull_request` y no tiene `closed_at`, sea cual sea su `status`. Incluye un PR cerrado por
  una persona, por borrar la rama o por una limpieza. Esa tarea queda sin juzgar, fuera del
  Scorecard e invisible para el poller. Sólo `agentic close --all` o `agentic close --issue N`
  actúan sobre ella. Seguimiento: reportar estas tareas por una lectura que el humano ya
  corre, en un issue aparte.
- **Tres lectores del estado del PR.** Interpretan la misma población por separado: la
  candidatura a merge (`rules.pr_abandoned`), el barrido de cierres del poller (sólo
  `MERGED`) y `agentic close`. Unificarlos alcanza al Scorecard, así que es otro issue.
- **El enum no se verifica en ejecución.** `PullRequest.state` es un `Literal`, pero
  `GhTracker` pasa el valor de GraphQL sin comprobarlo. Un cambio de ortografía cae en una
  línea `pr_not_open` visible, nunca en silencio. El test del adaptador lo vigila.

No se agrega ni se mueve ninguna métrica, etiqueta, span, clave del documento ni prompt;
`prompts.fingerprint` no cambia.

## Adenda (2026-10-07): el merge apunta al pull request guardado, nunca al número del issue (GH-211)

El paso 9 llamaba `Tracker.merge(number, head_sha)` con el número del **issue**, y el adaptador
corría `gh pr merge <número>`. Cuando el issue y su pull request tienen números distintos
(GH-209 y el PR #210), `gh` rechazaba el merge; cuando el número del issue es el de otro PR
abierto, sólo `--match-head-commit` impedía mergear el PR equivocado. Esta adenda reemplaza,
por nombre, `Tracker.merge(number, head_sha)` del paso 9; el texto de ese paso queda como
estaba.

- **La llave.** `Tracker.merge(reference, head_sha)` recibe la referencia al pull request que
  `deliver` guardó en el documento (la URL), la misma llave que ya usa
  `Tracker.pull_request(reference)`. Nunca recibe un número de issue. El caso de uso pasa la
  referencia como un `str` opaco.
- **Quién la lee.** Sólo el adaptador la interpreta, con el mismo `pull_request_number` que
  usa `pull_request(reference)`.
- **Qué recibe `gh`.** `gh pr merge` recibe el número ya extraído, no la URL, y resuelve el
  repositorio desde `product_dir`, exactamente como `pull_request`: toda llamada a `gh` corre
  con `cwd=product_dir`. Así la lectura y el merge ven el mismo repositorio. Por eso la URL no
  se pasa a `gh`: podría llegar a un repositorio que la lectura nunca miró.
- **Una referencia ilegible.** El verbo se detiene en su primera lectura de `pull_request`,
  con `tracker_error`, antes de escribir la intención y antes de correr los checks. Dentro del
  adaptador, `merge` la lee antes de cualquier llamada a `gh` y un fallo es
  `MergeCallFailed("gh_unreadable")`, nunca un `ValueError` pelado. No hay clase nueva de
  `MergeFailure`.
- **Lo que no cambia.** El orden de la intención y del método, `_after_failure`, la elección
  del método y las clases de fallo por paso siguen igual.

No se agrega ni se mueve ninguna métrica, etiqueta, span, clave del documento ni prompt;
`prompts.fingerprint` no cambia.

## Adenda (2026-10-07): un merge-checks rojo dice qué falló y por qué (GH-212)

Un `merge_checks` con `passed: false` no decía qué check falló ni qué imprimió; un humano
tuvo que correr los siete de nuevo. Ahora el registro lo dice. La decisión de mergear no
cambia: `auto_merge_decision` y `MergeRefusal` siguen leyendo solo `passed`.

- **Claves de `merge_checks`.** `head`, `master`, `passed` y `duration_s` conservan nombre y
  sentido. Se agregan `checks` (una entrada `{name, passed, tail?}` por check declarado, en el
  orden declarado, y `tail` solo en las que fallaron) y `failed_step` (solo cuando `passed` es
  falso). Ambas se leen con `.get` (`[]` y `None`). Una entrada del `witness` que discrepa va al
  final; una que coincide no se registra; un paso de `setup` rojo es una entrada con el nombre de
  su clave.
- **`failed_step`** toma un valor del conjunto cerrado `worktree | conflict | setup | no_checks |
  witness | checks`. Lo arma `presubmit.merge_checks_record`, la única función que construye el
  registro; `passed` es la misma expresión de antes, movida ahí.
- **El tail.** Se limpia (secuencias ANSI CSI/OSC fuera, caracteres de control salvo `\n` fuera,
  tabs a cuatro espacios), luego las últimas `TAIL_LINES = 30` líneas, luego los últimos
  `MERGE_TAIL_CHARS = 2000` caracteres. Se guarda como lista de líneas: un escalar de varias
  líneas rompe la cirugía de una línea de `record_closure`. Solo se muestra; nunca se parsea ni
  se ramifica sobre él.
- **Tres superficies.** El log del hijo (`log`, con el resumen y cada tail indentado), la línea
  del poller (`... left to the human: checks_failed (Failed: tests)`, solo para `checks_failed`
  y solo si el registro es de ese head) y el comentario `Auto-merge refused:` (primera línea
  idéntica a la de siempre, luego el resumen y los tails, luego `Left for the human to merge.`).
  Cada tail va en una cerca con un acento grave más que la racha más larga que contiene; solo en
  el comentario, `@` y `#` seguidos de un carácter de palabra reciben un U+200B. El comentario
  no pasa de `MERGE_COMMENT_MAX = 60_000`: se sueltan tails desde la última entrada hacia atrás y
  los nombres siempre quedan.
- **Dedupe.** `checks_failed` compara (primera línea, línea de resumen): los mismos checks
  fallando callan aunque el tail cambie; otros checks fallando en el mismo head comentan de
  nuevo. Toda otra razón compara solo la primera línea, como antes.
- **Por qué no se reintenta antes de rechazar.** Convertiría un rojo en un merge, en el único
  camino que mergea sin un humano. Con el check nombrado, un humano puede decidir si fue un
  flake. Queda como issue de seguimiento.
- **§7.** No se agrega ni se mueve ninguna métrica, etiqueta ni atributo de span; las claves
  nuevas del documento son `merge_checks.checks` y `merge_checks.failed_step`, solo agregadas.
  `prompts.fingerprint` no cambia.
- **Con ADR-0046.** El merge sigue sin correr nunca una forma affected-only: un CI verde y fresco
  ocupa el lugar de la corrida completa del merge, así que el producto que declara `ci` y
  `affected_checks` debe correr la suite completa en ese CI. El camino local (sin `ci`, o con el
  CI verde pero viejo) corre las formas completas como dice ADR-0046.
- **§8.** Se ensancha: la salida de los checks y del `setup` del propio producto, limpia y
  acotada, llega al comentario del issue y al frontmatter. Quienes la ven son quienes ya leen el
  documento de tarea, donde `checks_markdown` ya copia los tails rojos. Costo: a lo sumo
  (checks + witness) × 2 KB por registro rojo, que se parsea en cada ronda del poller.
- **Riesgos conocidos.** Nada redacta secretos: un check o paso de `setup` que imprima uno lo
  publica. La salida de instalación ahora puede aparecer en un comentario. Unos 14 KB de
  frontmatter se parsean por ronda en una tarea roja. Un head cuyo último comentario vino del
  código anterior recibe un comentario más. Un flake queda nombrado, no reintentado.

## Adenda (2026-10-07): un producto que declara su CI lo usa como puerta de merge (GH-216)

Un producto con CI de GitHub en cada pull request ya corría ese CI antes de que el sistema
llegara al merge, y el sistema lo ignoraba: repetía todos los checks en la máquina compartida.
El resultado fueron dos casos del 2026-10-07: un PR aprobado con el CI rojo (apostrophe#146) y un
PR con el CI verde que la puerta local rechazó por carga (apostrophe#147). Esta adenda cambia la
puerta solo para el producto que lo pide.

- **Opt-in, sin detección.** `.agentic/stack.yaml` gana la clave `ci`: la lista de nombres exactos
  de check-run que gobiernan el merge (lista no vacía de textos no vacíos, sin duplicados; una
  clave mal formada es un `ValueError` que nombra la clave y la fuente). Se lee en la base de la
  tarea, como `checks` (ADR-0016, ADR-0044). Sin la clave el producto sigue el camino local de
  esta ADR sin ningún cambio, y no hay un fallback por detección de "no hay checks": un CI que no
  reporta nada sería leído como verde por quien no lo declaró. Un job condicional o una matriz se
  declaran con los nombres que GitHub reporta (`check (3.12)`, nunca `check`); `SKIPPED` cuenta
  como rojo, así que un job condicional no se declara.
- **Un solo veredicto.** `ci.ci_state` (dominio) es el único lugar donde el CI se clasifica
  verde, rojo o pendiente, desde los valores enum crudos de GraphQL; nada lee stderr ni prosa. El
  nombre se compara exacto, nunca por prefijo. Un nombre declarado que nunca se reporta es
  pendiente y queda en `missing`. Un valor desconocido es rojo, nunca verde.
- **El head debe ser el revisado.** El reporte se lee en una sola consulta GraphQL junto con su
  oid de commit; cuenta solo si ese oid es completo y `same_commit` con el head del PR y con el
  head revisado. Otro head es `wait`, nunca verde. Un reporte ilegible (`CiUnreadable`, causa
  `gh_unavailable` o `gh_unreadable`) es distinto de un reporte legible al que le falta un check.
- **`wait`, una decisión nueva.** `auto_merge_decision` gana el tipo `wait` y las razones
  `ci_failed`, `ci_missing` y `ci_unreadable`. El CI pendiente, un head distinto y un reporte
  ilegible esperan: el verbo sale sin escribir el documento, sin comentar y sin mover
  `merge_attempts` ni `merge_failures`, y la siguiente ronda lo reintenta. La espera tiene un tope
  declarado (`CI_WAIT_MAX_S = 3600`, un valor declarado, no medido), medido desde `pushed_at`
  (solo si `pushed_head` es el head del PR) o desde el `createdAt` del PR; nunca desde la fecha de
  un commit. Pasado el tope, un pendiente se rechaza como `ci_missing` y un ilegible como
  `ci_unreadable`. El CI rojo se rechaza como `ci_failed`, con una línea por check (nombre,
  conclusión, enlace) y sin tails. `ci_missing` nombra los checks declarados que nunca se
  reportaron; `ci_unreadable` nombra `gh` y la clase de causa, nunca un check del producto. El
  dedupe de GH-212 aplica sin cambios.
- **Frescura: se vuelve a los checks locales, no se reejecuta el CI.** El CI corre sobre el PR
  mergeado con su base; si master se movió desde entonces el resultado puede no valer. Se eligió
  no reejecutar el CI (`gh run rerun`) ni empujar un commit vacío: ambos mueven o repiten un head
  que el Tech Lead ya revisó. El CI verde cuenta como fresco solo si la base con la que corrió es
  el master actual, o si `Workspace.contains` responde `yes` (el head contiene a master, tras
  traer ambos shas). Un `contains` que no se puede leer cuenta como viejo, nunca como fresco.
  Verde y fresco: `agentic merge` no crea worktree, no corre `setup` ni checks, y mergea. Verde y
  viejo: sigue el camino local de esta ADR, sin cambios, y un check local rojo es `checks_failed`
  con los nombres, para el humano.
- **La puerta de `base_sha` falló.** Saber la base del CI exigía `pull_requests[0].base.sha` de
  las corridas de Actions; en este repositorio `pull_requests` viene vacío en cada corrida, así
  que `base_sha` queda siempre `None` y la frescura sale solo de `contains`. El poller nunca llama
  a git: lee con `ci_fresh=None` y solo decide si lanzar el verbo. Consecuencia: un head que no
  contiene a master cae al camino local, y el timeout por carga de apostrophe#147 puede repetirse
  en ese camino más angosto.
- **Claves del documento.** `auto_merge_via` (`ci` o `local`) se escribe solo si el producto
  declara `ci`; una clave ausente se lee como `local`. `auto_merge_ci` lista `{name, link}` de las
  corridas en que se apoyó y se escribe en el mismo `record_closure` que la intención; un merge por
  CI no escribe `merge_checks`. El comentario `Auto-merged:` nombra los enlaces. `deliver` guarda
  `pushed_head` y `pushed_at` en el documento del checkout del producto, sin hacer ningún commit
  después del push. Todas son claves nuevas, solo agregadas y leídas con `.get`.
- **El poller.** Ante un `wait` agrega una línea
  `auto-merge #N -> waiting: ci <pending|missing|unreadable> <nombres o causa> (<n>s of 3600)`,
  con `?` si no hay ancla; no imprime la línea de rechazo antes del tope. Lanza el verbo solo para
  `merge` o `run_checks`.
- **Lado de la revisión.** Si la base de la tarea declara `ci`, el Tech Lead recibe una sección
  `### CI` escrita por el sistema, con cada check declarado en el head revisado (estado y enlace)
  y el mismo check en el último commit de la rama base. Un check declarado rojo en el head lo
  registra el código, no el revisor: un hallazgo `blocking` (`process-suite-red`, ubicación
  `CI: <nombre>`, con el enlace de la corrida como evidencia), o una `observation` con ambos
  enlaces si el mismo check también está rojo en la rama base (ya era un rojo previo). No se
  duplica un enlace que ya aparece en la evidencia de otro hallazgo de la misma salida, y el
  veredicto se deriva de los hallazgos como siempre, así que una aprobación sobre un CI rojo sale
  `returned`. Los dos prompts de revisión ganan una línea que lo dice, por lo que
  `prompts.fingerprint` cambia para `tech_lead_review` y `tech_lead_review_light`.
- **Límite conocido de la revisión.** `deliver` empuja y abre el PR después de la revisión de
  integración, así que en una corrida nueva la revisión suele no tener CI que leer; la guarda de
  revisión solo dispara al reanudar o revisar de nuevo un head ya empujado. La guarda que siempre
  aplica es la puerta de merge. Mover el push antes de la revisión de integración queda como
  issue de seguimiento.
- **§7.** No se agrega, mueve ni renombra ninguna métrica, etiqueta ni atributo de span. Las
  claves nuevas del documento son las de arriba. Hace falta un `bl-*` nuevo al mergear por el
  `prompts.fingerprint` movido.
- **§8.** Se ensancha: el sistema lee check-runs, estados y corridas de workflow por `gh` con las
  credenciales del usuario; no escribe nada nuevo en GitHub. Costo: una llamada GraphQL por
  candidato por ronda del poller, una por pasada de revisión y una llamada `gh api` en el verbo,
  solo para productos que declaran `ci`; crece con los candidatos abiertos, no con el tamaño de los
  datos. La carga local baja: los checks locales corren solo cuando el CI es verde pero viejo.
- **Riesgos conocidos.** Un `gh` ausente o ilegible espera hasta el tope y luego se rechaza. La
  declaración rige desde la tarea posterior al merge, porque el stack se lee en la base. Si
  `deliver` muere entre el push y `finish`, el reloj cae al `createdAt` del PR. El dedupe de
  enlaces depende de que el revisor no cite uno literalmente.

## Adenda (2026-10-08): la sección CI llega bajo un solo encabezado (GH-226)

- El revisor lee `## CI`, escrito por `context_for`; `ci_review.py` no escribe ningún encabezado
  propio en el cuerpo de la sección (antes lo empezaba con `### CI` y el revisor veía los dos).
- Esto reemplaza, por nombre, la `### CI` que la adenda de GH-216 dio como nombre de la sección que
  mencionan los prompts de revisión; los dos prompts ahora nombran `## CI`.
- `prompts.fingerprint` cambia solo para `tech_lead_review` y `tech_lead_review_light`, y se pide un
  `bl-*` nuevo al mergear.
- No se mueve ninguna métrica, etiqueta, atributo de span ni clave del documento.

## Adenda (2026-10-16): el merge y los chequeos no cuentan contra el tope (GH-263)

El 2026-10-16 un PR aprobado y mergeable de apostrophe (GH-135, PR #241) esperó unos 15 minutos
detrás de dos corridas largas (GH-237, GH-166) que ocupaban los dos slots de
`AGENTIC_MAX_CONCURRENT_RUNS=2`, y el poller sólo escribió `nothing to do this round`. Un
`agentic merge --issue 135` a mano lo mergeó en pocos minutos.

- **Decisión.** Los hijos que no corren modelo quedan **exentos del tope**, no sólo con
  precedencia: darle precedencia no ayuda cuando los dos slots ya están ocupados por corridas de
  horas, y estos hijos gastan CPU y tiempo, no sesión ni plata. Terminar lo entregado primero era
  la regla del operador.
- **Esto reemplaza, por nombre,** dos frases de la adenda de GH-178: "un merge nunca le saca el
  lugar a trabajo de roles" y "un hijo de merge por vuelta, en un slot sobrante".
- **Tipos.** `rules.RunKind = Literal["role", "merge", "land_checks", "land_model"]` y
  `rules.counts_against_cap(kind)`, el único lugar que decide si un hijo cuenta: un `match`
  exhaustivo que termina en `assert_never`, verdadero para `role` y `land_model`. `run_kind_of`
  asigna el tipo a cada paso de `land`: `rerun` y `file_followups_and_land` son `land_checks`; el
  resto, y las recuperaciones sin paso, `land_model`. Todo `sync_master` es `land_model`, aun el
  limpio: `landing_sync.sync_master` reabre la tarea y lanza un `resume` (una pasada de modelo)
  cuando el merge toca los paths revisados, choca al correr o falla el sync o el push, y nada de
  eso se sabe antes de correrlo; un hijo exento nunca lanza un `resume`.
- **Registro.** Cada registro de corrida gana `kind`; uno ausente, desconocido o ilegible se lee
  como `role`. Se escribe una vez, a un temporal y `os.replace`, y no se reescribe mientras el hijo
  corre. `merge` se registra como `merge`; `run` y `resume` como `role`.
- **`--run-kind`.** El poller lanza `agentic land --issue N --run-kind {land_checks,land_model}`
  (por defecto `land_model`). `land` compara su tipo con el paso que recalcula: un hijo
  `land_checks` que encuentra un paso que gasta modelo no registra nada, no lanza nada y devuelve
  `skipped/run_kind_mismatch`.
- **Cota combinada de uno.** A lo sumo un hijo exento (`merge` o `land_checks`) vivo en la
  máquina y a lo sumo uno arrancado por vuelta, el merge antes que el `land`. La cota es del lado
  del plan y sólo rige para lo que arranca un poller: un `agentic merge` o `agentic land` directo
  de una persona no se frena ni se rechaza al registrarse.
- **Orden.** Las acciones exentas van al final de `PollPlan.actions`, para que la espera de hasta
  60 s a que un hijo se registre nunca demore una acción de rol. Un `land_model` sigue pidiendo un
  slot sobrante y va después del lanzamiento.
- **Un hijo por tarea.** `gather_merges` salta toda tarea que ya tiene un hijo registrado vivo de
  cualquier tipo en este producto, con la misma lectura de `live()` que el conteo.
- **Líneas de espera.** `poll_plan` devuelve `PollPlan(actions, deferred)`; cada `Deferred`
  (`no_slot` o `exempt_busy`) es una línea del poller, y una vuelta con una espera nunca escribe
  `nothing to do this round`.
- **§7.** No se agrega, mueve ni renombra ninguna métrica, span, atributo de span, etiqueta, clave
  del documento ni prompt; `prompts.fingerprint` no se mueve. Lo nuevo: las líneas de espera del
  poller, la línea de resultado `run_kind_mismatch` y el campo `kind` del archivo de registro de
  corridas, que vive fuera de todo repo.
- **§8.** Se ensancha: el poller puede correr un hijo `merge` o `land_checks` por encima de
  `AGENTIC_MAX_CONCURRENT_RUNS`. Costo: CPU y tiempo de pared, no sesión ni dólares. El costo por
  vuelta no crece con el tamaño de los datos: una lectura más de los registros vivos, que el poller
  ya hacía.
- **Riesgos conocidos.** Un `agentic land` directo de una persona se registra como `land_model` y
  cuenta aunque su paso sólo reejecute chequeos: se equivoca hacia retener trabajo. El poller
  clasifica con el sha de upstream que memorizó y el hijo recalcula después; un desacuerdo estable
  repite `run_kind_mismatch` cada vuelta y sólo se ve en la línea del hijo. Un conjunto de chequeos
  exento corre junto a hasta `cap` corridas de rol; si la máquina aún se sobrecarga, la palanca es
  el valor de esa cota y no el tope. Los registros escritos antes de este cambio no tienen `kind` y
  cuentan como `role` hasta que mueren.

## Adenda (2026-10-16): un aterrizaje entregado usa un cupo reservado, no queda exento (GH-270)

Caso: GH-131 (PR #252) y GH-247 (PR #259), aprobados y con `merge_conflict`, esperaron horas `land #N waits: no_slot` porque dos corridas largas de rol ocupaban el tope. Mover el aterrizaje antes del lanzamiento no alcanza: con `available_slots == 0` la precedencia no desaloja a nadie.

Decisión: `rules.LAND_HEADROOM = 1`. Un aterrizaje `land_model` puede arrancar mientras las corridas vivas que cuentan (`role` + `land_model`) sean menos que `AGENTIC_MAX_CONCURRENT_RUNS + LAND_HEADROOM`. Lanzamientos, reanudaciones y retomas usan el tope sin más. `counts_against_cap` no cambia. Se descartó la exención: el costo real del aterrizaje es la reanudación que lanza (`agentic resume --detach`, registrada como `role`), que ya cuenta, y una cota aparte no acotaría nada. Se descartó la expropiación: cambiaría corridas en curso.

- Orden: cierres, retomas, reanudaciones debidas, el aterrizaje `land_model` más bajo, el lanzamiento, hijos exentos. Con `R` = retomas + reanudaciones de la vuelta, `slots_left = max(available - R, 0)` y `L = land_slots - R`: si `slots_left > 0` y `L > 0` consume un cupo del plano; si `slots_left == 0` y `L > 0` usa el reservado y no consume; si `L <= 0` queda `Deferred(no_slot)`. `poll_plan(..., land_slots=None)` equivale a `available_slots`; `run_once` pasa `max(cap + LAND_HEADROOM - live, 0)` con `live` sin recortar.
- El aterrizaje no arranca mientras `account_paused`: una pausa `account_limit` de este producto, ni agotada ni persistida, espera su calendario (`Deferred(..., "pause_waiting")`). No mira pausas `upstream`/`network` ni el pacing. Riesgo conocido: el 429 de otro producto de la misma cuenta no se ve; el costo es casi nulo porque el 429 llega en el primer turno.
- Hueco aterrizaje-reanudación: `spawn_and_mark` escribe `resume_spawns`/`resume_spawned_at` y después espera, hasta `REGISTER_WAIT_S`, una entrada `(producto, tarea, kind="role")`; si vence, lo registra en una línea y termina. `_await_registration` gana `kind` y `product` opcionales; sin ellos, igual que antes. El registro sigue siendo la única fuente de `live`.
- §8: las corridas vivas que cuentan pueden llegar a tope + 1 (con el tope por defecto de 1, dos sesiones), sólo por un aterrizaje o su reanudación; a lo sumo uno a la vez por máquina, acotado por tarea por `LANDING_PASSES_MAX` y `LANDING_SYNCS_MAX`. Un `sync_master` limpio retiene el cupo lo que dura su `agentic land`; no se parte en dos clases en esta tarea.
- §7: sólo se mueve una línea de poll (`pause_waiting`); ninguna métrica, etiqueta, atributo ni clave de documento.

## Adenda (2026-10-23): el conflicto tiene dos lecturas y un solo hecho (GH-326)

Caso: el PR #324 (GH-320) quedó `CONFLICTING` en GitHub, que no corre los workflows de `pull_request` sobre un PR en conflicto; el CI nunca reportó y el poller esperó `CI_WAIT_MAX_S` como `ci_missing` antes de que `land` actuara. La sonda `merge-tree` de GH-255 no lo detectó; el material no dice si devolvió `False` sobre un master fresco, si midió contra un `upstream.sha` viejo o si `conflict_probe_wanted` era falso.

Decisión: `MergeFacts.conflicts` sigue siendo un solo hecho, derivado en un solo lugar, `rules.merge_conflicts_from`. Es `True` cuando GitHub lee `CONFLICTING` y esa lectura se hizo sobre la cabeza (`head_oid`) y la punta de la base (`base_oid`) que el poller o el verbo están decidiendo (`same_commit` con `pr_head` y `current_master`); en cualquier otro caso (`MERGEABLE`, `UNKNOWN`, sin lectura, cabeza vieja, base distinta o ausente) vale lo que dio la sonda, sin cambios. `UNKNOWN` (GitHub aún calcula) no es un conflicto: se relee en la vuelta siguiente, sin espera añadida. `assess_merge` y `merge_task` pasan por la misma función, y el verbo la vuelve a consultar en su última mirada antes de `Tracker.merge`.

- Sin valor nuevo en `MergeRefusal`: la negativa sigue siendo `merge_conflict`, fuera de `COMMENTED`, y `landing_step` no cambia. Cada línea de log de `merge_conflict` nombra su origen, `github` o `probe`: texto libre, no una etiqueta.
- Un conflicto que GitHub ve y git no puede repetir `sync_master`; lo acota `LANDING_UNSYNCED_MAX` por sha de upstream.
- §7: ninguna señal se mueve. §8: dos campos escalares más en una consulta GraphQL que ya se hace una vez por tarea y por vuelta; el costo sigue siendo proporcional a los PR abiertos.

## Adenda (2026-10-26): terminar antes de empezar (GH-362)

Caso: desde 2026-10-12 el poller de apostrophe escribió `land #N waits: no_slot` 139 veces, y el 25 de octubre el aterrizaje de agentic-system #352 esperó mientras corridas de apostrophe llenaban cada cupo. El Cupo reservado de GH-270 es de un producto: el poller de A no ve que el de B llena la máquina mientras un aterrizaje de A espera. Esta adenda enmienda la de GH-270; no cambia `counts_against_cap` ni `LAND_HEADROOM`.

Decisión, dos reglas del plan:

- **Espera de aterrizaje.** Cada poller, con el `plan.deferred` de la vuelta, publica en el registro de corridas qué aterrizajes `land_model` suyos esperan cupo (`holds_launches`: sólo `Deferred("land", _, "no_slot")` y `Deferred("land", _, "land_queued")`; `exempt_busy` y `pause_waiting` nunca publican). La escritura va dentro de `registry.exclusive()`, justo después de `poll_plan` y antes de `execute`, así que ninguna salida deja el registro de la vuelta anterior: una vuelta sin espera borra el archivo. El archivo es `waiting/<sha256(raíz de tareas resuelta)[:12]>.json` con `{product, root, written_at, boot_id, since, issues}`, entero, junto a `launches/` y `spend/`. Mientras otra raíz tenga un registro fresco, `poll_plan(others_landing=True)` retiene todo arranque que cuenta y está por debajo de un aterrizaje: lanzamientos, reanudaciones y retomas de cualquier fase. Los aterrizajes, los cierres y los hijos exentos no se retienen; un producto nunca se retiene a sí mismo (compara su clave de raíz).
- **Vencimiento sin que el escritor lo limpie.** El adaptador descarta un registro más viejo que `rules.LAND_WAIT_TTL` (15 min), uno de otro `boot_id` y uno ilegible (con una nota); no decide nada más: si un registro retiene algo lo decide `rules.poll_plan`. Un poller muerto nunca retiene a otro por más de 15 minutos, y uno vivo reescribe cada vuelta.
- **Cercanía.** `rules.CLOSENESS = ("launch", *PHASES, "delivered", "land")` y `rules.closeness(state)`; `poll_plan` arranca retomas y reanudaciones en orden `(-rango, tipo, issue)`, la retoma antes que la reanudación a igual rango, y el aterrizaje compite con el rango de `land`. Con un `run_once` de producción (`H = A + 1` o `H = A = 0`) arrancan los mismos hijos que antes y sólo cambia el orden de las acciones. Reparto del cupo, con `A = available_slots`, `H = land_slots` y `n` las retomas y reanudaciones que podrían arrancar:

  | caso | aterrizaje | retomas y reanudaciones, en orden de rango | el lanzamiento ve |
  |---|---|---|---|
  | `account_paused` | `Deferred(pause_waiting)` | `min(n, A)`, plano | `A - started` |
  | `H <= 0` | `Deferred(no_slot)` | ninguna | 0 |
  | `H > 0`, `A > n` | cupo plano | las `n`, plano | `A - n - 1` |
  | `H > 0`, `A <= n`, `H > A` | cupo reservado | `A`, plano | 0 |
  | `H > 0`, `A <= n`, `H == A` | cupo plano | `A - 1`, plano | 0 |

  Retomas y reanudaciones nunca usan el cupo reservado y a lo sumo un aterrizaje arranca por vuelta.
- **Aterrizaje contra aterrizaje (primero en llegar, primero en aterrizar).** Dos productos con un aterrizaje esperando se disputan el único cupo reservado de la máquina; gana la espera más vieja. `rules.land_queued_behind(own_since, own_key, others)` es verdadero cuando algún registro vigente de otra raíz tiene un `since` anterior al propio, y a igual `since` gana la `root_key` menor; un producto sin espera propia todavía (`since` nulo) va detrás de cualquier registro vigente. `run_once` lo calcula de la misma lectura de `land_waits` y se lo pasa a `poll_plan(earlier_land_wait=...)`: mientras es verdadero, el aterrizaje `land_model` que habría arrancado queda `Deferred("land", n, "land_queued")` y no ocupa cupo (el siguiente sigue en `no_slot`; `pause_waiting` gana si hay pausa por límite de cuenta; los hijos exentos no se tocan). `land_queued` abre una Espera de aterrizaje igual que `no_slot` (`holds_launches`): publica un registro que conserva su `since` (`land_wait_since`), así que conserva su lugar en la fila, y `scripts/poll_waits.py` cuenta las dos razones como la misma espera abierta. La espera más vieja no va detrás de ninguna, así que no hay interbloqueo; un escritor muerto vence a los 15 minutos y libera la fila. Riesgo conocido, acotado: si el registro propio se pierde (vence o se borra a mano) mientras el aterrizaje espera, el producto vuelve a empezar con `since` nulo y cede ante cualquier registro vigente hasta que publique el suyo; pierde su lugar, no el aterrizaje.
- **Costo asumido.** Un producto con varios aterrizajes seguidos retiene los arranques de los otros mientras dure su cola; es el precio de terminar antes de empezar.
- **Fuera de alcance.** La serialización de los hijos exentos (`exempt_busy`, adenda de GH-263) no cambia; una corrida lanzada a mano nunca se retiene ni se rechaza, sólo cuenta en `registry.live()` contra el tope; y esta adenda no afirma que el tiempo hasta el merge mejore: lo que mide `scripts/poll_waits.py` es la espera del aterrizaje en el diario del poller.
- **§7.** No se agrega, mueve ni quita ninguna métrica, span, etiqueta, clave del documento ni prompt; lo nuevo es el archivo `waiting/` del registro, fuera de todo repo. **§8.** Un archivo más por producto; el costo por vuelta es lineal en el número de productos (dos o tres) y no crece con los datos.

## Adenda (2026-10-27): una ganancia inerte del master no invalida los chequeos (GH-353)

Caso: `merge.run_checks` corre la suite entera cuando el master se movió desde el pase registrado, aunque lo que el master ganó no pueda cambiar ningún chequeo. Con varias tareas en vuelo el master se mueve entre vueltas, y cada suite de ~30 minutos retrasa también a las demás tareas aprobadas (un solo hijo de merge o `land_checks` por máquina, GH-263). Esta adenda enmienda la regla de reutilización de GH-326/GH-335 (`checks_head == pr_head` y `checks_master == current_master`); no cambia la negativa de la regla de solapamiento.

Decisión:

- **La regla.** Un pase registrado sobre un master anterior sigue valiendo cuando la **ganancia** del master desde ese pase (`git diff <master de la fila>..<master actual>`) es **inerte**: tiene al menos un camino, ninguno cae en el conjunto nunca-inerte y todos coinciden con algún glob que el producto declara. Lo calcula una sola función pura, `domain/master_gain.py` (`gain_inert`, `masters_covered`); ningún adaptador, poller ni nodo lo repite.
- **La negativa del solapamiento se mantiene.** "Volver a correr sólo si el diff del master toca los archivos del PR" sigue rechazada: los caminos de la tarea no entran en la regla, porque caminos disjuntos pueden chocar semánticamente. Lo único que se mira de la tarea es que no edite `.agentic/stack.yaml` (ahí vive la declaración) y que se conozca su lista de caminos; si no, no está cubierto.
- **`merge_inert`.** Clave opcional del stack, lista de globs, leída de la cabeza del PR (no del master): una tarea que cambia la declaración la cambia en su propia cabeza, y la regla se niega. **El sistema no trae ningún valor por defecto**: sin declaración, o con una vacía, todo producto decide exactamente como antes. Declararlo es una promesa del producto de que ningún chequeo lee esos caminos.
- **Nunca inerte.** Definido una vez en `master_gain.py`, ningún glob declarado lo puede habilitar: `.agentic/stack.yaml`, todo `.agentic/**` salvo `.agentic/tasks/*.md`, `.github/**`, `CODING_STANDARDS.md`, un camino fuera del repo y todo lo que `check_results.env_drifted` marca (manifiestos y locks a cualquier profundidad, caminos que un comando de setup nombra). No pregunta a `rules.guarded_path`.
- **El comparador.** Segmento a segmento, con `posixpath.normpath` y sensible a mayúsculas: `*`, `?` y `[...]` no cruzan un `/`; un segmento `**` entero vale cero o más segmentos (`docs/*.md` no coincide con `docs/a/b.md`; `docs/**` sí). El cargador del stack rechaza, con `glob_refusal`, un glob `empty`, `absolute`, `backslash`, con un segmento `..` o con el primer segmento como patrón (`wildcard_root`: `**`, `*`, `*.md`, `**/x`). `docs/**` y `.agentic/tasks/*.md` se aceptan.
- **Ante la duda, se corre.** No cubierto: un master de fila que no es ancestro del actual o desconocido, un diff que falla, una ganancia vacía entre shas distintos, un stack de la cabeza ilegible o no traído, un `merge_inert` ausente o vacío, una tarea que edita `stack.yaml`. Un caso, una prueba.
- **Un master por fila.** Cada fila de `merge_checks` registra el master sobre el que corrió (`master`); una fila de un documento viejo, sin la clave, cae al `master` del registro, y las filas del nodo `full_checks` no llevan ninguno (nadie las lee como `merge_checks`). Una repetición (`rerun_record`) ya no sella con el master nuevo filas que corrieron sobre el viejo.
- **Una decisión cubierta no gasta intento** y deja `auto_merge_inert_from`, los masters de fila sobre los que se apoyó el merge, junto con la intención.
- **Costo aceptado.** Un `rerun` del aterrizaje cuyas filas conservadas corrieron sobre un master que la ganancia no cubre lleva a una suite completa; antes se sellaba el master nuevo sobre filas que no lo habían visto.
- **El punto 2 del issue** (el último chequeo completo de la corrida cuenta como `merge_checks`) lo entrega ADR-0065: con el master sin moverse desde la base, el árbol integrado es el de la cabeza y la puerta de merge reutiliza los pases de `full_checks` por clave. `full_checks` no pasa a ser una segunda fuente de `merge_checks`.
- **Riesgo residual.** Un glob declarado es la promesa de que ningún chequeo lee ese camino. Una tarea que agrega una prueba que lee un camino declarado inerte lo vuelve vivo, y nada lo detecta. Por eso la lista de un producto se escribe estrecha (`.agentic/tasks/*.md`, no `docs/**` si sus pruebas leen `docs/`).
- **§7.** No se agrega ni se mueve ninguna métrica, span ni etiqueta; la única clave nueva del documento es `auto_merge_inert_from`. **§8.** Una lectura `contains` y una `gain_paths` por master de fila distinto y por llamada del verbo de merge, sólo cuando el master se movió, el producto declara `merge_inert` y ninguna negativa previa a los chequeos aplica.
