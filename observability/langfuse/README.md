# Langfuse local

Backend OpenTelemetry para ver, paso a paso, qué hace cada rol: cada llamada al
modelo, cada tool call y su costo. Claude Code exporta OTel de forma nativa, así
que el sistema no necesita instrumentación propia: alcanza con las variables
`OTEL_*` del `.env` de la raíz.

```bash
docker compose up -d        # desde este directorio
```

Incluye un OpenTelemetry Collector en `localhost:4318` que reenvía a Langfuse con
la auth puesta (`otel-collector.yaml`). Claude Code apunta ahí, sin headers:
su exportador OTel no sabe mandar `Authorization: Basic <b64>` porque corta el
valor en el espacio y no decodifica `%20`.

UI en http://localhost:3000. Usuario y contraseña en `.env` de este directorio
(no versionado; `docker compose` lo lee solo). Las claves del proyecto también
están ahí y ya van en el `.env` de la raíz como header `Authorization`.

Datos en volúmenes de Docker: sobreviven a `down`, se borran con `down -v`.
