---
status: accepted
date: 2026-11-17
---

# El poller retiene al hijo apilado mientras su padre espera

GH-899, parte 3c de GH-838. Un hijo apilado (documento con `stack_parent`) espera mientras
`stacking.pause_cause` de la cadena de sus padres no dice `none`. Hoy ningún documento escribe
`stack_parent`, así que el comportamiento no cambia: una ronda sin documentos apilados hace las
mismas lecturas y las mismas acciones.

## Política

- `stack_hold.compute` corre una vez por ronda, justo después de leer la cola y antes de cualquier
  gatherer. Solo mira los hijos de `queued_all` con un `stack_parent` legible; con ninguno no hace
  ninguna lectura. Comparte el `StackMemo` y el `upstream` de la ronda.
- Los gatherers que evalúan, anotan, ordenan, cuentan o cargan presupuesto a un candidato
  (`gather_resumes`, `gather_retakes`, `gather_merges`) reciben el conjunto retenido como `skip`
  explícito. Solo `gather_launches` y `gather_orphans` reciben la cola sin los retenidos; todo
  otro consumidor recibe `queued_all`. Un filtro final quita del plan toda acción retenida.
- Una corrida viva no se mata.
- `unreadable` retiene y dice una línea por ronda; no escribe nota ni fila. Un registro
  `stack_parent` ilegible no nombra a quién esperar: no se retiene, lo decide `stacking.refusal`.

## Episodios

Un episodio es el tramo en que el hijo espera por `(padre, causa)`. Se abre con una `Tracker.note`
(`stacking.pause_note` más el marcador `<!-- agentic-stack-pause: <padre> <causa> <k> -->`, con
`k` = filas previas de ese par + 1) y una fila abierta en `stack_pauses`, escrita solo con
`add_stack_pauses`. Si el marcador ya está en los comentarios solo se escribe la fila. Una nota
fallida va a `tracker_failures`, la fila se escribe igual y la nota no se reintenta. Un cambio de
causa o de padre sella la fila vieja en la misma escritura y abre la nueva.

El padre de la fila es el padre directo del hijo; la causa puede venir de un ancestro.

## Escritor libre

Nota, fila y sellado se escriben solo si `stack_hold.free_writer`: ninguna corrida viva del
producto en `registry.live()` y ningún lock que `RunLock.refusal` lea vivo. Un lock muerto o de
otro arranque es libre; uno de otro host se lee vivo. El mismo predicado en apertura y cierre. Un
hijo que no es libre no escribe nada y deja `agentic: poll: #N held (stacked, at work)`. Una
ronda `dry_run` o retenida por espacio no lee comentarios ni escribe nada.

## Sellado

Cuando la causa pasa a `none`, `resumed_at` se sella en cada fila abierta, conservando su `at`,
antes de los gatherers: el tiempo retenido nunca cuenta como quietud. Un hijo con lock muerto se
retoma esa misma ronda; uno sin lock espera `ORPHANED_QUIET` una vez. Ese es el costo.

## Riesgos

- Lock muerto con el padre `unreadable` más de 24 h: no hay fila que sellar y, al soltarse,
  `gather_retakes` ve un documento más viejo que `STALE_AFTER` y emite `Retakes spent:`. Una caída
  de `gh` de más de un día produce ese aviso; el humano lo libera con `agentic resume`.
- Una caída larga de `gh`, o un hijo con lock vivo, deja a su issue sin nota; solo la línea del
  poller lo muestra.

## Solo del poller

Los verbos manuales (`resume`, `land`, `merge`) y `assess_merge` no leen `stack_hold`; la barrera de
merge sigue siendo `stacking.refusal` (`stack_parent_unmerged`). Ningún módulo de `agentic/`
fuera de `poll.py` lo importa.
