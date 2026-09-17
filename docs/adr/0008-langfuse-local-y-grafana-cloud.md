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
