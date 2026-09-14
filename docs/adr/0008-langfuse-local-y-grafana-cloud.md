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
