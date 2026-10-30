# Cómo funciona

Punto de entrada a la documentación de agentic-system como producto: un equipo de roles
agénticos que recibe issues de GitHub y deja pull requests. Esta página solo ordena los
enlaces; cada una dice de dónde sale lo que afirma. Parte de ella se escribe a mano y parte
se genera con `scripts/docs/generate.py` (ADR-0074, ADR-0076): lo generado no se edita y
`uv run python scripts/docs/check.py` falla si quedó viejo.

## El diseño

El diseño completo está en [README.md](../README.md), por secciones:

- sección 1, los roles, y sección 2, los tipos de tarea y sus caminos;
- sección 3, las reglas transversales: devoluciones, loops, tope de tarea, cola de
  escalados, cierre;
- sección 4, el detalle de cada rol;
- sección 14, la arquitectura del código: paquete, configuración, pausa y reanudación, el
  poller como servicio, fallo de rol, fan-out y observabilidad.

El vocabulario de cada contexto está en [CONTEXT-MAP.md](../CONTEXT-MAP.md) y en los
`CONTEXT.md` que ese mapa enlaza, por ejemplo `agentic/core/process/CONTEXT.md`.

## Arquitectura

Páginas escritas, de lo general a lo particular:

1. [Contexto](architecture/contexto.md): el dueño, GitHub, Claude, los productos y Langfuse.
2. [Contenedores](architecture/contenedores.md): la CLI, las unidades del poller, el clon
   stage0 y los repos de producto con sus worktrees.
3. [Componentes](architecture/componentes.md): los tres módulos, `shared` y los puertos.

Flujos clave, como diagramas de secuencia:

- [De un issue a su merge](architecture/flujos/de-issue-a-merge.md)
- [Aterrizaje](architecture/flujos/aterrizaje.md)
- [Reanudación tras un escalado](architecture/flujos/reanudacion.md)

## Diagramas generados

- [Grafo de roles](architecture/generated/role-graph.md), de `agentic.composition:graph`, con
  una página por fase:
  [classify](architecture/generated/role-graph-classify.md),
  [define](architecture/generated/role-graph-define.md),
  [plan](architecture/generated/role-graph-plan.md),
  [build](architecture/generated/role-graph-build.md),
  [review](architecture/generated/role-graph-review.md),
  [deliver](architecture/generated/role-graph-deliver.md),
  [escalado y reanudación](architecture/generated/role-graph-escalation.md) y
  [land](architecture/generated/role-graph-land.md).
- Paquetes de `process`: el
  [índice](architecture/generated/packages-process.md) primero y, debajo, sus páginas:
  [domain](architecture/generated/packages-process-domain.md),
  [application](architecture/generated/packages-process-application.md) y
  [adapters](architecture/generated/packages-process-adapters.md), cada una con las de sus
  grupos o sus tipos de adaptador.
- Clases de `process/domain`: el
  [índice](architecture/generated/classes-process-domain.md) primero y, debajo, una página
  por grupo de `CLUSTERS`, partida por cantidad si el grupo es grande.
- Paquetes de `knowledge` y `measurement`: los índices
  [knowledge](architecture/generated/packages-knowledge.md) y
  [measurement](architecture/generated/packages-measurement.md), con una página por capa.
- Clases de los otros módulos y capas, en `docs/architecture/generated/`; el índice está en
  [componentes](architecture/componentes.md).

## Referencias

- [Referencia de la CLI](reference/cli.md): la ayuda de cada comando de `agentic`.
- [Referencia de `.agentic/stack.yaml`](reference/stack-yaml.md): el contrato que un producto
  declara y que el sistema lee.

No hay API HTTP: las interfaces del sistema son la CLI y ese contrato.

## Conocimiento de producto

Lo que el sistema hace, desde el punto de vista de quien lo usa, vive en `docs/product/`:

- [Devoluciones de cambios](product/changes-requested.md)
- [Clasificación](product/classification.md)
- [Retrabajo](product/rework.md)
- [Fallo de rol](product/role-failures.md)

## Decisiones del dueño y orquestación

- [Decisiones del dueño](decisiones-del-dueño.md): las reglas generales que el dueño dio y que
  todo rol lee enteras; el archivo de cada producto está en su propio `docs/product/`.
- [Orquestación](orquestacion.md): qué hace y qué nunca hace quien orquesta el sistema, cómo
  mergea, traba tareas y usa el entorno local.

## Decisiones

Cada decisión con costo está en [docs/adr/](adr/), numerada. Las que más se citan arriba: la
documentación técnica se divide en generada y escrita
([ADR-0074](adr/0074-la-documentacion-tecnica-se-divide-en-generada-y-escrita-y-una-puerta-falla-si-la-generada-esta-vieja.md))
y el generador pasa por `check.py`
([ADR-0076](adr/0076-la-documentacion-generada-de-agentic-system-sale-de-un-script-que-pasa-por-check-py.md)).
