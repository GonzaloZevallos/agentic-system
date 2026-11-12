# Proceso

Los roles, la tarea y su documento, el camino y sus reglas, y la corrida de cada rol. Es
el dominio central del sistema: los otros dos contextos lo conocen, él no los conoce.

## Language

### Actores

**Rol**:
Un agente con responsabilidad fija dentro del proceso. Hay 19.
_Avoid_: agente (a secas, cuando se habla de uno concreto)
_Campo_: role — CrewAI, como atributo de su Agent; el resto del campo dice `agent` a secas

**Humano**:
Aprobador y destino de los escalados. Revisa en lote, una vez por día.
_Campo_: human-in-the-loop — estándar (LangGraph, OpenAI Agents SDK, Google ADK)

**PjM** (Project Manager):
El rol que clasifica, enruta, registra todo, controla loops y cierra.
_Avoid_: Lead, orquestador
_Campo_: de la organización de software humana; el patrón agéntico es supervisor (LangGraph) u orchestrator (Microsoft Agent Framework)

**PM** (Product Manager):
Dueño del PRD y del conocimiento de producto, que destila al aprobar el PRD y, en una feature con UI, otra vez en su validación final, conciliando las capacidades con el diff entregado (ADR-0075). El sistema escribe y commitea lo que PM devuelve; Tech Lead no revisa esa prosa. El commit queda en `close_docs`, y el predicado `rules.head_reviewed` lo acepta como cabeza revisada solo si su padre es la cabeza revisada y todas sus rutas están bajo `docs/product/`.
_Campo_: de la organización de software humana

**Pensador**:
Rol que produce: Product Analyst, UX Designer, UI Designer, Developer, Tech Writer, Reflector. En el código y en los prompts se llama `actor`.
_Campo_: actor — Reflexion (Shinn 2023), que lo define junto a Evaluator y Self-Reflection; generator en Cobbe 2021 y Lightman 2023

**Tech Writer**:
Pensador independiente, sin validador propio (ADR-0058). Escribe los textos de cara a una persona que una feature con `touches_copy` toca, sobre el flujo y el diseño validados, y después de la implementación los revisa. Sus aprendizajes pasan por el Reflector como los de los demás roles.
_Avoid_: redactor, copywriter (el rol revisa contra criterios del producto, no inventa una voz)
_Campo_: technical writer / content designer — del diseño de contenido

**UI Lead**:
Validador de UI Designer (ADR-0040): valida el diseño de pantallas sobre el flujo validado y, en la validación final de PM, juzga las comparaciones.
_Avoid_: Design Lead
_Campo_: sin término de arte

**QA Performance**:
Validador cuya fuente propia es la medición del sistema: el sistema mide la pieza integrada contra los presupuestos del plan (ADR-0059) y el rol lee la tabla, no corre ningún comando. Lo convoca Tech Lead en el plan (`performance`), existe por sí solo sin QA Lead, y su veredicto (`approved`, `returned`, `unverified`) puede devolver el trabajo a la integración o dejar el auto-merge en manos del humano (ADR-0060).
_Avoid_: QA Lead, benchmark
_Campo_: sin término de arte; lo más cerca es el performance gate de un pipeline de CI, que compara una medición con un umbral pero no tiene un rol que la lea

**Ejecutar**:
Escribir archivos del producto y correr comandos sin aprobación por comando. Solo Developer ejecuta, dentro de una jaula: su checkout, un tope de costo y lo que el sistema le niega aun así. Correr los chequeos del producto sin escribir no es ejecutar: Tech Lead, al revisar, lee el código y el diff y corre los chequeos sobre el test que respalda una objeción, y los entrevistadores escriben únicamente el glosario y los ADRs.
_Campo_: sin término de arte para el permiso; el campo nombra el mecanismo (tool use), no la autonomía que le concede

**Validador**:
Rol que revisa a un pensador aportando una fuente que el pensador no tuvo. Tech Lead es un validador que además escribe el plan, y luego lo revisa en cada subtarea y en la integración; el Tech Lead efímero valida ese mismo plan antes de que exista, trayendo un modelo distinto del que lo escribe. En el código y en los prompts se llama `verifier`, no `judge`: el judge opina y el verifier ejecuta, y desde ADR-0016 el Tech Lead corre los chequeos que respaldan cada objeción.
_Avoid_: revisor, judge
_Campo_: verifier — Cobbe 2021 y Lightman 2023 ("Let's Verify Step by Step"); Evaluator en Reflexion, critic en CriticGPT, LLM-as-a-judge en Zheng 2023

**Par**:
El rol que devuelve y el rol que recibe la devolución. Las devoluciones se cuentan por par; cuando hay subtarea, por subtarea.
_Campo_: sin término de arte

**Tech Lead efímero**:
Segundo Tech Lead que existe solo durante la entrevista técnica, con un modelo distinto del de Tech Lead y prompt de adversario. Entrevista a Tech Lead; no decide. No es un rol de la lista: es el entrevistador del pingpong.
_Campo_: sin término de arte

**Crítico del plan**:
Rol que revisa una sola vez el borrador en frío del Tech Lead bajo el brazo `plan_review=critic`, en contexto nuevo y con el modelo distinto del que escribe el plan. Es adversario: devuelve hallazgos con evidencia e invariantes propuestos, y el Tech Lead revisa el plan una sola vez. No es un rol de la lista: es el revisor del plan en esa ruta, como el Tech Lead efímero lo es en la entrevista.
_Campo_: sin término de arte

**Estimador previo**:
Rol sin herramientas que lee el pedido y estima el costo y los archivos de la tarea antes de que exista el plan. Corre en toda tarea cuya asignación nombra `plan_review`, para medir el control en la misma escala. No es un rol de la lista. Si falla, la tarea va a la entrevista.
_Campo_: sin término de arte

**Ruta de planificación**:
La decisión de qué etapa de solución toma una tarea, `interview` o `critic`, y lo que quedó registrado en `plan_route` del documento: estimación previa, umbral, ruta, si estuvo mal ruteada y cómo salió el crítico (`ran`, `failed` o `skipped`). `route` decide antes del plan con la estimación previa; `regate` lo repite con la estimación del borrador y manda a la entrevista a la que lo alcanza. Son funciones puras de `plan_route`; el nodo del plan solo las llama.
_Campo_: sin término de arte

**Developer integrador**:
El Developer que termina de unir las subtareas en la rama de tarea: lo que la integración incremental no pudo mergear solo, y el trabajo de integración que el plan declare. Corre solo si hay algo de eso; sin conflicto y sin trabajo declarado, la rama ya está unida y va directo a su revisión. La revisión de la pieza integrada, en cambio, corre siempre. Cuando la tarea tiene una sola subtarea, no hay integración: la rama de esa subtarea es la rama de tarea.
_Campo_: sin término de arte

**Rol proactivo**:
Rol con reloj propio que genera tareas o insumos sin que nadie se lo pida.
_Campo_: ambient agent — LangChain (Chase, enero de 2025): un agente que escucha un flujo de eventos en vez de un mensaje humano. Google ADK usa el nombre sin definirlo

**Reflector**:
El rol que, con una tarea ya cerrada, lee sus devoluciones, hallazgos, observaciones de diseño y decisiones humanas, y propone lecciones puntuadas para el rol al que cada una se le puede atribuir (ADR-0056). Solo propone: no escribe ningún playbook, y lo que propone lo decide el curador, que es código. No corre dentro de ninguna tarea.
_Avoid_: PjM (el que mejora el proceso en el diseño original), curador (como si fuera un rol: es código), crítico
_Campo_: reflector — ACE, Agentic Context Engineering (Zhang et al., 2025), que lo separa del curador; Reflexion (Shinn 2023) llama self-reflection a lo que un agente se dice sobre su propio intento

**Puerta del Reflector**:
Por dónde una tarea cerrada entra a una reflexión (ADR-0056). `Gate` es `review` o `decision`; `Door` es un documento con su gate. `review` admite toda entrega que aterrizó (`merged`, `merged_with_changes`, `auto_merged`) y cuenta desde su `closed_at`; `decision` admite solo las decisiones humanas de una tarea que terminó sin entrega (descartada, dividida, PR cerrado, escalada con el issue cerrado) y cuenta desde el fin de la tarea. Cada puerta cuenta desde `started_at` del ledger; sin ledger, desde `first_pass_floor`: la N-ésima más reciente de las dos puertas, con N = `reflect_max_tasks_per_run`.
_Avoid_: filtro de elegibilidad, cola
_Campo_: sin término de arte

**Cupo de chequeo**:
Los núcleos que una puerta de entrega tiene reservados mientras corre sus chequeos, anotados en un ledger compartido por toda la máquina (ADR-0070). Una cuarta parte de los núcleos es una reserva que sólo usan las puertas más cercanas a terminar (aterrizaje e integración); una puerta de subtarea nunca la toca. Cuando la espera se agota, la puerta toma el cupo mínimo y queda marcada como vencida: se anota por encima de la cota, sin quitarle nada a la reserva. Lo que espera un cupo no cuenta contra el timeout de los chequeos.
_Avoid_: slot, permiso, cuota
_Campo_: semáforo contando entre procesos (Dijkstra) con prioridades y envejecimiento; la reserva es la de un scheduler de lotes (Slurm, `reserved cores`)

**Playbook**:
Las lecciones de un rol en un producto, a lo sumo 12, como datos versionados en el repo del producto; entra al prompt del rol como una sección propia después de rellenar los placeholders. Solo cambia por deltas (agregar, fusionar, sumar una cita, retirar), nunca reescrito entero. Lo que `keep` no deja pasar espera en `candidates.yaml`, y lo que dejó de aplicar se archiva en `archived.yaml` sin borrarse. Con un playbook vacío el prompt y su hash son los de siempre.
_Avoid_: memoria, guía de estilo, contexto aprendido
_Campo_: playbook — ACE (Zhang et al., 2025), donde son bullets con contadores de ayuda y daño que un curador mantiene con deltas incrementales

**Lección**:
Una línea imperativa de a lo sumo 240 caracteres, para un rol, atada a una categoría (de hallazgo, de diseño o de decisión humana) y a las tareas cerradas que la citan, con una confianza de que es general y la cuenta de tareas donde apareció. Se rechaza lo que tenga salto de línea, backtick, llaves dobles o un enlace, y una que una persona ya rechazó no vuelve a proponerse.
_Avoid_: regla, tip, instrucción (la instrucción es el prompt)
_Campo_: item — ACE, el bullet del playbook; lesson learned en Reflexion

### Trabajo

**Tarea**:
Unidad de trabajo que entra al sistema. Siempre de un tipo: bug, feature o platform enhancement.
_Avoid_: ticket, issue (el issue es la bandeja, no la tarea)
_Campo_: task — estándar (CrewAI, Google ADK, Temporal); instance en SWE-bench

**Subtarea**:
Parte de una tarea que Tech Lead asigna a un Developer.
_Campo_: subtask — usado por MetaGPT y CrewAI, sin definición compartida entre frameworks

**Camino**:
La secuencia de roles que recorre una tarea según su tipo, solo de ida. Adónde vuelve una devolución lo decide el motivo, no el camino.
_Avoid_: flujo, pipeline
_Campo_: workflow — estándar (Microsoft Agent Framework, OpenAI, Google ADK, Temporal, CrewAI Flows); graph, con node y edge, en LangGraph

**Tamaño**:
Cuánto es la tarea según PjM al clasificarla: `small`, `normal` o `large`. Una pantalla nueva o un flujo nuevo nunca es `small`. Se guarda en el documento de la tarea (GH-591) y, junto con los riesgos y la etiqueta del dueño, decide el camino inicial; la parte 1 sólo lo registra y nada rutea por él todavía. Un tamaño ausente se lee como `full`, nunca como pequeño.
_Avoid_: complejidad, estimación (la estimación es dinero y archivos, de Tech Lead)
_Campo_: sin término de arte; en SWE-bench la dificultad es una etiqueta de la tarea

**Riesgo**:
Un id de una lista cerrada que PjM marca cuando la tarea toca una zona delicada. Son seis y el sistema los trae: `data-migration`, `money-rounding`, `authn-authz-sessions`, `tenant-isolation`, `user-data-deletion` y `public-api-break`; el producto suma los suyos como áreas de riesgo (glosario de Conocimiento) y la comparación es exacta. Un riesgo ausente, un id desconocido o un stack ilegible dan siempre `full`: una lista vacía de riesgos es un hecho que PjM afirma, nunca lo que queda cuando no se pudo mirar. Un riesgo marcado y válido hace `full` el camino salvo que el dueño ponga `path:light`.
_Avoid_: peligro, alerta
_Campo_: risk flag en los esquemas de revisión de cambios; sin definición compartida

**Promoción**:
El único cambio de `path` después de clasificar, y sólo de `light` a `full`: `task_path.promote` lo hace, una vez, y guarda de qué rol vino, por qué (`plan_over_cap`, `risk_area`, `owner` o `role`, más una línea de hasta 240 caracteres) y cuándo. No es una devolución ni un escalado: no toca ningún contador. Bajo la etiqueta del dueño `path:light` no aplica nada y lo dice. `initial_path` queda siempre junto a `path`, y `path_cause` explica sólo el camino inicial, nunca la promoción. No confundir `path` (el camino de la tarea, `light` o `full`) con el **Camino** de arriba (la secuencia de roles según el tipo).
_Avoid_: escalado, devolución, degradación
_Campo_: sin término de arte

**Ruta ligera**:
El camino `light` de una feature o un bug (GH-591): del clasificador va directo al plan, y se salta la definición, la validación de PM, el diagnóstico, la rama de diseño y los textos; los verificadores `final_validation_due`, `copy_review_due` y `qa_performance_due` dan falso, y el plan no convoca `performance` ni pasa de 2 subtareas (sin la etiqueta del dueño `path:light` el sistema lo **promueve** con `plan_over_cap`; con ella `plan_check_for` lo rechaza). La puerta de merge no cambia. Sólo `task_path.path_for` la decide y sólo `task_path.promote` la deja. Los roles (Tech Lead, Developer) y la red `risk_hits` no promueven: dejan un `promote_request` en su subtarea (o `plan_promote_request` en el plan), y el turno serializado del sistema aplica el primero en el orden fijo `risk_area`, `role`, `plan_over_cap`. Tras una **Promoción** el sistema manda la tarea a `define` o `diagnose` (`rules.promotion_route`) y no despacha subtareas hasta que corra. Un documento sin `path` es `full`.
_Avoid_: camino corto, fast path, atajo
_Campo_: sin término de arte

