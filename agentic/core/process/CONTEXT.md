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
Lo que hace Tech Writer después de la implementación, en el nodo `copy_review` y antes de la validación final: compara los textos del diff con los vigentes y con `WRITING_STANDARDS.md`. Sus hallazgos son `important` u `observation`, nunca bloqueantes: no devuelve ni forma par, y un importante se corrige en el pase final (ADR-0053), una sola vez por tarea. Solo ese nodo escribe y salda las filas de textos, que llevan el identificador `copy:<n>.<i>:<ubicación>`.
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
Lo que el sistema hace con lo que dejó la corrida anterior de una tarea cuando esa tarea se reinicia de cero: commitea en cada worktree lo que nadie commiteó, los saca, y renombra la rama de tarea y las de sus subtareas bajo `archive/run<N>-<commit del sistema>/`, la de tarea primero. No borra nada, y por eso una corrida vieja se puede leer después; el issue recibe una línea diciendo bajo qué prefijo quedó. `N` es el número de archivado de esa tarea, y que la rama de tarea se renombre antes que las de subtarea es lo que hace visible un archivado que se cortó a la mitad: un reintento lo termina en el mismo prefijo en vez de abrir otro.
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
El estado de una Tarjeta, uno de siete: Corriendo, En PR (aprobada, esperando CI y merge), Trabada, Necesita humano, En cola, Fuera del sistema, Hecha. Se decide por precedencia (Hecha, Corriendo, Necesita humano, Fuera del sistema, Trabada, En cola) con un mapa cerrado de los valores de `Status` del documento; solo `board.card` la calcula. Su Bloqueo dice por qué una Tarjeta está Trabada o Necesita humano.
_Avoid_: estado, status, columna
_Campo_: `Status` de GitHub Projects (renombrado Etapa)

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

**Chequeo completo de la tarea**:
La corrida de todos los `checks` del producto, sin forma afectada, una sola vez sobre la cabeza integrada y antes de abrir el pull request, para un producto que declara `delivery_checks` (ADR-0064). La hace el sistema, sin rol ni costo de modelo, en el checkout de la tarea, que exige limpio y deja limpio. Una corrida roja reabre la integración con una fila por chequeo fallido; una verde la asienta. La entrega empuja sólo con una corrida pasada de esta cabeza (o de una que difiere sólo en el documento de la tarea): sin ella no empuja y vuelve a correr. Si el producto declara `ci:`, el chequeo completo es el CI de GitHub sobre la cabeza integrada (ref `agentic-ci/<id>`) y no corre `checks` en local; si el CI no se puede usar, corre el nivel local y escribe la causa del retroceso (ADR-0064, adenda GH-376).
En el código se llama `full_checks`, el nodo y el registro del frontmatter.
_Avoid_: suite final, gate completo

**Chequeo exigido por el plan**:
Un nombre de chequeo del CI que los criterios de hecho del issue necesitan en verde sobre la cabeza del pull request. Lo declara el plan (`ci_required`) y es distinto del `ci` del stack. Es condición necesaria de un merge, nunca suficiente: un CI verde que cubre sólo chequeos exigidos por el plan no decide el merge, lo deciden los chequeos locales (ADR-0045, adenda GH-371).
En el código se llama `ci_required`, `ci_plan_only` y `ci_gate_names`.
_Avoid_: chequeo declarado (eso es el `ci` del stack), criterio de CI

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
Un chequeo que falló y pasó al reintentar sobre la misma cabeza, dentro de la misma llamada de una puerta (ADR-0066). La puerta lo deja pasar, y queda registrado en `flakes` con el chequeo, la cabeza, la puerta y los ids de las pruebas que fallaron la primera vez, que son los que `failed_ids` leyó con el patrón `test_id` del producto. Se archiva o se comenta como issue en el merge, nunca dentro de un intento del Developer, y registrarlo nunca cambia, demora ni rechaza una entrega o un merge. Un chequeo que agotó su tiempo no se reintenta y nunca es un Flake; uno que falla dos veces es un fallo, con la salida del reintento.
En el código se llama `flake_verdict` (dominio, `domain/presubmit.py`) y `FlakeRecord` con su reductor `add_flakes` (`domain/task.py`); una fila de reintento lleva `rerun: true`.
_Avoid_: test intermitente
_Campo_: flaky test — Software Engineering at Google, cap. 11; retries de Playwright y `pytest-rerunfailures`, que reintentan pero no registran la cabeza

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
Pase de Tech Lead sobre la entrega de una subtarea o de la pieza integrada, con hallazgos y un veredicto que se deriva de ellos: aprobada o devuelta. Una subtarea tiene tantas revisiones como pases recibió.
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
Máximo de devoluciones por par antes de escalar. Hoy 2.
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
El final de una tarea cuyo plan no cabe (más de 150 USD o de 40 archivos, o por encima del p90 de su balde; ADR-0054): no construye nada. Abre un issue por parte, encadenados con `Blocked by:` y con la etiqueta `agentic`, cada uno con su propio pull request chico; comenta la lista en el issue original y termina la corrida sin pull request, y no se relanza. Es un final, no una pausa: no se reanuda. Una parte nunca se parte de nuevo: se le dice de entrada que lo es y escribe solo subtareas; si su plan tampoco cabe, se construye entera sin preguntar mientras el mayor entre su costo declarado y la referencia del historial no pase el doble de la línea (300 USD), y el sistema lo dice una vez en el issue; solo por encima de eso la pregunta "construirla entera o detenerla" se hace una sola vez, con el plan guardado, y escala con la clase `estimate`.
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

