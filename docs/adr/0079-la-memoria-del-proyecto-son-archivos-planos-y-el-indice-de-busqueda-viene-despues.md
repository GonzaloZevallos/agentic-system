---
status: accepted
date: 2026-10-28
---

# La memoria del proyecto son archivos planos en el repo, y la búsqueda sobre ellos viene solo cuando hace falta

GH-431. Las reglas que el dueño da al contestar un escalado vivían en el comentario de un
issue o en la memoria privada de la sesión que orquesta. La tarea siguiente no las veía y las
mismas preguntas volvían (e2e que no traba merges, partir solo por encima de 150 USD, el pull
request que abre la entrega). Si cambia quien orquesta, esa memoria se pierde. El dueño pidió
(2026-10-28) que la memoria sea del proyecto, no de quien orquesta, y que se registre cómo
escala.

## Decisión

1. **Archivos planos en el repo son la única fuente de verdad.** Markdown, versionado y
   revisado en pull requests: `docs/decisiones-del-dueño.md` y el de cada producto con las
   reglas del dueño, `docs/orquestacion.md` con las de quien orquesta, los ADRs, el glosario
   y las capacidades. `knowledge_index` describe cada uno en una línea y cada rol abre lo que
   necesita (divulgación progresiva, búsqueda agéntica con grep). Los archivos de decisiones
   del dueño van enteros, solo con las entradas vigentes.
2. **Cuando el índice deje de caber en el contexto de un rol**, se agrega una búsqueda de
   texto completo local sobre esos mismos archivos (SQLite FTS5), que el rol consulta con una
   herramienta. Es un índice y nunca un segundo almacén: se reconstruye desde los archivos.
3. **Embeddings locales (por ejemplo sqlite-vec)** solo ante un caso medido en que la búsqueda
   léxica falle, como encontrar una devolución pasada dicha con otras palabras.
4. **Ningún servicio externo de memoria.** La memoria queda en el proyecto.

## Precedencia entre una regla del dueño y un ADR

- Una regla vigente del dueño decide la pregunta que cubre, sin escalar, y quien la aplica cita
  su id `D-<hex>`.
- Si toca un ADR y es más nueva que él, gana, y la tarea deja un hallazgo `outside` para
  enmendar el ADR; si es más vieja que el ADR, pierde contra él. Cada entrada declara los ADRs
  que toca en una línea `Rozan:`.

## Las entradas no se editan

El texto de una entrada nunca se edita: corregir es reemplazar. La entrada nueva cita el id de
la reemplazada en `Reemplaza:` y la vieja se tacha en el mismo commit; el id sale del texto
(`D-` y 8 hexadecimales del sha256 del texto normalizado), así que dos ramas que agregan
reglas distintas no necesitan un contador compartido, el mismo problema que ADR-0049 resuelve
para los números de ADR. `.gitattributes` fusiona los archivos con `merge=union` para que dos
ramas que solo agregan al final no choquen.

## Consecuencias

- El costo en contexto crece lineal con las entradas vigentes; pasar de 120 líneas vigentes
  avisa en `preflight` y no rechaza nada.
- Ni FTS5 ni embeddings se construyen ahora; esta decisión solo fija el orden en que se
  agregarían y la condición para cada paso.