**Etapa de definición**:
Tramo del camino de feature entre Product Analyst y el PRD. Estrictamente secuencial.
_Campo_: sin término de arte

**PRD**:
El texto de PM que gobierna el resto de la tarea: comportamiento, flujo, diseño y, de los textos, lo que PM deja a la sección `Tech Writer` (ADR-0058).
_Avoid_: spec, requerimientos, definición única (el nombre anterior del mismo término)
_Campo_: de producto humano. `spec` en el mundo agéntico viene pegado a un flujo de archivos (GitHub Spec Kit: `/specify` → `/plan` → `/tasks`) que no es el nuestro

**Flujo**:
Lo que UX Designer escribe por cada job del PRD cuando la tarea toca UI: desde dónde empieza la persona hasta el job cumplido, cada paso con su pantalla, lo que puede salir mal y cómo se recupera, a dónde vuelve y cuántos toques lleva. Es la evaluación de la experiencia: la fricción se mide en pasos contra el job. Lo valida UX Lead y PM lo consolida en el PRD. Si la tarea no cambia el flujo, lo dice y nada más.
_Avoid_: user journey, crítica de pantallas (evaluar lo diseñado sin escribir el flujo)
_Campo_: user flow — del diseño de interacción; el conteo de pasos contra la tarea viene del modelo KLM (Card, Moran y Newell, 1980)

**Recorrido**:
Lo que hace UX Lead para validar un flujo: recorre el prototipo del producto en un navegador, paso a paso por cada job, y compara con el flujo escrito. Cada hallazgo nombra la pantalla y el paso. Es su fuente propia: el UX Designer escribió el flujo sin recorrerlo.
_Avoid_: revisión heurística (una lista de criterios sin navegar no es fuente propia)
_Campo_: cognitive walkthrough — Wharton, Rieman, Lewis y Polson (1994). Donde el flujo pasa por una pantalla que el prototipo no tiene, ese tramo se evalúa sobre el texto y el hallazgo lo dice ("sin prototipo")

**Revisión de textos**:
Lo que hace Tech Writer después de la implementación, en el nodo `verify`, junto con la validación final y los demás verificadores de la cabeza integrada: compara los textos del diff con los vigentes y con `WRITING_STANDARDS.md`. Sus hallazgos son `important` u `observation`, nunca bloqueantes: no devuelve ni forma par, y un importante se corrige en el pase final (ADR-0053), una sola vez por tarea. Solo ese nodo escribe y salda las filas de textos, que llevan el identificador `copy:<n>.<i>:<ubicación>`.
_Avoid_: validación de textos (no valida ni devuelve), aprobación de textos
_Campo_: content review / copy edit

**Textos vigentes**:
Lo que la revisión de textos compara contra el diff: la sección `Tech Writer`, intacta (el sistema nunca la reescribe), más una entrada aparte que calcula el sistema con los `proposed` de las filas de textos ya saldadas.
_Campo_: sin término de arte

**Diseño de pantallas**:
Lo que UI Designer escribe sobre el flujo validado cuando la tarea toca UI: por cada pantalla que la tarea toca, qué pantalla del diseño del producto aplica (o cómo armar una nueva con sus componentes, si no existe), sus estados y lo que el PRD obliga a cambiar. Lo valida UI Lead, PM lo consolida en el PRD y cada brief de front nombra las suyas. La fidelidad que pide es igual a ojo: misma estructura, jerarquía, tipografía, colores, espaciado y componentes.
_Avoid_: spec visual (el glosario evita spec), visual
_Campo_: design spec / redlines — del handoff entre diseño y desarrollo

**Observación al diseño**:
Fricción que UX encuentra en el diseño del humano sin que impida cumplir el job. No pausa la tarea: se sigue con el diseño tal cual, como supuesto, y la observación le llega al humano en el pull request para que decida después. Si el problema sí impide cumplir el job (falta una pantalla o un estado que el PRD exige), no es observación: es pregunta para el humano. El sistema nunca cambia el diseño del humano por su cuenta.
_Avoid_: hallazgo (lo de un validador sobre un pensador), sugerencia
_Campo_: sin término de arte

**Comparación**:
Una pantalla del prototipo y la misma pantalla de la app de muestra, lado a lado y del mismo ancho. La saca un comando del producto. El Developer mira las suyas antes de entregar y UI Lead las juzga en la validación final de PM.
_Avoid_: par de capturas (Par ya es el rol que devuelve y el que recibe), screenshot diff (no se compara pixel a pixel)
_Campo_: visual comparison; el vecino estricto es visual regression testing, que sí compara pixeles

**Flag**:
Marca de alcance sobre una tarea, como "toca UI" o "toca textos", tomada del vocabulario de flags del producto. Los propone Product Analyst y los valida PM; el set validado reemplaza al propuesto.
_Campo_: sin término de arte

**Dictamen**:
Lo que Product Analyst dice de un bug después de reproducirlo y compararlo con la definición vigente de la capacidad: bug, comportamiento esperado o sin definición. PM lo valida, y cuando no había definición decide y escribe la que faltaba. Es al camino de bug lo que el PRD es al de feature: lo que gobierna el resto de la tarea. En el código se llama `Diagnosis`, y la palabra de PM sobre él, `Ruling`.
_Avoid_: diagnóstico (el nombre en el código, no el concepto), veredicto (eso es lo de PM sobre el dictamen), triage
_Campo_: triage — de los trackers (Bugzilla, Mozilla): confirmar que se reproduce y asignar prioridad y dueño. Acá el dictamen es sólo la comparación contra la definición; prioridad y dueño los fija el camino

**Pieza integrada**:
El código de todas las subtareas unido en la rama de tarea y revisado por Tech Lead. Lo une el sistema, subtarea por subtarea apenas cada una se aprueba; el Developer integrador resuelve lo que esa unión no pudo hacer sola y el trabajo de integración que el plan declare.
_Campo_: sin término de arte

**Ola**:
El conjunto de subtareas que el sistema despacha junto: cada una con su Developer y su revisión, en paralelo con las demás. Desde el ADR-0068 ya no hay barrera entre olas: una subtarea arranca apenas sus dependencias están aprobadas, sin esperar a las hermanas, hasta la cota `max_parallel_subtasks`. Una devolución se atiende cuando el turno del sistema, que corre solo después de cada terminación, la dejó lista. El nombre queda para el conjunto que `rules.dispatched_subtasks` considera listo en un momento dado.
_Avoid_: lote, fan-out (el mecanismo, no el concepto)
_Campo_: super-step — LangGraph, tomado de Pregel: los nodos que corren en paralelo son el mismo super-step. Nombra el paso del grafo, no el conjunto de subtareas

**Rama de tarea**:
La rama del producto donde termina el código de una tarea, una por tarea. Cada subtarea trabaja en su propia rama, aislada de las otras, y el sistema las trae acá.
_Campo_: branch — de git, no del campo agéntico

**Archivado**:
Lo que el sistema hace con lo que dejó la corrida anterior de una tarea cuando esa tarea se reinicia de cero: commitea en cada worktree lo que nadie commiteó, los saca, y renombra la rama de tarea y las de sus subtareas bajo `archive/run<N>-<commit del sistema>/`, la de tarea primero. El documento de tarea se commitea solo ahí, por el sistema y después del renombre, sobre la rama de tarea ya archivada (ADR-0078). No borra nada, y por eso una corrida vieja se puede leer después; el issue recibe una línea diciendo bajo qué prefijo quedó. `N` es el número de archivado de esa tarea, y que la rama de tarea se renombre antes que las de subtarea es lo que hace visible un archivado que se cortó a la mitad: un reintento lo termina en el mismo prefijo en vez de abrir otro.
_Avoid_: borrado, limpieza (el archivado no borra), backup
_Campo_: sin término de arte

**Restauración**:
Lo que `agentic restore --issue N --from archive/run<N>-<commit>` hace para traer de vuelta una tarea archivada que ya estaba entregada y aprobada, sin una corrida nueva: copia las ramas desde el prefijo del archivo en los commits que el documento registra (la de tarea en el que su revisión aprobó, cada una de subtarea en su `reviewed_head`), escribe el documento con un registro en `restorations` que corta la lista `landing` (lo anterior al corte es de la corrida archivada y no decide nada), reabre el pull request y salda las filas de plan de `PM: final validation` por la misma regla que el aterrizaje. No renombra ni borra ramas del archivo, no empuja un commit que el pull request no llevara ya y no toca etiquetas ni el estado del issue. Una por tarea (otro prefijo se rechaza: `already_restored`), y una tarea restaurada nunca se reinicia: el aterrizaje sincroniza con master o escala. Un reintento termina lo que falte desde lo observado.
_Avoid_: desarchivado, rescate
_Campo_: sin término de arte

**Corrida**:
Una ejecución del sistema sobre una tarea, desde que entra hasta el cierre o el escalado.
_Avoid_: run, ejecución
_Campo_: run — estándar (LangSmith, Langfuse, OpenTelemetry); session en Google ADK y el Claude Agent SDK, thread en LangGraph

**Corrida de rol**:
Una ejecución de un rol dentro de una corrida. Una tarea tiene tantas como roles visitó,
contando repeticiones.
_Campo_: invoke_agent — OpenTelemetry GenAI semantic conventions, hoy en estado Development. Es el único nombre preciso que existe: LangGraph dice super-step, OpenAI y el Claude Agent SDK turn, CrewAI iteration, Google ADK agent call, SWE-agent step

**Corrida exenta del tope**:
Un hijo de merge o un hijo de `land` que sólo chequea: no corre ningún modelo y no cuenta contra el tope de concurrencia. A lo sumo una está viva por máquina y a lo sumo una se arranca por vuelta. Un aterrizaje que gasta modelo (`land_model`) no es una corrida exenta: cuenta contra el tope y, cuando no queda cupo, usa el Cupo reservado para aterrizaje.
_Avoid_: corrida libre, non-counting run
_Campo_: sin término de arte
Quién la recibe lo decide la Cola exenta.

**Cola exenta**:
El orden en que los merges y los aterrizajes `land_checks` de todos los productos de la máquina compiten por la única Corrida exenta del tope. Arranca el candidato que más tiempo lleva sin intento (el instante de su último intento, o de su primera vez visto); a igualdad, un merge antes que un aterrizaje, y luego la raíz y el número de issue. Sin registros, es el orden de siempre: todos los merges antes que los aterrizajes, el issue más bajo primero. Un merge que acaba de rechazarse queda al final. Cada poller publica la espera de su cabeza (con TTL) y cede ante la más antigua de otra raíz; un candidato en `backoff`, ocupado o en una vuelta retenida no se publica. Un merge rechazado por `ci_untrusted_no_dispatch` no se reintenta sobre el mismo head y master hasta que uno cambie o venza `EXEMPT_REFUSAL_BACKOFF`; el poller dice una vez, tras `EXEMPT_STARVED_AFTER`, quién tiene el cupo. Las constantes viven en `domain/exempt_queue.py`.
Desde GH-588 solo compite la Cabeza del orden de aterrizaje de cada producto; los demás pull requests entregados esperan sin candidato.
_Avoid_: turno, cola de merges

**Orden de aterrizaje**:
La lista única, por producto, de los pull requests entregados y abiertos, del aprobado hace más tiempo al más reciente (`landing_queued_at`, que escribe `deliver` si falta y `restore`, y solo el reinicio del aterrizaje borra). Solo el primero se integra, se revisa y aterriza; los demás esperan aprobados, con el `merge-tree` de solo lectura como único chequeo (ADR-0090). Es lo que decide `domain/landing_order.head`, y nadie más ordena.
_Avoid_: merge queue, merge train, cola de merges
_Campo_: GitHub merge queue, GitLab merge trains, Bors («not rocket science rule»), Zuul

**Cabeza del orden**:
El pull request que el orden de aterrizaje pone primero: el que tiene un pase de aterrizaje vivo y, si no, el de `landing_queued_at` más viejo (desempate por issue, sin sello al final), salvo que la tarea sea de una persona (`landing.waits_on_human`: escalada, `needs-human`, parada deliberada, o una negativa de `HUMAN_OWNED_REFUSALS`), que nunca es Cabeza. Solo la Cabeza puede ser candidata de `found` o `lands`.
_Avoid_: primero de la cola, turno
_Campo_: head of the queue

**Cupo reservado para aterrizaje**:
El único cupo por encima del tope de concurrencia que sólo puede usar un aterrizaje que gasta modelo (`land_model`) de una tarea entregada: ni un lanzamiento, ni una reanudación, ni una retoma lo usan. Lo ocupa el aterrizaje mientras vive y, después, la reanudación que lanzó, que el aterrizaje espera ver registrada antes de terminar; así las corridas vivas que cuentan llegan a lo sumo al tope más uno. No es una exención: el aterrizaje y su reanudación cuentan. No arranca mientras una pausa por límite de cuenta del mismo producto espera su calendario. Lo que ve el resto de los productos mientras ese aterrizaje espera es la Espera de aterrizaje.
En el código se llama `LAND_HEADROOM`.
_Avoid_: exento, slot extra, cupo libre
_Campo_: sin término de arte

