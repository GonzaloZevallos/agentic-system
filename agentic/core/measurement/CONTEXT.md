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
Las cifras de tiempo (`cycle_h`, `active_h` y las horas de espera, pausa, fallo y sin atribuir) son informativas: `agentic eval compare` las imprime con su `n/m` y ninguna entra en el veredicto.
_Avoid_: KPI, north star, índice
_Campo_: scorecard — Kaplan y Norton; la lectura como orden lexicográfico es propia

**Factor paralelo**:
El tiempo de Developer de una tarea (las corridas del Developer y del integrador, cada una de su `started_at` a su `ended_at`) sobre el largo de la unión de esos intervalos y los de las revisiones de subtarea. 1.0 es todo en serie; 2.0, de a dos todo el tiempo. Los huecos sin ninguna corrida, las pausas entre invocaciones y la espera de cupo quedan fuera de todo intervalo. Sobre varias tareas es la suma de numeradores sobre la suma de denominadores. Es una lectura de los documentos (`agentic eval compare` la imprime), no un atributo, métrica ni etiqueta; y no es comparable 1:1 con la cifra de las transcripciones de sesión del issue #364 (1.26), que mide otra cosa.
_Avoid_: paralelismo, speedup
_Campo_: parallelism / speedup — la definición es la de la ley de Amdahl sobre tiempo ocupado, sin el tiempo de la corrida en serie

**Tiempo de espera**:
Los segundos que una tarea esperó al CI, y sólo al CI: la unión de `[max(pushed_at, node_started_at), run_ended_at]` de `full_checks`, `merge_ci` y la escalación `ci` (para esta última, el fin es el `at` de la entrada). Se deriva únicamente con `ci.waited_seconds`, que lleva a 0 una diferencia negativa; esa diferencia puede venir del desfase entre el reloj de la máquina y el de GitHub. Un registro sin `ci_timing` no suma, y uno cuya espera no se puede leer deja la cifra desconocida (`n/a`, la tarea cuenta como excluida), nunca 0.
_Avoid_: espera (a secas: la espera de cupo y la de un humano son otras), latencia
_Campo_: wait time / queue time — el vecino más cercano es el tiempo en cola de un pipeline de CI

**Tiempo en pausa**:
Los segundos entre que una tarea se pausa y la siguiente reanudación (`resumed`), nunca cerrada por una corrida. Empieza en el `ended_at` del último fallo que abrió la pausa, o en el `at` de la primera entrada de la pausa si no hay tal fallo. Es *externa* o *humana* según `rules.pause_kind`, el único clasificador (la medición no tiene lista propia): externa si todos los fallos que terminaron la corrida son de motivo externo (`rules.EXTERNAL_REASONS`; solo cuenta el último fallo de cada sección dentro de su tanda, no uno que la corrida ya reintentó, GH-659) y toda escalación es de clase `failure` o lleva una causa mecánica; *humana* en cualquier otro caso (la espera de un `ci` cuenta como humana solo si su causa no es mecánica). Advertencia: las horas humanas y externas no se comparan de un lado al otro de GH-593, porque lo mecánico pasó de humano a externo; tampoco de un lado al otro de GH-659, para las pausas con un fallo reintentado que no era externo. `eval compare` imprime además, fuera del veredicto, los escalados por causa y tipo y la mediana de horas de espera humana por causa, cada una con su `n` de `m` tareas. Una pausa abierta, sin reanudación, deja la cifra de la tarea fuera de la mediana.
_Avoid_: espera humana, bloqueo, tiempo muerto
_Campo_: sin término de arte — el vecino más cercano es el tiempo bloqueado (blocked time) del flujo Kanban

**Tiempo fallido**:
La unión de los intervalos `[started_at, ended_at]` de los fallos de una tarea: el tiempo de los intentos que no llegaron a nada. Es 0 sólo en una tarea sin fallos; un fallo sin sus dos instantes deja la cifra desconocida, y con ella las de espera, pausa y sin atribuir, que se restan después de esta (una clase sólo se conoce si se conocen las anteriores).
_Avoid_: tiempo perdido, retrabajo (el retrabajo se mide en costo)
_Campo_: sin término de arte — el vecino más cercano es el failed time del tiempo de ciclo de un pipeline

**Tiempo sin atribuir**:
El ciclo menos el tiempo activo recortado al ciclo, el fallido, el de espera y el de pausa, restados en ese orden (cada clase pierde lo que ya cubren las anteriores), de modo que cada segundo cuenta una vez y el resto nunca es negativo. Usa el activo recortado al ciclo, a diferencia de `active_h`, que no se recorta; y las medianas de cada fila no suman la mediana de `cycle_h`: cada una es su propia mediana, sobre las tareas donde la cifra se conoce.
_Avoid_: tiempo muerto, resto, tiempo perdido
_Campo_: sin término de arte — es el residuo de una partición del tiempo de ciclo

