# Langfuse local

Backend de trazas para ver, paso a paso, qué hace cada rol: cada llamada al modelo,
cada tool call y su costo. Recibe todo lo que pasa por el collector: los spans del
sistema (tarea y rol) y los nativos de Claude Code, con contenido.

```bash
docker compose up -d        # desde este directorio; levanta también el collector
```

UI en http://localhost:3000. Usuario y contraseña en `.env` de este directorio
(no versionado; `docker compose` lo lee solo). Las claves del proyecto también
están ahí: `LANGFUSE_OTEL_BASIC` es `base64(public:secret)` y la usa el collector
(`../collector/otel-collector.yaml`). El mismo `.env` lleva las tres variables de
Grafana Cloud (`../grafana/README.md`).

Datos en volúmenes de Docker: sobreviven a `down`, se borran con `down -v`.