**Escalado**:
Lo que un rol, un par o el tope dejan dicho cuando la tarea no puede seguir sin el humano, con las preguntas si las hay. El sistema lo entrega al humano en el issue y pausa la tarea: queda esperando con su estado guardado, sin gastar, y se retoma desde el mismo punto. Son ocho las situaciones que lo provocan (su clase de escalado): PjM no puede clasificarla, un par llegó a su límite, un rol falló dos veces o la entrega del propio sistema a GitHub falló, se alcanzó el tope de tarea, quedaron preguntas para el humano, una subtarea no pudo mergear su dependencia, el setup del producto falló en un checkout, o la estimación del plan no cabe, o una parte no cabe (ADR-0054). El motivo viaja como texto libre para el humano; cuál de las ocho situaciones fue, además, como dato cerrado (Clase de escalado). Una pausa causada solo por un disco lleno (`disk_full`) también es externa: el poller la retoma por su calendario, solo cuando el piso de espacio libre lo deja arrancar, y conserva `needs-human` (ADR-0069). Una pausa causada solo por el límite de la cuenta (y no persistida) no lleva `needs-human`: el poller la retoma a la hora de reinicio que dijo el mensaje, o por su calendario si no la dijo (ADR-0039, adenda de GH-381).
_Avoid_: causa, pausa, bloqueo, espera
_Campo_: escalation — de soporte y operaciones, no del campo agéntico; ahí el mecanismo es interrupt (LangGraph): frena el grafo, expone un valor al cliente y se reanuda con `Command(resume=...)`

