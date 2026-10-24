---
status: accepted
date: 2026-10-24
---

# El Brief no tiene tope de palabras y los criterios rechazan la regresión, no el estado

GH-334. apostrophe mergeó la PR #357 (tarea GH-307, tramo `standards-doc`) con `BRIEF.md` en
562 palabras (tope 500) y `DESIGN_STANDARDS.md` en 611 líneas (tope 300). La corrida
siguiente se rechazó al arrancar y la cola quedó parada con el poller diciendo `nothing to do
this round`.

## Decisión del dueño (2026-10-24)

> `BRIEF.md` has no size limit. Remove `BRIEF_MAX_WORDS` and the word-count refusal; the
> brief keeps only its shape check (the four mandatory sections). The line cap on criteria
> documents (`CRITERIA_MAX_LINES`) stays.

## Premisa corregida

El arranque nunca rechazó una corrida por el tope de líneas de criterios: `cli._warn_over_cap`
solo avisa (ADR-0055) y en el log de la corrida de GH-307 la línea 1 es ese aviso. El único
rechazo fue `brief_refusal`, por las palabras. Quitar el tope del Brief destraba apostrophe
por sí solo.

## Qué se decide

1. **El Brief no tiene tope de palabras.** Desaparecen `BRIEF_MAX_WORDS` y su rechazo; queda el
   chequeo de las cuatro secciones obligatorias, con su mensaje intacto (ADR-0025, adenda).
2. **Los criterios rechazan una regresión, no un estado.** Un documento de criterios se
   rechaza solo si en el head pasa `CRITERIA_MAX_LINES` **y** tiene más líneas que en su base.
   Un documento ausente en la base cuenta 0; el tope es por archivo, de modo que partir un
   documento en dos archivos que siguen pasando el tope se rechaza igual. El rechazo nombra el
   archivo y el conteo. Lo mira la Puerta de entrega (aviso temprano) y lo garantiza la
   decisión de merge, con dos razones nuevas de `MergeRefusal`: `criteria_over_cap` y
   `criteria_unreadable`, ambas con paso de aterrizaje `none` (para el humano; ningún `fix`).
   Nunca se rechaza al arrancar una corrida: ahí sigue el aviso del ADR-0055, y así una
   corrida cuyo trabajo es bajar un documento de su tope puede arrancar (punto 3 del pedido).
3. **Una suma entre subtareas** que cruza el tope sin que ninguna lo cruce sola solo la ve el
   merge, y queda para el humano: `criteria_over_cap` con paso `none` y una línea del poller
   por ronda. No hay reparación automática.
4. **Punto 2 del pedido, fuera de alcance.** El aviso del poller y la escalada de un documento
   de criterios sobre el tope en master partían de que el Brief rechazaba la corrida. Sin
   ese rechazo, no hay cola parada que avisar. El punto 3 lo cumple el aviso-y-sigue que ya
   hace el arranque.
5. **Una tarea cuya PR se cerró porque los tramos aterrizaron todo.** Si cada unidad aterrizó
   por tramos (según la ancestría de git de su `reviewed_head`, nunca `landed_slices` solo),
   la tarea se cierra con `closed_at` y `closed_by_slices`, sin `delivery_outcome`: no es una
   entrega y no suma ni cambia ningún valor de `DeliveryOutcome`. Si queda trabajo, recibe una
   corrida nueva. El poller dice cuál de las dos.

## Consecuencias

- ADR-0025 y ADR-0055 quedan con una línea de adenda que apunta aquí; no se reescriben.
- El glosario de conocimiento ("Estándares de diseño") reemplaza "nunca se rechaza" por la
  regla nueva.
