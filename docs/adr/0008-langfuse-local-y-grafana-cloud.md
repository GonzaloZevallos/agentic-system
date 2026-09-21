---
status: accepted
date: 2026-09-13
---

# Langfuse local para el nivel LLM, Grafana Cloud para las métricas del sistema

Dos herramientas a distinta altitud, un solo collector que reparte por `service.name`.
Langfuse, local, recibe todas las trazas: las del sistema y las nativas de Claude Code, con
prompts, respuestas y tool calls. Grafana Cloud recibe solo lo que emite el sistema: los
spans de tarea y rol, sin el título de la tarea, y las métricas del sistema definidas en
CONTEXT.md, "Medición". Nada de Claude Code sale a la nube.

El sistema emite las métricas con el SDK de OpenTelemetry desde el adaptador de grafo, con lo
que devuelve cada caso de uso: el patch ya trae costo, versión de la entrega, modelo,
devoluciones y escalado. Un módulo puro del dominio, `metrics.py`, define retrabajo,
devoluciones totales y la huella de prompts; el adaptador solo los lee y los registra. La
aplicación sigue sin ver telemetría y no hay port para ella (ADR-0007). Cada métrica lleva
como label el SHA corto del repo del sistema, marcado `+dirty` si hay cambios sin commitear,
y la huella del set de prompts que corrió: sin eso no se puede saber si un cambio mejoró
algo.

## Opciones descartadas

- **Solo trazas en Grafana Cloud, métricas derivadas con TraceQL o con el generador de
  métricas de Tempo**: las trazas se retienen 30 días y el generador solo da conteos y
  duraciones. La pregunta que importa es costo y retrabajo a meses vista; eso pide
  métricas propias, que duran 13 meses.
- **Grafana reemplaza a Langfuse**: Grafana no sabe mostrar una conversación con el
  modelo; Langfuse no sabe comparar semanas. Son preguntas distintas.
- **Todo a los dos destinos**: los spans de Claude Code llevan el prompt entero. Mandarlos
  a un servicio externo fue descartado por contenido; el filtro cuesta cuatro líneas.
- **Un port de telemetría**: contradice ADR-0007 y hoy la aplicación no necesita emitir
  nada que el adaptador no vea en el patch. Si algún día lo necesita, se revisa esto.
- **Mandar tokens**: el SDK solo entrega dólares y turnos. Costo en dólares alcanza para
  la decisión que hay que tomar.

## Consecuencias

- El collector deja de ser "de Langfuse": su config vive en `observability/collector/`,
  con la imagen `contrib` porque el filtro por OTTL y la extensión `basicauth` no están
  en la core. Sigue levantándose desde el compose de Langfuse porque comparte su red.
- Los nombres de métricas y labels son un contrato: cambiarlos rompe la comparación con
  las corridas anteriores. Se agregan, no se renombran.
- El JSONL de evaluación de README §14 conserva lo que ninguna métrica puede dar, el
  puntaje humano y la nota, y deja de guardar costo.
- Retrabajo se mide por costo, no por cantidad. Las devoluciones se cuentan aparte.

## Adenda 2026-09-14: las métricas son eventos, no tasas

La primera corrida real dejó el dashboard vacío. Las métricas eran contadores e histogramas
con temporalidad delta, acumulados en el collector (`deltatocumulative`) y leídos con
`increase()`. Prometheus nunca cuenta la primera muestra de una serie, y la serie lleva
`system_version`: en desarrollo casi cada versión corre una sola tarea, así que el panel
de una versión nunca mostraba su primera tarea, es decir, casi ninguna. Además `increase()`
extrapola entre muestras aisladas y Grafana Cloud limita cada consulta a 32 días, lo que
descartó reconstruir los deltas por PromQL.

Decisión: cada tarea terminada y cada corrida de rol es un **evento** que deja una
**muestra** por métrica (glosario de Medición). Los instrumentos son gauges y el adaptador
exporta al cerrar cada `finish`, así dos corridas con los mismos labels en el mismo minuto
no se pisan. Los paneles suman muestras (`sum_over_time`, `count_over_time`), exactos desde
la primera tarea de una versión. El collector deja de acumular; aplica al recrearlo.

Consecuencias:

- Los nombres OTel y los labels no cambian. Los nombres en Prometheus pierden los sufijos
  `_total`, `_sum`, `_count` y `_bucket`: el corte se hizo cuando en Grafana solo había una
  tarea de prueba y dos corridas del mismo día. La regla "se agregan, no se renombran"
  sigue vigente desde acá.
- Sin buckets no hay mediana por rol en PromQL sobre varias series; el panel pasa a
  promedio, exacto.
- Registrar y exportar es atómico (un lock): los Developers en paralelo (ADR-0004)
  cierran en hilos distintos con los mismos labels y un gauge guarda solo el último valor
  antes de exportar.
- Cada cierre de rol o tarea espera el export HTTP al collector local. Con el collector
  caído, cada cierre paga el timeout del exportador antes de seguir; la corrida no falla.