**Tiempo de entrega** (lead time):
Los segundos entre la última vez que se agregó la etiqueta `agentic` al issue y el `mergedAt` del pull request. Se guarda en el documento como `lead_time`: `{labeled_at, merged_at}` o `{unknown: no_merge | unreadable}` (sin pull request o cerrado sin fusionar, o una lectura que falló). Se agrupa por `initial_path` sólo en los documentos donde `cycle.path_recorded` vale (tienen `initial_path` y la marca `path_routed`); el resto es `unrecorded` y queda fuera de las medianas, porque corrió la ruta completa aunque dijera `light`. Un valor negativo, ilegible o ausente es desconocido, nunca 0. Sesgos conocidos: una tarea reetiquetada cuenta desde la última etiqueta, y una parte de una tarea dividida incluye el tiempo que esperó bloqueada. `eval compare` lo imprime por ruta con su `n` de `m` tareas, las promociones por tipo y las exclusiones por motivo, fuera del veredicto del scorecard.
_Avoid_: duración, tiempo de ciclo (el ciclo va de la primera corrida al cierre), tiempo hasta el merge (`time_to_merge_seconds` va de la apertura del PR al merge)
_Campo_: lead time (DORA, del commit al despliegue); aquí, del buzón al merge

**Brazo**:
La configuración de experimento bajo la que corrió una tarea: el valor de cada interruptor
declarado (`review_mode`, `skip_ephemeral`, `plan_review`, `reviewer_model` y `plan_shape`, este
último omitido cuando vale `free`), o `default` cuando ninguno se movió. Toda muestra que lleva versión del sistema lleva también su brazo.
Si la tarea tiene una asignación de brazo (ADR-0081), el brazo sale de los interruptores de esa asignación; si no, del `experiment` del documento.
_Avoid_: experimento (es el conjunto de brazos que se comparan), variante, configuración
_Campo_: arm — de los ensayos controlados y del A/B testing

**Hallazgo perdido** (`missed`):
Un hallazgo de la revisión sombra del brazo `single` (ADR-0088) para el que la revisión única, la de la unidad revisada, no tiene ninguna fila con el mismo archivo y la misma familia de categoría (`shadow_report.build`, con `finding_file` y `category_family` como únicas entradas). La cifra es una cota inferior: la familia es ancha, así que una fila de la misma familia en el mismo archivo oculta un defecto distinto, y las cabezas de las dos revisiones difieren. Un hallazgo cuya ubicación no nombra un archivo es `unlocated` y nunca cuenta como perdido; solo cuentan los registros de sombra `ran`, y los `late`, `unavailable` y `skipped` se informan aparte.
_Avoid_: falso negativo (no hay verdad de referencia: la sombra es otra revisión, no un oráculo), hallazgo omitido
_Campo_: missed finding / false negative — de la evaluación de revisores y de detectores; aquí no hay etiqueta humana, solo una segunda revisión como control

**Set de evaluación**:
Las tareas fijas que se re-corren idénticas contra cada versión de prompts, declaradas en
`.agentic/eval-set.yaml` como `entries: [{issue, base, title, body}]`: el número del issue, el sha
completo del commit del producto sobre el que se corre y el texto del issue congelado una vez.
`EvalSet.issues` (los números distintos de las entradas) es la única fuente de los issues del set.
_Avoid_: la forma vieja `issues: [...]`, que ya no existe
_Campo_: eval set — `evalset` en Google ADK, dataset en LangSmith; el benchmark del campo es SWE-bench

**Corrida de evaluación**:
Una entrada del Set de evaluación vuelta a correr una vez por Brazo, en una raíz propia
(`<producto>/.agentic/eval/EVAL-<issue>-<sha8>-<brazo>-<k>/`, un worktree desprendido en `base`),
con el texto congelado como única entrada y sin escribir en el tracker ni publicar ninguna
referencia. Termina en el estado `evaluated`, nunca se reanuda, y su documento queda copiado en
`<producto>/.agentic/eval/docs/`, fuera de `tasks/`. Se compara contra las corridas de los otros
brazos de la misma entrada y base; el desenlace histórico de la tarea real es solo una referencia.
_Avoid_: tarea de evaluación (es una corrida, no una tarea del producto), re-corrida
_Campo_: sin término de arte — el vecino más cercano es una ejecución de un caso de evaluación
contra una versión fija del sistema bajo prueba (SWE-bench fija el commit base de cada instancia)

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