**Espera de aterrizaje**:
El hecho que un producto publica en el registro de corridas mientras un aterrizaje con modelo (`land_model`) suyo espera cupo (`Deferred` `no_slot` o `land_queued`): lo reescribe cada vuelta del poller y lo borra la primera vuelta sin esa espera. Vence solo, sin que su escritor lo limpie, a los 15 minutos (`rules.LAND_WAIT_TTL`) o con otro arranque de la máquina, así que un poller muerto no retiene a nadie. Mientras otro producto tiene una vigente, este no arranca nada que cuente contra el tope por debajo de un aterrizaje (lanzamientos, reanudaciones, retomas); los aterrizajes, los cierres, los hijos exentos y las corridas lanzadas a mano no se retienen. Un producto nunca se retiene a sí mismo. Entre aterrizajes de productos distintos gana la espera más vieja (a igual `since`, la raíz de menor clave): el otro queda `land_queued`, publica su propia espera y conserva su lugar.
En el código es `rules.holds_launches` (qué `Deferred` la abre) y `rules.LandWait` (lo publicado).
_Avoid_: reserva, bloqueo, cola de aterrizaje
_Campo_: sin término de arte

**Espejo del tablero**:
Lo que el poller mantiene en el GitHub Project para que el dueño vea en un solo lugar en qué está cada issue de ambos productos: una Tarjeta por issue, escrita desde hechos que el poller ya calculó. Es una salida: ninguna decisión del sistema lee el valor de un campo del Project, sólo sus ids de nodo. Lleva la memoria de lo enviado y solo envía lo que cambió, así que una vuelta ociosa no llama al Project. Sin `AGENTIC_BOARD_PROJECT` no existe. El sistema nunca escribe Bloque ni Hito y nunca borra un ítem (ADR-0073).
En el código es `domain/board.py` y el puerto `Board`.
_Avoid_: dashboard, sincronización, tablero de verdad
_Campo_: sin término de arte

**Etapa**:
El estado de una Tarjeta, uno de siete: Corriendo, En PR (aprobada, esperando CI y merge), Trabada, Necesita humano, En cola, Fuera del sistema, Hecha. Se decide por precedencia (Hecha, Corriendo, Necesita humano, Fuera del sistema, Trabada, En cola) con un mapa cerrado de los valores de `Status` del documento; solo `board.card` la calcula. Su Bloqueo dice por qué una Tarjeta está Trabada o Necesita humano. El adaptador la encuentra como el campo SINGLE_SELECT `Etapa` o, si no existe, como el `Status` incorporado cuando trae las siete opciones; solo `_etapa_field` la resuelve.
_Avoid_: estado, status, columna
_Campo_: `Status` de GitHub Projects; la API conserva el nombre `Status` al renombrarlo

**Tarjeta**:
Los cinco valores que el Espejo del tablero escribe para un issue: Etapa, Bloqueo, Costo USD, Inicio y Fin. Su huella (un hash de los cinco) es lo que la memoria de lo enviado compara para saber si hay algo que enviar.
En el código es `board.Card`.
_Avoid_: ítem, fila, entrada
_Campo_: ítem de GitHub Projects

**Cercanía**:
Qué tan cerca de aterrizar está una tarea: el orden en que el poller arranca lo que cuenta contra el tope en una vuelta (reanudaciones, retomas y aterrizajes con modelo; el lanzamiento va último). Se ordena launch < definition < plan < development < review < delivered < land: una tarea con pull request está en `delivered`, una sin ella está en la fase más avanzada que alcanzó una de sus corridas (la más lejana, no la última), y una sin corridas está en `definition`. En el código es `rules.closeness`, un índice en `rules.CLOSENESS`; a igual cercanía decide el número de issue.
_Avoid_: prioridad, urgencia, proximidad
_Campo_: sin término de arte

**Revisión de stack**:
Lo que corre `agentic stack review`: Tech Lead en modo proactivo con el efímero como adversario, sobre un producto, sin tarea detrás — sin PjM, sin issue del que vino, sin rama, sin documento bajo `.agentic/tasks/`. Nunca cambia el stack directo: siempre termina en un issue de platform enhancement del producto, sin la etiqueta `agentic`, con el diff propuesto a `.agentic/stack.yaml`, el `CODING_STANDARDS.md` completo y el borrador del ADR del producto que explica la elección contra el Brief. Sin Brief no decide: termina en un issue `needs-info` que lo pide, y no propone nada (ADR-0025). Una propuesta que el propio camino dejó abierta bloquea una nueva hasta que el humano actúe (ADR-0034).
_Avoid_: cambio de stack, migración (ninguna corrida cambia código: siempre propone)
_Campo_: sin término de arte

### Cómo se piden cosas

**Consulta**:
Pregunta de un rol al dueño de una decisión, respondida dentro de la misma corrida, sin entregar la tarea. No es devolución ni escalado y no cuenta para ningún límite. Hoy: el Tech Lead consulta a PM durante la entrevista de solución lo que la definición no dice; PM responde en la misma corrida y su respuesta aclara la definición.
_Avoid_: pregunta para el humano (eso es lo que el dueño tampoco puede decidir)
_Campo_: sin término de arte

**Entrevista**:
Rondas de preguntas de un rol a otro para afinar una decisión antes de escribirla: el entrevistador pregunta y recomienda, el entrevistado decide. Hay dos: Product Analyst entrevista a PM sobre la definición, y el Tech Lead efímero entrevista a Tech Lead sobre la solución. Tiene tope de rondas; la transcripción entera queda en el documento de tarea. Lo que el entrevistado no puede decidir va a una consulta si la entrevista tiene consultado, y si no, al humano como pregunta. Una entrevista que escala se **retoma**, no se repite: la tarea reanudada sigue desde la sección y las sesiones que ya tiene, con dos rondas más de margen sobre el tope, nunca desde cero.
_Avoid_: pingpong, consulta (la consulta es una pregunta suelta; la entrevista es la forma del trabajo)
_Campo_: sin término de arte

**Pregunta para el humano**:
Lo que el dueño de una decisión marca como fuera de su alcance: ni el issue ni el conocimiento de producto (definiciones, glosario, ADRs) lo deciden, y sin la respuesta la definición cambia. Lo que un ADR decide nunca es pregunta para el humano; lo que no cambia la definición tampoco, es un supuesto. En la definición la marca PM entrevistado; en la solución, PM consultado. El Tech Lead nunca pregunta al humano. Se plantea una sola vez: nadie la responde durante la corrida, así que lo ya planteado no se repite en rondas siguientes ni el Tech Lead lo vuelve a consultar. Se retira cuando una ronda posterior la resuelve: el dueño la responde y la saca de la lista. Escala la tarea con las preguntas que siguen abiertas al terminar la entrevista.
_Avoid_: duda (una duda que PM resuelve con un supuesto no es pregunta)
_Campo_: sin término de arte: el mecanismo tiene nombre (interrupt, LangGraph) y la pregunta misma no

**Supuesto**:
Decisión que el dueño toma sobre lo que ni el pedido ni el conocimiento resuelven pero no cambia la definición, y deja escrita en su respuesta. No va al humano.
_Avoid_: pregunta para el humano
_Campo_: sin término de arte

**Decisión del humano**:
Lo que el humano responde a un escalado: todo lo que comenta en el issue después del último aviso del sistema. Entra al documento de tarea, la lee todo rol y no se vuelve a preguntar: vale para toda la tarea y pesa sobre lo que contradiga.
_Avoid_: respuesta, comentario
_Campo_: sin término de arte

**Handoff**:
El bloque "Para el siguiente rol" con que cierra cada sección.
_Campo_: handoff — de OpenAI (primero Swarm, después el Agents SDK), hoy vocabulario compartido con AutoGen y langgraph-swarm. Allá es la transferencia de control a otro agente; acá es el bloque de texto que se le deja al que sigue

### Cómo se entrega y se revisa

**Puerta de entrega**:
Lo que el sistema mira cuando un Developer entrega, antes de que la entrega cuente: que haya commit, que el árbol esté limpio y que los chequeos del producto pasen. Si algo falta, rechaza la entrega con el motivo dentro del mismo turno, como mucho dos veces; después la deja pasar con el resultado de los chequeos escrito por el sistema en la sección del Developer. Un rechazo de la puerta no es devolución ni fallo de rol. Si el producto declara `delivery_checks`, la puerta corre sólo ese nivel rápido (con formas afectadas si las hay) y el resto corre una vez por tarea (*Chequeo completo de la tarea*). Un chequeo rojo que no agotó su tiempo se reintenta una sola vez dentro de la misma llamada, antes de rechazar; si pasa al reintento es un Flake y la entrega pasa (ADR-0066).
En el código se llama `Presubmit`, el port, y `CheckoutPresubmit`, la implementación sobre el checkout de un Developer.
_Avoid_: gate, validación, hook (el mecanismo, no el concepto)
_Campo_: presubmit check — Software Engineering at Google, cap. 20: "an analysis that blocks committing a pending code change". required status check en GitHub, gated commit en Azure DevOps, admission control (validating, no mutating) en Kubernetes

**Alcance de revisión**:
Lo que cubre una pasada del revisor sobre una cabeza que avanzó desde la última aprobada, y lo decide una sola función pura, `landing.review_scope`, desde la cadena de primer padre de la punta aceptada a la cabeza (ADR-0089). Tiene tres valores. `carried`: el merge del upstream fue limpio y no hay commits propios después, la aprobación avanza sin revisión. `resolution`: hay una resolución de merge o commits propios después del primer merge, el revisor recibe solo ese texto (el remerge-diff de cada merge más el diff de los posteriores) y su veredicto cubre solo eso. `full`: todo lo demás, con la causa que lo dio (`first`, `no_merge`, `rewritten`, `own_before_merge`, `foreign_merge`, `unreadable`, `no_remerge_diff`, `over_cap`, `renumber`). Cada pasada deja una fila en `review_scopes` (`{unit, head, base, scope, cause, at}`), que escribe solo `landing.scope_patch` en el mismo patch que mueve `reviewed_head`; nada decide a partir de ella.
En el código se llama `ReviewScope` y `ScopeCause`, el veredicto `ScopeVerdict`, y `RESOLUTION_LINES_MAX` el tope del texto.
_Avoid_: acarreo (el valor `carried` es un alcance, no el concepto), revisión parcial

**Conjunto de verificadores**:
Los verificadores de solo lectura de la cabeza integrada que aplican a la tarea (`full_checks`, `copy_review`, `final_validation`, `qa_performance`), que arrancan juntos en el nodo `verify` sobre la misma cabeza; `deliver` espera a todos. Una devolución lleva los hallazgos de todos los que devolvieron (`returned_by` el primero en orden fijo, `returned_with` los demás), un hermano que escala conserva los deltas de los demás, y los archivos de capacidad que aprobó `final_validation` se commitean una sola vez al final (`pending_close_docs`), solo si ninguno devolvió (ADR-0084).
En el código se llama `verify` (`application/verifiers.py`) y `rules.combined_return`.

**Chequeo completo de la tarea**:
La corrida de todos los `checks` del producto, sin forma afectada, una sola vez sobre la cabeza integrada y antes de abrir el pull request, para un producto que declara `delivery_checks` (ADR-0064). La hace el sistema, sin rol ni costo de modelo, en el checkout de la tarea, que exige limpio y deja limpio. Una corrida roja reabre la integración con una fila por chequeo fallido; una verde la asienta. La entrega empuja sólo con una corrida pasada de esta cabeza (o de una que difiere sólo en el documento de la tarea): sin ella no empuja y vuelve a correr. Si el producto declara `ci:`, el chequeo completo es el CI de GitHub sobre la cabeza integrada (ref `agentic-ci/<id>`) y no corre `checks` en local; si el CI no se puede usar (el push falló sin remedio, es decir una negativa permanente, o un fallo desconocido o transitorio que agotó sus reintentos (ADR-0080, adenda GH-487); ninguna corrida empezó, un job no arrancó, `gh` ilegible), la tarea escala con clase `ci`: no corre `checks` en local, no escribe registro ni devuelve la tarea, y al reanudar vuelve a entrar al nodo sobre la misma cabeza (ADR-0064, adenda GH-376 y GH-462). Con `ci_dispatch`, una cabeza que toca `.github/` se publica antes de cada despacho en `refs/agentic-ci-dispatch/<id>` (nunca bajo `refs/heads/`) y esa ref sólo la borra el barrido; una corrida despachada cuyos jobs fallidos cayeron en `Set up job` o en el checkout es `checkout_failed`: CI no usable, escala con clase `ci` y nunca se cobra al Developer (ADR-0080, adenda GH-504). Sólo una cabeza que toca `.github/` en un producto sin `ci_dispatch` corre el nivel local (`ci_fallback: untrusted`). Una cabeza de confianza cuyos pushes no reciben corrida ni tras el republish (una rama anterior al disparador `agentic-ci/**`) se juzga con el mismo despacho cuando el upstream traído declara `ci_dispatch`; los nombres de `ci:` se leen en esa misma revisión y el registro lleva `dispatch_cause: no_run` y `stack_at` sin que nada decida con ellos (ADR-0080, adenda GH-531). Si en esa corrida un chequeo agota su tiempo y el stack lo mapea (`ci_covers`) a un job de CI, la fila queda diferida: no es un fallo ni un pase, el registro lo anota en `deferred`, no devuelve la tarea ni asienta hallazgos, y el merge exige que ese job esté verde en la cabeza del pull request (ADR-0064, adenda GH-453). Con `ci:`, la revisión de la unidad final puede haber empujado ya esa cabeza: ver *Empuje especulativo* (ADR-0080, adenda GH-465).
Corre dentro del conjunto de verificadores: solo y primero cuando no espera nada lento, junto con los demás cuando espera el veredicto del CI (ADR-0084). En el código se llama `full_checks`, el verificador y el registro del frontmatter.
Para un producto que declara `ci:`, la puerta de merge no corre la suite en la máquina del dueño: si ningún pase registrado cubre la cabeza sobre el master actual, pide a GitHub una corrida fresca de la cabeza fusionada con ese master (`merge_ci`) y su veredicto decide (ADR-0080, adenda GH-463). Un par que terminó `no_verdict` sobre el master actual se relee antes de rechazar `ci_no_verdict`: una corrida que terminó después de la gracia decide (ADR-0080, adenda GH-552). La relectura escribe después de leer y solo si es terminal (GH-564): un verde o un rojo decide, un `timed_out` o `unreachable` estampa `reread_at` y `reread_state` y gasta la relectura, y una corrida todavía en curso no escribe nada y la puerta espera (`ci_wait`). Una corrida viva sobre la cabeza nunca se clasifica `no_run`.
Si al construir ese par git encuentra un conflicto, queda registrado como hecho de esa cabeza y ese master (`merge_conflict`) y gana sobre el `mergeable` de GitHub: `UNKNOWN` es no leído, nunca limpio; la ronda siguiente rechaza `merge_conflict` y el aterrizaje lo resuelve, sin contador (ADR-0080, adenda GH-492).
_Avoid_: suite final, gate completo

