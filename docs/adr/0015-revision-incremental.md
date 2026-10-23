---
status: accepted
date: 2026-09-14
---

# Una revisión después de una devolución es incremental: arranca del head ya revisado

En la segunda corrida de GH-12 cada revisión del Tech Lead costó entre 5.5 y 10 USD, más
que la subtarea que revisaba (issue #7). Dos pases por subtarea, devolución y aprobación,
son unos 20 USD. El pase de aprobación de `bootstrap` volvió a verificar la frontera
congelada, el `uv.lock` y el CI que el primer pase ya había aprobado, cuando el Developer
había tocado un solo archivo: revisaba otra vez desde la base de la subtarea, con el brief
entero como spec.

**Toda revisión posterior a una devolución es incremental.** El caso de uso guarda en la
subtarea el head que cada pase revisó (`reviewed_head`). El pase siguiente toma ese commit
como punto fijo del `code-review`, no la base de la subtarea, y como spec la lista
`### For the Developer` de su propio pase anterior más el brief. Verifica cada ítem y lo
que el diff toque además; lo que el pase anterior aprobó y el diff no toca queda aprobado
y no se revisa de nuevo. La primera revisión de una subtarea sigue siendo completa: base
de la subtarea, brief y definición de PM como spec. Aplica igual a la integración.

**El costo es cobertura.** Un Developer que rompa algo fuera del diff entre dos pases no
existe: git no lo permite. Lo que sí se pierde es un segundo par de ojos sobre lo que el
primer pase dejó pasar. Lo aceptamos: el segundo pase con el mismo modelo y el mismo
prompt no es un revisor distinto, y GH-12 mostró que lo que encontraba en el pase de
aprobación era lo que el diff traía, no lo que el primer pase había aprobado.

## Opciones descartadas

- **Revisar siempre desde la base, con subagentes más baratos**: baja el precio del pase
  pero no el trabajo repetido; los subagentes en Sonnet (ADR-0013, adenda) entran además,
  no en vez.
- **Que el Developer declare qué archivos tocó y el revisor mire solo esos**: el diff ya
  lo dice sin confiar en el Developer.
- **Un solo pase, sin devolución**: el límite de devoluciones existe para que un error
  técnico vuelva al mismo Developer (README §3); el pase de aprobación es lo que cierra ese
  loop.

## Adenda: el canal entre revisiones (issue #42, 2026-09-16)

La revisión incremental abarata el pase siguiente de **una** subtarea. No dice nada de lo
que una revisión sabe de las otras, y ahí había un agujero: el revisor lee
`(ORIGINAL, "PM", "Tech Lead")` más su subtarea, nunca las revisiones hermanas. En la
corrida 4 las dos devoluciones de integración ya figuraban en revisiones de subtarea como
"observación, no cargo" (doble conteo en `recurring_revenue`, checker ciego a imports
relativos en tres revisiones), y cuatro Developers redescubrieron en paralelo el mismo
workaround del checker. Es una falla de canal, no de juicio (issue #17).

**Dos canales, y hacen falta los dos.**

**Los invariantes** son del plan: reglas que toda subtarea cumple, cada una verificable
sobre el diff de una sola ("toda query sobre suscripciones aplica el fold de duplicados").
Las propone el Tech Lead efímero desde lo que destapó la entrevista, Tech Lead las adopta o
las rechaza con motivo, viajan al brief de cada Developer, y la revisión las verifica como
verifica el brief y las costuras: romper una es bloqueante, con categoría `plan-invariant`.

**La tabla de hallazgos** (`## Tech Lead: findings`) acumula los hallazgos de todas las
revisiones de la tarea con estado `open | fixed | dismissed`. Una fila es un `location` más
un `claim`; el veredicto de la primera vez es el que queda, así que una revisión posterior
no relitiga lo que otra ya descartó. La revisión incremental mira lo abierto y lo nuevo que
bloquea. La leen el revisor, el Developer y el integrador, acotada a lo abierto.

**Se escribe agregando y se lee plegando.** Las ramas de una ola corren a la vez y no se
ven entre sí, así que ninguna puede plegar la tabla: cada revisión **agrega** al estado
(`findings`, reducer append-only) y el fan-in, que es el único momento en que las ramas
están mergeadas, escribe la sección que lee la ola siguiente. El crudo de todas las pasadas
queda en el frontmatter; la sección lleva lo abierto, que es lo que entra en el próximo
prompt.

**Una fila se cierra porque un revisor lo dijo, nunca porque dejó de nombrarla.** El
revisor entrega `settled`, los lugares que verificó y ya no valen. Cerrar por silencio
sería lo contrario de lo que este mismo ADR le pide: toda pasada después de la primera es
incremental y tiene instrucción explícita de *no* volver a mirar lo que ya aprobó, así que
no decir nada de una fila casi siempre quiere decir "no miré". Cerrando por silencio, la
primera devolución de cualquier subtarea le borraría a esa subtarea las observaciones que
venía arrastrando, que es justo la clase de cosa de la que trata #17.

Es también lo que deja cerrar una fila de alcance `plan`: la subtarea que la levantó ya
está aprobada y nadie la vuelve a revisar, pero la revisión de integración lee la rama
entera y puede darla por saldada.

**La clave se normaliza.** Dos corridas del revisor no escriben el mismo string para la
misma objeción; un espacio de más o una mayúscula partirían en dos la fila que todo esto
existe para juntar. Misma normalización que usa la entrevista para las preguntas del
humano. Y la columna `Subtask` nombra a **todas** las subtareas que la levantaron, porque
los prompts rutean sobre eso ("una fila de otra subtarea" contra "una fila tuya").

**No esperarle a la tabla lo que la tabla no da.** Con el pipeline de ADR-0004 adenda
quinta, la revisión de `api` corre al mismo tiempo que la de `domain`: en la **primera ola**
ninguna ve a la otra y la tabla está vacía. Y la primera ola es exactamente el caso de #17.
La tabla rinde en la segunda pasada y en la revisión de integración; el canal que funciona
desde el arranque son los invariantes, porque están en el plan antes de que nadie codee.
Lo que hace servible a la tabla es que la vuelta de una devolución pasa por el fan-in y no
por adentro de la rama, así que la segunda ola arranca viendo la tabla entera.

**El revisor no gana memoria de sesión.** Se evaluó y se descartó (README §13): cada
revisión es una función sin estado de (invariantes, brief, diff, tabla de hallazgos). La
sesión larga es la de la planificación, donde el contexto es el mismo problema visto cada
vez más hondo; en la revisión sería el contexto de otra rama, que es justo lo que no se
quiere arrastrar. Lo que una revisión tiene que saber de otra entra por la tabla, que es
dato y se puede medir.

## Adenda (2026-10-06): la aprobación cierra los bloqueantes previos de su propia subtarea (GH-201)

**La excepción.** "Una fila se cierra sólo porque un revisor la dio por saldada" tiene una
excepción: cuando Tech Lead **aprueba** la subtarea S en la versión N, los registros
`blocking` de S, de alcance `subtask` y de versión menor que N, cuya clave el propio
review aprobatorio no vuelve a levantar, dejan de estar abiertos. El motivo es ADR-0016:
el veredicto se deriva de "no existe un bloqueante", así que una aprobación con un
bloqueante abierto es una contradicción. Pasó en apostrophe GH-107 (PR #111): el
bloqueante de v1 lo arregló el Developer, la v2 aprobó con `settled` vacío, y la fila
quedó `open` para siempre, con el auto-merge refusando por `open_findings`.

**Cómo.** `rules.settled_on_approval` decide qué se cierra, y es el único lugar que lo
decide. `review.py` agrega el resultado al parche (`approval_settled`) sólo en la rama de
aprobación; nunca se deriva del estado de la subtarea al leer. Cada `SettledRow` es
`{subtask, location, claim, version}` y la cobertura es **por registro**
(subtarea, clave, versión), nunca por clave: `findings_rows` aplica primero el `settled`
del revisor, sin cambios (sólo por ubicación y global), y después la cobertura. Una fila
se cierra sólo si **todos** sus registros abiertos están cubiertos; un `important` o una
`observation` de la misma clave, un registro de una subtarea hermana o una regresión en
una versión posterior la mantienen abierta, en cualquier orden de agregado.

**El caso de la integración.** Una clave levantada por `api` y también por `integration`
sigue abierta aunque `api` apruebe en v2, hasta que la revisión de integración la dé por
saldada. Es intencional: `api` no puede dar por cerrado lo que la integración también vio.

**Riesgos.**

- Una v2 aprobada sin re-verificar una fila la cierra en silencio. Se acepta: aprobar
  significa "no existe un bloqueante" (ADR-0016).
- El `settled` del revisor sigue siendo por ubicación y global, y puede esconder una
  reaparición posterior en la misma ubicación. Es el comportamiento de hoy y la
  integración lo necesita para las filas `plan`; queda como seguimiento.
- Los documentos ya aprobados no se vuelven a plegar: apostrophe GH-107 sigue con la fila
  abierta y se mergea a mano.
- Un `important` que el revisor olvida listar en `settled` sigue bloqueando el auto-merge.

**Medición.** La huella (`prompts.fingerprint`) de `tech_lead_review` y
`tech_lead_review_light` se mueve, porque cada prompt gana una cláusula; se pide un
`bl-*` nuevo al mergear. `agentic.review.findings` no lleva la huella como etiqueta, así
que sigue siendo comparable. No se agrega, mueve ni renombra ninguna métrica, etiqueta ni
span, ni se renombra ninguna clave del documento: la única clave nueva es
`approval_settled`, que se escribe sólo si no está vacía, por lo que un documento sin ella
se renderiza igual byte a byte.

**Addendum (GH-318, ADR-0060).** Un escritor más del pliegue: una corrida aprobatoria de QA Performance asienta toda fila abierta con ubicación `QA Performance`. `settled_on_approval` no cambia; el escritor y su motivo están en el ADR-0060.
