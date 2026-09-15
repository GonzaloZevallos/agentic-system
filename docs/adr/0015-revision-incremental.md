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
