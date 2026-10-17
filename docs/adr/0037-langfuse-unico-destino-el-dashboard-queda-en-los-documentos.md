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

## Adenda (2026-10-17, GH-273): scores por tarea en una traza portadora

Esta adenda no edita nada de arriba: lo registra como evidencia para GH-273 ("un score por
cifra de negocio de cada tarea, aggregable por un widget"). La primera sección de esta ADR
dejó dicho por qué: los widgets de Langfuse v4 sólo agregan sus medidas fijas (conteo,
latencia, costo/tokens de generación, **valores de score**) y agrupan por dimensiones fijas
(nombre, modelo, `environment`, `release`, ...), nunca por metadata de span. Para que el
costo, el ciclo y las devoluciones de una tarea sean números agregables hay que escribirlos
como scores, sobre una traza que sea de la tarea y no de una invocación. El plan de GH-273
elige una **traza portadora** por incarnation de tarea (un span construido por un
`TracerProvider` que no exporta, enviado una vez como protobuf OTLP) y un lote `score-create`
a la ingesta nativa. Antes de que otra subtarea construya sobre eso, esta compuerta lo
verifica contra la instancia corriendo.

### Instancia y método

```
$ docker ps --format '{{.Names}} {{.Ports}}' | grep langfuse-langfuse-web
langfuse-langfuse-web-1 0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp

$ curl -s http://localhost:3000/api/public/health
{"status":"OK","version":"4.35.0"}
```

La misma instancia, `events_only`, de la compuerta de arriba. Las credenciales se leyeron de
`observability/langfuse/.env` (ruta absoluta fuera del worktree) desde el script de prueba, que
no las imprime; ninguna se copió al repo ni a esta página. Las trazas de prueba se llaman
`agentic.gate.*` y viven en el `environment` `agentic-gate-probe`.

**Cómo se leyó el resultado.** En `events_only` los endpoints de lectura puntual (`/api/public/
traces`, `/api/public/scores`, `/api/public/v2/scores`) siguen respondiendo `404` (ver arriba),
así que "lo que muestra la UI" no se puede pedir por la API pública. Dos lecturas sí
alcanzan: `GET /api/public/v2/observations?traceId=…` (la API de lectura v4, que sí está
habilitada) y una consulta directa a ClickHouse (`127.0.0.1:8123`, usuario y contraseña por
defecto de `docker-compose.yml`, sólo `SELECT`), sobre las tablas `events_core` y `scores`: son
las tablas de las que la UI y los widgets de v4 leen. No se abrió la UI en un navegador: lo
que se afirma abajo es lo que está en esas tablas, no una captura de pantalla.

El script de prueba (vive sólo acá; ningún archivo del repo lo contiene):

```python
import base64, hashlib, json, os, sys, urllib.request
from opentelemetry.sdk.trace import TracerProvider, IdGenerator
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans

HOST = "http://localhost:3000"
env = dict(
    l.strip().split("=", 1)
    for l in open("/home/zeta/dev/repos/agentic-system/observability/langfuse/.env")
    if "=" in l and not l.startswith("#")
)
PUB, SEC = env["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"], env["LANGFUSE_INIT_PROJECT_SECRET_KEY"]
AUTH = "Basic " + base64.b64encode(f"{PUB}:{SEC}".encode()).decode()


def ident(label):
    h = hashlib.sha256(f"agentic.gate.{label}".encode()).hexdigest()
    return h[:32], h[32:48]


class Fixed(IdGenerator):
    def __init__(self, t, s):
        self.t, self.s = int(t, 16), int(s, 16)

    def generate_trace_id(self):
        return self.t

    def generate_span_id(self):
        return self.s


class Cap(SpanExporter):
    def __init__(self):
        self.spans = []

    def export(self, spans):
        self.spans += list(spans)
        return SpanExportResult.SUCCESS


def post(path, body, ctype, count=False):
    req = urllib.request.Request(
        HOST + path,
        data=body,
        method="POST",
        headers={"Content-Type": ctype, "Authorization": AUTH},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def carrier(label, start_ns, end_ns):
    t, s = ident(label)
    cap = Cap()
    tp = TracerProvider(
        resource=Resource.create({"service.name": "agentic-system"}), id_generator=Fixed(t, s)
    )
    tp.add_span_processor(SimpleSpanProcessor(cap))
    sp = tp.get_tracer("agentic.gate").start_span(
        "agentic.gate.task",
        start_time=start_ns,
        attributes={
            "langfuse.release": "gate-probe-release",
            "langfuse.environment": "agentic-gate-probe",
            "langfuse.trace.name": "agentic.gate.task",
        },
    )
    sp.end(end_time=end_ns)
    return t, s, encode_spans(cap.spans).SerializeToString()


def batch(label, trace_id, value, ts):
    evs = []
    for name, v, dt in (
        ("agentic.gate.cost_usd", value, "NUMERIC"),
        ("agentic.gate.origin", "original", "CATEGORICAL"),
        ("agentic.gate.delivered", 1, "BOOLEAN"),
    ):
        sid = hashlib.sha256(f"agentic.gate.score.{label}.{name}".encode()).hexdigest()[:32]
        evs.append(
            {
                "id": hashlib.sha256(f"event.{sid}".encode()).hexdigest()[:32],
                "type": "score-create",
                "timestamp": ts,
                "body": {
                    "id": sid,
                    "traceId": trace_id,
                    "name": name,
                    "value": v,
                    "dataType": dt,
                    "environment": "agentic-gate-probe",
                    "timestamp": ts,
                    "metadata": {"release": "gate-probe-release", "arm": "default"},
                },
            }
        )
    return json.dumps({"batch": evs}).encode()


if __name__ == "__main__":
    step, label = sys.argv[1], sys.argv[2]
    start, end = 1_760_000_000_000_000_000, 1_760_003_600_000_000_000  # 2025-10-09T08:53:20Z .. +1h
    ts = "2025-10-09T09:53:20.000Z"
    t, s, body = carrier(label, start, end)
    if step == "span":
        print("trace_id", t, "span_id", s, "bytes", len(body))
        print(post("/api/public/otel/v1/traces", body, "application/x-protobuf"))
    elif step == "scores":
        print("trace_id", t)
        print(
            post(
                "/api/public/ingestion", batch(label, t, float(sys.argv[3]), ts), "application/json"
            )
        )
```

### (1) ¿Un cuerpo protobuf OTLP con ids y tiempos fijados es aceptado, y `langfuse.release` / `langfuse.environment` llegan?

El span lo hace un `TracerProvider` con un `IdGenerator` que devuelve el trace id y el span id
derivados, un procesador que sólo captura, tiempos de inicio y de fin explícitos
(`2025-10-09T08:53:20Z` a `+1 h`, distintos de "ahora" a propósito) y los atributos
`langfuse.release=gate-probe-release` y `langfuse.environment=agentic-gate-probe`; se codifica
con `encode_spans` del SDK y se postea una vez:

```
$ date -u
Sat Oct 17 04:38:41 AM UTC 2026
$ uv run python /tmp/gate273/probe.py span q1
trace_id c94a47cac75f45e2424597b50020df1d span_id 1340e116ae3f0ffb bytes 379
(200, '{"name":"otel-ingestion-job","data":{"id":"d2905243-343f-41f9-96ce-ba69e2a3f522","timestamp":"2026-10-17T04:38:42.565Z","name":"otel-ingestion-job","payload":{"data":{"fileKey":"events/otel/agentic-system/2026/10/17/04/38/b8379332-ef8f-40b8-add9-6a3c7d743f2f.json","publicKey":"<public key, elidida>"},"authCheck":{"validKey":true,"scope":{"projectId":"agentic-system","accessLevel":"project","orgId":"agentic"}}, … "queueQualifiedName":"bull:otel-ingestion-queue"}')
```

La ingesta responde `200` y encola el trabajo. Lo que quedó escrito (ClickHouse, ~10 s después):

```
$ SELECT id, name, release, environment, timestamp FROM traces FINAL WHERE id='c94a47cac75f45e2424597b50020df1d'
(sin filas: en events_only la tabla legada `traces` ya no se escribe)

$ SELECT trace_id, span_id, name, start_time, end_time, release, environment FROM events_core FINAL WHERE trace_id='c94a47cac75f45e2424597b50020df1d' FORMAT Vertical
Row 1:
trace_id:    c94a47cac75f45e2424597b50020df1d
span_id:     1340e116ae3f0ffb
name:        agentic.gate.task
start_time:  2025-10-09 08:53:20.000000
end_time:    2025-10-09 09:53:20.000000
release:     gate-probe-release
environment: agentic-gate-probe

$ curl -s -u "$PUB:$SEC" "http://localhost:3000/api/public/v2/observations?traceId=c94a47cac75f45e2424597b50020df1d"
{"data":[{"id":"1340e116ae3f0ffb","traceId":"c94a47cac75f45e2424597b50020df1d","startTime":"2025-10-09T08:53:20.000Z","endTime":"2025-10-09T09:53:20.000Z","projectId":"agentic-system","parentObservationId":null,"type":"SPAN","name":"agentic.gate.task","level":"DEFAULT","statusMessage":"","version":"","environment":"agentic-gate-probe","latency":3600,"timeToFirstToken":null,"userId":"","sessionId":"","isRootObservation":true,"bookmarked":false,"public":false,"modelId":null,"inputPrice":null,"outputPrice":null,"totalPrice":null}],"meta":{}}
HTTP:200
```

El `trace_id` y el `span_id` son los fijados, los tiempos son los explícitos (no el de
ingesta) y las columnas `release` y `environment` del span quedan con los atributos
`langfuse.release` y `langfuse.environment`; la API v2 devuelve el `environment` y `latency`
= 3600 s, la hora fijada. **Verdicto: `favorable`.** Salvedad: en v4 la "traza" no es una fila
propia sino el agrupamiento por `trace_id` de las observaciones de `events_core`; el `release`
está a nivel de observación. La UI no se abrió.

### (2) ¿Un lote `score-create` a `/api/public/ingestion` es aceptado, con ids del cliente y `timestamp`?

Tres scores (`NUMERIC`, `CATEGORICAL`, `BOOLEAN`), cada uno con el id del evento y el id del
score derivados por el cliente (`sha256` truncado a 32 hex), `traceId`, `name`, `value`,
`dataType`, `environment`, `timestamp` (`2025-10-09T09:53:20.000Z`) y `metadata`:

```
$ uv run python /tmp/gate273/probe.py scores q1 1.25
trace_id c94a47cac75f45e2424597b50020df1d
(207, '{"successes":[{"id":"1c89894f0b628dd146bfd4ed99975f0b","status":201},{"id":"b057790d9a176389ca367a3f730cd199","status":201},{"id":"01513f3fa93fb15715ed0440f83adbfe","status":201}],"errors":[]}')

$ SELECT id, trace_id, name, value, string_value, data_type, environment, timestamp, created_at, metadata FROM scores FINAL WHERE trace_id='c94a47cac75f45e2424597b50020df1d' ORDER BY name FORMAT Vertical
Row 1:
id:           738b639106287ae5b43cc9f011a60033
trace_id:     c94a47cac75f45e2424597b50020df1d
name:         agentic.gate.cost_usd
value:        1.25
string_value: ᴺᵁᴸᴸ
data_type:    NUMERIC
environment:  agentic-gate-probe
timestamp:    2025-10-09 09:53:20.000
created_at:   2026-10-17 04:38:43.934
metadata:     {'release':'gate-probe-release','arm':'default'}

Row 2:
id:           6cfe7767ea4f6854b67b1bead420ed4b
trace_id:     c94a47cac75f45e2424597b50020df1d
name:         agentic.gate.delivered
value:        1
string_value: True
data_type:    BOOLEAN
environment:  agentic-gate-probe
timestamp:    2025-10-09 09:53:20.000
created_at:   2026-10-17 04:38:43.940
metadata:     {'release':'gate-probe-release','arm':'default'}

Row 3:
id:           9339cf11f69077c3468bff248a1bf2bd
trace_id:     c94a47cac75f45e2424597b50020df1d
name:         agentic.gate.origin
value:        0
string_value: original
data_type:    CATEGORICAL
environment:  agentic-gate-probe
timestamp:    2025-10-09 09:53:20.000
created_at:   2026-10-17 04:38:43.936
metadata:     {'release':'gate-probe-release','arm':'default'}
```

El lote vuelve `207` con tres `201` (el `id` de cada `success` es el id del **evento**). Los
tres scores están en `scores` con el **id de score del cliente** (`738b…` es
`sha256("agentic.gate.score.q1.agentic.gate.cost_usd")[:32]`, comprobado a mano), el `value`,
el `data_type`, el `environment` y la `metadata`. El `timestamp` del score es el que mandó el
cliente (2025-10-09); `created_at` es el de ingesta (2026-10-17). Un `BOOLEAN` se guarda como
`value=1`, `string_value=True`; un `CATEGORICAL` como `value=0`, `string_value=<la categoría>`:
un widget que agregue `origin` o `type` lo hace por `string_value`. **Verdicto: `favorable`.**

### (3) ¿Reenviar el mismo lote, con los mismos ids y un valor cambiado, actualiza el score?

El mismo lote, mismos ids de evento y de score, `cost_usd` de `1.25` a `9.75`:

```
$ uv run python /tmp/gate273/probe.py scores q1 9.75
trace_id c94a47cac75f45e2424597b50020df1d
(207, '{"successes":[{"id":"1c89894f0b628dd146bfd4ed99975f0b","status":201},{"id":"b057790d9a176389ca367a3f730cd199","status":201},{"id":"01513f3fa93fb15715ed0440f83adbfe","status":201}],"errors":[]}')

$ SELECT id, name, value, created_at, updated_at FROM scores WHERE trace_id='c94a47cac75f45e2424597b50020df1d' ORDER BY name, created_at
738b639106287ae5b43cc9f011a60033	agentic.gate.cost_usd	9.75	2026-10-17 04:38:43.934	2026-10-17 04:39:14.920
738b639106287ae5b43cc9f011a60033	agentic.gate.cost_usd	1.25	2026-10-17 04:38:43.934	2026-10-17 04:38:49.230
6cfe7767ea4f6854b67b1bead420ed4b	agentic.gate.delivered	1	2026-10-17 04:38:43.940	2026-10-17 04:39:14.918
6cfe7767ea4f6854b67b1bead420ed4b	agentic.gate.delivered	1	2026-10-17 04:38:43.940	2026-10-17 04:38:49.204
9339cf11f69077c3468bff248a1bf2bd	agentic.gate.origin	0	2026-10-17 04:38:43.936	2026-10-17 04:39:14.924
9339cf11f69077c3468bff248a1bf2bd	agentic.gate.origin	0	2026-10-17 04:38:43.936	2026-10-17 04:38:49.237

$ SELECT id, name, value, timestamp FROM scores FINAL WHERE trace_id='c94a47cac75f45e2424597b50020df1d' ORDER BY name
738b639106287ae5b43cc9f011a60033	agentic.gate.cost_usd	9.75	2025-10-09 09:53:20.000
6cfe7767ea4f6854b67b1bead420ed4b	agentic.gate.delivered	1	2025-10-09 09:53:20.000
9339cf11f69077c3468bff248a1bf2bd	agentic.gate.origin	0	2025-10-09 09:53:20.000
```

El segundo envío no crea un score nuevo: reemplaza al primero **por id**. `created_at` se
conserva (04:38:43) y `updated_at` avanza; el valor leído es `9.75`, no `1.25` y no la suma.
La tabla es un `ReplacingMergeTree`: hasta que ClickHouse fusione las partes, la lectura
cruda ve las dos versiones de la fila y sólo una lectura con `FINAL` (o por `id` con la última
`updated_at`) ve una. Que la UI y los widgets lean con esa deduplicación es lo esperable de la
tabla, pero **no se comprobó** abriendo un widget; una consulta directa y sin `FINAL` (como la
de este renglón) sí ve las dos. **Verdicto: `favorable`**
con esa condición: el idempotente es por id, y por eso la identidad del plan incluye el
`start` de la corrida: el write de cierre pisa al write de fin de la misma corrida (mismo
id, a propósito) y ninguna otra corrida se pisa.

### (4) ¿Un score enviado a un trace id antes de que llegue su span queda atado cuando el span llega?

Un trace id nuevo (`q4`), con los scores enviados **primero**, y el span después:

```
$ uv run python /tmp/gate273/probe.py scores q4 3.5
trace_id 152aef211cbfc6537177c0f0b9b94a15
(207, '{"successes":[{"id":"8916b6cfc518bf66a0a5ceb92447af17","status":201},{"id":"6b267bcd0eff6efb80c788fe0d1ee5a8","status":201},{"id":"12bbe5bd3331a82f734694d92b1d2f19","status":201}],"errors":[]}')

$ SELECT count() FROM events_core WHERE trace_id='152aef211cbfc6537177c0f0b9b94a15'
0
$ SELECT name, value, environment FROM scores FINAL WHERE trace_id='152aef211cbfc6537177c0f0b9b94a15' ORDER BY name
agentic.gate.cost_usd	3.5	agentic-gate-probe
agentic.gate.delivered	1	agentic-gate-probe
agentic.gate.origin	0	agentic-gate-probe

$ uv run python /tmp/gate273/probe.py span q4
trace_id 152aef211cbfc6537177c0f0b9b94a15 span_id 7929da1242d4096f bytes 379
(200, '{"name":"otel-ingestion-job", … }')

$ SELECT span_id, name, release, environment FROM events_core FINAL WHERE trace_id='152aef211cbfc6537177c0f0b9b94a15'
7929da1242d4096f	agentic.gate.task	gate-probe-release	agentic-gate-probe
$ SELECT name, value, environment FROM scores FINAL WHERE trace_id='152aef211cbfc6537177c0f0b9b94a15' ORDER BY name
agentic.gate.cost_usd	3.5	agentic-gate-probe
agentic.gate.delivered	1	agentic-gate-probe
agentic.gate.origin	0	agentic-gate-probe

$ curl -s -u "$PUB:$SEC" "http://localhost:3000/api/public/v2/observations?traceId=152aef211cbfc6537177c0f0b9b94a15"
{"data":[{"id":"7929da1242d4096f","traceId":"152aef211cbfc6537177c0f0b9b94a15","startTime":"2025-10-09T08:53:20.000Z","endTime":"2025-10-09T09:53:20.000Z", … ,"name":"agentic.gate.task", … "environment":"agentic-gate-probe", … }
```

Los scores no se descartan por no tener todavía una traza: se guardan igual (el `200`/`201`
de arriba y la fila en `scores` con el span ausente) y siguen allí, intactos, cuando el span
llega. No hay una clave foránea entre `scores.trace_id` y `events_core.trace_id`: se unen al
leer, por el id, así que "atado" aquí es "el mismo `trace_id` en las dos tablas". Por la misma
razón, el orden span→scores que usa el plan no es necesario para que el score exista, sino
una precaución. **Verdicto: `favorable`**, con la misma salvedad que (1): no se abrió la UI
para ver la página de la traza.

### Veredicto y consecuencias

| Pregunta | Verdicto |
|---|---|
| (1) Protobuf OTLP aceptado; `release`/`environment` del span llegan | `favorable` |
| (2) Lote `score-create` aceptado, ids del cliente, `timestamp` respetado | `favorable` |
| (3) Reenvío con los mismos ids actualiza, no duplica | `favorable` |
| (4) Score antes del span queda atado cuando éste llega | `favorable` |

| Resultado | Consecuencia |
|---|---|
| Upsert por id no respetado, o instancia `unreachable` | `backfill` agrega el ledger local (`run_registry_dir/langfuse/<12 hex del directorio del producto>.json`, escrito de forma atómica) y no reenvía lo ya enviado. |
| Sin mapeo de `release`/`environment` desde el span | `release` se lee de la metadata del score. |
| Sin atado tardío | Riesgo conocido, registrado. |

Los cuatro verdictos son `favorable` y la instancia fue alcanzable: **ninguna de las tres
consecuencias se activa**. `backfill` no agrega el ledger, `release` se lee del span
(`langfuse.release`; también va en la metadata de cada score) y el orden span→scores queda
como precaución. Dos cosas que las subtareas siguientes deben tener presentes, ambas ya
visibles arriba: un score `BOOLEAN` o `CATEGORICAL` se agrega por `string_value`, no por
`value`; y la deduplicación por id es de lectura (`FINAL`), no de escritura.

Las trazas de prueba (`agentic.gate.*`, `environment=agentic-gate-probe`, dos trace ids) quedan
en la instancia: en `events_only` no hay un endpoint público para borrarlas, y un widget que
filtre por el `environment` de un producto real no las ve.

### La decisión de GH-273: scores sobre una traza portadora por tarea

La compuerta de arriba dio los cuatro verdictos `favorable`; esta parte registra lo que el
sistema hace con ello. Nada de lo anterior cambia: `telemetry.py`, `TaskRun.finish`, el
colector, los prompts y todo atributo de span existente quedan como estaban.

- **Una traza portadora por tarea y por incarnación.** Cada tarea terminada tiene una traza
  propia con un solo span, `agentic.task.scores <id>`, que lleva `langfuse.release`,
  `langfuse.environment`, `arm` y `baseline`; los números salen como scores sobre esa
  traza, lo único por traza que un widget de Langfuse v4 agrega. La identidad la deriva una
  sola función, `task_scores.identity(producto, id de tarea, inicio)`: el id de traza, el
  del span, el de cada score y el del evento de ingesta. Incluye el **inicio de la corrida**
  que lo produjo, así que un `--fresh` o cada brazo de `eval run` tiene su propia identidad
  y ninguna corrida pisa los scores de otra; el write de fin y el de cierre de una misma
  corrida comparten identidad, y el segundo actualiza al primero por id (pregunta 3).
- **El tiempo.** El span corre del primer `started_at` al último `ended_at` de `runs`; un
  documento sin marcas (anterior al 2026-09-18) usa el `created_at` del PR y luego el
  `createdAt` del issue para los dos extremos. Todo score lleva `timestamp` igual al fin.
  Nunca se usa el reloj, `closed_at` ni el mtime de un archivo.
- **Cada score, su fuente y cuándo se omite.** Está en la tabla de `observability/README.md`
  ("Scores por tarea"), y un test la mantiene igual a `task_scores.SCORE_NAMES`. La regla
  común: una entrada que falta omite su score y nunca lo escribe como `0` o `false`
  (`active_h` sin marcas, `cycle_h` sin merge o sin lectura de `gh`, `delivered` sin
  `delivery_outcome`, `origin` sin título). `cost_usd`, `returns`, `failed_cost_usd` y `type`
  salen siempre, y un documento sin `cost_usd` da `0.0`: es el único lugar donde un dato
  ausente se lee como cero, y el pedido lo fija así.
- **`auto_merged` cuenta como entregada.** El pedido define `delivered` como "el PR se
  mergeó"; si lo juzgó una persona es la pregunta del scorecard (ADR-0045), no la de este
  score.
- **Credenciales.** `AGENTIC_LANGFUSE_HOST`, `AGENTIC_LANGFUSE_PUBLIC_KEY` y
  `AGENTIC_LANGFUSE_SECRET_KEY`. El prefijo `AGENTIC_` es lo que ya mantiene fuera de cada
  subproceso de rol (`own_configuration_unset`), de los chequeos del producto y de la app de
  muestra (`without_own_configuration`) todo lo que lo lleva; por eso no son un campo de
  `Configuration` y `composition.task_scorer()` las lee de `os.environ` y se las da sólo al
  adaptador. Con alguna sin definir no se escribe nada. Las `LANGFUSE_*` sin prefijo de un
  `.env` humano siguen tan expuestas como hoy.
- **El cable.** El span lo construye un `TracerProvider` que no exporta (un procesador que
  sólo captura), se codifica con `encode_spans` del SDK y se envía una vez como protobuf a
  `/api/public/otel/v1/traces`; los scores van en un solo lote `score-create` a
  `/api/public/ingestion`. Siempre el span primero, un solo POST por llamada acotado por
  `LANGFUSE_TIMEOUT_S`, sin reintento y sin `OTLPSpanExporter` en el camino de una corrida.
  El cliente atrapa todo, imprime una línea con la llamada y la tarea, nunca la clave, un
  encabezado ni un cuerpo, y devuelve.
- **El backfill** es `agentic scores backfill --product-dir <dir> [--dry-run]`: recorre
  `documents.ids()`, puntúa los terminados (`ready_for_review`, `discarded` o con
  `closed_at`) con las mismas funciones `task_time`, `identity`, `dimensions` y
  `task_scores` que la ruta en vivo, y no escribe ningún documento. Lee el PR y el issue a lo
  sumo una vez cada uno, con la cota de ADR-0050. Imprime una línea por documento (`scored`,
  `partial:<omitidos>`, `skipped:<motivo>` o `failed`) y una última con el conteo; sale `1`
  si alguno quedó `partial` o `failed`, y `2` sin Langfuse configurado salvo `--dry-run`. Se
  detiene tras 3 escrituras fallidas seguidas. Como los ids son derivados, repetirlo completa
  huecos sin duplicar, y por eso no hace falta el ledger local que la tabla de consecuencias
  de la compuerta reservaba: ninguna de sus tres consecuencias se activó.
- **Desviación: "un conjunto por tarea terminada".** El pedido dice un conjunto de scores por
  tarea histórica; una tarea sin terminar no tiene cifras finales y sale
  `skipped:not_finished`: se puntúa en vivo cuando termine.
- **El backfill real lo corre la persona** después del merge y de re-fijar stage0 a ese sha:
  desde un checkout del propio sistema `default_ports` se niega a correr (ADR-0017), así que
  esta tarea no lo ejecutó contra Langfuse.

Evidencia del `--dry-run` de esta tarea (lee los documentos, el PR y el issue por `gh` y no
escribe nada en Langfuse). `apostrophe`, sobre el repositorio real:

```
$ uv run agentic scores backfill --product-dir /home/zeta/dev/repos/apostrophe --dry-run
scored 108, partial 0, skipped:not_finished 9, failed 0
```

`agentic-system`: el propio checkout se niega (ADR-0017, arriba), así que se corrió sobre una
copia de sus documentos de tarea y de su `stack.yaml` en un repositorio temporal con el mismo
`origin`:

```
$ uv run agentic scores backfill --product-dir /tmp/as-dry --dry-run
scored 104, partial 0, skipped:not_finished 1, failed 0
```

Riesgos conocidos: un score enviado antes de que su traza llegue queda guardado y se une al
leer por el id (pregunta 4), pero la página de la traza no se abrió en la UI; las claves
`LANGFUSE_*` sin prefijo de un `.env` quedan expuestas como hoy; el backfill necesita que
Langfuse y `gh` sean alcanzables desde donde corre; y la deduplicación por id es de lectura
(`FINAL`), no de escritura, así que una consulta cruda sin `FINAL` ve las dos versiones de un
score actualizado. Un ledger local de lo enviado, si hiciera falta, tendría el riesgo de dos
escritores concurrentes sobre el mismo archivo.
