---
status: accepted
date: 2026-09-24
---

# La revisión de stack corre roles sin tarea: propone, no cambia, y no deja telemetría

`agentic stack review` corre el Tech Lead en modo proactivo con el efímero como
adversario, sobre un producto, sin que haya ninguna tarea. Todo lo que ese camino
querría reusar está atado a un id de tarea: `Tracker` resuelve GitHub por
`issue_number(task_id)`, `run_role` lee `task_cap(state, cfg)` y `state["cost_usd"]`
de un `TaskState`, `finish` llama `ports.documents.save(apply(state, patch))`, y
`graph._node` abre `role_span(..., product, arm=...)`. Esto fija dónde cede cada una
de esas ataduras. Lo que queda abierto se dice al final, en vez de aparentar que está
cerrado.

## El verbo de creación va en `Tracker`, y devuelve la URL

`open_issue(title: str, body: str) -> str`, en `Tracker` y no en `Inbox`. `Tracker` es el lado de escritura de la costura `gh` y ya tiene la
maquinaria que una creación necesita: `GhTracker._gh` resuelve el repo desde
`cwd=product_dir`, traduce `FileNotFoundError` en "gh is not installed" y
`CalledProcessError` en un `ValueError` con el que la CLI sale. `GhInbox` no tiene
nada de eso — llama `subprocess.run` inline, una vez, para una lectura — así que un
verbo de escritura ahí sería una segunda copia del manejo de errores de `_gh` en una
clase cuyos otros verbos son álgebra de ids.

"Keyed by task id" es una propiedad de los seis verbos que `Tracker` tiene hoy, no del
port: `open_pull_request` ya crea algo que todavía no existe y devuelve su URL. Se
devuelve la **URL** y no el número, espejando `open_pull_request`; `gh issue create`
imprime una URL y `tracker.pull_request_number` es el precedente para parsearla de
vuelta cuando alguien quiera el número.

**Ninguna etiqueta**, y por eso el verbo no toma un parámetro `labels`. La decisión 2
del pedido exige únicamente la **ausencia** de `agentic` (README §5 la hace la
aprobación); nunca pidió una etiqueta positiva. Y `gh issue create --label X` sale
distinto de cero cuando la etiqueta no existe en el repo del producto, o sea que una
entrevista entera se paga y se tira por una palabra. El estado va en el título
(`[needs-info] …`), que no es una precondición sobre el repo del producto que el
sistema no crea. Un parámetro con un solo llamador que siempre pasa nada es la forma
especulativa que §4 prohíbe; sin el parámetro, la ausencia de `agentic` es estructural
en vez de un default que alguien pueda pisar. README §5 no se toca: ninguna tercera
etiqueta entra al tracker del producto.

**Seguridad (§8).** Es el único lugar donde este cambio ensancha lo que el sistema
puede hacer: gana la capacidad de **crear** issues en el repo del producto, con las
credenciales de `gh`, nunca con la etiqueta `agentic` — así que una propuesta que abre
no puede arrancar una corrida por su cuenta; sólo el humano poniendo la etiqueta
(README §5).

**Forma vieja que se preserva.** `GhInbox` conserva su `subprocess.run` inline; esta
tarea no lo migra. Los dos caminos de error de los adaptadores de GitHub quedan
distintos hasta que otra tarea los unifique.

## Los roles corren por `run_role`, con estado sintético y corpus aparte

Se pasa por `run_role`: el tope por intento, el reintento único con el error como
entrada, la continuación de sesión de `run_with_retry`, la guarda de esquema y la
jaula de negaciones vienen gratis. Saltearlos para la lectura más cara del sistema
—el Brief, el stack, los estándares y treinta y un ADRs— no es un canje que valga
evitar un id sintético.

El estado es sintético, el id es `stack-<YYYY-MM-DD>`, y el documento va bajo
`.agentic/stack-reviews/`, **nunca** `.agentic/tasks/`. Mecánicamente:
`document.relative_path(id, subdir=TASKS_SUBDIR)` gana un keyword con default y
`FilesystemTaskDocuments.__init__` gana `subdir=TASKS_SUBDIR`, leído por `path_for` y
por `ids()`. Todo sitio de construcción existente queda byte a byte igual, y
`close --all`, `eval score` y `_bucket_documents` —que llegan al corpus por
`documents.ids()` y no por un glob propio— no pueden ver el corpus nuevo, porque
reciben los ports construidos por default. Un solo módulo sigue siendo dueño del
layout, que es por qué no se escribe una segunda clase de adaptador.

