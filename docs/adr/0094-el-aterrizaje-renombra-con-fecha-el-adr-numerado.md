---
status: accepted
date: 2026-11-12
---

# El aterrizaje renombra con fecha el ADR numerado de un producto que nombra sus ADR por fecha

GH-611. Enmienda el ADR-0049. En apostrophe, desde el #693, los ADR se nombran
`docs/adr/<aaaammdd>-<slug>.md`; las ramas anteriores seguían con `NNNN-` y el aterrizaje las
renumeraba contra el master una y otra vez (GH-592: la cabeza con `0079-x.md` pasó a `0085-x.md`
tras una decisión aplicada y volvió a chocar) sin asentarse nunca. Regla del dueño: cuando el
producto nombra sus ADR por fecha, el ADR numerado de la rama que choca con el master se renombra
a la forma fecha-y-slug, no se renumera. No se le pregunta a ningún humano.

## Decisión

- **Disparador.** El producto declara `adr_naming: dated` en su stack. Se lee solo del stack en el
  sha de master de la ronda (`merge.py`, una sola lectura: `adr_collision` devuelve `Undated` en un
  producto fechado y de ahí sale `MergeFacts.adr_naming`) o en `context.upstream.sha` (el paso).
  Clave ausente o stack ilegible: numerado, y el flujo numerado queda byte por byte como era.
- **Tabla.** En `adr_number_taken` con `adr_naming == dated`, `landing_step` devuelve `renumber`
  (también para `Ambiguous`) mientras `renumbers < LANDING_RENUMBERS_MAX` y la cabeza no tenga un
  rechazo; pasado eso, un `escalate_adr` y luego `none`. Nunca `apply_adr_decision`, aunque haya
  entradas numéricas resueltas de rondas anteriores.
- **Paso `renumber`, camino fechado.** Relee el nombrado en `context.upstream.sha`, recalcula la
  colisión desde ese sha (nunca usa el sha, la lista de movimientos ni el `escalate_adr` de una
  ronda anterior), trae el upstream (`_bring_in`) y llama a `adr_dated.rename`, que escribe los
  archivos. Corre el `docs.generate` declarado por el producto bajo un límite de pared
  (`check_timeouts['docs']`, si no `GENERATE_WALL_S = 600`), hace un solo commit con `commit_paths`
  (renombrados, reescritos y documentos generados) y empuja con lease sobre la cabeza. Ningún
  módulo del sistema conoce la plantilla de ADR del producto: sus convenciones vienen de las claves
  del stack y de su `docs.generate`.
- **Fecha.** La del `date:` del front matter del propio ADR; si no hay, la fecha de autor más
  antigua (UTC) del commit que añadió su ruta, siguiendo renombres; nunca la del commit que lo
  movió.
- **Referencias.** El nombre completo anterior, un enlace cuya etiqueta es el número, un
  `docs/adr/NNNN` y un `ADR-NNNN` suelto (solo en una línea que el master no tiene). Una lista o
  rango con un número movido, o un número que ninguna forma cubre, no se adivina: `unrewritable`.
- **Resultados.** El registro `renumber` lleva `mode: dated` y un `outcome`: `renamed`; `declined`
  (`slug_taken` o `unrewritable`, definitivo para esa cabeza); `retry` (`generate_failed` o
  `unreadable`, cuenta para el tope pero no es un rechazo). Un lease perdido o un modo cambiado no
  registra nada. El acarreo de la aprobación (`renumber_carries`) no cambia: solo `docs/` acarrea.
  La pregunta `escalate_adr` nombra la causa y no ofrece un `Decision:` numérico.
- **Sin compuerta local.** No hay presubmit en el paso: el merge corre el nivel completo sobre la
  cabeza empujada, que es quien juzga el renombre, y repetirlo aquí cuesta minutos sin ver nada que
  el merge no vea. Lo que sí se verifica localmente es que `docs.generate` termine.
- **Liberación.** Un `escalate_adr` numérico sin respuesta se libera solo
  (`landing.released_by_naming`, gemelo de `released_by_ci`) cuando el stack en el upstream declara
  `dated`, una vez por (cabeza, upstream) y sin texto de `Human`. `waits_on_human` lee su marca.

## Consecuencias

- Un producto numerado no cambia: `adr_collision`, la tabla y `renumber_adr` se comportan igual.
- Un ADR cuyo slug ya existe en el master, o cuyas citas son ambiguas, se pregunta una vez con la
  causa; no se reintenta.