Opciones descartadas: convertir en el collector (mismo resultado, pero el collector
interpretaría y no se podía probar sin recrearlo); dejar `increase()` y esperar dos tareas
por versión (en desarrollo no ocurre).

## Adenda 2026-09-14: Langfuse pricea desde tokens; el sistema no manda dólares

La corrida GH-12 de 71 USD mostró costo 0 en todas las trazas de Langfuse (issue #9). Los
spans de Claude Code llegaban bien y anidados bajo el rol, y `claude_code.llm_request`
entraba como generación con modelo y precio asignados, pero Claude Code manda los tokens
con nombres propios (`input_tokens`, `output_tokens`, `cache_read_tokens`,
`cache_creation_tokens`) y Langfuse solo pricea `gen_ai.usage.*`. Sin usage no hay costo.
Los spans del sistema llevan `role.cost_usd` y `task.cost_usd`, que Langfuse ignora.

Decisión: el collector copia esos cuatro contadores a `gen_ai.usage.*` con las claves de
la tabla de precios de Langfuse (`transform/claude_code_usage`, solo en la pipeline de
Langfuse) y Langfuse calcula el costo de cada llamada. El sistema no manda dólares a
Langfuse: el costo de un rol o de una tarea ahí es la suma de sus llamadas. El span de
rol se marca `langfuse.observation.type = agent`, que es como Langfuse llama a la corrida
de un agente; la tarea queda sin tipo. La cifra exacta del SDK sigue en el documento de
tarea y en Grafana.

Consecuencias:

- El collector ya no solo filtra: traduce. Los atributos originales se conservan (se
  agregan, no se renombran). Un collector levantado con la config anterior sigue dejando
  el costo en 0 hasta que se lo recree; `observability/collector/probe.py` lo detecta.
- Langfuse interpreta `gen_ai.usage.input_tokens` como OpenAI (input con caché incluida)
  y le resta la caché; Anthropic lo manda sin caché. El collector suma lectura y escritura
  de caché al input antes de mandarlo, o Langfuse lo deja en 0. La primera sonda lo
  mostró: costo 0.00625 en vez de 0.01125, con el input sin cobrar.
- El costo por rol en Langfuse puede quedar por debajo de `role.cost_usd`: Claude Code
  emite parte de sus llamadas fuera de toda interacción (284 de 546 en GH-12) y esas no
  cuelgan del rol, aunque sí suman al total de la traza. Re-parentarlas pediría que el
  collector supiera qué rol estaba activo, y no lo sabe.
- Si Langfuse y el documento difieren más que un redondeo, la causa está en esas llamadas
  sueltas o en la tabla de precios de Langfuse, no en el sistema.

Opciones descartadas: mandar `gen_ai.usage.cost` con el costo del SDK en el span de rol
y en la raíz de la tarea, como proponía el issue: Langfuse suma el costo de todas las
observaciones de la traza, así que cada dólar contaría dos veces, y las llamadas seguirían
en 0. Mandarlo solo en el rol: exacto, pero pierde la altitud que Langfuse existe para
dar (cada llamada, con lectura y escritura de caché) y la raíz no puede llevar el total.

## Adenda (2026-09-16): el nombre de la serie no sigue al del dominio

El dominio renombró **Devolución** a `ChangesRequested` (issue #53): `Return` no es término
de arte y el que la industria entera reconoce es *request changes*, uno de los tres valores
de `event` de una review de GitHub. La medición **no lo sigue**, y esto dice por qué.

Se quedan con el nombre viejo, exactamente cuatro cosas:

- la métrica `agentic.task.returns`,
- la clave de label `return.category`,
- el atributo de span `role.return_categories`,
- y el valor `returned` de `role.outcome`, que además es un valor de `Status` y del enum de
  veredicto que el modelo emite.

El motivo es que **renombrarlas parte la historia en dos a la altura de este commit**: las
corridas viejas quedan bajo un nombre y las nuevas bajo otro, y ningún panel las suma. Eso
no lo arregla el dashboard, y el valor del renombre es comunicar mejor, no arreglar un bug;
pagar con las series es un precio desproporcionado. Las dos alternativas se descartaron:
aceptar el corte con una anotación de Grafana deja el dashboard mintiendo para siempre sobre
cualquier ventana que cruce la fecha, y emitir las dos métricas un tiempo mete código
temporal que hay que acordarse de borrar, contra un set de evaluación (#27) que todavía no
existe para marcar cuándo.

**La misma regla alcanza al documento de tarea**, y ahí no es cosmética. El frontmatter sigue
escribiendo `returns`, `return_history` y `subtasks[<id>].return_category`, porque
`resume.from_document` lee el frontmatter de vuelta al estado: con las claves renombradas,
una tarea pausada antes de este commit reanudaría con el contador del par **en cero y sin
decir nada**. El issue decía que los documentos ya escritos no se migran porque son registro
crudo; es cierto para leerlos, y falso para reanudar desde ellos.

La regla, entonces, en una línea: **lo que sobrevive a la corrida conserva su nombre; lo que
se reescribe sigue al dominio.** Series y documentos del primer grupo; código, prompts y
variables de entorno del segundo (`AGENTIC_MAX_RETURNS_PER_PAIR` pasa a
`AGENTIC_MAX_CHANGES_REQUESTED_PER_PAIR`, y un `.env` viejo deja de tener efecto en
silencio). El mapeo entre los dos vive en un solo lugar, `RECORDED_AS` y
`SUBTASK_RECORDED_AS` en `document.py`, con `render` de ida y `parse` de vuelta, y un test
que abre un documento escrito antes del renombre.

Lo que queda pendiente: si alguna vez el nombre de la serie se alinea, es una migración con
el set de evaluación re-corrido entero, no una edición.

## Adenda (2026-09-17): cada muestra dice sobre qué producto corrió

Desde ADR-0017 hay dos productos, y ninguna métrica decía cuál. `TaskRun.finish` etiquetaba
`task.type`, `task.status`, `system.version` y `prompts.fingerprint`; con un solo producto
eso alcanzaba, con dos los paneles suman peras con manzanas. Costo por tarea, devoluciones
por categoría y `rework_usd` mezclaban un SaaS de gimnasios con un sistema agéntico, la
línea de base de §9 dejaba de significar algo, y la comparación que más interesa (¿el
sistema es más caro de mantener a sí mismo que al gimnasio?) era justo la imposible.

Decisión: un label **`product`** en todo span y toda métrica, de tarea y de rol, con el
nombre que **el producto declara** en `.agentic/stack.yaml`, clave nueva y obligatoria.

Por qué declarado y no deducido. Las otras dos fuentes candidatas no sobreviven a lo que
pasa de verdad con un repo: el **nombre del directorio** cambia con `git clone <url> otro-nombre`
y con mover el checkout, y el **remoto** cambia en un fork y no existe en un repo que nunca
se publicó. Un archivo versionado viaja con el repo, es el mismo en todos sus checkouts por
construcción, y es el producto diciendo cómo se llama en vez del sistema adivinándolo. Ya
había precedente: desde #36 el stack se declara y no se infiere, y el archivo es obligatorio,
así que la clave nueva no agrega un modo de fallo que no existiera.

Es un slug (minúsculas, dígitos y guiones) porque termina siendo valor de label en
Prometheus y variable del dashboard: un producto con dos grafías son dos productos en
todos los paneles.

Consecuencias:

- **Se agrega, no se renombra** (la regla de arriba sigue): las series viejas quedan sin
  `product`, y un panel que filtra `product=~".*"` no las ve. Es el corte que se aceptó,
  porque lo que hay atrás son corridas de un solo producto y el valor del label es de acá
  en adelante.
- El nombre es la única clave del archivo que **no** se lee en el commit base de la tarea,
  sino en HEAD: dice *quién* es el repositorio, no qué regía en un commit. Leído en el base,
  una tarea anterior a la clave reanudaría sin nombre, y un renombre a mitad de tarea
  partiría sus muestras en dos labels.
- `preflight` rechaza un producto sin nombre antes de gastar un peso. Pasada esa puerta, un
  nombre ilegible no mata un nodo: el span sale con `unknown`, igual que `system_version`
  cuando no hay git. Importa para la puerta que `preflight` no cubre, `langgraph dev`.
- El dashboard suma una variable `Producto`, arranca con **uno solo** seleccionado, y todo
  panel abre por `product`, así que elegir "All" compara los dos en vez de sumarlos.
- El collector no cambia: el label viaja en el span y en la métrica como cualquier otro.
- El repo del gimnasio tiene que declarar su nombre para seguir corriendo; es una línea en
  su `.agentic/stack.yaml`.

Opciones descartadas: un **atributo de recurso** OTel (`service.namespace`), que en
Prometheus no baja a label de serie sino a `target_info`, así que no se puede filtrar un
panel por él; una **variable de entorno** o un flag de la CLI, que hace que el mismo producto
cambie de nombre según cómo lo invoquen y se olvide en silencio; y un archivo nuevo
`.agentic/product.yaml`, que es un segundo obligatorio para una línea.

## Adenda (2026-09-17): el span de rol se llama `invoke_agent`, con las convenciones GenAI

"La ejecución de un solo agente dentro de una corrida" no tiene nombre preciso en la
industria: LangGraph dice *super-step*, el Claude Agent SDK y OpenAI dicen *turn* (un
round-trip del loop, no un agente), CrewAI dice *iteration*, ADK *agent call*, SWE-agent
*step*. El único vocabulario cerrado que existe es el de las **OpenTelemetry GenAI semantic
conventions**: el span se llama `invoke_agent {gen_ai.agent.name}` y `gen_ai.operation.name`
toma valores de una lista cerrada. Alinearse con él es interoperabilidad real, no un
renombre cosmético: cualquier lector de trazas GenAI (Langfuse, Grafana, Phoenix) entiende
la corrida de un rol sin configuración nuestra.

Decisión: **solo el span de rol** adopta el semconv. El de tarea, los siete gauges de
`Instruments` y todo label de métrica quedan byte a byte igual — la interoperabilidad es
una propiedad de las trazas, que viven 30 días; la serie de 13 meses que este documento
existe para dar sigue con sus nombres, que "se agregan, no se renombran".

El span de rol pasa a llamarse `invoke_agent <Role.name>` y lleva `gen_ai.operation.name =
invoke_agent`, `gen_ai.agent.name` (`Role.name`, verbatim) y `gen_ai.agent.id`
(`Role.slug`, verbatim: seis roles responden "Tech Lead", y distinguirlos es lo único para
lo que existe la clave). Lleva también `gen_ai.provider.name` y `gen_ai.system`, las dos
grafías de "anthropic": los spans propios de Claude Code todavía usan `gen_ai.system`
(`probe.py`), y un lector que conozca una grafía y no la otra vería padre e hijos como
proveedores distintos. Ninguno de los dos es cierto para la tarea: no es una sola llamada,
así que no lleva ningún atributo `gen_ai.*`.

**La regla para lo que el semconv no nombra** (costo en dólares, versión de la entrega,
fase, categorías de devolución): la clave que **ya** se escribe igual en el span y en un
label de métrica conserva esa grafía; toda otra clave de aplicación toma el prefijo
`agentic.`. `product`, `role` y `role.outcome` son la misma cadena en las dos señales hoy,
así que se quedan sin prefijo — inventarles el prefijo sería fabricar una asimetría, no
preservarla. `role.model` y `role.rework` no lo son (la métrica los llama `model` y
`rework`), así que pasan a `agentic.role.model` y `agentic.role.rework`; junto con
`agentic.role.cost_usd`, `.failed_attempts`, `.version`, `.turns`, `.session_id`,
`.prompt_hash` y `.return_categories`. `return_categories` conserva la palabra por la misma
razón que la adenda de arriba: nadie suma un atributo de span entre corridas, pero
renombrar la palabra igual habría partido la historia de lectura del atributo en dos.

Ningún atributo de nivel `chat` (tokens, modelo de request o de response, parámetros de
muestreo) va en el span de rol: un rol gasta a través de varios modelos en la misma corrida
(`RoleRun.finish` guarda el último de cada patch), y un solo modelo en un span agregado
sería una afirmación falsa para un lector conforme al semconv. Tampoco va ninguna cifra en
dólares bajo una clave `gen_ai.*`: sigue la misma razón que la adenda de arriba sobre
`gen_ai.usage.cost` — Langfuse suma el costo de toda la traza y cada dólar contaría dos
veces.

`langfuse.observation.type = agent` se queda como está: es la clave del vendor, no la
convención, y ninguna prueba de este repo puede ver si Langfuse prioriza esa clave o el
`gen_ai.operation.name` nuevo cuando las dos están presentes — es la pregunta abierta que
lleva la segunda sonda. El collector (`transform/claude_code_usage`) no se toca: sigue
traduciendo los contadores de Claude Code a `gen_ai.usage.*` para que Langfuse precie, y
tocarlo repetiría el costo 0.00625 en vez de 0.01125 que la adenda de arriba ya documentó.

`opentelemetry.semconv` no se declara como dependencia — es un módulo privado, en estado
Development, que llega transitivamente con `opentelemetry-sdk`. Los nombres de la
convención son literales en `agentic/core/measurement/adapters/otel/semconv.py`, copiados a
mano y fijados por un test unitario: la fuente de verdad es la convención, no el paquete.

Consecuencias:

- Las trazas de antes y de después de este commit no comparten claves de atributo del
  span de rol: se paga a sabiendas treinta días de comparabilidad traza a traza.
- `gen_ai.agent.name` sigue siendo ambiguo a propósito (seis roles responden "Tech Lead");
  una herramienta externa que solo lea el nombre ve un agente donde hay varios.
- Nada de esto toca el punto 4 del pedido original: la entrada "Corrida de rol" del
  glosario de Proceso ya dice `_Campo_: invoke_agent`.

Opciones descartadas: prefijar *todo* atributo no-semconv con `agentic.`, sin excepción —
así descrito prohibiría `product`, `role` y `role.outcome`, que se quedan bare a propósito;
que la cadena que identifica un rol sea una y la misma en cada sitio que la toca un diff —
superado por pedir que la fuente sea siempre `Role.name` o `Role.slug`, que un revisor
puede chequear mirando un solo call site en vez de comparar tres.

## Adenda (2026-09-18): la corrida de rol es la muestra, no el nodo

Del 13 al 18 de septiembre, lo medido no alcanzaba para saber dónde se va la plata ni si
el trabajo es bueno. `tech_lead`, `tech_lead_review`, `tech_lead_interviewee`,
`tech_lead_ephemeral` y sus variantes `*_platform` eran todos `role="Tech Lead"`: el 58%
del gasto sin distinguir plan, entrevista y revisión. Sin tokens ni llamadas: solo Langfuse
las tenía, y en `events_only` no expone métricas. Las escaladas eran un conteo sin motivo.
Cada invocación (`agentic resume`) repetía el acumulado de la tarea, así que el retrabajo
se contaba dos veces y todo el costo iba a la versión que terminaba, no a las que
corrieron. Nada medía calidad: ni hallazgos por revisión, ni si una entrega llegó roja al
presubmit, ni rondas o preguntas de una entrevista.

Decisión, en cinco familias, todas eventos, todas etiquetas de cardinalidad acotada (un
enum, un `Role.slug`, o la salida de una función `rules.*` cerrada; `system.version` y
`prompts.fingerprint` siguen siendo las dos únicas sin acotar):

1. **Labels nuevos en las métricas de rol que existen**: `role.slug`, `phase`
   (`rules.phase_of`) y `role.outcome` también en `agentic.role.cost_usd` (antes solo en
   `.duration`). Se agregan, no se renombran — el spine, `role-labels-spine`.
2. **Tokens, llamadas, turnos y herramientas** por corrida de rol, desde
   `ResultMessage.usage` y del conteo del runner sobre los mensajes que ya observa —
   subtarea `tokens`.
3. **Calidad**: hallazgos por revisión (`agentic.review.findings`), chequeos de presubmit
   por entrega (`agentic.presubmit.checks`, `.duration`) y fallas de rol por motivo
   (`agentic.role.failures`, `.failure_cost_usd`) — subtarea `quality`.
4. **Entrevistas**: rondas, preguntas por destinatario, invariantes propuestos y
   adoptados — subtarea `interviews`.
5. **Tarea**: deltas por invocación (`agentic.invocation.*`, lo que una corrida de
   `run`/`resume` gastó ella sola, nunca el acumulado) y el motivo de escalado como un
   campo del estado, `escalation_class`, cerrado y escrito por un solo constructor de
   dominio junto con `escalated` — subtarea `task-events`.

**El problema que el spine resuelve primero: un span, muchas corridas de rol.**
`RoleRun.finish` leía `runs[-1]`: las cuatro rondas efímeras del plan, las cuatro de la
entrevistada y las consultas de PM colapsaban en una muestra con un modelo, una sesión y
un hash de prompt. Ahora itera cada entrada de `runs` de la patch: una muestra de
`agentic.role.cost_usd` y un span hijo por corrida, con su propio agente, modelo, sesión y
hash de prompt — la forma que esta misma adenda ya fijó para un rol, aplicada por fin a
algo que es una corrida sola. El span de rol sigue agregando (el modelo y la sesión de la
última corrida, como siempre) y pasa a llevar la **tupla** de cada hash de prompt que
corrió, no el último que pisó a los otros.

**La regla residual: la plata que ninguna corrida contabiliza.** `run_record` graba
`cost_usd = output.cost_usd`, que ya incluye los intentos fallidos, así que para un nodo
sano las corridas suman el `cost_usd` de la patch. Dos caminos dejan plata sin corrida: la
escalada de `run_role` y `graph._node` cerrando con `run.finish({})` en una excepción.
Después de iterar las corridas, el adaptador deja una muestra residual por la diferencia
sobre medio centavo (el mismo piso que `rules.spent_by_phase` ya usaba) y una muestra en
cero para una patch vacía — sin esto la tarea enviaría la misma discrepancia que el issue
#72 existe para arreglar, mientras lo posterga a propósito.

**Un `record()` nunca pisa el mismo instrumento con los mismos labels.** Dos corridas que
comparten cada label salvo la sesión ya no colapsan en una muestra: `Run._emit` pasa de
una vez por nodo a una vez por corrida (y una más para la residual, y una más para las
fallas por motivo). El volumen de flushes crece con el número de corridas por patch, no
por nodo — antes, una entrevista de ocho rondas dejaba un flush; ahora deja nueve.

**Nombres que se agregan, nunca se repiten.** `agentic.role.duration` queda exactamente
como estaba — mismos labels, mismo grano de nodo, mismos bytes en el span de span-test —
porque una duración por corrida pide su propia serie: `agentic.role.run_duration`, medida
*dentro* del cupo de concurrencia, con la espera en `agentic.role.queue_wait`, su propia
serie, para que un panel nunca reporte cola como trabajo. `agentic.role.cost_usd` gana
labels solamente, lo que es aditivo y no cambia ninguna suma histórica.

**`role.outcome` en un grano por corrida.** El mismo juego de valores en ambas series
(`delivered`, `returned`, `escalated`, `failed`): el veredicto de la patch se aplica a la
corrida contra la que se escribió — la última cuya sección es una clave de
`patch["sections"]` — y toda otra corrida de una patch con varias es `delivered`; `failed`
y `escalated` viven en la muestra residual, que es lo único sin corrida detrás. Una función
pura en `core/measurement/domain/metrics.py`.

**Consecuencias.**

- Un documento escrito antes de esta adenda no tiene `slug`, `started_at`, `ended_at` ni
  `queue_wait_s` en sus corridas, ni `reason`, `slug` ni `section` en sus fallas: cada
  lectura usa `.get()` con su default documentado, y una corrida sin `slug` deja
  `role.slug=unknown` en vez de perderse (el mismo trato que `product` e `id` tuvieron en
  sus propias adendas).
- El nodo de revisión pasaba `TECH_LEAD` (el rol del plan) a `role_span`: la revisión y el
  plan compartían `role.slug=tech_lead`, la causa concreta del 58% sin distinguir que abre
  esta adenda. Pasa `TECH_LEAD_REVIEWER` (`role.slug=tech_lead_review`); el rol que el
  caso de uso corre no cambia, solo la etiqueta del span.

Opciones descartadas: convertir los cinco pares corrida-por-corrida en una sola muestra
agregada con más labels — pierde la posibilidad de un `queue_wait` o un `run_duration` por
corrida, que es justo el punto; mandar la duración por corrida bajo el nombre existente de
`agentic.role.duration` — partiría en dos el significado de una serie de trece meses a
mitad de camino, lo que la regla "se agrega, no se renombra" existe para evitar.

## Adenda (2026-09-18, segunda): un dólar de retrabajo, una sola muestra, y la muestra de tarea es de la Corrida

La adenda de arriba llevó el costo al grano de corrida y dejó dos cosas sin cerrar, que el
issue #72 cobra. `metrics.rework_cost_usd` sumaba las corridas con `version > 1` más todo
`failures`, y `run_role` ya mete el costo de esas fallas adentro del `cost_usd` de la corrida
que entrega: la plata de una falla dentro de una corrida devuelta se contaba dos veces
(`task.rework_usd=25.64` contra 12.34 en los spans de rol, corrida 4 de GH-12). Y el label
`rework` del grano de rol marcaba sólo `version > 1`, así que la plata de falla de una
corrida de primera entrega no aparecía en ninguna muestra marcada y la suma nunca podía
cerrar. El comportamiento lo gobierna `docs/product/rework.md`; acá va lo que cambia de esta
decisión.

**La plata de falla de una corrida es un dato de la corrida, no una deducción.** `Failure`
no tiene versión ni id de corrida, y en un estado acumulado dos corridas de la misma
`section` (v1 y v2) no se pueden distinguir por su `section`: la unión existe sólo en el
momento en que `run_with_retry` suma `spent(failures)` al `cost_usd` que devuelve. Ahí se
graba, en `run_record`, como campo nuevo de `RoleRun`, `failed_cost_usd`. De ahí salen las
dos vistas con una sola función: por corrida, todo el `cost_usd` si `version > 1` y
`failed_cost_usd` si no; por estado o por patch, esa suma más lo que ninguna corrida
contabiliza (`cost_usd − Σ runs`). `delta(before, after)` da la cifra de una invocación con
la misma función y sin código nuevo. Descartado acumular un escalar `rework_usd` en el
estado: sería una segunda fuente de verdad que `apply`, `delta`, el documento y
`resume.from_document` tendrían que arrastrar, y no evita necesitar igual la cifra por
corrida. Un documento escrito antes de esta tarea no tiene el campo y sus corridas
`version == 1` leen `0.0`: sub-reportan su plata de falla, en las dos vistas por igual, y eso
es preferible a inventar una atribución por orden de lista.

**Sólo `agentic.role.cost_usd` se parte.** Una corrida `version == 1` con intentos fallidos
deja dos muestras de esa serie, mismos labels salvo `rework`: el resto en `false` y
`failed_cost_usd` en `true`. El bloque de tokens, llamadas, turnos, herramientas,
`run_duration` y `queue_wait` se emite **una sola vez**, bajo el label set de la corrida:
esos tokens son los de la llamada que entregó, y un panel de tokens no se mueve porque se
movió plata. Una corrida sin intentos fallidos queda byte a byte como antes. Se conservan las
dos sumas a la vez —el costo por rol y el retrabajo por rol— sin serie ni label nuevos, que
es lo que ninguna otra forma daba: una muestra marcada agregada encima contaría esos dólares
dos veces en el panel de costo, y `agentic.role.failure_cost_usd` sigue siendo no aditiva con
`cost_usd` a propósito, porque contesta por qué falló y no qué fue retrabajo.

**Toda residual es retrabajo, y el piso deja de filtrar la emisión.** Un dólar que ninguna
corrida contabiliza es plata que no produjo entrega, que es la letra del glosario. Y el piso
de medio centavo que esta misma adenda había puesto como filtro de emisión no se sostiene:
es por patch, y la tolerancia que `docs/product/rework.md` promete es por tarea, así que
veinte patches pueden esconder ~0.10 USD sin que ninguna quede por encima del piso. Se
separan los dos trabajos que el número hacía: `RESIDUAL_EPSILON_USD = 1e-9` decide si una
residual es ruido de punto flotante (la suma de las corridas de una entrevista en otro
orden) y no se emite, y `RESIDUAL_FLOOR_USD = 0.005` se queda siendo lo que siempre fue, la
tolerancia de redondeo con que `rules.spent_by_phase` y el test de aceptación comparan. La
muestra en cero de una patch vacía no cambia. La residual deriva además su `phase` en
cascada —la corrida que entregó, si no la `section` de la primera falla, si no `unknown`
literal— y nunca omite la clave: un label ausente y un label con valor son series distintas
y nadie las suma después. Por lo mismo, `_emit_failures` pasa a escribir `unknown` donde hoy
omite `phase`, lo que deja dos series donde un lector esperaría una para las fallas emitidas
antes de este commit.

**La muestra de tarea sale cuando termina la Corrida, no en cada Invocación.** `TaskRun.finish`
separa sus dos trabajos: los atributos del span se estampan siempre (una invocación pausada o
caída sigue siendo legible en Langfuse) y los cinco gauges de tarea salen sólo si un predicado
puro sobre `Status` dice que la Corrida terminó: `ready_for_review` y `discarded`. Una pausa
no emite, y una caída tampoco —el checkpoint permite reanudarla y el sistema no puede saber si
alguien lo hará—, así que una tarea pausada dos veces deja una muestra y no tres. Lo que una
invocación gastó ella sola lo sigue diciendo `agentic.invocation.*`, que no se toca. El
universo de las métricas de tarea pasa a ser **Corridas terminadas**.

**El dashboard se edita en el mismo commit.** "Escaladas" y su gemelo por versión contaban
`agentic_task_finished{task_status="waiting_on_human"}`, que desde acá no se emite nunca más:
pasan a `agentic_task_paused`, que ya lleva `escalation_class` y cuenta la pausa cuando
ocurre. Los filtros `task_status!~"waiting_on_human|crashed"` **se quedan**: las muestras ya
emitidas con ese estado viven trece meses, y sacar el filtro las haría entrar en los promedios
y en los conteos, que es este mismo doble conteo resucitado desde la historia.

**Riesgos conocidos, escritos para no descubrirlos en un panel.** La plata que un nodo gasta y
pierde al levantar una excepción antes de devolver patch queda fuera de las dos vistas y de
`agentic.invocation.*`: sólo la conoce `RunLimits`, que vive una corrida y no se emite;
recuperarla pide que los diecisiete nodos devuelvan patch antes de propagar y es otra tarea.
La igualdad entre las dos vistas es un invariante por tarea que se verifica en el test de
aceptación y en la regresión de punta a punta del grafo, nunca en un panel: ninguna serie de
rol lleva id de tarea y ninguna lo va a llevar, porque la cardinalidad acotada es lo que esta
adenda protege. En Grafana la resta se lee agregada sobre Corridas terminadas de una ventana
cerrada, filtrando `product` y `system.version`, y sirve como detector de olor: una tarea
pausada al cierre de la ventana aporta sus muestras de rol y ninguna de tarea, así que la
resta puede dar negativa sin que nada esté mal. Lo que sí significa algo es una discrepancia
positiva grande y persistente, del orden de los 13 dólares de GH-12.

## Adenda (2026-09-21, novena): toda métrica dice sobre qué versión del sistema corrió

Lo que se mide es el sistema, no el producto: el eje de todo panel es `system_version`. Siete
familias no lo llevaban y por eso se leían por rol, sin poder compararse entre versiones:
`agentic.role.tokens`, `.calls`, `.turns`, `.tool_calls`, `.subagent_cost_usd`, `.duration`,
`.failures`, `.failure_cost_usd`, `agentic.presubmit.checks`, `.duration` y las tres
`agentic.interview.*`. Esta adenda **revierte una frase de la adenda del 2026-09-18**, que las
dejó sin el label a propósito ("un panel de tokens agrupa por rol y modelo, nunca por el
checkout que lo corrió", issue #24): el panel que motivaba esa frase hoy agrupa por versión,
porque la comparación de una versión con la siguiente es la única pregunta para la que el
dashboard existe. Se agrega el label; no se renombra ni se borra ninguna serie.

**El label se agrega en un solo lugar por juego.** `token_labels` —el único juego de labels de
`telemetry.py` definido por sustracción— desaparece: cada gauge por corrida de `_emit_run` setea
`labels` verbatim, así `agentic.role.max_turns` sigue graficable al lado de `agentic.role.turns`
por construcción y no por un comentario (issue #81). `_emit_failures`, `_emit_presubmit` y
`_emit_interview` suman `system.version` a su juego. Ningún sitio de emisión escribe la clave por
su cuenta: la pone el helper.

**`agentic.role.duration` no lo lleva, y su panel se mueve a otra serie.** La adenda del
2026-09-18 la congeló *porque* la duración por corrida ganó su propia serie, y
`agentic.role.run_duration` ya lleva `system.version` y `role`: darle el label al grano de nodo
sería una cifra computada de dos maneras, que es lo que esta tarea existe para sacar. "Duración
promedio por rol" pasa a leer `run_duration` **y** `queue_wait`, dos series, `sum by
(system_version, role)`: `queue_wait` existe justamente para que un panel nunca reporte cola como
trabajo, y sin él una versión con más contención se leería más rápida mientras el reloj de pared
empeora. Sin apilar: la suma no es el reloj de un nodo, porque una patch puede tener varias
corridas. Consecuencia: después de esta tarea ningún panel consulta `agentic.role.duration`. Sigue
emitida y congelada, y el número de grano de nodo se lee en el documento de tarea y en el span de
rol de Langfuse.

**`_emit_presubmit` deja de omitir `phase`.** Escribía la clave sólo si la patch tenía una corrida
con veredicto; la adenda segunda del 2026-09-18 ya había decidido lo contrario para la muestra
residual y para `_emit_failures` ("un label ausente y un label con valor son series distintas y
nadie las suma después") y este sitio quedó afuera. Pasa a escribir `unknown`. Es aplicar una regla
escrita, no una decisión nueva.

**`agentic.role.cold_fallback` queda con un solo juego de labels, y eso le quita tres.** Se emitía
desde `_emit_run` con el juego completo por corrida y desde `_emit_failures` con uno corto: dos
formas para una serie. Un helper arma uno solo —`product`, `role`, `role.slug`, `phase`,
`system.version`— y los dos sitios lo llaman. La rama de corrida **pierde** `model`, `rework` y
`role.outcome`, a propósito: el hecho que la serie registra es que el sistema soltó la sesión del
llamador y arrancó en frío (ADR-0004, sexta adenda), que es una propiedad del rol y de la fase y no
de un modelo; inventarlos del lado de las fallas habría sido la atribución fabricada que la adenda
segunda del 2026-09-18 rechaza para el `phase` de la residual. Se dice acá porque es el único lugar
donde esta tarea borra labels, y la regla general sigue siendo que se agregan. La serie gana además
su fila en `observability/README.md`, que nunca tuvo.

**El `$version` del dashboard deja de salir de las tareas terminadas.** Se armaba con
`label_values(agentic_task_finished, system_version)`, y desde la adenda segunda del 2026-09-18 esa
serie sólo existe para Corridas terminadas: una versión cuyas tareas pausaron todas no aparecía en
el desplegable. Mientras las siete familias no filtraban por versión daba igual; con el filtro
puesto, la versión cuyos roles fallan tanto que escalan todo —justo la que hay que mirar en
`agentic.role.failures`— habría quedado innombrable. Pasa a `label_values(agentic_role_cost_usd,
system_version)`: toda corrida de rol la emite, y la residual sale aun con la patch vacía. Una
versión que corrió y no terminó nada aparece con los paneles de tarea vacíos, que es honesto.

**Los cortes históricos, escritos para no descubrirlos en un panel.** Son tres, de la misma forma
que el corte de `product` del 2026-09-17: las muestras de las siete familias emitidas antes de este
commit no llevan `system.version`, así que un panel con una versión concreta elegida no las ve (con
`All`, `.*` sí las alcanza); las de presubmit anteriores omiten `phase` y dan dos series donde un
lector espera una; y las de `cold_fallback` anteriores llevan tres claves que las nuevas no. Nada se
rellena: son eventos, y las muestras ya emitidas no se reescriben.

**Cómo se verifica.** La tabla de `observability/README.md` pasa a ser contrato ejecutable: para
cada familia que esta tarea toca, la unión de las claves de label de las muestras que un
`RoleRun.finish` real emite tiene que ser **igual** a la fila de esa métrica —nunca un subconjunto—
contra una patch máxima declarada en el plan, y una familia que no emite ninguna muestra es una
falla con su nombre en el mensaje, nunca un skip. `agentic.role.cost_usd` y `agentic.role.duration`
entran como controles: la primera tiene que seguir igual a su fila, la segunda igual a su fila de
tres claves, así el mismo test que prueba que el label llegó prueba que la serie congelada no se
movió. Del lado del dashboard, un assert nuevo exige que toda expresión que lee una de estas
familias lleve `system_version` en su `by (...)` y `system_version=~"$version"` en su selector; el
parser de labels de `tests/test_dashboard.py` suma `!=` y `!~` a su alternación, porque esta tarea
mete el primer matcher negativo del dashboard (`model!=""`, que excluye la muestra residual, la
única sin corrida y por lo tanto sin tokens detrás).

Descartado: un ADR nuevo en vez de esta adenda —dejaría la frase revertida del 2026-09-18 como lo
primero que encuentra quien pregunte por qué los tokens no llevan versión—; darle el label a
`agentic.role.duration`; y ponerle a `$ / 1k tokens de salida` un nombre que esconda su caveat: la
fila del README dice lo que es cierto, dólares de las corridas del rol con los intentos fallidos
adentro sobre los tokens de salida de las corridas que reportaron alguno, con la residual excluida.