Dos consecuencias que se deciden en vez de dejarlas implícitas. Una segunda revisión
del mismo día **pisa** su documento: el issue es el artefacto durable e inventar un
contador no compra nada. Y la forma del id queda deliberadamente fuera de
`^GH-(\d+)$`, así que cada verbo de `Tracker` que se guarda con `issue_number` es un
no-op contra ella (`tracker.py:75-89`, `if ... is not None` en los cinco): una revisión
de stack no puede comentar ni etiquetar el issue de nadie. `open_pull_request` es el
único verbo que no se guarda —sólo su prefijo `Closes #N` es condicional— y por eso
esta forma de id no se reusa nunca para abrir un pull request.

**El tope.** Se hereda `rules.task_cap` antes de que haya plan, la base de 30 USD, y
se dice en el plan en una frase: un revisor que lea `task_cap` aplicado a algo que no
es una tarea tiene que encontrar el motivo escrito, no inferir un bug.

## `preflight()` no se toca, y este camino no emite telemetría

`preflight()` queda byte a byte. El verbo compone su propia puerta en `cli.py` con
las piezas sueltas: `refuse_its_own_checkout` siempre; `ports.flags.load()` y
`load_prompt` sobre `ROLES` siempre —no cuestan nada y son lo que atrapa un
placeholder suelto antes de que rompa `agentic run` para gym-saas—; y
`ports.stack.checks/product` **sólo cuando `.agentic/stack.yaml` existe**, que es el
único hecho que este verbo existe para cambiar. `git diff --stat -- agentic/core/
process/application/preflight.py` vacío es la evidencia de regresión, y va en el plan
como tal.

**No se agrega ningún camino de inferencia para el nombre del producto, porque este
camino no emite telemetría.** Ni span, ni métrica, ni familia. Corre fuera del grafo,
así que `_node` nunca abre `role_span`, y no llamará `telemetry.record_*` como hace
`close` para `agentic.delivery.*`. Una emisión condicional —etiquetada cuando hay
stack declarado, muda cuando no— sería una familia con dos formas, que es lo que
ADR-0030 rechazó para `cold_fallback`; y un fallback al basename del directorio es el
segundo camino de inferencia que la adenda del 2026-09-17 de ADR-0008 rechazó por su
nombre. Sin nada emitido no hay etiqueta que llenar y la pregunta se disuelve.

**Observabilidad (§7).** Este cambio **no agrega, no saca y no mueve ninguna señal**.
El costo aceptado es que la plata que gasta una revisión es invisible en Grafana; queda
registrada por corrida en el documento de la revisión, y eso se escribe en el plan como
riesgo conocido, no se descubre en un panel.

## Las señales son datos, salvo la que ninguna serie registra

Las señales computables entran al prompt como dato, aplicando el precedente de
ADR-0019 verbatim: un antecedente adivinado se equivoca en el paso que decide todo lo
que sigue.

El ancla es **el último commit que tocó `.agentic/stack.yaml`**, no "el último ADR de
stack": es git pelado, no necesita convención de nombre ni marcador en el frontmatter
de `docs/adr/`, y es literalmente "desde que el stack se decidió por última vez". Un
repo sin ese archivo —el caso greenfield— satisface toda señal vacuamente, que es la
respuesta correcta ahí.

- (a) `BRIEF.md` tiene un commit más nuevo que ése.
- (b) diez o más documentos cuyo `delivery_outcome` es `merged`/`merged_with_changes`
  y cuyo timestamp de cierre es posterior a la fecha de ese commit; los dos campos
  existen desde ADR-0028 y se leen por `documents.ids()`/`load`.
- (c) **se cae** como condición que el rol evalúa: nada registra fallo por herramienta
  contra fallo por código, y un rol que lo afirme está fabricando evidencia. Sobrevive
  como `agentic stack review --reason "<texto>"`, la señal libre del humano, que cuenta
  como disparador por sí sola y se reproduce verbatim en el cuerpo del issue entre las
  señales que se cumplieron. Sin ese flag el verbo no podría atender el caso que (c)
  nombra, que es peor que sacarla.

La función de decisión es pura —`(brief_commit, stack_commit, [(closed_at, outcome)],
reason) -> Signals`, en `core/process/domain/`— y las lecturas de git y de documentos
que la alimentan son del llamador. Ésa es la costura unitaria.

## Tres roles nuevos, todos `replace(...)`, y ningún valor de etiqueta nuevo