**Corrida superada o cancelada**:
Una corrida de GitHub Actions cuyo rojo no dice nada del código. Superada: la cabeza tiene otra corrida con run id mayor (comparado como entero) y un check del mismo nombre completo (la expansión de matriz o la forma truncada tal como vino, no el nombre declarado), sea cual sea su conclusión; su check nunca decide, porque el veredicto de una cabeza es la última corrida de cada nombre (ADR-0080, adenda GH-606). Con el mismo run id (un re-run) gana el `started_at` mayor. Cancelada: su check suite terminó `CANCELLED` (su run id está en las suites que el adaptador leyó así); nunca desplaza a una anterior ya decidida, y si es lo único que tiene un nombre, el nombre queda pendiente y sus checks van en `cancelled`. Ambas las define código puro con datos estructurados (nunca logs ni prosa); un check cuyo run id no se lee no se descarta ni descarta a otro. Un rojo de una corrida superada o cancelada nunca es una devolución, una fila de hallazgo, un registro `full_checks` fallido ni un `merge_ci` rojo ni sale en la evidencia de `Auto-merge refused`: se espera la corrida más nueva, se reintenta una sola vez por cabeza (`ci_cancel_rerun`, un hecho, no una lista) o se escala con clase `ci`. El aterrizaje y la lectura de la base la leen como pendiente, nunca como roja.
En el código se llama `ci.current_runs`, `ci.cancelled_run`, `CiVerdict.cancelled`, el estado `cancelled` de `FullTierVerdict` y el reintento, `ci_cancel_rerun`.
_Avoid_: corrida roja, fallo de CI (una corrida cancelada no falló)

**Empuje especulativo**:
El push de la cabeza de la unidad final (la integración, o la subtarea única) a `agentic-ci/<id>` que hace su revisión antes de lanzar al Tech Lead, para que el CI corra mientras él lee. Sólo en un producto que declara `ci:` y `delivery_checks`, con una cabeza confiable, sin pull request y sin escalado. No salta ningún chequeo: el veredicto sigue siendo el de `ci.full_tier_verdict` y `full_checks` lo consume sólo si la cabeza aprobada es el mismo sha; si no, o si la corrida se canceló o nunca apareció, publica y espera como siempre. Una devolución descarta el registro. El push se reintenta con la misma regla que todo paso de git del camino del CI y, si aun así falla, sólo deja una línea en el log (ADR-0080, adenda GH-487). El estado guarda el hecho del push (`ci_speculation: {head, pushed_at}`), nunca un veredicto, y el registro `full_checks` gana `speculative: true` cuando consumió esa corrida (ADR-0080, adenda GH-465).
En el código se llama `speculation` (`domain/speculation.py`: `push_due`, `consumable`, `event_lost`) y `ci_speculation`.
_Avoid_: corrida local especulativa (no existe: sólo corre GitHub), caché de CI (se reutiliza por sha, no por contenido)

**Chequeo exigido por el plan**:
Un nombre de chequeo del CI que los criterios de hecho del issue necesitan en verde sobre la cabeza del pull request. Lo declara el plan (`ci_required`) y es distinto del `ci` del stack. Es condición necesaria de un merge, nunca suficiente: un CI verde que cubre sólo chequeos exigidos por el plan no decide el merge, lo deciden los chequeos locales (ADR-0045, adenda GH-371).
En el código se llama `ci_required`, `ci_plan_only` y `ci_gate_names`. El nombre es el de una corrida de chequeo del CI o el id de un job de matriz, que nombra todas sus expansiones: `pytest` vale por `pytest (areas-1, tests/…)` y por las demás (GH-535, que reemplaza la lectura de GH-485). Ya al escribir el plan, `plan_check_for` rechaza un nombre que los workflows de la base de la tarea prueban que no es de una corrida (el id de un job con otro `name:` literal; el id de un job de matriz sólo si su `name:` literal es otro), salvo que la tarea toque ese workflow; lo lee el módulo puro `domain/ci_workflow.py` (`check_run_names`) y la razón nombra el job y el nombre que sí lleva la corrida. Todo lo indecidible (`uses:`, un `name:` con `${{`, YAML ilegible) pasa y queda al chequeo en tiempo de ejecución.
_Avoid_: chequeo declarado (eso es el `ci` del stack), criterio de CI

**Ruta exigida por el plan**:
Una ruta que los criterios de hecho del issue necesitan cambiada: el diff integrado toca al menos una de las que el plan declara (`required_paths`), o la revisión de la unidad revisada agrega una fila bloqueante del sistema. El plan no puede exigir una ruta que el sistema nunca versiona (`rules.unstageable`). Después de una decisión del humano, la revisión de la unidad revisada puede mover o quitar rutas, nunca agregar: cita la última decisión sin usar y la transcribe, y el registro (`required_paths_revisions`) guarda la lista que revisó (`revises`). La lista vigente es la de la última revisión cuyo `revises` es igual a la del plan; si no hay, la del plan. El texto del plan no se reescribe.
En el código se llama `required_paths`, `forbidden_paths`, `path_revision` y `required_paths_in_force`.
_Avoid_: ruta obligatoria del issue, criterio de archivo

**Nombre de chequeo sin corrida**:
Un nombre exigido (del plan o del `ci` del stack) que ninguna corrida de chequeo de la cabeza lleva mientras todas las corridas de workflow de esa cabeza están `completed`. Es un defecto de planificación, no un CI lento ni un fallo del Developer: no escribe registro `full_checks`, no abre fila de devolución ni suma contador. Lo decide una sola función pura, `ci.unreported(report, names, confirmed)`, que sólo habla cuando `report.runs_completed` es `True` y el llamador confirma: dos lecturas completas iguales en el nivel completo, `ci_waited_s >= 2 * CI_POLL_S` en la puerta de merge y el aterrizaje. Cualquier estado desconocido es espera. El nivel completo escala con la clase `ci` y la causa `unknown_names`; la puerta de merge rechaza con `ci_unknown_names`, el aterrizaje escala una vez por (cabeza, nombres) sin gastar presupuesto, y la revisión y la línea del poller dicen «no es un nombre de chequeo de esta cabeza» en vez de `pending`. La coincidencia es exacta primero; si ninguna corrida lleva el nombre exacto, es una expansión de matriz `<nombre> (<valores>)`; GitHub puede truncarla a `<nombre> (<valores>...` (o `…`) sin el paréntesis de cierre, y esa forma también cuenta (GH-556). Una corrida pertenece a un solo nombre. Un nombre que sólo coincidió por expansión nunca está en verde mientras `report.runs_completed` no sea `True`.
En el código se llama `ci.unreported`, `unknown_names` y `ci_unknown_names`.
_Avoid_: chequeo pendiente (un nombre que nadie reporta no está pendiente), fallo de CI

**Verificación declarada**:
Una corrida larga que el producto declara en `verifications` de su stack (`{command, wall_s}`) y que el Developer pide a la herramienta `run_check` por su nombre, porque dura más que un paso en primer plano. El sistema la corre hasta su tope (`wall_s`, nunca sobre el techo `verification_wall_max_s` que fija el dueño: un valor mayor se rechaza en el arranque, jamás se recorta) y le devuelve el exit code y el log. Dentro de ella, **`not_ready`** es el resultado de una corrida que salió con el código que el producto declara en `not_ready_exit` (por convención 75): se reintenta una vez, y si la carga de la máquina estaba saturada en los dos intentos es `unverified`. Ni `not_ready` ni `unverified` son un fallo del código: nunca entran en `flakes`, nunca abren un issue de flakiness ni cuentan como devolución. Un producto sin `verifications` se comporta como antes. Ver ADR-0065, adenda de 2026-10-31.
_Avoid_: job en background (el modelo no tiene uno: la herramienta espera), chequeo largo (un chequeo es lo que corre la puerta), flake (un `not_ready` no es uno)
_Campo_: long-running verification / readiness probe — EX_TEMPFAIL (75) como convención de «reintentar más tarde»

**Presupuesto de tamaño** (`size_budget`):
Cuánto código escrito a mano admite un pull request: como mucho 15 archivos que no sean tests y 400 líneas agregadas que no sean tests, más un tope propio de 600 líneas agregadas de tests; los generados (lockfiles, `docs.paths` y `generated:` del stack, leídos del commit base) nunca cuentan, y documentación, ADRs, README y prompts sí. Los números viven solo en `size_budget`; `rules.estimate_fits` es la única puerta y la línea de 150 USD queda como chequeo secundario. Mientras no haya 10 tareas con plan y medida, lo declarado a mano se juzga por 1,5. Al entregar el sistema mide el diff y guarda `planned_size` y `actual_size`. Un plan anterior, sin campos de tamaño, se juzga con la línea de costo y 40 archivos. Ver ADR-0093.
_Avoid_: límite de archivos, tope de tamaño

**Presupuesto de chequeo**:
Los segundos que un producto declara para un chequeo suyo. Se compara una sola vez, en el *Chequeo completo de la tarea*, contra la duración de la fila que decidió el veredicto de ese chequeo (la del reintento verde si fue un Flake; un pase reutilizado cuenta con la duración guardada; un chequeo que agotó su tiempo nunca se compara), y nunca en la puerta de entrega, la de merge ni el aterrizaje. Si un chequeo lo pasa, el sistema escribe el exceso en el registro del chequeo completo; no devuelve ni bloquea nada, y no es una fila de la tabla de hallazgos. Un producto sin presupuestos se comporta como antes.
En el código se llama `check_budgets`, la clave del stack, y `over_budget`, el campo del registro `full_checks`.
_Avoid_: límite de tiempo (el *timeout* de un chequeo corta una corrida; el presupuesto sólo mide), presupuesto por nivel (el tiempo de pared de un nivel no es la suma de sus chequeos)
_Campo_: performance budget — web.dev y Lighthouse CI, un tope declarado que una corrida mide; acá el tope es del tiempo de un chequeo de la suite

**Resultado reutilizable**:
El resultado de un chequeo que pasó, guardado bajo el nombre del chequeo, su comando exacto, el árbol (`HEAD^{tree}`) de un checkout limpio sobre el que corrió y una huella de su entorno (comandos de setup, versión de stage0, presupuesto). Cualquier puerta, de entrega, de merge o de aterrizaje, que pida el mismo chequeo con la misma clave toma ese pase en vez de correrlo de nuevo. Sólo se guardan pases, sobre árbol limpio, sin que el entorno haya derivado desde el setup, y valen 24 h; un fallo nunca se reutiliza, porque puede ser flaky. Lo firma un secreto de la máquina: una entrada sin firma válida es como si no existiera. Una fila de reintento (`rerun: true`) nunca se guarda: el primer intento falló, así que de ese chequeo no queda nada.
En el código se llama `check_key` y `env_drifted` (dominio, `domain/check_results.py`), `CheckResults`, el port, y `FilesystemCheckResults`, el almacén; una fila reutilizada lleva `reused: true`.
_Avoid_: caché de chequeos (un resultado no se invalida por tiempo sino por contenido), memoización
_Campo_: content-addressable build cache — Bazel remote cache, Nix; acá la acción es un chequeo y su entrada, el árbol de código más el entorno

**Flake**:
Un chequeo que falló y pasó al reintentar sobre la misma cabeza, dentro de la misma llamada de una puerta (ADR-0066, ADR-0085). El reintento corre solo los tests que fallaron (la forma `rerun_checks`) en la puerta de entrega, la de merge y el chequeo completo, y solo si `failed_ids` los encontró todos (`ids_complete`); si no, o si la forma queda roja sin resolver, corre una vez el comando entero. La forma puede llevar `{failed_ids}`, que el sistema rellena con los ids que `failed_ids` leyó, cada uno entre comillas de shell; un id que el sistema no puede insertar con seguridad hace que corra el comando entero. La puerta lo deja pasar, y queda registrado en `flakes` con el chequeo, la cabeza, la puerta, la fecha `at` (ISO UTC, que pone la aplicación) y los ids de las pruebas que fallaron la primera vez, que son los que `failed_ids` leyó con el patrón `test_id` del producto; el *Bloque de flakes del CI* aporta registros del mismo tipo. Solo se abre o comenta un issue en el merge, nunca dentro de un intento del Developer, y únicamente cuando una clave `(chequeo, test)` llega a `flake_issue_tasks` tareas distintas dentro de `flake_issue_days` días; por debajo del umbral no hay ninguna llamada al tracker. Registrarlo nunca cambia, demora ni rechaza una entrega o un merge. Un chequeo que agotó su tiempo no se reintenta y nunca es un Flake; uno que falla dos veces es un fallo, con la salida del reintento.
En el código se llama `flake_verdict`, `rerun_command`, `rerun_resolved` y `check_runs` (dominio, `domain/presubmit.py`), `FlakeRecord` con su reductor `add_flakes` (`domain/task.py`), `flake_key` y `keys_over_threshold` (`domain/landing.py`) y `file_task_flakes` (`application/landing_flakes.py`); una fila de reintento lleva `rerun: true`.
_Avoid_: test intermitente
_Campo_: flaky test — Software Engineering at Google, cap. 11; retries de Playwright y `pytest-rerunfailures`, que reintentan pero no registran la cabeza


