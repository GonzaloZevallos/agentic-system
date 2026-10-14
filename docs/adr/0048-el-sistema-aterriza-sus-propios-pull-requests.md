---
status: accepted
date: 2026-10-14
---

# El sistema aterriza sus propios pull requests

Entre 2026-10-08 y 2026-10-13 quedaron abiertos 16 pull requests aprobados durante cinco
días, porque `auto_merge_decision` los rechazó y el rechazo esperaba a un humano. Los
aterrizó a mano la sesión orquestadora: resolvió conflictos, corrió CI, corrigió hallazgos.
Nada de eso quedó trazado, costeado ni aprendido. Zeta (2026-10-13): "Todo lo debería
resolver el sistema agéntico solo, luego pensamos gates cuando llevemos al sistema al
siguiente nivel." Este ADR (GH-239) da a cada rechazo un camino que el sistema recorre solo,
acotado, trazado y anotado en el documento de la tarea.

## Qué enmienda

Enmienda por nombre, sin editar ninguna de estas frases (el historial queda donde se escribió):

- ADR-0045: la tabla de rechazos (suma `open_findings_blocking`, `open_findings` pasa a
  significar sólo `important`, y `guarded_path` sale), los caminos guardados (ya no
  rechazan) y GH-216, "no se reejecuta el CI" (el aterrizaje sí lo reejecuta).
- ADR-0034: la ausencia de un parámetro `label` en `Tracker.open_issue`.
- ADR-0039: el calendario de reanudación por límite de cuenta.

## La tabla: rechazo → paso

`domain/landing.landing_step(refusal, facts, spent, blocking_owners)` es el único lugar que
decide **qué hacer en vez de** mergear; `auto_merge_decision` sigue siendo el único que
decide **si** mergear. `LandingStep.kind` es un literal cerrado.

| Rechazo | Paso |
|---|---|
| `not_ready`, `pr_not_open` | `none` |
| `eval_set` | `none`: el merge humano de una tarea del set es la medición (ADR-0045) |
| `eval_set_unreadable`, `ci_missing`, `ci_unreadable` | `wait`; `diagnose` pasado `ci.CI_WAIT_MAX_S` |
| `not_approved`, `head_not_reviewed` | `re_review` |
| `open_findings_blocking` | `fix` sobre las unidades dueñas de los registros `blocking` abiertos |
| `open_findings` (sólo `important`) | `file_followups_and_land` |
| `ci_failed` | `rerun` una vez por head; después `fix` |
| `checks_failed` | `rerun` de los chequeos fallidos; después `fix`; con `failed_step == conflict`, `sync_master` |
| `attempts_exhausted` | `sync_master`; `diagnose` agotados los presupuestos |
| `merge_failed` | `diagnose` |
| `master_moved` | `wait`: la vuelta siguiente decide de nuevo |

Un `fix` sin dueños nombrados reabre la unidad revisada. Una tarea ya diagnosticada no tiene
paso (`none`).

Se eligió, de las dos salidas que ofrecía el issue para los hallazgos, ambas según la
severidad: un `blocking` manda a una pasada más de Developer → Tech Lead; un `important`
sobre una cabeza aprobada se archiva como seguimiento (un issue `agentic`, con la evidencia)
y el pull request aterriza.

## Presupuestos

- `LANDING_PASSES_MAX = 3` pasadas de modelo por tarea (`re_review`, `fix` y un
  `sync_master` que conflictúa).
- `LANDING_SYNCS_MAX = 6` `sync_master` limpios por tarea.
- Una pasada o un sync que excedería su presupuesto es el paso `diagnose`.

Cada paso se anota en `landing: [{head, step, refusal, at, cost_usd, resolved_at?}]`, bajo el
lock de corrida y **antes** de despacharse; la clave `(head, step)` nunca se despacha dos
veces. Es la forma de idempotencia: dos vueltas del poller sobre el mismo rechazo no duplican
trabajo.

## Caminos guardados y CI no confiable

`guarded_path` deja de ser un rechazo y pasa a ser un registro: un prefacio `Guarded changes`
que nunca se recorta en el cuerpo del PR, la clave `auto_merge_guarded` y una mención en el
comentario `Auto-merged:`. `rules.ci_untrusted(touched_paths)` es `None` si los caminos se
desconocen y `True` si alguno empieza con `CI_DEFINITION_PREFIX = ".github/"`: en ambos casos
un CI verde nunca decide `merge` (falla cerrado) y decide el chequeo local declarado en la
base de la tarea. El set de evaluación se sigue leyendo al sha de upstream.

## Camino terminal

`diagnose` corre un `landing_diagnoser` de sólo lectura, una vez por tarea. Luego el sistema
cierra el pull request, archiva la corrida y reinicia el issue con `--fresh`, con el
diagnóstico como sección del sistema. `landing_restarts` y `landing_cost_carried` viven en el
documento y los copia `--fresh`; los comentarios son sólo un registro. Con `RESTART_DEPTH_MAX
= 1`, un segundo agotamiento escala con clase `failure` y no archiva nada.

## Lo que se ensancha (README, sección de seguridad)

El sistema puede ahora: mergear caminos guardados, reejecutar el CI (`gh run rerun --failed`),
abrir issues con la etiqueta `agentic` (a lo sumo uno de seguimiento por tarea y uno de flake
por nombre de chequeo; una tarea de linaje ≥ 1 no archiva ninguno y los escribe en el cuerpo
de su PR) y cerrar sus propios pull requests. Con el tope de tarea desactivado no hay techo en
dólares: sólo la cota estructural de 2 corridas × (3 pasadas + 1 diagnóstico) a 10 USD por
intento. El peor caso del calendario de límite de cuenta es de 168 comentarios `Auto-resumed`
en una semana.

## Vocabulario

`Aterrizaje` entra al glosario de `agentic/core/process/CONTEXT.md`. Los tipos nuevos
(`LandingStepKind`, el valor `open_findings_blocking`) son literales cerrados con un test de
exhaustividad; las claves de span nuevas usan el prefijo `agentic.landing.` y no se agrega,
mueve ni renombra ninguna familia de métricas. Toda clave nueva del documento es opcional y se
lee con `.get`: un documento sin ella se renderiza idéntico.