`TECH_LEAD_EPHEMERAL_STACK`, `TECH_LEAD_INTERVIEWEE_STACK`, `TECH_LEAD_STACK`, el
primero con `tools=READ_CODE`/`allowed=READ_CODE_RULES` para que el efímero no escriba
nada. Ningún rol existente se edita: el diff muestra tuplas agregadas y cero editadas,
y eso es la evidencia de regresión del lado de los roles.

No hay rama de tarea acá y, por la decisión 2 del pedido, el borrador del ADR es el
cuerpo de un issue, así que la superficie de escritura que ADR-0014 le da al efímero
—`CONTEXT.md` y `docs/` en la rama de tarea— no tiene dónde aterrizar legítimamente.
`grilling` degrada a no editar archivos; es aceptable y queda dicho.

`max_turns`: 12 para los dos asientos de la entrevista, **20** para el autor, que tiene
que emitir el diff del stack, un `CODING_STANDARDS.md` entero y un borrador de ADR en
una sola entrega. `agentic.role.failures{reason="max_turns"}` sobre esos slugs es la
serie que diría que la estimación estuvo mal.

**El `Interview` nuevo lleva `stage="solution"`, no un tercer valor.** `Interview.stage`
es una etiqueta cerrada de `agentic.interview.*`; un tercer valor cambia un conjunto
cerrado por una lectura que nadie pidió, y una revisión de stack es una decisión de
etapa de solución. Ningún valor de etiqueta nuevo entra al sistema.

El entrevistado reusa `PlatformAnswers` —ningún esquema nuevo ahí— y no hay consultado,
porque no hay PM en este camino. Cuando la entrevista termina con preguntas todavía
abiertas no hay tarea que pausar ni checkpoint: el verbo toma la misma salida que el
caso sin Brief, un issue `needs-info` con las preguntas y sin propuesta. Una salida,
reusada.

**Estándares (§9).** Este cambio no introduce ni mueve ningún literal de versión, así
que nada nuevo cae bajo la forma de `unknown_pins`.

## Una propuesta abierta suprime la siguiente

El ancla no avanza cuando una propuesta se **abre**: `.agentic/stack.yaml` se mueve
recién cuando el humano etiqueta, una tarea construye y el humano mergea. Entre esos dos
momentos toda corrida recomputa las mismas señales. Este verbo existe para que lo llame
el poller de la etapa 5, así que el estado estable no es un duplicado: es un issue por
poll, indefinidamente, cada uno con un `CODING_STANDARDS.md` entero y una entrevista
pagada. Lo mismo vale para la salida `needs-info`: un producto sin Brief recibiría un
issue por poll para siempre, y nada en el sistema escribe un Brief.

**Una propuesta ya abierta suprime, y se chequea en la puerta antes de que corra ningún
rol**, con la forma que `_refuse_open_pull_request` ya tiene en `cli.py`. `Inbox` —el
lado de lectura— gana `open_stack_reviews() -> list[str]`; una respuesta no vacía
rechaza por nombre con esa URL y no gasta nada. El marcador es un comentario HTML
legible por máquina en el cuerpo, `<!-- agentic:stack-review anchor=<sha> -->`, nunca
una etiqueta.

**La supresión se llava del marcador solo, no del ancla**: dos propuestas de stack
abiertas para un producto no es un estado que nadie quiera, ni siquiera cuando el Brief
se volvió a mover; el humano cierra la vieja, y una propuesta abierta a la vez es el
invariante que vale. El sha del ancla viaja en el marcador como diagnóstico y el rechazo
lo imprime, así que "ya propuesto, anclado en `<sha>`" distingue una propuesta rancia de
una vigente sin abrirla. La salida `needs-info` lleva el mismo marcador y se suprime
igual.

Descartado: `--dry-run` por default con un `--open` explícito. Rompe el poller para el
que este verbo existe.

## El greenfield tiene que ser un repositorio, y el documento se excluye

`repository_at` devuelve `None` tanto para "no es un repositorio" como para "git no está
instalado", así que sobre un directorio pelado la comparación contra el common-dir real
de stage0 difiere y `refuse_its_own_checkout` **pasa** justo sobre la entrada que menos
puede juzgar. El verbo exige un repositorio git y rechaza nombrando las dos mitades,
antes de gastar nada, con el par de `tmp_path` como test rechazante: sólo `BRIEF.md`
rechaza, el mismo path después de `git init` sigue.

