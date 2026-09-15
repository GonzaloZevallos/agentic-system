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
