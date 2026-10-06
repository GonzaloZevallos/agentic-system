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
