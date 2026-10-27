# Medición

Las métricas del sistema sobre las corridas y las trazas que las llevan. Lee lo que el
proceso registra; el proceso no la conoce.

## Language

**Retrabajo**:
Corrida de rol que repite trabajo ya hecho en la tarea, sea por devolución o por fallo de rol. Medido en plata: todo el costo de una corrida posterior a una devolución, y de una corrida de primera entrega sólo el costo de sus intentos fallidos, más la plata de intentos fallidos que ninguna corrida contabiliza. Cada dólar cuenta una sola vez, y el valor de la tarea es la suma de los de sus corridas. Las devoluciones internas y los Developers en paralelo no son retrabajo.
_Avoid_: reintento (el reintento es un hecho; el retrabajo es lo que cuesta)
_Campo_: rework — de manufactura y de ingeniería de software clásica; DORA no lo mide, su vecino es change failure rate

**Métrica del sistema**:
Un hecho medido sobre las corridas: costo, duración, devoluciones, escalados. Hasta
ADR-0037 era un evento OTel exportado a un backend de métricas aparte (Grafana Cloud);
ese backend se fue con Langfuse como único destino, y el mismo hecho hoy vive como
atributo del span de tarea o de rol que ya existía, o se lee del documento de la tarea
(`observability/README.md` dice, familia por familia, dónde quedó cada una). Distinta de
la métrica de producto, que vive en cada capacidad.
_Campo_: sin término de arte para la distinción que nos importa (medir el sistema, no el producto)

**Muestra**:
El valor que una corrida de tarea o de rol deja en cada métrica del sistema al terminar,
hoy estampado en el span como atributo (o leído del documento de la tarea) en vez de
exportado como evento a un backend de métricas aparte (ADR-0037). Una corrida, una
muestra; nada se acumula entre corridas.
_Campo_: data point — OpenTelemetry; sample en Prometheus (el nombre queda; el backend de
series que lo motivó, no)

**Versión del sistema**:
El SHA corto del repo del sistema con el que corrió una tarea, más `+dirty` si el checkout
tenía cambios. Identifica la corrida; no sirve para comparar, porque casi cada tarea corre
sobre uno distinto.
_Avoid_: versión (a secas, se confunde con la del producto), release
_Campo_: `service.version` — OpenTelemetry resource

**Línea de base**:
Un hito del sistema declarado por el humano con un tag de git en master; el eje de toda
comparación. Cada corrida lleva la línea de base bajo la que corrió, en el span y en el
documento de la tarea, y una versión del sistema pertenece a exactamente una. Dos líneas
de base se comparan por el scorecard, calculado sobre los documentos de tarea de cada una
(`agentic eval compare`, ADR-0037).
_Avoid_: versión, cohorte, época, hito (se usa en prosa, no como término)
_Campo_: baseline — de la evaluación de modelos; `baseline_id` del set de evaluación es más fino
(incluye la declaración y el brazo) y no lo reemplaza

**Ventana**:
Las 10 tareas cerradas de un lado de una comparación de playbook: las 10 que corrieron con una misma sección de playbook de un rol y las 10 anteriores. Cuenta solo el brazo `default` y no las tareas cuyo playbook cambió a mitad de corrida; no concluye con menos de 10 por lado ni cuando cruza más de una línea de base.
_Avoid_: período, cohorte, muestra (la muestra es otra cosa en este glosario)
_Campo_: evaluation window — el diseño de PjM del README (10 tareas); before/after en la evaluación de cambios de prompt

**Scorecard**:
Las cuatro métricas del sistema, en orden, que dicen si una línea de base es mejor que otra:
autonomía (entregas fusionadas limpias sobre las entregas juzgadas por un humano: las que el sistema fusionó solo no cuentan, y sin cinco juzgadas no hay tasa, sólo `n/m`), fallos propios por tarea (sin
los de límite de cuenta ni los de caída del proveedor), costo por entrega y retrabajo. Mejora si
la primera no baja y la segunda baja; las otras dos desempatan. Es una consulta sobre los
documentos de tarea de una línea de base, no una muestra ni un backend de métricas: la misma
lectura que hace `agentic eval compare` — el único lector; esta tarea no empuja ningún score
a Langfuse (ADR-0037). "Propios" se lee sobre el motivo del fallo: quedan afuera los motivos
externos (`account_limit`, el límite de la cuenta del proveedor; `upstream`, su caída; y
`network`, la red del lado del sistema caída, nunca leída de la prosa del error; y
`disk_full`, el disco de la máquina lleno, derivado de lo medido) y quedan
adentro los nuestros, el tope por intento (`limit`) incluido, que es una configuración
nuestra y no de nadie más. Los fallos anteriores al commit que separó esos motivos llevan
`sdk` y no se reescriben, así que esta cuenta no se compara de un lado al otro de ese commit.
_Avoid_: KPI, north star, índice
_Campo_: scorecard — Kaplan y Norton; la lectura como orden lexicográfico es propia