**Bloque de flakes del CI**:
La línea `agentic-flakes: {"v":1,"check":"<nombre>","tests":["<id>",...]}` que un job de CI imprime con los tests que su propio reintento encontró flaky (ADR-0085). Es evidencia de la cabeza bajo prueba, no un veredicto: el chequeo completo (en su camino de CI) y la puerta de merge lo leen de los jobs de `ci:` solo si el reporte de CI es de la cabeza que juzgan, y de él salen únicamente `FlakeRecord`. Gana el último bloque por (job, chequeo); un chequeo que el stack del commit base no declara, un id largo o con salto de línea, comilla invertida, `<!--`, `-->` o `{{`, y una línea malformada se descartan; un bloque ausente o ilegible no registra nada y no cambia ningún veredicto.
En el código se llama `FLAKE_BLOCK_PREFIX`, `CiFlakeBlock`, `clean_flake_ids` y `parse_flake_blocks` (dominio, `domain/ci.py`; `scripts/ci_flakes.py` escribe el bloque con la misma limpieza), `CiLogs.flake_block` con `GhCiLogs` (`adapters/github/ci_logs.py`) y `ci_flakes.block_records` (`application/ci_flakes.py`).
_Avoid_: reporte de flakes
_Campo_: sin término de arte; lo más cerca es el reporte de tests flaky que algunas CI publican (JUnit con reintentos), que acá es una línea de log sin autoridad

**Nota al tracker**:
Una escritura informativa a GitHub (un comentario o una etiqueta que se quita) cuyo fallo no termina la corrida (GH-389). Si la llamada falla después de los reintentos de `gh`, no lanza: devuelve el fallo, el sistema lo registra en `tracker_failures` del documento de la tarea y la corrida sigue. El registro es una fila `{target, cls, at}` con `target` `comment` o `label` y una clase cerrada (`timed_out`, `unreachable`, `transient`, `rate_limited`, `refused`) que sale del tipo del error, nunca de lo que `gh` imprimió; una fila por `(target, cls)` y diez como máximo. Un `gh` que no está instalado no es una Nota: la corrida falla como siempre. El aviso de escalado, el split, el discard, el solo-tracker, el comentario de listo para revisión, el push y la creación del PR nunca son una Nota: siguen fallando el paso.
En el código se llama `Tracker.note` y `Tracker.drop_label` (puerto), `TrackerFailure` y su reductor `add_tracker_failures` (dominio, `domain/task.py`).
_Avoid_: comentario opcional, best effort suelto (la lista de llamadores es cerrada)
_Campo_: best-effort notification; la contraparte de un *fire-and-forget* con registro del fallo

**Ganancia inerte del master**:
Lo que el master ganó desde que corrió un pase registrado, cuando ningún camino de esa ganancia puede cambiar el resultado de los chequeos: todos coinciden con un glob que el producto declara en `merge_inert` (leído de la cabeza del PR) y ninguno cae en el conjunto nunca-inerte (`.agentic/stack.yaml`, `.agentic/**` salvo `.agentic/tasks/*.md`, `.github/**`, `CODING_STANDARDS.md`, manifiestos y locks). Si lo es, el pase sigue valiendo sobre el master nuevo y el merge no corre la suite; ante cualquier duda (diff ilegible, master que no es ancestro, ganancia vacía, stack ilegible) no lo es y se corre. El sistema no declara nada por defecto (ADR-0045, adenda GH-353).
En el código se llama `gain_inert` y `masters_covered` (dominio, `domain/master_gain.py`); la ganancia la lee `Workspace.gain_paths`.
_Avoid_: diff del master que no toca la tarea (la regla de solapamiento, que ADR-0045 rechaza), caché de merge
_Campo_: sin término de arte; lo más cerca es el path filtering de CI (`paths-ignore` en GitHub Actions), que mira los archivos de un cambio para saltar un trabajo, aunque acá la lista la declara el producto y la miran sólo los caminos que el master ganó

**Testigo del checkout**:
El comando con que un producto dice dónde vive el código que sus chequeos acaban de medir: la Puerta de entrega lo corre en el checkout del Developer, antes de los chequeos y dentro de la misma mirada (hay commit y el árbol está limpio), y exige que la última línea no vacía de su salida sea una ruta absoluta que, resuelta, caiga adentro de ese checkout. Lo declara el producto en su stack, es opcional, y un producto que no lo declara tiene la puerta de siempre, byte a byte. Afirma menos de lo que parece y eso está escrito: prueba que el testigo, corrido en el mismo checkout y con el mismo entorno que reciben los chequeos, resolvió adentro; no prueba nada sobre el proceso que corrió `pytest` un minuto antes. Rechaza una sola vez, con su propio contador: el presupuesto de rechazos de los chequeos no se toca. Un desacuerdo que la puerta dejó pasar queda igual escrito en la sección del Developer, que es lo único que cruza hacia el revisor.
En el código se llama `witness`, la clave del stack, y `witness_refusal`, el veredicto puro en `domain/presubmit.py`.
_Avoid_: sonda, probe, chequeo (un chequeo es lo que el producto declara bajo `checks`; éste no lo es y no lleva su métrica)
_Campo_: witness — de la verificación formal y del testing basado en propiedades, donde un testigo es la evidencia que exhibe una propiedad; acá es el comando que el producto declara para exhibirla

