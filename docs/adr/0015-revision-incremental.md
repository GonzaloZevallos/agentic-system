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

**Qué pasada cierra una fila lo dicen las corridas, no los hallazgos.** Un pase que no
encuentra nada no agrega nada, y es justo el pase que cierra: si la última pasada se
contara desde los hallazgos, ninguna fila se cerraría nunca. Se cuenta desde `runs`, que
tiene una entrada por revisión haya encontrado lo que haya encontrado.

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
