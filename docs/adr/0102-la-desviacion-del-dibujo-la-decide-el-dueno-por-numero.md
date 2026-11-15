---
status: accepted
date: 2026-11-15
---

# La desviación del dibujo la decide el dueño, por número

GH-766, parte 1b (GH-772). Cuando un rol entregaría algo visible distinto del dibujo aprobado
(Dibujo aprobado), no lo decide el sistema ni el PM: lo decide el dueño. Este documento fija
las piezas que lo hacen posible. Ningún nodo las usa todavía, así que el comportamiento no
cambia: sin `design` no se escribe ninguna clave nueva del estado.

## Decisión

**La clave.** Una desviación se identifica por `ruta:elemento` (`departure_key`): la ruta
normalizada con `posixpath.normpath` (sin `./`), nunca con casefold, y el elemento citado con
casefold y espacios colapsados. La ruta no puede contener `:`, ser absoluta ni salir de la
carpeta del diseño; el elemento no puede quedar vacío. Nada más entra en la clave, ni lo que
se entregaría ni texto libre. La regla de ruta vive solo en `drawing.normalize_drawing`.

**El número estable.** Lo asigna únicamente `task.departure_records`: una clave existente
conserva su número y una nueva recibe `max(number)+1`, en el orden de entrada. Ningún esquema
que recibe un rol tiene `number` ni `drawing_departures`. El texto del escalado lo arma solo
`drawing.departure_question`: cita la ruta tal cual y el número, nunca la clave.

**El reductor.** `add_drawing_departures` lo escribe solo el turno serializado del sistema. Una
fila existente conserva `number`, `raised_at`, `raised_by` y el texto; `answer` pasa de `None`
a un valor una sola vez y `fix_passes` toma el máximo. La primera escritura, con la clave
ausente, pasa por el mismo pliegue que las demás, y no levanta ante un conflicto.

**La respuesta.** El dueño responde por el número, y la respuesta llega solo como un veredicto
estructurado en `resume`; nunca se deduce de un comentario libre.

**La causa.** `drawing` vive en `SpecEscalationCause`, la causa ancha. `SpecConflictCause`
(contradiction, conflict, data_loss) sigue siendo la estrecha: tipa los campos `spec_conflict`
de los esquemas y la tabla de preguntas de conflicto, y un rol nunca levanta `drawing`.

**La pertenencia.** Si una ruta pertenece al dibujo aprobado lo comprueba el nodo que la use;
la clave y los esquemas solo validan la forma. `DrawingDeparture`, `DrawingCheck` y
`DefinitionTurn` existen sin consumidor, y una prueba recorre el esquema de cada salida de rol
para asegurar que ninguna los alcanza.

**El registro `NO_DRAWING_RULE`.** Cada prompt bajo `prompts/` (incluido `spec/`) lleva
`{{drawing_rule}}` exactamente una vez o figura en `NO_DRAWING_RULE`, y nunca ambas cosas. El
motivo sigue una gramática cerrada: `pending GH-766 part N` (N en 3, 4 o 5), `covered by
<prompt>` (el destino lleva el marcador o está `pending`), `plan carries the rule`, `no visible
change` (solo nombres `_platform` o `_stack`) y `never decides what a person sees` (una lista
fija). GH-766 se cierra solo cuando no queda ningún `pending`.

## Criterio de la parte 1 → parte que lo cumple

| Criterio | Parte |
|---|---|
| `captures`, render y causa `drawing` | 1a |
| `Departure`, clave, texto, `NO_DRAWING_RULE`, clases de esquema y registro | 1b |
| `DefinitionTurn` en `roles.py` y `Answers`/`Consultation*WithDrawing` | parte 3 |
| Variante del plan | parte 4 |
| `DesignReview` | parte 5 |

## Consecuencias

- El registro guarda `key`, pero ningún nodo la lee todavía; quien la consuma primero debe
  comprobar que coincide con `departure_key(drawing, element)`.
- El escaneo prueba que cada motivo sigue la gramática, no que sea cierto: las partes 3 y 5
  revisan los `covered by` cuando agregan el marcador.
- El glosario del contexto `process` define **Desviación del dibujo**.
