---
status: accepted
date: 2026-09-12
---

# Escalados al humano con `interrupt()`, no terminando el turno del grafo

Cuando un par llega al límite de devoluciones, PjM escala al humano y la tarea queda pausada
hasta que responde, a veces un día después. Implementamos la pausa con `interrupt()` de
LangGraph y reanudamos con `Command(resume=decisión)`, sobre un checkpointer durable
(SqliteSaver en `.agentic/state.sqlite` del producto).

`open_deep_research`, que tomamos como referencia, hace lo contrario: termina el turno con
`Command(goto=END)` y confía en que el usuario vuelva sobre el mismo `thread_id`. Ese patrón
funciona porque su pausa está en un punto fijo al inicio del grafo. La nuestra puede caer en
cualquiera de cinco pares en el medio del camino. Terminar el turno obligaría a que cada nodo
sepa reanudarse; `interrupt()` guarda la posición exacta.

## Consecuencias

- El grafo se exporta sin compilar para `langgraph dev`; la CLI lo compila con SqliteSaver.
- `thread_id` es el id de la tarea.
- Alguien tiene que llamar `resume`: la CLI `agentic resume` lee el comentario del issue cuando
  la etiqueta `needs-human` ya no está. Un poller con reloj propio la reemplaza en la etapa 5.

## Adenda (2026-09-15): la pausa va en un nodo propio, y el documento es el camino de rescate