**Factor paralelo**:
El tiempo de Developer de una tarea (las corridas del Developer y del integrador, cada una de su `started_at` a su `ended_at`) sobre el largo de la unión de esos intervalos y los de las revisiones de subtarea. 1.0 es todo en serie; 2.0, de a dos todo el tiempo. Los huecos sin ninguna corrida, las pausas entre invocaciones y la espera de cupo quedan fuera de todo intervalo. Sobre varias tareas es la suma de numeradores sobre la suma de denominadores. Es una lectura de los documentos (`agentic eval compare` la imprime), no un atributo, métrica ni etiqueta; y no es comparable 1:1 con la cifra de las transcripciones de sesión del issue #364 (1.26), que mide otra cosa.
_Avoid_: paralelismo, speedup
_Campo_: parallelism / speedup — la definición es la de la ley de Amdahl sobre tiempo ocupado, sin el tiempo de la corrida en serie

**Brazo**:
La configuración de experimento bajo la que corrió una tarea: el valor de cada interruptor
declarado (`review_mode`, `skip_ephemeral`, `reviewer_model`), o `default` cuando ninguno se
movió. Toda muestra que lleva versión del sistema lleva también su brazo.
_Avoid_: experimento (es el conjunto de brazos que se comparan), variante, configuración
_Campo_: arm — de los ensayos controlados y del A/B testing

**Set de evaluación**:
Las cinco tareas fijas que se re-corren idénticas contra cada versión de prompts.
_Campo_: eval set — `evalset` en Google ADK, dataset en LangSmith; el benchmark del campo es SWE-bench

**Muestra residual**:
La plata de una patch que ninguna Corrida de rol contabiliza: los intentos fallidos de una
escalada, o un nodo que cerró sin patch. Una muestra más de `agentic.role.cost_usd`, en cero
para una patch vacía, para que la suma de las muestras de una patch sea siempre su costo.
_Campo_: sin término de arte — la industria mide el gasto por llamada, no el que una corrida
paga sin dejar un registro de la corrida que lo pagó

**Resultado de entrega**:
El veredicto del humano sobre el pull request de una tarea, leído por `agentic close` y no
por ningún revisor del sistema: si fusionó, si lo corrigió antes de fusionar, o si lo cerró
sin fusionar. También puede ser el merge del propio sistema, que no es un veredicto
(**Fusionado por el sistema**). Es la métrica de calidad que falta después de `ready_for_review`, donde hoy se
corta toda medición.
_Campo_: outcome — el vecino más cercano es change failure rate (DORA), que mide si un deploy
rompió algo, no si el humano corrigió el trabajo antes de aceptarlo

**Fusionado** (`merged`):
El pull request se fusionó sin ningún commit humano posterior al último commit del sistema:
el humano aceptó la entrega tal cual.
_Campo_: merged — GitHub, para el estado de un pull request fusionado

**Fusionado con cambios** (`merged_with_changes`):
El pull request se fusionó, pero con al menos un commit humano, que no sea de fusión,
posterior al último commit del sistema: el humano corrigió antes de aceptar.
_Campo_: sin término de arte — GitHub no distingue este caso de una fusión limpia

**Fusionado por el sistema** (`auto_merged`):
El sistema fusionó el pull request, tras la decisión de ADR-0045 (aprobado por Tech Lead,
chequeos verdes sobre el head mergeado con el master, sin hallazgos importantes abiertos).
No es un veredicto del humano: no puntúa, no entra en la autonomía ni en el costo por
entrega, y las tareas del set de evaluación nunca llegan a este valor.
_Avoid_: fusionado automático, merge limpio (`merged` es el humano aceptando tal cual)
_Campo_: sin término de arte — GitHub no distingue quién fusiona un pull request

**Cerrado** (`closed`):
El pull request se cerró sin fusionarse: el humano descartó la entrega.
_Campo_: closed — GitHub, para un pull request que se cierra sin fusionar