El chequeo que además habría matado la decisión 4: `main()` ya hace
`if not cfg.agentic_dir.is_dir(): raise ValueError(...)`, fuera de toda rama por comando.
Un repo greenfield no tiene `.agentic/`, así que el verbo saldría con código 2 antes de
llegar a su propia puerta. Ese rechazo pasa a ser condicional al comando; todo otro
comando lo conserva byte a byte, y el plan lo dice, porque relajar un rechazo global es
exactamente la edición que un revisor tiene que ver justificada en vez de descubrir.
`FilesystemTaskDocuments.save` ya hace `mkdir(parents=True, exist_ok=True)` y
`FilesystemRunLock._write` también, así que ni el documento ni el lock necesitan cambio
sobre un repo sin `.agentic/`.

**El documento se excluye**, con la entrada en el `.gitignore` de este repo en el mismo
diff, al lado de `.agentic/state.sqlite` y `.agentic/worktrees/`, con su motivo en una
línea. Nada lee un documento de revisión de stack de vuelta —`resume.from_document`
reconstruye una tarea y esto no es una tarea—, una segunda corrida del mismo día lo pisa
sin contador, y su valor es diagnóstico, no probatorio: lo contrario de `.agentic/tasks/`,
que ADR-0001 hace el registro crudo y ADR-0017 commitea a propósito. Excluirlo cierra
gratis el barrido de `archive_previous_run`, porque `commit_all` es `git add -A` y
respeta las reglas de ignore del producto; por eso la entrada va en el `.gitignore`
versionado y no en `.git/info/exclude`.

## Tres campos y un cuerpo medido

`StackProposal` lleva `verdict`, `stack_diff`, `standards` y `adr_draft` como campos
separados, nunca un solo blob de `markdown`: el valor entero de la guarda de esquema es
nombrar las claves exactas que una entrega omitió (ADR-0011, quinta adenda), y un blob no
le da nada que nombrar.

`ISSUE_BODY_MAX_CHARS = 60_000`, debajo de los 65_536 de GitHub con lugar para el
marcador y el encabezado de señales, con un rechazo puro en el dominio que nombra el
conteo de bytes y el tamaño de cada pieza —la forma que `knowledge.brief_refusal` usaba
para su tope de palabras, hoy retirado (ADR-0061), porque un rechazo que nombra el número es
diagnosticable y la salida distinta de cero de `gh` no lo es—. `open_issue` escribe el cuerpo a un archivo
temporal y pasa `--body-file`, así el único límite que puede morder es el de GitHub y
nunca el de argv.

**El fallback se decide acá y no en el turno 18**: un cuerpo por encima del tope **no se
trunca y la entrevista no se vuelve a correr**. El caso de uso escribe las tres piezas
verbatim en el documento de la revisión —que existe en disco igual— y abre el issue con
el veredicto, las señales, el `stack_diff` y el `adr_draft`, diciendo en el cuerpo que
los estándares pasaron el tope y nombrando la ruta del documento de donde el humano
copia. Nada pagado se pierde y no se inventa un segundo verbo de tracker.

## Los dos pines que el plan agrega, y la ola única

`roles.INTERVIEWS`, una tupla a nivel de módulo con cada spec de `Interview`, reemplaza
la tupla hard-codeada de `tests/core/process/domain/test_rules.py:1054`.
`INTERVIEW_PARTICIPANT_SLUGS` sigue escrito a mano, así que el motivo de su comentario
sobrevive y su mensaje de falla nombra el único lugar que un spec futuro tiene que
editar. Sin esto, un cuarto spec deja la suite en verde mientras los dos asientos nuevos
caen al brazo `product_analyst_bug` de `turn_cut_retry_message` —"no entregues un
veredicto al que no llegaste"—, que es la instrucción equivocada para un asiento de
entrevista que el SDK cortó, produce un reintento plausible y no aparece en ningún
artefacto.

`tests/core/process/application/test_prompts_stack_flavour.py` no se toca:
`STACK_TOKENS` no se acorta, no se angosta y no se exime, y los roles se quedan en
`ROLES`. El prompt enseña **cómo decidir** y todo nombre concreto llega como **dato**:
el `.agentic/stack.yaml` vigente, que los roles leen con su propio `Read` en el checkout
del producto, y los ADRs y el Brief, que llegan por `## Product knowledge`. Los dos están
llenos de los tokens y ninguno es texto de prompt. Es ADR-0012 una capa más afuera:
prácticas en el sistema, materialización en el producto. Las descripciones de campo del
esquema quedan igual de agnósticas —poner los ejemplos ahí pasaría el test derrotando su
docstring— y la trampa del substring pelado (`ignore` matchea inglés corriente) va
nombrada en el brief.