**Clase de escalado**:
El motivo de un escalado como dato, de una lista cerrada: PjM no pudo clasificar la tarea, un par llegó a su límite de devoluciones, un rol falló dos veces o la entrega del propio sistema a GitHub falló (issue #203), se alcanzó el tope de tarea, quedaron preguntas para el humano, una subtarea no pudo mergear su dependencia, el setup del producto falló en un checkout (issue #24), o la estimación del plan no cabe (`estimate`, ADR-0054). La escribe un solo constructor de dominio junto con el escalado mismo (`rules.escalation`), así que una patch nunca pone uno sin el otro, y se limpia con él al reanudar. Distinta del motivo en texto, que sigue siendo lo que lee el humano; un documento de antes de esta tarea no la tiene. El tope por intento (issue #66) no es una de las ocho: para la tarea es un fallo de rol, no un escalado.
_Avoid_: motivo de escalado, tipo de escalado (para la clase)
_Campo_: sin término de arte

**Reanudación**:
La tarea sigue desde donde se pausó. Hace falta una decisión del humano cuando hay preguntas abiertas, un par llegó a su límite o la estimación no cabe (`estimate`: ni el sondeo ni el sondeo periódico la reanudan; solo una decisión, que además deja la revisión de la estimación), y con ella el contador de ese par vuelve a cero; la pregunta de una parte que pasa el doble de la línea (una que pasa la línea sin llegar al doble no pregunta) se hace una sola vez y cualquier reanudación con comentario la construye entera sin planificar de nuevo (detenerla es no reanudar: cerrar el issue como no planificado libera a las partes que dependen de ella); un tope alcanzado o un fallo de rol se reanudan sin decisión, y una corrida interrumpida también. Nada se reanuda bajo un tope ya gastado. Si el estado guardado se perdió, se reconstruye desde el documento de tarea, cruzado con git.
_Avoid_: reintento (eso es tras un fallo de rol), relanzar (eso es empezar de cero)
_Campo_: resume — estándar (LangGraph, Temporal, el Claude Agent SDK)

**Publicación debida**:
La cabeza aprobada de una tarea que su rama remota todavía no tiene, o que tiene sin que la tarea esté en `ready_for_review`: la integración la aprobó (con niveles, además el chequeo completo pasó sobre ella) y la corrida murió antes de empujarla, de actualizar el pull request o de fijar el estado. La comprueba la reanudación desde el documento, que sólo la declara si la rama de tarea sigue en esa cabeza o difiere de ella únicamente en el documento; la saldan la entrega y nada más: se retoma en `deliver`, sin correr ningún rol. Una lectura de origen que falla no la declara: la reanudación lo dice y no sugiere empezar de cero.
En el código se llama `publish_owed`, el campo del estado, y `approved_head`, la regla pura que dice cuál es esa cabeza.
_Avoid_: reentrega, re-push (la entrega ya aprobó; sólo falta publicar)
_Campo_: sin término de arte

**Corrida interrumpida**:
Una corrida que terminó sin cierre ni escalado porque su proceso murió. No espera a nadie: se reanuda sin decisión del humano, desde el último estado guardado. El poller la retoma sin decisión del humano cuando su lock es de un arranque anterior (una Parada por apagado incluida), como mucho 3 veces por cada decisión del humano; si murió en el mismo arranque, la deja al humano. Una caída por excepción del grafo en el mismo arranque también la retoma el poller, como mucho 2 veces por versión del sistema y 6 en total por cada decisión del humano; una muerte por señal en el mismo arranque sigue siendo del humano. Lo que el rol en curso hizo y no entregó puede estar en su checkout sin commitear, y su costo en dólares no se conoce: el sistema registra los tokens que vio y lo dice, en vez de callarlo.
_Avoid_: cortada, abortada, crash (el estado de telemetría, no el concepto)
_Campo_: sin término de arte

**Corrida huérfana**:
Una tarea de la bandeja (issue abierto con la etiqueta `agentic`), en un estado anterior a la entrega, sin registro de lock, sin corrida viva y sin que su documento se haya escrito en una hora. El poller la retoma hasta 3 veces seguidas que no corrieron nada, contadas desde el último comentario del humano, y siempre desde el documento (`--from-document`). Se la frena sacando la etiqueta `agentic` o cerrando el issue. No es una Corrida interrumpida: esa conserva su lock.
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
Una corrida que el apagado del sistema detuvo con una señal. Conserva su registro de lock, con la marca, para que el arranque siguiente pueda retomarla como Corrida interrumpida; cualquier otra parada por señal libera el lock.
_Avoid_: caída, corte
_Campo_: sin término de arte

**Invocación**:
Una llamada a `agentic run` o `agentic resume`: entra, corre lo que el grafo tenga para correr y termina (entregada, escalada, o caída). Distinta de la Corrida, que es toda la ejecución del sistema sobre una tarea de punta a punta: una tarea que se pausó y se reanudó dos veces tuvo una Corrida y tres Invocaciones, y cada una gastó y tardó solo lo suyo, nunca el acumulado de la tarea que sí lleva la Corrida. `langgraph dev` corre el grafo directo, sin pasar por una invocación de la CLI, así que no deja ninguna de sus muestras.
_Avoid_: corrida, ejecución (eso es la Corrida completa), run (en inglés, para no perder la distinción con Corrida)
_Campo_: `InvocationKind` (`run` | `resume`) en el código; sin término de arte compartido — los frameworks hablan de resume/invoke sin nombrar la llamada suelta

**Fallo de rol**:
El rol se rompe: su ejecución falla, se le acaban los turnos sin resultado, la salida estructurada falta o es inválida, el `markdown` está vacío. Se reintenta una vez con el error como entrada, continuando la conversación del intento fallido cuando la hay. El motivo que queda escrito nombra siempre una causa: cuando el harness no deja material, lleva lo que el proceso sí dejó (stderr, código de salida), nunca un texto vacío. Distinto de devolución; no cuenta para el límite. Un intento estancado es uno de sus motivos (`stall`), y un disco lleno otro (`disk_full`): externo, como el límite de la cuenta, porque falló la máquina y no el rol.
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
saber qué está vigente; para eso está el conocimiento de producto.
_Campo_: sin término de arte. `blackboard`, la palabra que uno esperaría, es de la IA clásica (Hearsay-II; Nii 1986) y no aparece en el código de LangGraph, AutoGen, CrewAI, Google ADK ni el OpenAI Agents SDK

**Sección**:
La parte del documento que escribe un rol. Cada rol declara qué secciones lee.
_Campo_: sin término de arte

**Bandeja**:
Los issues de GitHub del repo del producto con la etiqueta `agentic`. La entrada, no el documento.
_Campo_: inbox — de los sistemas de tickets; el contenedor concreto es el issue tracker

**Cola de escalados**:
Los issues con la etiqueta `needs-human`.
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
La copia del sistema, fijada a un sha, que ejecuta una corrida. No es la que se está editando: el sistema que corre y el que se modifica son dos, aunque el repo sea el mismo. Por analogía con el bootstrapping de un compilador, **stage1** es el sistema nuevo corrido por el viejo y **stage2** el sistema nuevo corrido por sí mismo; la prueba de que salió bien no es un número sino que stage3 dé idéntico a stage2.
_Avoid_: runtime (es el harness de cada rol), sistema fijado
_Campo_: stage0 — del bootstrapping de rustc (rustc-dev-guide), que llama "the same-result test" a la comparación de stage3 con stage2

**Tope de turnos**:
Máximo de turnos que un rol tiene para entregar en un intento. Se dimensiona por el trabajo que la entrega de ese rol exige, no por lo que le alcanza a otro rol que comparte el prompt base. Agotarlo sin resultado es un fallo de rol, y continuar la sesión en el reintento no lo repone.
_Avoid_: presupuesto (eso es lo que se espera gastar), max_turns (el nombre en el código, no el concepto)

**Negación**:
El rechazo del sistema a una herramienta o un comando que el rol no tiene: le llega al rol con el motivo que nombra la regla de su rol, y queda escrita en la sección de esa corrida como una sola línea con cuántas fueron. No es fallo de rol ni devolución y no cuenta para ningún límite.
_Avoid_: deny (el mecanismo, no el concepto), bloqueo, error de permisos

**Emisión de entrega**:
Cada llamada del rol a la herramienta de entrega estructurada dentro de un intento, la valide o no lo que la recibe después. El sistema cuenta cuántas hubo por intento y, si el intento entregó, por corrida entera; queda escrita en la sección de esa corrida como una sola línea, sólo cuando fueron más de una. Distinta de Negación: una negación es el sistema rechazando una herramienta que el rol no tiene, y una emisión de entrega es el rol llamando a la que sí tiene, una o más veces.
_Avoid_: reintento (eso es después de un fallo de rol completo), llamada (demasiado amplio)
_Campo_: sin término de arte

**Clasificación**:
La decisión de PjM sobre de qué tipo es una tarea —bug, feature o platform enhancement— y, con eso, qué camino recorre. Es una decisión de tipo y de ruteo: qué cede ante una discrepancia entre la definición y el comportamiento lo decide PM dentro del camino de bug, y no entra en la confianza con que PjM clasifica.
_Avoid_: triage, dictamen (el dictamen es de Product Analyst sobre un bug ya clasificado)

**Aterrizaje**:
Lo que el sistema hace con un pull request aprobado cuyo merge `auto_merge_decision` rechazó, en vez de dejarlo al humano (ADR-0048): esperar, traer el upstream a la rama, volver a revisar, corregir, repetir el CI o los chequeos, hacer el pase final de importantes, archivar el seguimiento de fuera de alcance y mergear, renumerar el ADR cuyo número el master ya tomó (`renumber`, ADR-0049), o aterrizar por tramos lo ya aprobado y, solo cuando eso no se puede, diagnosticar y reiniciar (el reinicio es el último recurso: avisa en el issue con el motivo y el costo hasta el momento, sin esperar a nadie). Cada paso lo elige una sola tabla (`landing_step`), está acotado por presupuestos por tarea y queda en la lista `landing` del documento antes de ejecutarse. Los pases del modelo (`re_review`, `fix`) comparten un tope por tarea; las sincronizaciones con conflicto tienen el suyo por sha de upstream (el movimiento de master que la tarea no controla no gasta el presupuesto de sus revisiones), y todas las sincronizaciones juntas un techo por tarea. El diagnóstico tiene un veredicto cerrado (`sync` o `unlandable`): un veredicto `sync` lleva a una `sync_master` más para ese sha, una sola, antes de cualquier reinicio; el segundo diagnóstico para el mismo sha se lee como `unlandable`. Solo `after_diagnosis` convierte un veredicto en sincronizar, reiniciar o escalar. Distinta de Reanudación: una reanudación retoma una corrida cortada; un aterrizaje trabaja sobre un pull request ya entregado. Una excepción: el pase final de importantes puede correr antes del pull request y escribe su entrada con `before_pr: true`; ningún lector que suponga un pull request la trata como un paso suyo.
_Avoid_: rescate, merge manual
_Campo_: sin término de arte

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
