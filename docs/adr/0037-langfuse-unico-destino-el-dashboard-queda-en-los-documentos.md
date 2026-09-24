---
status: accepted
date: 2026-09-24
---

# Langfuse queda como único destino; el dashboard de Grafana Cloud queda en los documentos de tarea

Supersede la mitad Grafana Cloud de ADR-0008 (la decisión inicial y la adenda del
2026-09-14 "las métricas son eventos, no tasas" en la parte que las manda a un backend de
Prometheus) y su adenda del 2026-09-18, segunda ("un dólar de retrabajo, una sola muestra,
y la muestra de tarea es de la Corrida") en la parte que cierra la cardinalidad porque
"ninguna serie de rol lleva id de tarea y ninguna lo va a llevar". Supersede también, por
nombre, ADR-0032 ("el dashboard se publica por script con el datasource como variable") y
ADR-0033 ("la verificación acepta el literal `$DS_PROMETHEUS`, no el uid sustituido"): los
dos describen un publicador de dashboards de Grafana que este diff borra. Ninguno de los
cuatro se edita — un ADR aceptado se supersede, nunca se edita (ADR-0010) — y esta ADR es
donde alguien que busque "por qué ya no hay Grafana" tiene que llegar.

La licencia de Grafana Cloud se termina. La mitad Langfuse de ADR-0008 no se toca: sigue
siendo el destino de todas las trazas, del sistema y de Claude Code, por el mismo collector.
Lo que se va es el segundo destino y todo lo que sólo existía para alimentarlo.

## Qué pasa con cada hecho que hoy es una métrica

Langfuse no ingiere métricas OTel — sólo trazas, observaciones y scores — así que esto no es
copiar panel por panel. Cada familia de `observability/README.md` cae en uno de tres casos,
y esa tabla es la lista normativa, familia por familia: esta ADR no la repite, dos documentos
diciendo la misma lista es la deriva que ADR-0021 y ADR-0029 ya rechazan.

1. **Ya es un atributo de span** — sin cambio: el costo, el retrabajo, la versión de la
   entrega, el modelo, los turnos, la sesión, la huella de prompts y las categorías de
   devolución del span de rol; el costo, el retrabajo, las devoluciones, los fallos, las
   corridas y el escalado del span de tarea, con sus siete labels.
2. **Un conteo por dimensión sin span propio** — pasa a ser un atributo de span con un
   array de strings `"<dimensión>=<conteo>"`, en el span cuyo grano ya tiene la familia. No
   es una forma nueva: es la que ADR-0008 (adenda del 2026-09-17) ya eligió para
   `agentic.role.return_categories`, aplicada a sus hermanas.
3. **Nada** — la familia no tiene reemplazo en Langfuse; su fila en `observability/README.md`
   nombra quién la lee ahora (`agentic eval compare`, el documento de tarea).

**La traza de tarea lleva el número de issue.** La restricción de cardinalidad de la segunda
adenda del 2026-09-18 de ADR-0008 ("ninguna serie de rol lleva id de tarea y ninguna lo va a
llevar") protegía a Prometheus: una serie por tarea explota la cardinalidad de un backend de
series. Ese backend no existe más de este lado. En Langfuse una traza por tarea es la unidad
normal de lectura, así que la traza de tarea lleva el id desde este commit. Los labels que
hoy segmentan (`product`, `system_baseline`, `arm`, `system_version`, `type`, `role`,
`model`) viajan como tags o metadata de la traza para poder filtrar por ellos.

**El scorecard se calcula igual que hoy y de una sola fuente.** `core/measurement/domain/
scorecard.py` ya computa las cuatro cifras de ADR-0028 (autonomía, fallos propios por tarea
excluyendo `account_limit` y `upstream`, costo por entrega, retrabajo) leyendo los documentos
de tarea de una línea de base — el mismo camino que `agentic eval compare` recorre. Ninguna
cifra se deriva dos veces: Langfuse nunca calcula un scorecard propio, y esta tarea no le
empuja ninguno tampoco — la compuerta de la sección siguiente resultó desfavorable o
`unreachable` en tres de sus cuatro respuestas, así que `langfuse-panels` no construyó ningún
cliente de ingesta de scores (ver "La subtarea de paneles" más abajo). El lector del
scorecard es `agentic eval compare` sobre los documentos de tarea de la línea de base; "los
mismos números leídos por los dos caminos" es cierto porque hay un solo camino, no por
reconciliar dos.

## Spelling: las dos mitades de la regla de ADR-0008

La adenda del 2026-09-17 de ADR-0008 era de dos lados: "la clave que ya se escribe igual en
el span y en un label de métrica conserva esa grafía; toda otra clave de aplicación toma el
prefijo `agentic.`". Este diff borra el lado métrica de esa frase — no hay más labels de
métrica — así que sólo sobrevive el segundo lado, y gobierna solo desde acá:

- **Toda clave nueva que este diff o cualquier subtarea de esta tarea agregue se llama
  `agentic.<familia>.<nombre>`**, sin excepción, sin inventar un prefijo distinto.
- **Ninguna clave bare existente se renombra**: `product`, `role`, `role.outcome` y las
  claves de label del span de tarea quedan exactamente como están. "Se agregan, no se
  renombran" sigue vigente, y cubre también el nombre de un atributo de span.

## `agentic close` deja de gatear sobre una muestra que no va a existir

El endpoint gate (`metrics_configured()`) y el orden `flush` antes de `record_closure`
existían porque un veredicto necesitaba una muestra detrás. Sin backend de métricas no hay
sujeto para ese predicado, y un chequeo que siempre da verdadero es una puerta muerta.
`close` pasa a ser lo que ya era en parte: un lector del pull request que escribe cinco
claves de frontmatter. El barrido corre sin condición, se frena ante una falla de `gh` en
vez de una falla de flush, y conserva cada código de salida y cada otra línea de skip byte a
byte — sólo el test de la puerta de endpoint se edita en `tests/test_cli.py`. Esto es
estrictamente más capaz y sigue siendo un cambio de comportamiento: va en el cuerpo del pull
request, en una frase, para el humano (ADR-0019).

## Qué se pierde, dicho acá

- **La lectura agregada de 13 meses.** Prometheus retenía las métricas ese tiempo; Langfuse
  corre sobre `observability/langfuse/docker-compose.yml`, con volúmenes Docker nombrados en
  una laptop, sin TTL declarado en ningún lado del repo y sin backup. El documento de tarea
  es el registro durable; Langfuse es una superficie de lectura sobre la ventana que la
  instancia sostenga, nunca el archivo.
- **El detector de olor de retrabajo por panel** (la resta entre las dos vistas de
  `agentic.role.cost_usd` y `task.rework_usd` sobre una ventana cerrada de Grafana). Su
  reemplazo es leer los mismos dos números del documento de tarea o de la traza, sin panel
  agregado detrás — `agentic eval compare`.
- **Todo lo demás que un panel de Grafana mostraba sin equivalente directo** queda en la fila
  de su familia en `observability/README.md`, con su lector (`agentic eval compare`, la
  API de Langfuse, o el documento de tarea). Esta ADR no repite esa lista.

## La compuerta contra la instancia corriendo

Cuatro preguntas, contra el `langfuse:4` que corre en este checkout (`docker ps`: contenedor
`langfuse-langfuse-web-1`, imagen `docker.langfuse.com/langfuse/langfuse:4`, puerto `3000`),
con las credenciales de `observability/langfuse/.env` (ruta absoluta fuera del worktree,
leída, nunca escrita). Comandos y salidas, verbatim.

### Salud de la instancia

```
$ curl -s http://localhost:3000/api/public/health
{"status":"OK","version":"4.35.0"}
```

### (i) ¿Se puede crear un score numérico contra un trace id que la ingesta ya vio, y contra uno que no?

Primero se intentó crear una traza con la API nativa de ingesta (`trace-create`), para tener
un trace id "visto" de forma inequívoca:

```
$ curl -s -u "$PUB:$SEC" -X POST http://localhost:3000/api/public/ingestion \
    -H "Content-Type: application/json" \
    -d '{"batch":[{"id":"<event-id>","type":"trace-create","timestamp":"<now>",
         "body":{"id":"<trace-id>","name":"gate-probe-task",
                 "tags":["arm=default","product=agentic-system"],
                 "metadata":{"issue":129,"role":"tech_lead"}}}]}'
{
  "successes": [],
  "errors": [
    {
      "id": "810baef9-a1d7-4b5a-8924-674fa3ffceaf",
      "status": 400,
      "message": "Event type not accepted",
      "error": "Event type \"trace-create\" is not accepted by /api/public/ingestion when
                 LANGFUSE_MIGRATION_V4_WRITE_MODE is events_only. This endpoint only accepts
                 score and log events. Upgrade the client or integration to a v4-compatible
                 SDK or OTLP ingestion path. ..."
    }
  ]
}
```

Esta instancia corre con `LANGFUSE_MIGRATION_V4_WRITE_MODE=events_only`: la única forma de
crear una traza es OTLP. Se mandó una directo al endpoint OTLP de Langfuse (sin pasar por el
collector, para aislar la pregunta):

```
$ curl -s -o /dev/null -w "HTTP:%{http_code}\n" -X POST http://localhost:3000/api/public/otel/v1/traces \
    -H "Content-Type: application/json" -H "Authorization: Basic $LANGFUSE_OTEL_BASIC" \
    --data @/tmp/otlp_trace.json   # un ResourceSpans con service.name=agentic-system
HTTP:200
```

Con esa traza (`traceId=6b8f2f500f544e79b7ad68345a1a4272`) confirmada por la ingesta OTLP, y
un segundo trace id fabricado que ninguna ingesta vio nunca:

```
$ curl -s -u "$PUB:$SEC" -X POST http://localhost:3000/api/public/scores \
    -H "Content-Type: application/json" \
    -d '{"traceId":"6b8f2f500f544e79b7ad68345a1a4272","name":"agentic.gate.known","value":1,"dataType":"NUMERIC"}'
{"id":"fab6f0dc-5d37-4b94-86fb-dab8ae086f32"}   HTTP:200

$ curl -s -u "$PUB:$SEC" -X POST http://localhost:3000/api/public/scores \
    -H "Content-Type: application/json" \
    -d '{"traceId":"<trace-id-que-nunca-se-ingirio>","name":"agentic.gate.unknown","value":1,"dataType":"NUMERIC"}'
{"id":"511729ec-39ad-4bf3-a4e6-07d58f53ea22"}   HTTP:200
```

**Respuesta**: sí, se puede — la creación de un score es asincrónica (un evento encolado) y
la API responde `200` con un id **igual** tanto si el trace id existe como si no; no valida
sincrónicamente. Qué pasa después con el score huérfano (si se descarta en el worker, si
queda sin trace o si se adjunta si la traza llega más tarde) **no se pudo verificar contra
esta instancia**: en `events_only` los endpoints de lectura puntual también están
deshabilitados —

```
$ curl -s -u "$PUB:$SEC" "http://localhost:3000/api/public/traces/6b8f2f500f544e79b7ad68345a1a4272"
{"message":"This endpoint is not available on deployments running in Langfuse v4 events_only mode. Learn more about Langfuse v4 at: https://langfuse.com/docs/v4"}
HTTP:404

$ curl -s -u "$PUB:$SEC" "http://localhost:3000/api/public/scores?traceId=6b8f2f500f544e79b7ad68345a1a4272"
{"message":"This endpoint is not available on deployments running in Langfuse v4 events_only mode. Learn more about Langfuse v4 at: https://langfuse.com/docs/v4"}
HTTP:404
```

— así que la mitad "qué pasa con uno que no existe" queda `unreachable` sobre esta instancia.

### (ii) ¿Una consulta de dashboard/métricas puede agrupar por una clave de metadata, o solo por tag, name, user/session y score name?

```
$ curl -s -u "$PUB:$SEC" "http://localhost:3000/api/public/metrics"
{"message":"This endpoint is not available on deployments running in Langfuse v4 events_only mode. Learn more about Langfuse v4 at: https://langfuse.com/docs/v4"}
HTTP:404

$ curl -s -u "$PUB:$SEC" "http://localhost:3000/api/public/metrics?query=<agrupando+por+tags>"
{"message":"This endpoint is not available on deployments running in Langfuse v4 events_only mode. Learn more about Langfuse v4 at: https://langfuse.com/docs/v4"}
HTTP:404

$ curl -s -u "$PUB:$SEC" "http://localhost:3000/api/public/metrics?query=<agrupando+por+metadata.issue>"
{"message":"This endpoint is not available on deployments running in Langfuse v4 events_only mode. Learn more about Langfuse v4 at: https://langfuse.com/docs/v4"}
HTTP:404
```

**Respuesta**: `unreachable` — la API de métricas entera está deshabilitada en este modo de
despliegue. No se pudo verificar ninguna forma de `groupBy` contra esta instancia.

### (iii) ¿Existe un endpoint de crear/actualizar dashboard en esta imagen?

```
$ curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:3000/api/public/dashboards"
404
$ curl -s -o /dev/null -w "%{http_code}\n" -X POST "http://localhost:3000/api/public/dashboards" -d '{}'
404
```

Las dos respuestas son la página 404 estándar de Next.js (`"This page could not be found."`),
no un 404 de la API pública con `message` — es decir, la ruta no existe en absoluto, a
diferencia de (i) y (ii) donde el 404 sí trae el `message` de "deshabilitado en events_only".

**Respuesta**: no. `unfavorable` — no hay endpoint de dashboards en esta imagen, deshabilitado
o no.

### (iv) ¿Los tags son solo strings planos — `arm=default` como string es la única forma de key/value?

No se pudo confirmar de forma positiva contra esta instancia: el único camino de creación de
trazas en `events_only` es OTLP (arriba, (i)), y OTLP no tiene un concepto de "tags" de
Langfuse — son un campo propio de la API nativa de ingesta, y esa API rechaza
`trace-create` en este modo (ver el error de (i)). No hay endpoint de lectura para inspeccionar
qué, si algo, terminó en `tags` sobre la traza ingerida por OTLP.

**Respuesta**: `unreachable`.

### Veredicto de la compuerta

Tres de cuatro respuestas son `unreachable` o `unfavorable` contra la instancia que corre hoy
(`langfuse:4`, `events_only`): no hay API de métricas, no hay API de dashboards, y no se pudo
confirmar la forma de los tags. La única favorable, (i), viene con su propia mitad
`unreachable`. Por la regla que este mismo plan fija de antemano ("si el piso no se alcanza",
ADR-0021 aplicado en avance): **la subtarea de paneles no construye ningún dashboard**. Envía
solamente el párrafo que registra esto, con esta transcripción como evidencia — "no construir
nada" es un resultado de primera clase, no una escalada ni un fallo de rol.

### La subtarea de paneles: ningún panel, ningún cliente de ingesta

Con el veredicto de arriba, la subtarea de paneles y su publicador no construyen nada:
ningún dashboard de Langfuse, ninguna definición versionada, ningún cliente de ingesta de
scores. No hay credenciales `LANGFUSE_*` de ingesta en ningún módulo nuevo, no hay un
segundo transporte, y no hay ensanche de superficie (CODING_STANDARDS.md §8) para
justificar. "No construir nada" es el resultado completo de esa subtarea, no una entrega
parcial a la espera de otra vuelta.

Las dos filas del dashboard de Grafana que un score o una traza de Langfuse podrían haber
mostrado quedan con su lector nombrado, en vez de con un panel:

- **Scorecard** — las cuatro cifras de ADR-0028 (autonomía, fallos propios por tarea,
  costo por entrega, retrabajo) por línea de base. Su lector es `agentic eval compare`,
  que ya las calcula desde los documentos de tarea con la misma función de
  `core/measurement/domain/scorecard.py` que este ADR describe arriba — no hay una
  segunda cuenta que reconciliar.
- **Experimentos** — el mismo corte de cifras segmentado por `arm` dentro de una línea de
  base (ADR-0028, ADR-0030). Esta fila **no tiene reemplazo**: `agentic eval compare`
  bucketiza sólo por línea de base (`scorecard.bucket_by_baseline`, que agrupa por
  `system_baseline`/`system_commit` y nunca por `arm`) y su parser (`agentic eval
  compare <from> <to>`) no toma un brazo como argumento, así que no hay comando que
  separe un brazo de otro dentro de una línea de base — el corte por experimento del
  panel de Grafana se pierde con esta tarea. Lo que queda es el `experiment` que cada
  documento de tarea ya guarda en su frontmatter (ADR-0030, un dict por tarea, no
  agregado) y la clave hermana `experiment` que `agentic eval score` escribe en cada
  línea del JSONL (issue #28) — legible directamente, a diferencia de `baseline_id`, que
  sólo mezcla esa snapshot en un digest SHA-256 truncado a 8 hex (para que los dos brazos
  de una misma declaración caigan en líneas de base distintas) y no se puede leer de
  vuelta. Separar un brazo de otro es leer esos documentos o ese JSONL a mano, o escribir
  un script de un solo uso sobre ellos, nunca un comando existente.

## Consecuencias

- El collector (`observability/collector/otel-collector.yaml`) pierde `exporters.otlphttp/
  grafana`, `extensions.basicauth/grafana` y el bloque `extensions:` entero,
  `processors.filter/system_only`, `processors.attributes/drop_title` y las pipelines
  `traces/grafana` y `metrics/grafana`. `traces/langfuse` y `transform/claude_code_usage`
  quedan byte a byte iguales — ninguno de los dos processors ni el exportador borrados
  tenían efecto sobre esa pipeline. `observability/collector/probe.py` no se toca: que siga
  idéntico es la prueba más barata de que la mitad Langfuse no se movió.
- `observability/grafana/` (el dashboard versionado y su README de alta) y
  `scripts/publish_dashboard.py` quedan como historia: se borran del árbol; este commit es
  donde estaban.
- Ningún `GRAFANA_*` sobrevive fuera de `docs/adr/0032` y `docs/adr/0033`, que conservan el
  suyo porque un ADR aceptado no se edita.
- `agentic close` deja de gatear sobre `metrics_configured()`; ver la sección de arriba y el
  cuerpo del pull request para el humano.
- El glosario de Medición (`agentic/core/measurement/CONTEXT.md`) actualiza *Métrica del
  sistema*, *Muestra*, *Línea de base* y *Scorecard* para describir un backend de métricas
  que dejó de existir; `tests/test_language.py` no puede detectar que esa prosa quedó vieja,
  así que el cambio se nombra acá y en el brief de la subtarea que lo hizo.
- La tabla de `observability/README.md` es la lista normativa de qué pasó con cada familia;
  esta ADR apunta a ella y no la repite.

## Opciones descartadas

- **Copiar el dashboard panel por panel a un backend de métricas de Langfuse**: Langfuse no
  ingiere métricas OTel, sólo trazas, observaciones y scores. No hay panel por panel posible.
- **Mantener los dos destinos** hasta confirmar que Langfuse alcanza: la licencia de Grafana
  Cloud se termina, no hay a qué esperar, y dos caminos vivos es la deriva que este plan
  entero existe para cortar.
- **Forzar `LANGFUSE_MIGRATION_V4_WRITE_MODE=dual`** para que la API nativa de ingesta y de
  lectura respondan: cambia el modo de la instancia de producción del propio sistema para
  responder una pregunta de diseño, un efecto colateral que la compuerta no necesita pagar —
  la respuesta `unreachable` documentada es la que corresponde al despliegue real.