Los tres roles llevan `reads_knowledge=True` y `reads_standards=True`; ni `{{brief}}` ni
`{{stack}}` ni un placeholder de señales. Las señales que se cumplieron más el texto de
`--reason` verbatim **son** la `description` del estado sintético, o sea `ORIGINAL`, el
mismo asiento que ocupa el pedido de una tarea. Así ningún prompt que esta tarea agrega
nombra un placeholder que no exista ya, `preflight.PLACEHOLDERS` queda intacto, y la
puerta por la que un placeholder suelto rompe `agentic run` para gym-saas no se abre
nunca. El plan dice qué devuelve una lectura de conocimiento greenfield —capacidades
vacías, glosario vacío, ADRs vacíos, el Brief, los estándares por default— para que
`reads_knowledge=True` contra un repo de un archivo no se lea como peso muerto.

**Una sola ola — corregido a dos.** La primera versión de este ADR sostenía una sola ola
sobre el argumento de que la cadena era ordenamiento de tipos y nada más: las tres
subtareas no comparten ningún archivo, y el plan es el artefacto que fija un tipo antes
de que nadie codee, que es para lo que existe el canal de invariantes (ADR-0015, adenda).
El plan escribe el dataclass `Signals` campo por campo, la firma del caso de uso y
`Tracker.open_issue(title, body) -> str` verbatim. Eso sigue siendo cierto para
`signals ∥ proposal`: `application/stack_review.py` toma el texto de la solicitud como
un `str` llano y nunca importa el tipo `Signals`, así que un desajuste entre esas dos
subtareas aflora recién en la pasada de integración, que ve la rama entera, y cuesta un
Developer en vez de dos barreras más y dos merges de dependencia (ADR-0004, quinta
adenda: 693 s en serie contra 179 s en paralelo). El único archivo compartido a la
vista, `tests/fakes.py`, es de B, que agrega `open_issue` y `open_stack_reviews` a los
dobles; las pruebas de CLI de C los consumen y no editan ese archivo.

El argumento es **falso para `verb`**: `cli.py` importa los módulos de las otras dos
subtareas **por nombre** —`application.stack_review.stack_review`,
`domain.stack_review.{body, body_refusal, marker, open_proposal}`,
`domain.stack_signals.{signals, fired, description}`—, y un import de un módulo que
todavía no existe es una suite roja en el propio presubmit de esa subtarea, que es una
dependencia de código y no de tipos: el canal de invariantes fija la forma de los datos
que cruzan la frontera, nunca si el archivo del otro lado ya está en la rama para que
`import` no reviente. La corrección: dos olas, `signals ∥ proposal` primero, después
`verb`, que además declara la dependencia explícitamente en su propio encabezado
("Depends on: `signals`, `proposal`"). `verb` edita este ADR en el mismo diff que la
corrige, porque el propio invariante de esta tarea lo exige.

## Riesgos conocidos

- **La supresión depende del índice de búsqueda de GitHub.** `gh issue list --search
  "<marcador> in:body"` no es determinístico: el índice es eventualmente consistente, así
  que un issue abierto segundos antes puede no encontrarse, y el tokenizador de la
  búsqueda no trata `<!--`, `:` ni `=` como literales, así que el marcador puede no
  matchear como frase. Las dos cosas producen justo el duplicado que la supresión existe
  para evitar, y en un repo recién creado no hay índice todavía. La forma que no tiene
  ese modo de falla es listar los issues abiertos (`--state open --json url,body`) y
  matchear el marcador **localmente**, como substring, en una función pura del dominio:
  sin índice, sin tokenización, y el marcador pasa a ser un contrato verificable byte a
  byte. Queda escrito acá para que un duplicado no se lea como que la supresión no se
  implementó.
- **El sistema no escribe el `.gitignore` de otro producto.** En gym-saas el documento
  queda sin trackear hasta que ese repo agregue su línea, y un `--fresh` ahí lo barrería
  a un commit de archivado. Se nombra con su arreglo de una línea en vez de inventar un
  mecanismo que edite el archivo de ignore de un producto, que es una superficie de
  escritura que esta tarea no tiene por qué abrir.
- **La plata que gasta una revisión es invisible en Grafana**, por la decisión de no
  emitir telemetría de arriba. Queda por corrida en el documento de la revisión.
- **La supresión bloquea el verbo hasta que el humano actúe.** Una propuesta rancia que
  nadie cerró impide abrir una nueva; es deliberado, y el rechazo imprime el sha del
  ancla para que la diferencia se vea sin abrir el issue.
