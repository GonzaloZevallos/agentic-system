---
status: accepted
date: 2026-09-24
---

# La revisión de stack ordena por la historia de primer padre de `HEAD`, no por fechas

Supersede dos frases de ADR-0034: la señal (a), "`BRIEF.md` tiene un commit más nuevo que
ése", y la lectura de `closed_at` de la señal (b). ADR-0034 es un ADR aceptado y no se edita
(ADR-0010); quien busque por qué esas dos frases ya no rigen llega acá.

## Qué estaba mal

La fecha de un commit (`%cI`) es cuándo se escribió en su rama, no cuándo llegó a la rama
principal; `closed_at` es cuándo corrió `agentic close`. En gym-saas (2026-09-24) el último
cambio de `BRIEF.md` (6d7a108, PR #30) está fechado 18:26:11 y llegó a master con af341d6 a
las 18:38:17; el ancla (d748370, ADR-0013, PR #32) está fechada 18:32:51 y llegó con 0ae0919 a
las 18:37:20. Comparadas por fecha, el Brief es anterior al ancla y la señal no dispara; la
decisión de stack de ADR-0013 se tomó, en verdad, sin el Brief en master. Comparar fechas como
texto además falla cuando los desfases horarios difieren.

## Decisión

Ningún reloj decide nada en este verbo. El orden es la **posición en la cadena de primer
padre de `HEAD`**:

- Una lectura por invocación, `git rev-list --first-parent --reverse HEAD`, da un mapa
  `sha → posición` (base 0). Su costo es lineal en el largo de la línea principal, se paga una
  vez por invocación y nunca por documento.
- El commit que trajo una ruta a la línea principal es
  `git log --first-parent -1 --format=%H HEAD -- <ruta>`.
- **`HEAD`, no `origin/HEAD`.** `preflight` y `checkouts.task_base` ya entienden por
  "principal" a `HEAD` del `product_dir`; `origin/HEAD` discreparía con el resto del sistema y
  rompería el repo greenfield sin remoto que ADR-0034 acepta. Un checkout parado en otra rama
  lee esa rama como la principal, igual que el resto del sistema.
- **El ancla** es el commit que trajo `stack.yaml` a la línea principal: el merge de aterrizaje
  en un repo que mergea con `--no-ff`, el commit mismo en uno con squash o fast-forward. Es una
  sola lectura: el sha que se registra (`marker`, `description`, `--dry-run`, `Signals.anchor`)
  y el que decide toda comparación son el mismo. Para gym-saas su valor pasa de `d748370` a
  `0ae0919`. Los marcadores escritos antes de este cambio llevan el significado viejo (el
  commit, no el aterrizaje); la supresión sólo mira `MARKER_PREFIX`, así que las propuestas
  abiertas siguen suprimiendo.
- **Señal (a):** el Brief cambió si aterrizó estrictamente después del ancla. Si un mismo
  commit trajo los dos archivos, el stack se decidió con ese Brief en el árbol.
- **Señal (b):** una entrega cuenta si su resultado es `merged` o `merged_with_changes`, su
  `merge_commit` tiene posición en la cadena y ésta es posterior al ancla (o no hay ancla).
  Se cuentan tareas desde los documentos, no merges crudos de la línea principal, que
  contarían cosas que no son tareas.
- `stack_signals.py` sólo recibe enteros; no importa puertos, `Path` ni `subprocess`.

## `merge_commit` y el backfill

`PullRequest` gana `merge_commit` (`mergeCommit { oid }` en la consulta GraphQL) y `close`
escribe la clave `merge_commit` en el frontmatter sólo para un PR que mergeó; si no, se omite.
Un documento cerrado antes de este cambio no la tiene, así que `stack_signals.needs_merge_commit`
(cerrado, resultado mergeado, sin `merge_commit`) decide en un solo lugar qué documentos se
completan; `close --all` y `close --issue N` la usan. El backfill escribe **sólo**
`merge_commit` (nunca reescribe `closed_at`, `delivery_outcome`, `human_commits`,
`human_lines_changed` ni `time_to_merge_seconds`), con el estado `backfilled` y salida 0. Un
documento que nunca se completa **no cuenta**: la señal (b) subcuenta hasta que el humano corre
`agentic close --all` en cada producto.

## Declarado, no verificado en este ciclo

- El `mergeCommit.oid` de GitHub cae en la cadena de primer padre de la rama base para merge,
  squash y rebase-merge.
- El `oid` de un rebase-merge es el último commit rebasado.
- Un checkout atrasado respecto de `origin` subcuenta hasta que hace pull.

## Estilo de merge de cada producto

gym-saas mergea con `--no-ff` (la tabla del pedido: af341d6 y 0ae0919 son merges de
aterrizaje). agentic-system también; `git log --first-parent` de este repo:

```
e1493e0 Merge pull request #133 from GonzaloZevallos/agentic/GH-129
966e882 Merge pull request #132 from GonzaloZevallos/agentic/GH-131
5a0f48d Merge pull request #130 from GonzaloZevallos/agentic/GH-128
```