**Guarda de esquema**:
Lo que el sistema mira antes de que la llamada de un rol que ejecuta a la herramienta de entrega estructurada llegue al CLI: si `schemas.validate` la rechazaría, deniega dentro del mismo turno con las claves exactas que le faltan, para que el modelo no vuelva a emitir la misma forma inválida a ciegas (issue #88). Deniega como mucho una vez por corrida; agotada esa vez deja pasar la entrega inválida, que sigue el camino de siempre: fallo de rol de esquema, reintentado. No decide nada por su cuenta — la validez sigue siendo de `schemas.validate` — y no es la Puerta de entrega: esa mira el commit, el árbol y los chequeos del producto, y esta guarda no mira nada de eso.
En el código se llama `SchemaGuard`, el port, y `RoleSchemaGuard`, la implementación que lo envuelve; corre antes que la Puerta de entrega, en el mismo `PreToolUse` sobre la herramienta de entrega.
_Avoid_: validación (el mecanismo lo es; el concepto es el rechazo con reintento adentro del turno), presubmit (esa puerta es otra)
_Campo_: sin término de arte

**Puerta de arranque**:
Lo que el sistema mira antes de gastar un peso, cuando una corrida arranca: que el producto no esté en el repositorio desde el que corre el sistema, que el vocabulario de flags cargue y que el prompt de cada rol renderice. Cualquiera de las tres que falle corta ahí con el motivo, y cortar no cuesta nada. Un aviso no es un rechazo: un stage0 con cambios sin commitear se dice y la corrida sigue, porque la deja incomparable pero no equivocada.
En el código se llama `preflight`, y el rechazo por el repositorio propio lo pregunta también el composition root: el grafo se puede invocar sin pasar por la CLI.
_Avoid_: validación, preflight (el nombre en el código, no el concepto)
_Campo_: preflight check — de aviación, vía CI/CD (Helm y los operadores de Kubernetes lo usan así). El "preflight request" de CORS es otra cosa

**Revisión**:
Pase de Tech Lead sobre la entrega de una subtarea o de la pieza integrada, con hallazgos y un veredicto que se deriva de ellos: aprobada o devuelta. Una subtarea tiene tantas revisiones como pases recibió. El motivo de una revisión devuelta es el de la fila que la devolvió, sea del revisor o del sistema.
_Avoid_: review, code review (el nombre de la skill, no del pase)
_Campo_: code review — estándar

**Revisión incremental**:
Revisión que arranca del head revisado en el pase anterior, no de la base de la subtarea, y toma la devolución anterior como lo que hay que verificar. Toda revisión después de una devolución es incremental.
_Campo_: sin término de arte; GitHub lo ofrece sin nombrarlo, como "changes since your last review"

**Head revisado**:
El commit hasta donde llegó la última revisión de una subtarea. Se guarda como el id completo del commit; uno anterior puede estar abreviado, y se compara con `same_commit`, nunca por igualdad de texto. Un resume desde el documento que encuentra la rama de la tarea en un descendiente estricto del head revisado de la integración, sin pull request, toma los commits posteriores como un incremento y manda solo la integración de vuelta a una revisión incremental; una segunda vía (`reintegrate`, GH-408) acepta la rama de la tarea movida solo por merges de subtareas aprobadas en su head revisado, en la línea del primer padre, mientras otras subtareas están devueltas: la integración vuelve a `pending`, conserva su head revisado y depende de las subtareas no aprobadas (`rules.reintegrate_patch`); cualquier otro head movido se rechaza (`rules.integration_increment`).
_Campo_: sin término de arte

**Integración incremental**:
Traer cada subtarea a la rama de tarea apenas se aprueba, en vez de todas juntas al final. Lo hace el sistema, en su turno serializado después de cada terminación (ADR-0068). Un merge que no aplica no es un fallo: queda anotado para el integrador.
_Campo_: de la integración continua, de la que toma la idea (integrar seguido y chico) y no el mecanismo

**Invariante**:
Regla del plan que toda subtarea cumple, verificable sobre el diff de una sola. La propone el Tech Lead efímero desde lo que destapó la entrevista; Tech Lead la adopta en el plan o la rechaza con motivo. Llega al brief de cada Developer y la revisión la verifica como verifica el brief y las costuras. Es el único canal entre revisiones que funciona en la primera ola, porque está escrito antes de que nadie codee.
_Avoid_: regla, restricción, contrato
_Campo_: invariant — de Design by Contract (Meyer): una condición que se sostiene en todo momento. Allá es sobre una estructura de datos o un loop; acá es sobre un plan repartido entre subtareas que no se ven

**Hallazgo**:
Lo que Tech Lead encuentra al revisar, con su lugar, la afirmación, la evidencia (el comando que corrió y su salida, o la cita del estándar o del brief), la severidad, el alcance y la categoría. Solo un hallazgo bloqueante sobre la subtarea la devuelve; el veredicto se deriva de los hallazgos. El alcance `outside` marca trabajo que la tarea no hizo o que el issue no pidió: nunca devuelve y va al único issue de seguimiento de la tarea, con la etiqueta `needs-triage`. Una objeción de comportamiento que no se reprodujo es una observación.
_Avoid_: comentario, issue
_Campo_: finding — de auditoría y seguridad de nube (AWS Security Finding Format), no de code review: SARIF dice result, que es el dato, y GitHub dice alert, que es la presentación

**Tabla de hallazgos**:
Los hallazgos de todas las revisiones de una tarea, cada uno con su estado: abierto, arreglado o descartado. Una fila es un lugar más una afirmación, y el veredicto de la primera vez es el que queda: una revisión posterior no relitiga lo que otra ya descartó. Una fila se cierra sólo porque un revisor la dio por saldada; dejar de nombrarla no la cierra, porque una revisión incremental tiene instrucción de no volver a mirar lo que ya aprobó. La única excepción es la aprobación: aprobar una subtarea cierra los bloqueantes previos de esa misma subtarea que la revisión no vuelve a levantar (ADR-0015, adenda de GH-201); la otra es una corrida aprobatoria de QA Performance, que asienta las filas abiertas de su propia ubicación, porque ninguna revisión posterior las vuelve a levantar (ADR-0060); la tercera es una corrida pasada del chequeo completo de la tarea, de versión mayor, que asienta las filas de ubicación `full_checks:<chequeo>` (ADR-0015, adenda de GH-358); la cuarta es una corrida de `PM: final validation`: aprobar asienta las filas abiertas de su ubicación de versión menor, y devolver asienta las anteriores que no vuelve a levantar, porque su validación no es incremental y relee la tarea entera (ADR-0071). La leen el revisor, el Developer y el integrador, en la sección del documento, que lleva las abiertas; el registro crudo de todas las pasadas vive en el frontmatter. La escribe el sistema en el fan-in, que es el único lugar que ve todas las ramas de una ola; cada revisión solo agrega. Muere con la tarea.
_Avoid_: backlog, lista de issues, deuda
_Campo_: sin término de arte; lo más cerca es la lista de alerts de GitHub code scanning, que vive en el repositorio y no muere con el cambio que la abrió

**Revisión sombra**:
Bajo `review_mode: single`, la revisión que cada rama recibía hasta ahora, corrida en paralelo a la revisión única de la integración, sobre la cabeza ya aprobada de cada rama, para medir qué ve de más. Nunca decide nada: no devuelve, no escala, no toca `findings`, estados ni `changes_requested`; solo deja un `ShadowRecord` en `shadow_reviews` (`ran`, `late`, `unavailable` o `skipped`, con su causa), su propia sección que ningún rol lee y su costo, que los topes no cuentan. No toma el cupo de roles de la corrida: sus plazas son `shadow_slots`, siempre una menos. En el código, `shadow_review` y `ShadowRecord` (ADR-0088).
_Avoid_: revisión paralela (las ramas del fan-out también corren en paralelo y deciden), segunda revisión, revisión de respaldo
_Campo_: shadow mode / shadow deployment — se corre el sistema candidato sobre el tráfico real y se compara su salida con la del vigente sin que ninguna llegue al usuario

### Cómo se rechaza trabajo

**Devolución**:
Rechazo con entrega hacia atrás. Cuenta para el límite del par. En el código se llama `ChangesRequested`, y su categoría `ChangeCategory`; el veredicto sigue siendo el valor `returned`, que es dato ya escrito, igual que la métrica `agentic.task.returns` y las claves del documento de tarea (ADR-0008, adenda).
_Avoid_: rechazo (salvo para el acto puntual de QA), rebote
_Campo_: request changes — GitHub, uno de los tres valores de `event` de una review (APPROVE, REQUEST_CHANGES, COMMENT); el par critique + revision en Constitutional AI (Bai 2022)

**Motivo de devolución**:
Por qué se devuelve: de comportamiento o técnico. Decide quién recibe una devolución de QA: PM si es de comportamiento, el Developer de la subtarea si es técnico, Tech Lead cuando el camino no tiene PM.
_Campo_: sin término de arte
Son tres términos y no uno porque cada uno tiene un lector distinto: el motivo es texto para quien recibe la devolución, la categoría es dato cerrado que la medición cuenta, y la devolución es el hecho que cuenta para el límite del par.

**Categoría de devolución**:
El motivo de una devolución como dato, de una lista cerrada: violación de un estándar (arquitectura, tipado silenciado, costuras, otro), brecha contra la spec (comportamiento, test faltante, alcance), entrega que no es entrega (sin commitear, suite en rojo), calibración del plan o problema que cruza subtareas. Las devoluciones de PM a Product Analyst llevan la categoría de definición. Es lo que la medición cuenta y lo que hace computable "el mismo motivo entre los mismos roles". Distinta del motivo en texto, que sigue siendo para quien recibe la devolución.
_Avoid_: tipo de devolución, motivo (para la categoría)
_Campo_: lo más cerca son las nueve etiquetas de Conventional Comments (praise, nitpick, suggestion, issue, todo, question, thought, chore, note), pero clasifican el comentario y no la devolución

**Devolución interna**:
Devolución que se resuelve dentro del par sin pasar por PjM. Se reporta a PjM igual.
_Campo_: sin término de arte

**Límite de devoluciones**:
Máximo de devoluciones por par antes de que la decisión única (`rules.on_return_limit`) elija: seguir contando, reiniciar o escalar. Hoy 2. El par Tech Lead→Developer de una subtarea elegible reinicia (Reinicio limpio) en el primer límite y escala en el segundo; los demás pares escalan en el primero.
_Campo_: sin término de arte; `max_iter` en CrewAI y `max_turns` en el OpenAI Agents SDK topean el loop de un agente solo, no el ida y vuelta entre dos

### Cómo termina una corrida

**Listo para revisión**:
La tarea tiene su pieza integrada en un pull request del producto y espera al humano. Mientras el camino no tenga QA, el merge del humano sin tocar el diff es el cierre, y es lo único que puntúa en el set de evaluación.
_Avoid_: terminada, cerrada (todavía no lo está)
_Campo_: ready for review — GitHub, el estado de un pull request que sale de draft

**Cierre**:
PjM da por terminada la tarea: aprobada, con las aprobaciones del tipo, o descartada, cuando PM dictamina que el comportamiento es el esperado, o partida, cuando el plan no cabe y se reparte en issues encadenados, o solo tracker, cuando lo único que queda es trabajo del tracker y se le deja una lista al humano.
_Campo_: sin término de arte

**Descartado**:
El cierre de un bug cuyo dictamen PM validó como comportamiento esperado: el producto ya hace lo que debe y no hay nada que construir. El sistema comenta el motivo en el issue y termina la corrida sin pull request; cerrar el issue es del humano, como mergear el pull request. Es un final, no una pausa: no se reanuda.
_Avoid_: rechazado, cerrado (el que cierra es el humano), wontfix (la etiqueta de triage de este repo, no el estado)
_Campo_: won't fix, works as intended — resoluciones de los trackers (Bugzilla `WONTFIX` y `WORKSFORME`, Chromium `WontFix` y `WorkingAsIntended`). Acá es un estado de la tarea, no una resolución del issue

**Partida**:
El final de una tarea cuyo plan no cabe (por encima del presupuesto de tamaño, de la línea de 150 USD o del p90 de su balde; ADR-0054, ADR-0093): no construye nada. Abre un issue por parte, encadenados con `Blocked by:` y con la etiqueta `agentic`, cada uno con su propio pull request chico, su tamaño, su `kind` y su `guard`; comenta la lista en el issue original y termina la corrida sin pull request, y no se relanza. Es un final, no una pausa: no se reanuda. Una parte que sigue por encima del presupuesto se parte de nuevo, hasta profundidad 2 (el marcador lleva `depth D`; uno sin profundidad se lee como 1); pasada esa, escala con la clase `estimate`, con su plan, y construirla entera ya no es una opción. La parte que se parte tiene exactamente una hija terminal, y el pull request de esa hija cierra también el issue de la parte padre, que libera a las que dependían de ella; una tarea de profundidad 0 que se parte conserva su lista para el humano.
_Avoid_: descartado (acá sí hay algo que construir, en las partes), dividida (la unidad es la parte, no la tarea), subtarea (esas se construyen dentro de una tarea; las partes son tareas)
_Campo_: sin término de arte

**Solo tracker**:
El final de una tarea cuyo plan es solo trabajo del tracker (sin subtareas, sin partes, con una lista y cero archivos; ADR-0062): el sistema comenta la lista una vez en el issue, con un marcador de contenido, y termina la corrida sin llamar a un rol, sin pull request y sin resultado de entrega (`tracker_only`). Es un final, no una pausa: no se reanuda, salvo que el comentario haya fallado, y entonces vuelve a este nodo, nunca al plan.
_Avoid_: descartado (acá sí hay algo que hacer, pero lo hace el humano), partida (no se abren issues)
_Campo_: sin término de arte

**Trabajo de tracker**:
Lo que el humano debe hacer en el tracker del producto (cerrar, retitular o comentar issues) porque ningún rol tiene `gh`: una lista de casillas en un comentario del issue. Nunca es una subtarea; junto a subtareas viaja con la entrega, y esa tarea cierra su issue al fusionarse como cualquier otra: la lista queda de comentario en el issue cerrado y no retiene a lo que depende de él (ADR-0062, addendum de GH-344). Si el comentario falla en la entrega, el pull request se abre igual con la lista acotada en el cuerpo y en el comentario de listo, sin registro (en solo tracker, en cambio, escala y se reanuda en el nodo; ADR-0062). Junto a partes, el split publica la lista en el issue original, más los ítems que el sistema agrega por cada issue abierto que sigue bloqueado por esa tarea; reapuntarlos queda a cargo del humano (ADR-0062, addendum de GH-350).
_Avoid_: subtarea (esas las construye un Developer), tarea de tracker
_Campo_: sin término de arte

**Reinicio limpio**:
Lo que hace el sistema, una vez por subtarea, cuando su par Tech Lead→Developer llega al límite de devoluciones y `restart.eligible` es verdadero (subtarea nunca aprobada, devuelta solo por la revisión, sin pull request): archiva el intento en una rama `archive/restart<k>-…`, salda sus hallazgos abiertos como descartados y arranca un Developer nuevo, sin sesión ni secciones del intento archivado, a partir de la sección `Restart <subtarea>` (el brief original, los hallazgos de las dos pasadas y una nota de qué no funcionó). El intento reiniciado tiene su contador propio y `restarts` registra el reinicio en el mismo parche (`restart.restart_patch`); la respuesta del humano no lo borra. Distinto de reiniciar una tarea entera (D-03258899), que este mecanismo no enmienda.
_Avoid_: reintento (eso es tras un fallo de rol), relanzar
_Campo_: clean restart / fresh context — Claude Code ("tras dos correcciones, limpiá el contexto"), Cursor

**Causa mecánica**:
La causa de un escalado que no pide juicio (`MechanicalCause`, lista cerrada): `ci_gh_unavailable`, `ci_gh_unreadable`, `ci_run_unfinished` (el CI no dio veredicto) y `delivery_transient` (la entrega a GitHub falló por un error transitorio). Se decide donde se levanta el escalado, por la clase del error y su tipo registrado, nunca por el texto del mensaje. `rules.auto_resumable` la devuelve y el poller la retoma con `RETAKE_SCHEDULE`, con tope `MECHANICAL_RETAKE_MAX` (3 retomas por tarea, sumadas entre causas); `rules.pause_kind` es el único que dice si una pausa es mecánica. Gastado el tope, escala con la evidencia. Todo lo demás es de juicio.
_Avoid_: transitoria, flaky (esas describen el síntoma, no la decisión)
_Campo_: transient error / retryable — de los sistemas distribuidos; Codex escala "solo por juicio"

**Escalado**:
Lo que un rol, un par o el tope dejan dicho cuando la tarea no puede seguir sin el humano, con las preguntas si las hay. El sistema lo entrega al humano en el issue y pausa la tarea: queda esperando con su estado guardado, sin gastar, y se retoma desde el mismo punto. Son diez las situaciones que lo provocan (su clase de escalado: `classification`, `returns`, `failure`, `cap`, `questions`, `unmergeable`, `setup`, `estimate`, `system_delivery` y `ci`): PjM no puede clasificarla, un par llegó a su límite, un rol falló dos veces, se alcanzó el tope de tarea, quedaron preguntas para el humano, una subtarea no pudo mergear su dependencia, el setup del producto falló en un checkout, la estimación del plan no cabe o una parte no cabe (ADR-0054), la entrega del propio sistema a GitHub falló, o el CI de GitHub no dio veredicto sobre la cabeza integrada de un producto que declara `ci:` (GH-462). El motivo viaja como texto libre para el humano; cuál de las diez situaciones fue, además, como dato cerrado (Clase de escalado). Debajo del motivo, el comentario trae un bloque de una palabra: una pregunta, la respuesta recomendada y las opciones, cada una un comando del CLI, sacado de una tabla cerrada por clase y causa (solo presentación). Un presupuesto de retomas gastado (`Retakes spent: `) no es un escalado: es la Corrida interrumpida o huérfana que el poller deja al humano, con la misma etiqueta. Una pausa causada solo por un disco lleno (`disk_full`) también es externa: el poller la retoma por su calendario, solo cuando el piso de espacio libre lo deja arrancar, y conserva `needs-human` (ADR-0069). Una pausa causada solo por el límite de la cuenta (y no persistida) no lleva `needs-human`: el poller la retoma a la hora de reinicio que dijo el mensaje, o por su calendario si no la dijo (ADR-0039, adenda de GH-381). Un escalado de aterrizaje que solo causó el CI (cabeza y base rojas, `ci_escalation`) lo libera `landing_plan.release` cuando el CI de la base vuelve a verde o con un comentario del dueño, una vez por par (head, sha del upstream), sin `### Decision` ni texto en `Human` (`released_by_ci`); nunca pide `--fresh` (GH-442). Cada entrada de pausa que lo registra (el fallo de rol, el escalado mismo y el retomado) lleva el instante `at`, en ISO-8601 UTC, que estampa la capa de aplicación; falta en las entradas escritas antes y entonces se lee como desconocido. El escalado de clase `ci` que no escribió registro de chequeos (un `no_verdict`) lleva además la medición de CI (`ci`) con la que se deriva la espera. Un Escalado de `escalate_adr` (una colisión de ADR que la regla no resuelve) se libera con la decisión del dueño en el issue: el aterrizaje la lee como la liberación de esa pregunta (`released_at` en su entrada) y corre `apply_adr_decision`.
_Avoid_: causa, pausa, bloqueo, espera
_Campo_: escalation — de soporte y operaciones, no del campo agéntico; ahí el mecanismo es interrupt (LangGraph): frena el grafo, expone un valor al cliente y se reanuda con `Command(resume=...)`

**Clase de escalado**:
El motivo de un escalado como dato, de una lista cerrada: PjM no pudo clasificar la tarea, un par llegó a su límite de devoluciones, un rol falló dos veces (`failure`), la entrega del propio sistema a GitHub falló (`system_delivery`, issue #203), se alcanzó el tope de tarea, quedaron preguntas para el humano, una subtarea no pudo mergear su dependencia, el setup del producto falló en un checkout (issue #24), la estimación del plan no cabe (`estimate`, ADR-0054), o el CI no dio veredicto sobre la cabeza integrada (`ci`, GH-462; no devuelve la tarea y no escribe registro; el poller la retoma solo si su Causa mecánica lo permite, y si no, es del humano). La escribe un solo constructor de dominio junto con el escalado mismo (`rules.escalation`), así que una patch nunca pone uno sin el otro, y se limpia con él al reanudar. Distinta del motivo en texto, que sigue siendo lo que lee el humano; un documento de antes de esta tarea no la tiene. El tope por intento (issue #66) no es una de las diez: para la tarea es un fallo de rol, no un escalado.
_Avoid_: motivo de escalado, tipo de escalado (para la clase)
_Campo_: sin término de arte

**Reanudación**:
La tarea sigue desde donde se pausó. Hace falta una decisión del humano cuando hay preguntas abiertas, un par llegó a su límite o la estimación no cabe (`estimate`: ni el sondeo ni el sondeo periódico la reanudan; solo una decisión, que además deja la revisión de la estimación), y con ella el contador de ese par vuelve a cero; la pregunta de una parte que ya no puede partirse (pasada la profundidad 2) se hace una sola vez, con el plan guardado, y no ofrece construirla entera (detenerla es no reanudar: cerrar el issue como no planificado libera a las partes que dependen de ella); un tope alcanzado o un fallo de rol se reanudan sin decisión, y una corrida interrumpida también. Un escalado de Causa mecánica lo retoma el poller solo, hasta 3 retomas por tarea (`MECHANICAL_RETAKE_MAX`, contadas desde el último comentario del humano); el reinicio limpio ya gastado no se borra al reanudar. Nada se reanuda bajo un tope ya gastado. Si el estado guardado se perdió, se reconstruye desde el documento de tarea, cruzado con git.
_Avoid_: reintento (eso es tras un fallo de rol), relanzar (eso es empezar de cero)
_Campo_: resume — estándar (LangGraph, Temporal, el Claude Agent SDK)

**Publicación debida**:
La cabeza aprobada de una tarea que su rama remota todavía no tiene, o que tiene sin que la tarea esté en `ready_for_review`: la integración la aprobó (con niveles, además el chequeo completo pasó sobre ella) y la corrida murió antes de empujarla, de actualizar el pull request o de fijar el estado. La comprueba la reanudación desde el documento, que sólo la declara si la rama de tarea sigue en esa cabeza o difiere de ella únicamente en el documento; la saldan la entrega y nada más: se retoma en `deliver`, sin correr ningún rol. Una lectura de origen que falla no la declara: la reanudación lo dice y no sugiere empezar de cero.
En el código se llama `publish_owed`, el campo del estado, y `approved_head`, la regla pura que dice cuál es esa cabeza.
_Avoid_: reentrega, re-push (la entrega ya aprobó; sólo falta publicar)
_Campo_: sin término de arte

**Corrida interrumpida**:
Una corrida que terminó sin cierre ni escalado porque su proceso murió. No espera a nadie: se reanuda sin decisión del humano, desde el último estado guardado. El poller la retoma cuando su lock está muerto y la tarea está en la bandeja, sea cual sea la causa: otro arranque (una Parada por apagado incluida), una excepción del grafo, o una muerte en el mismo arranque (SIGKILL, el OOM killer, un scope cortado). Todas gastan un solo presupuesto de 3 retomas por tarea (`domain/retakes.py`), con un solo calendario (`RETAKE_SCHEDULE`; la primera retoma es inmediata, salvo la de una corrida sin lock, que espera una hora de quietud), que se reinicia cuando el humano comenta o corre `agentic resume`; la retoma del poller, que comenta `Resumed by the poller: `, no lo reinicia. Gastado el presupuesto, o con un lock muerto del mismo arranque (o de arranque desconocido) sin marca de caída cuyo documento lleva 24 h sin tocar, el poller no retoma: pone `needs-human` y deja un solo comentario `Retakes spent: ` con lo que murió, cuántas retomas se hicieron y el comando para seguir. Una Parada deliberada no es una Corrida interrumpida y nunca se retoma. Lo que el rol en curso hizo y no entregó puede estar en su checkout sin commitear, y su costo en dólares no se conoce: el sistema registra los tokens que vio y lo dice, en vez de callarlo. Una pasada de aterrizaje cortada con la rama ya publicada se lee de la última entrada de `landing` (`rules.landing_pass`): `reopened` (alguna unidad reabierta) y `unfinished` (todas aprobadas, la entrada con `cost_base`, faltó el fan-in, los checks completos o `deliver`) se reanudan en `integrate`, o en `deliver` si la publicación se debe; `unstarted` (entrada sin `cost_base`) no se reanuda sino que la corre `agentic land --issue N`, sobre la misma entrada.
_Avoid_: cortada, abortada, crash (el estado de telemetría, no el concepto)
_Campo_: sin término de arte

**Corrida huérfana**:
Una tarea de la bandeja (issue abierto con la etiqueta `agentic`), en un estado anterior a la entrega, sin registro de lock, sin corrida viva y sin que su documento se haya escrito en una hora. El poller la retoma contra el mismo presupuesto de 3 retomas por tarea que una Corrida interrumpida, contadas desde el último reinicio del presupuesto, y siempre desde el documento (`--from-document`); gastado el presupuesto, avisa con `Retakes spent: ` y `needs-human`. Se la frena sacando la etiqueta `agentic` o cerrando el issue. No es una Corrida interrumpida: esa conserva su lock. Una corrida parada por una señal que no es un apagado tampoco es huérfana: conserva su lock con la marca de Parada deliberada.
_Avoid_: varada (la del aterrizaje, GH-257), stranded
_Campo_: sin término de arte

**Directorio temporal**:
El directorio en el disco, bajo `<raíz de scratch>/<clave del producto>/<tarea raíz>/` y fuera de todo repositorio, que cada chequeo, cada setup y cada intento de rol recibe como `TMPDIR` y que se borra cuando termina, salga bien o mal. La raíz es `AGENTIC_SCRATCH_DIR`, por defecto `$XDG_CACHE_HOME/agentic/scratch`; una raíz dentro del repositorio del producto o del de stage0 se rechaza antes de gastar (`ScratchInARepository`). Lo compone una sola función y lo respalda un registro de dueño escrito antes de crearlo y borrado después de borrarlo; lo que cae en `/tmp` queda fuera de este control. Las capturas que una tarea cita no viven acá sino en la carpeta de evidencia de la tarea (`.agentic/kept/<tarea>/`, que sigue en el producto), que se conserva 14 días tras el cierre (ADR-0069, ADR-0072).
_Avoid_: temporales, `/tmp`, caché
_Campo_: scratch directory; `Scratch` y `path_for` en el código

**Piso de espacio libre**:
El espacio libre, en megabytes, bajo el cual el poller no arranca nada: se mide en tres volúmenes, el de la raíz de scratch, el del producto y `/tmp`, y la ronda solo cierra tareas hasta que los tres lo superan. Por defecto 2048 (`AGENTIC_SCRATCH_FLOOR_MB`). Se mira al arrancar, no durante una corrida. El mismo piso, junto con un mínimo de inodos libres, es lo que convierte un intento fallido en `disk_full`, que lee solo el directorio del intento, su checkout y `/tmp` (ADR-0069, ADR-0072).
_Avoid_: cuota, límite de disco
_Campo_: free-space floor (disk watermark en almacenamiento); `space_holds` y `AGENTIC_SCRATCH_FLOOR_MB` en el código

**Arranque**:
Un encendido de la máquina, identificado por el id de arranque del kernel. Un registro de lock o de corridas vivas escrito en otro arranque está muerto, diga lo que diga su pid: el sistema operativo pudo haber reutilizado ese número para un proceso ajeno. Un registro sin id de arranque (anterior a este cambio, o una máquina sin `/proc`) no afirma nada: lo decide el sistema operativo, como siempre.
_Avoid_: reinicio (es el acto, no el período), sesión
_Campo_: boot id, del kernel de Linux (`/proc/sys/kernel/random/boot_id`)

**Parada por apagado**:
Una corrida que el apagado del sistema detuvo con una señal. Conserva su registro de lock, con la marca de apagado, para que el arranque siguiente pueda retomarla como Corrida interrumpida. Cualquier otra parada por señal es una Parada deliberada.
_Avoid_: caída, corte
_Campo_: sin término de arte

**Invocación**:
Una llamada a `agentic run` o `agentic resume`: entra, corre lo que el grafo tenga para correr y termina (entregada, escalada, o caída). Distinta de la Corrida, que es toda la ejecución del sistema sobre una tarea de punta a punta: una tarea que se pausó y se reanudó dos veces tuvo una Corrida y tres Invocaciones, y cada una gastó y tardó solo lo suyo, nunca el acumulado de la tarea que sí lleva la Corrida. `langgraph dev` corre el grafo directo, sin pasar por una invocación de la CLI, así que no deja ninguna de sus muestras.
_Avoid_: corrida, ejecución (eso es la Corrida completa), run (en inglés, para no perder la distinción con Corrida)
_Campo_: `InvocationKind` (`run` | `resume`) en el código; sin término de arte compartido — los frameworks hablan de resume/invoke sin nombrar la llamada suelta

**Fallo de rol**:
El rol se rompe: su ejecución falla, se le acaban los turnos sin resultado, la salida estructurada falta o es inválida, el `markdown` está vacío. Se reintenta una vez con el error como entrada, continuando la conversación del intento fallido cuando la hay. El motivo que queda escrito nombra siempre una causa: cuando el harness no deja material, lleva lo que el proceso sí dejó (stderr, código de salida), nunca un texto vacío. Distinto de devolución; no cuenta para el límite. Un intento estancado es uno de sus motivos (`stall`), y un disco lleno otro (`disk_full`): externo, como el límite de la cuenta, porque falló la máquina y no el rol. Un prompt que no se puede renderizar (un placeholder sin valor, o un archivo que la instantánea de la corrida no pudo leer) también es un fallo de rol, con el motivo `render`: no se reintenta, no cuesta nada (el rol nunca corrió) y es propio, un error nuestro y no de la máquina. Guarda los instantes de su propio intento (`started_at`, cuando empezó, y `ended_at`, cuando se atrapó el fallo; ISO-8601 UTC, ausentes en un fallo anterior) y las corridas de chequeo del Developer que alcanzó a lanzar.
_Avoid_: error (demasiado amplio), reintento (es parte del fallo, no otro término)
_Campo_: sin término de arte; el reintento sí lo tiene, `retry`, pero nombra la vuelta y no la rotura

**Intento estancado**:
Intento de un rol que lleva más que `stall_timeout_s` sin señal de vida: ni mensaje del stream, ni salida o vuelo de un hook de entrega, ni, con una herramienta del hilo principal pendiente, CPU o estado R/D de un hijo marcado del intento. Un vigilante lo mata por la marca del intento y lo falla con el motivo `stall`, que se reintenta como cualquier fallo de rol (ADR-0057). Un dato ilegible cuenta como actividad.
_Avoid_: colgado, timeout (el umbral es solo una parte)

**Tope por intento**:
Gasto máximo de un intento de cualquier rol. Excederlo es un fallo de rol. Red de seguridad contra un rol desbocado, no presupuesto de trabajo. Hoy 10 USD.
_Avoid_: presupuesto (eso sería lo que se espera gastar), guardrail (no significa tope de gasto en ningún framework: en el OpenAI Agents SDK es validación de input y output con tripwire, en CrewAI validación del output de una task)
_Campo_: per_instance_cost_limit — SWE-agent

**Tope de tarea**:
Gasto acumulado de una tarea a partir del cual el sistema escala y pausa en vez de seguir. Es un techo plano, el mismo para cualquier tamaño de plan. Se mira antes de cada intento de cualquier rol, contra lo que gastó la corrida entera y no sólo la rama que va a gastar. Subirlo al reanudar es decisión del humano, que puede pasar el techo y nunca lo baja. Alcanzado, lo que ya está corriendo termina: sólo frena lo que todavía no arrancó. No es fallo de rol ni devolución y no cuenta para ningún límite. Como el tope por intento, es red de seguridad, bien arriba de lo que cuesta una tarea sana. Hoy 250 USD.
_Avoid_: presupuesto de tarea
_Campo_: total_cost_limit — SWE-agent

**Tope por estimación**:
Red de seguridad al doble de la estimación vigente de la tarea (ADR-0054). Se mira antes de cada intento de cualquier rol, junto al tope de tarea y contra el mismo gasto de la corrida entera; alcanzado, sólo frena lo que todavía no arrancó y la tarea escala con la clase `estimate`, con lo gastado, el tope, las subtareas que faltan y una estimación nueva propuesta. Una tarea sin estimación (documento anterior) no tiene este tope. No lo reanuda el sondeo: solo una decisión del humano, que deja vigente la propuesta o el valor de `--estimate`, y se rechaza si la estimación nueva ya está gastada al doble. No sustituye ni cambia el tope de tarea, que sigue plano.
_Avoid_: tope de tarea, presupuesto

### Artefactos

**Documento de tarea**:
Un archivo por tarea con frontmatter YAML de PjM y una sección Markdown por rol. El registro
crudo del trabajo de la tarea, materia prima del conocimiento de producto: guarda también las
entregas que una devolución pisó y las corridas de cada rol. Nadie lee tareas viejas para
saber qué está vigente; para eso está el conocimiento de producto. Vive en el disco del producto
y nunca se versiona en una rama de entrega (ADR-0078): ninguna ruta bajo `.agentic/tasks/` se
commitea salvo en el archivado.
_Campo_: sin término de arte. `blackboard`, la palabra que uno esperaría, es de la IA clásica (Hearsay-II; Nii 1986) y no aparece en el código de LangGraph, AutoGen, CrewAI, Google ADK ni el OpenAI Agents SDK

**Sección**:
La parte del documento que escribe un rol. Cada rol declara qué secciones lee.
_Campo_: sin término de arte

**Parada deliberada**:
Una corrida que un humano, o una señal que no es un apagado (SIGTERM, SIGHUP, SIGINT), terminó a propósito, o que `agentic stop --issue N` marcó y detuvo. El registro de lock se conserva con la marca `stopped_deliberately`, escrita antes de cualquier señal cuando la pone `agentic stop`, y después de recibirla (en `settle_lock`) cuando la señal le llega a la corrida misma, y con el pid, el id de arranque y el starttime de la corrida original (solo un `agentic stop` sin registro escribe el suyo). El poller nunca la retoma, y no gasta el presupuesto de retomas; se continúa con `agentic resume --issue N`. Una pausa por el límite de la cuenta y una tarea que espera al dueño tampoco se retoman, pero no por esta marca: son escalados. Un `kill -9` no es una Parada deliberada: no deja marca y se retoma.
_Avoid_: pausa (una pausa es un escalado que espera al humano), cancelación
_Campo_: sin término de arte; `stopped_deliberately` en el registro de lock

**Bandeja**:
Los issues de GitHub del repo del producto con la etiqueta `agentic`. La entrada, no el documento.
_Campo_: inbox — de los sistemas de tickets; el contenedor concreto es el issue tracker

**Cola de escalados**:
Los issues con la etiqueta `needs-human`. Entran también las tareas a las que el poller se la puso al gastar su presupuesto de retomas (con un comentario `Retakes spent: `), aunque no sean un Escalado: las saca `agentic resume`.
_Campo_: sin término de arte

**Pull request de tarea**:
El pull request del producto que abre el sistema desde la rama de tarea, con el PRD y el plan de Tech Lead en el cuerpo. Es la entrega al humano; el issue recibe el link al pull request y al documento de tarea. Nada más se escribe en el issue: los roles se hablan por el documento.
_Campo_: pull request — GitHub; merge request en GitLab

**Override de prompt**:
Un prompt de rol que vive en el repo del producto y pisa el default del sistema.
_Campo_: sin término de arte

### Repos

**Sistema**:
Este repo: roles, prompts, caminos y las reglas del proceso. Es también uno de los dos productos sobre los que trabaja (ADR-0017).
_Campo_: sin término de arte. `self-hosting`, la palabra de compiladores para el programa que se construye a sí mismo, está tomada: en Temporal y Kubernetes significa "operás vos la infra"

**Producto**:
El repo sobre el que trabaja el sistema, que recibe como parámetro. Hay dos: el SaaS de gimnasios y el propio sistema, que desde ADR-0017 se desarrolla a sí mismo. El sistema nunca escribe en el checkout desde el que corre, que es stage0; que el repo del producto sea también el suyo no cambia esa regla.
_Campo_: sin término de arte

**Contexto de producto**:
La raíz del repo del producto, que el sistema recibe como parámetro. Ahí está todo lo que varía por producto: el código, los ADRs y `.agentic/`.
_Campo_: workspace root — de las herramientas de desarrollo; `cwd` en el Claude Agent SDK

**Checkout**:
Una copia de trabajo de un repo en el disco, con su rama y su árbol: un clon o un `git worktree`. Dos checkouts de un mismo repositorio son directorios distintos que comparten `.git`, o sea las mismas ramas, los mismos objetos y el mismo índice de worktrees. Por eso la regla de que el sistema nunca escribe donde corre se cobra por **repositorio** y no por directorio: escribir en un worktree del repo de stage0 es escribir en el repo que está ejecutando la corrida, aunque el directorio sea otro.
_Avoid_: copia, working directory, repo (el repo es uno; los checkouts, varios)
_Campo_: working tree — git, que llama "checkout" tanto al acto como al resultado

**Stage0**:
La copia del sistema, fijada a un sha, que ejecuta una corrida. No es la que se está editando: el sistema que corre y el que se modifica son dos, aunque el repo sea el mismo. Por analogía con el bootstrapping de un compilador, **stage1** es el sistema nuevo corrido por el viejo y **stage2** el sistema nuevo corrido por sí mismo; la prueba de que salió bien no es un número sino que stage3 dé idéntico a stage2. Una actualización explícita puede mover stage0 bajo una corrida viva: la corrida fija los textos de sus prompts y de los estándares por defecto en el primer nodo que construye sus puertos y los reutiliza en los demás nodos de esa corrida, así que no renderiza uno más nuevo que su código; el código que se importa de forma perezosa y las skills del plugin no se fijan.
_Avoid_: runtime (es el harness de cada rol), sistema fijado
_Campo_: stage0 — del bootstrapping de rustc (rustc-dev-guide), que llama "the same-result test" a la comparación de stage3 con stage2

**Tope de turnos**:
Máximo de turnos que un rol tiene para entregar en un intento. Se dimensiona por el trabajo que la entrega de ese rol exige, no por lo que le alcanza a otro rol que comparte el prompt base. Agotarlo sin resultado es un fallo de rol, y continuar la sesión en el reintento no lo repone. Un rol listado en `RETRY_TURNS` tiene su propio tope absoluto en el reintento posterior a un corte por turnos, y cada intento registra el tope con el que corrió.
_Avoid_: presupuesto (eso es lo que se espera gastar), max_turns (el nombre en el código, no el concepto)

**Negación**:
El rechazo del sistema a una herramienta o un comando que el rol no tiene: le llega al rol con el motivo que nombra la regla de su rol, y queda escrita en la sección de esa corrida como una sola línea con cuántas fueron. No es fallo de rol ni devolución y no cuenta para ningún límite.
_Avoid_: deny (el mecanismo, no el concepto), bloqueo, error de permisos

**Corrida de chequeo del Developer**:
Cada llamada de Bash de un Developer que ejecuta uno de los chequeos que el producto declara (`tests`, `lint`, `types`...), reconocida sólo por `domain/check_runs.py`, que nunca da por chequeo un `git`, `python`, `uv` o `bash` a secas. Queda en el documento con el segmento que coincidió (sin asignaciones de entorno al frente, limpiado y de hasta 200 caracteres), el nombre declarado cuando coincide exacto, el instante de inicio, la duración y el código de salida. Una duración nula es una llamada sin terminar o desprendida; una duración con código nulo es un código desconocido (lo decidió una tubería o un segmento posterior), nunca un éxito. Se guardan las últimas 50 por intento y las demás se cuentan. Registrarla no decide nada: ni el veredicto ni la entrega. Distinta de la Puerta de entrega: ésta corre los chequeos por cuenta del sistema; una corrida de chequeo es el Developer corriéndolos por su cuenta.
_Avoid_: ejecución de tests (sólo una parte de los chequeos), llamada de Bash (demasiado amplio)
_Campo_: sin término de arte

**Emisión de entrega**:
Cada llamada del rol a la herramienta de entrega estructurada dentro de un intento, la valide o no lo que la recibe después. El sistema cuenta cuántas hubo por intento y, si el intento entregó, por corrida entera; queda escrita en la sección de esa corrida como una sola línea, sólo cuando fueron más de una. Distinta de Negación: una negación es el sistema rechazando una herramienta que el rol no tiene, y una emisión de entrega es el rol llamando a la que sí tiene, una o más veces.
_Avoid_: reintento (eso es después de un fallo de rol completo), llamada (demasiado amplio)
_Campo_: sin término de arte

**Clasificación**:
La decisión de PjM sobre de qué tipo es una tarea —bug, feature o platform enhancement— y, con eso, qué camino recorre. Es una decisión de tipo y de ruteo: qué cede ante una discrepancia entre la definición y el comportamiento lo decide PM dentro del camino de bug, y no entra en la confianza con que PjM clasifica.
_Avoid_: triage, dictamen (el dictamen es de Product Analyst sobre un bug ya clasificado)

**Asignación de brazo**:
La decisión del poller, al lanzar una tarea, de bajo qué brazo del experimento declarado por el producto (`.agentic/experiment.yaml`) corre: `alternate` reparte por déficit sobre las cuentas y `seeded` por el hash del issue. Queda en el documento como `assignment` (`experiment`, `arm`, `switches`) y en el registro del lanzamiento mientras no hay documento; el relanzamiento conserva el brazo. Con la declaración inválida o un conflicto con el `.env`, la tarea corre sin asignar. Se lee solo a través de `metrics.arm_of`, nunca por el nombre del brazo (ADR-0081, que enmienda el ADR-0030).
_Avoid_: tratamiento, cohorte, grupo
_Campo_: random assignment / allocation — de los ensayos controlados; `alternate` es *deficit round-robin* y `seeded` un *hash bucketing*

**Aterrizaje**:
Lo que el sistema hace con un pull request aprobado cuyo merge `auto_merge_decision` rechazó, en vez de dejarlo al humano (ADR-0048): esperar, traer el upstream a la rama, volver a revisar, corregir, repetir el CI o los chequeos, hacer el pase final de importantes, archivar el seguimiento de fuera de alcance y mergear, renumerar el ADR cuyo número el master ya tomó (`renumber`, ADR-0049), o aterrizar por tramos lo ya aprobado y, solo cuando eso no se puede, diagnosticar y reiniciar (el reinicio es el último recurso: avisa en el issue con el motivo y el costo hasta el momento, sin esperar a nadie). Cada paso lo elige una sola tabla (`landing_step`), está acotado por presupuestos por tarea y queda en la lista `landing` del documento antes de ejecutarse. Los pases del modelo (`re_review`, `fix`) comparten un tope por tarea; las sincronizaciones con conflicto tienen el suyo por sha de upstream (el movimiento de master que la tarea no controla no gasta el presupuesto de sus revisiones), y todas las sincronizaciones juntas un techo por tarea. El diagnóstico tiene un veredicto cerrado (`sync` o `unlandable`): un veredicto `sync` lleva a una `sync_master` más para ese sha, una sola, antes de cualquier reinicio; el segundo diagnóstico para el mismo sha se lee como `unlandable`. Solo `after_diagnosis` convierte un veredicto en sincronizar, reiniciar o escalar. Distinta de Reanudación: una reanudación retoma una corrida cortada; un aterrizaje trabaja sobre un pull request ya entregado. Una excepción: el pase final de importantes puede correr antes del pull request y escribe su entrada con `before_pr: true`; ningún lector que suponga un pull request la trata como un paso suyo. Tras liberar un escalado de CI, el paso lo da la tabla (`released_step`): `sync_master` si el head no contiene el upstream, un `rerun` si lo contiene, una espera si la ascendencia no se lee; `resume` nombra `agentic land` solo si esa misma decisión dice que `land` mueve la tarea (GH-442). Tras la decisión del dueño sobre una colisión `Ambiguous`, el paso `apply_adr_decision` (GH-544, adenda de ADR-0049) renumera con la regla, un pase del Developer edita sólo las líneas que la regla no puede decidir (los `spots`), puede mover dentro de su archivo, sin cambiarles el texto, las líneas que la regla ya reescribió (`bound`: etiquetas y nombres de archivos movidos, y el título del ADR; GH-576), y se empuja con lease; los límites se comparan por contenido (`adr_numbers.outside_bounds`) y, si el pase los rebasa, el resultado es `out_of_bounds` con las rutas y líneas fuera de límites (`out_of_bounds_lines`, saneadas, nunca el texto de una línea; `out_of_bounds_more` cuenta las que no se muestran); cada entrada de apply registra `bounds` (`ADR_EDIT_BOUNDS`) y el tope de decisiones solo cuenta las entradas de la versión vigente, de modo que subir esa versión es la única forma de devolver cupos; no gasta pases (`LANDING_PASSES_MAX`) y su revisión pendiente se exime una vez del presupuesto. Su tope de decisiones (`LANDING_ADR_DECISIONS_MAX`) es por colisión, la clave (cabeza, `moves`), no por tarea (GH-570, `adr_decisions_spent`); gastado el tope, la pregunta deja de ofrecer una decisión. Si el `apply_adr_decision` termina `checks_red`, la entrada guarda qué chequeos estaban en rojo y sus últimas líneas saneadas (`red_checks`, `red_more`, `presubmit.red_checks`) y la misma pregunta de `escalate_adr` los muestra, para que el dueño decida con datos. Una línea que ninguna confirmación no-merge de la rama agregó (la trajo un merge) y que ni el master ni la base del merge tienen es también un `spot` si lleva una forma ADR (`ADR-NNNN`, `docs/adr/NNNN`) de un número movido, en la ruta automática y en la decidida (GH-579); un número suelto en esa línea nunca lo es. Los límites del pase se informan por línea, nunca como archivo entero (`outside_bounds` no devuelve la línea 0; un cambio solo de fin de línea o de salto final está dentro de límites), las líneas que la regla (b)/(c) reescribió (`ADR-NNNN`, listas `ADRs`) son `bound`, y toda línea listada que la decisión deja igual se nombra en un mensaje de commit con `keep <ruta>:<línea>` (`adr_numbers.kept_spots`; un commit vacío vale): el resultado es `applied` solo si cada `spot` cambió de texto o está en un `keep`, si no es `role_failed`. La entrada `escalate_adr` registra `upstream` y, si al aplicar el sha es otro, el resultado es `stale_moves`; la pregunta lista cada `spot` como `ruta:línea` (`ADR_EDIT_BOUNDS` = 3). La puerta del apply corre los chequeos de entrega (el nivel de entrega, `fast_tier` sobre `delivery_checks`, como la entrega de un Developer) y el nivel completo lo juzga el merge de la cabeza empujada (GH-585); si un chequeo agotó su tiempo y ninguno está en rojo, el resultado es `checks_timeout` (`presubmit.gate_verdict`, por el campo `timed_out` de la fila): no empuja, no gasta cupo y la pregunta dice qué chequeo y cuántos segundos; la entrada registra `gate_tier` (`delivery` o `all`) y un `checks_red` sin esa clave no gasta cupo. Cuando la decisión del dueño no deja ningún `spot` (la regla (d), un enlace Markdown `[NNNN](…/NNNN-slug.md)`, reescribió toda referencia, por ejemplo una fila de índice), no hay pase de modelo: el sistema hace el commit mecánico, corre una vez los chequeos de entrega sobre él, comprueba con `adr_collision` que no quede colisión y recién entonces empuja con lease; un chequeo en rojo, un rechazo del testigo o de rutas termina `checks_red`, nunca `applied`, y las líneas reescritas no se reordenan (un índice cuyos chequeos exigen orden termina `checks_red`). `stale_moves` es solo que el master se movió desde la pregunta o que los `moves` de la regla cambiaron; el resto de las causas tiene su resultado propio (`git_failed`, `no_collision`, `ambiguous`), ninguno gasta cupo de decisión (GH-582). La pregunta dice que la regla reescribe las líneas y que una `Decision:` las aplica sin pase de modelo solo cuando el cálculo decidido no deja `spots` y sí líneas reescritas; si no se pudo calcular, conserva «Fix by hand».
_Avoid_: rescate, merge manual
_Campo_: sin término de arte

Desde GH-588 el aterrizaje actúa solo sobre la Cabeza del orden de aterrizaje: un pull request que espera su turno solo recibe `wait` o `none` (ADR-0090).

**Tramo aterrizado**:
Un prefijo estricto, en orden topológico y cerrado bajo `depends_on`, de las subtareas aprobadas de una tarea cuyo aterrizaje se agotó, que el sistema arma desde master y mergea en un pull request propio (`closes_issue=False`, fuera de `pull_request`) antes de reiniciar (ADR-0048, addenda GH-297). Cada unidad del tramo cumple `review_carries` contra lo que master cambió y ninguna toca las rutas del trabajo del integrador; lo que queda sigue en el pull request principal. Si ningún tramo es posible, el reinicio dice por qué con un motivo cerrado (`single_unit`, `no_clean_slice`, `slice_refused`, `unreadable`).
_Avoid_: parte, partida
_Campo_: sin término de arte

**Pase final de importantes**:
El paso que, antes del merge, reabre las unidades dueñas de los hallazgos `important` abiertos de la tarea (y la unidad revisada para los de alcance de plan) para una sola pasada de Developer y revisión. No es devolución: no cuenta para el límite del par, y su entrada en el historial de devoluciones lleva la marca del aterrizaje. Cuenta cuando la re-entrega lo resuelve; lo que sigue abierto después aterriza y se lista en el comentario del merge. Puede correr antes de abrir el pull request (ADR-0053, enmienda GH-354): cuando la revisión final aprueba con importantes `owned`/`plan` abiertos, el nodo `fix_important` reabre las unidades antes de `full_checks`; su entrada en `landing` lleva `before_pr: true` y gasta el mismo presupuesto de un pase, antes o después del pull request.
_Avoid_: devolución, segundo fix
_Campo_: sin término de arte

**Sincronización no lograda**:
Un paso `sync_master` del aterrizaje cuya entrega volvió con un head que no contiene el sha del upstream para el que se despachó. Se cierra (`closed_at`, con su costo) y dice qué le faltó (`lacks`), pero no queda resuelta: `resolved_at` lo lleva solo una sincronización cuyo head contiene ese sha. Cuenta, por sha de upstream, contra su propio tope; agotado, el paso es el diagnóstico. Una sincronización con conflicto cuenta además, por sha de upstream, contra su propio tope (nunca contra los pases del modelo); la que sigue a un veredicto `sync` está exenta de ese tope, pero no del de sincronizaciones no logradas.
_Avoid_: sincronización resuelta, reintento
_Campo_: sin término de arte