La cuarta corrida de GH-12 escaló por tope de tarea con las siete subtareas aprobadas y la
integración devuelta: faltaban un Developer y una revisión, y no había cómo reanudar
(issue #21). Esta adenda construye lo decidido arriba y fija dónde va cada pieza.

**`interrupt()` va en `pause`, un nodo sin efectos después de `escalate`.** LangGraph
reanuda un nodo interrumpido desde su comienzo, re-ejecutando todo lo anterior al
`interrupt()`. Dentro de un caso de uso volvería a pagar el rol; dentro de `escalate`
volvería a etiquetar y comentar el issue. `pause` no hace nada más que esperar: con la
respuesta devuelve el patch de reanudación (`rules.resumed`: sin escalado, el estado que la
tarea tenía, el contador del par que llegó al límite en cero, la decisión como sección
`Human`, que lee todo rol sin declararla, y la integración si el escalado llegó antes de
que la revisión la agregara), y el
ruteo va a donde el estado dice que se frenó (`rules.resume_target`, función pura con la
misma información que `after_validate` y `after_review`). Un segundo escalado vuelve a
pasar por `escalate` y `pause`. Una subtarea que llega al límite queda `returned` sin
contar la devolución, así la respuesta la devuelve a su Developer.

**El estado previo se deriva, no se guarda.** El escalado pisa `status` con
`waiting_on_human`, y el documento también. De dónde venía la tarea lo dicen las
subtareas, las corridas por sección y el historial de devoluciones (`rules.resumed_status`),
así la misma regla sirve para el checkpoint y para el documento.

**Checkpointer `AsyncSqliteSaver` en `.agentic/state.sqlite`**, excluido por
`.git/info/exclude` como los worktrees. No `SqliteSaver`: la CLI corre `ainvoke` bajo anyio y
el saver síncrono no implementa las lecturas async. El grafo exportado para `langgraph dev`
sigue sin compilar.

**`agentic resume --issue N`.** Usa el checkpoint si existe. Toma como decisión todos los
comentarios del issue posteriores al último del sistema (`Escalated:`, `Resumed:`,
`Ready for review:`), juntos (sin comentario del sistema no hay decisión: lo anterior es la tarea): el humano
puede responder en varios, y el resumen muestra la
decisión entera antes de confirmar. Un marcador como `Decision:` queda para el poller de la
etapa 5, que necesita saber cuándo el humano terminó. Sin decisión sale con 1 si hay
preguntas pendientes o un par llegó a su límite, porque resetear el contador sin nada nuevo repite la misma devolución. Un tope
alcanzado o un fallo de rol se reanudan sin decisión. Antes de gastar
muestra la tarea, dónde se frenó, el motivo, lo gastado por fase, el tope nuevo, qué reanuda,
las sesiones y los worktrees, y pide confirmación salvo `--yes`; `--dry-run` no gasta nada.
Rechaza reanudar si el tope en vigor no supera lo gastado, o si no queda nada que correr (una
tarea ya entregada). Quita `needs-human`, comenta
"Resumed: …" y sigue con `Command(resume={"decision": texto})`. Un checkpoint de una
corrida que se cayó sin escalar reanuda los nodos pendientes tal como están.

**El documento es el camino de rescate.** Una tarea pausada antes del checkpointer, como
GH-12, o cuyo checkpoint se perdió, se siembra desde el documento con `--from-document`:
se filtra lo que no es estado (`plugins`, `system_commit`), se hace `git worktree prune`, se
cruza con git (la rama existe, `base` coincide con `agenticBase`, una subtarea aprobada
sigue en su `reviewed_head`) y un desacuerdo rechaza en vez de reanudar sobre otro código.
El estado entra en un thread vacío como si `escalate` acabara de correr
(`aupdate_state(as_node="escalate")`), y desde ahí es igual a un checkpoint: `pause`,
respuesta, ruteo. No se comenta dos veces.

**`agentic run` no arranca sobre una tarea con estado** (pausada, caída o terminada en el
checkpoint, o con el documento escalado) salvo `--fresh`, que borra el thread. Sin eso
pisaría el documento, sumaría costo y corridas sobre las del thread y reutilizaría ramas
con otro plan.

Descartado: que la CLI busque sola las tareas cuyo issue ya no tiene `needs-human`. El
humano nombra la tarea que reanuda; la cola sigue siendo la etiqueta, y el poller de la
etapa 5 hará la búsqueda.

## Adenda (2026-10-04): un store de checkpoints por tarea

apostrophe GH-8: varias corridas de un mismo producto escriben en el único
`.agentic/state.sqlite` desde procesos distintos, y una se cae con `database is
locked`. WAL y un `busy_timeout` generoso ya estaban puestos -- `AsyncSqliteSaver.setup`
corre `PRAGMA journal_mode=WAL` y `sqlite3.connect` trae `timeout=5.0` por defecto -- y
no alcanzan: el choque es entre *procesos*, no entre hilos de uno solo, y bajo WAL un
`SQLITE_BUSY_SNAPSHOT` vuelve en el acto, sin esperar el timeout.

**Un store por tarea.** `thread_id` ya es el id de tarea, y ningún comando lee entre
tareas: dos tareas nunca compiten por el mismo archivo si cada una tiene el suyo,
`.agentic/state/<task_id>.sqlite` (`shared.configuration.state_file_for`).
`composition.checkpointer(cfg, task_id)` es el único sitio que abre una conexión al
store o construye un `AsyncSqliteSaver`, y rutea en tres pasos: si el archivo propio ya
existe, ese; si no, y el compartido tiene al menos una fila de `checkpoints` con ese
`thread_id`, el compartido, tal cual hoy; si no, el propio, nuevo.

**La consulta de ruteo nunca escribe.** Antes de conectar, comprueba
`legacy.resolve().is_file()`; si abre, es de solo lectura,
`sqlite3.connect(legacy.as_uri() + "?mode=ro", uri=True)`, nunca un f-string con la ruta
cruda -- `Path.as_uri()` escapa `#` y `%`, que un f-string no. Un `OperationalError` de
"no such table" lee como cero filas (un compartido de antes de que existiera la tabla);
cualquier otro, por ejemplo un `database is locked` durante la recuperación del WAL tras
un crash, sube: pasa al arrancar, antes de cualquier rol, así que no cuesta plata.

**La migración es la reapertura después de `adelete_thread`.** `cli._run --fresh` y
`cli._resume --from-document` ya borran el thread antes de invocar; con el thread
borrado, el ruteo ya no encuentra filas en el compartido, así que una segunda apertura
de `composition.checkpointer(cfg, task_id)`, con el grafo compilado de nuevo, resuelve
al archivo propio. La primera conexión no retiene ningún lock mientras corre la
segunda: `adelete_thread` hace commit y las lecturas previas (`aget_state`,
`_refuse_a_started_task`) son `SELECT`. Una tarea reanudada desde su checkpoint
(`resume` sin `--from-document`) no reabre: sigue en el archivo donde el ruteo la
encontró.

**Riesgos conocidos.** Una tarea vieja reanudada desde su checkpoint sigue en el
compartido hasta que se reinicia o se cierra -- compite solo con otras tareas viejas,
nunca con una nueva. Los archivos se acumulan, uno por tarea, y nada los borra, el
compartido incluido -- la misma situación que ya tienen los threads hoy. Que una corrida
muera por cualquier otra excepción sigue dejando la tarea `developing` sin proceso
vivo, sin escalar y sin que el poller la vea: queda fuera de esta adenda (`_invoke`,
`poll.py` y `rules.auto_resumable` no cambian).
