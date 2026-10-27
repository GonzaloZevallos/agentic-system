---
status: accepted
date: 2026-10-27
---

# La duración de cada chequeo se registra, y el chequeo completo la compara con un presupuesto

GH-365. Las suites crecen sin un límite y cada función nueva vuelve más lento cada puerta
posterior. En apostrophe, en dos semanas, los e2e pasaron de unos 61 a unos 110, vitest de
unos 1.135 a unos 2.037 y pytest de unos 1.854 a unos 2.288; la mediana de una puerta que
pasa fue de 5-7 min a 20-24 min. Hoy nadie ve la duración por chequeo: `merge_checks`
guarda sólo el total. Medido el 2026-10-26 sobre las tareas iniciadas desde el 2026-10-12.

## Decisión

### La duración por chequeo, en los registros

Cada entrada de `merge_checks` y cada `FullCheckEntry` gana un `duration_s` opcional, y la
tabla de chequeos de la puerta de entrega gana una columna de segundos. Toda cifra sale de
`domain/presubmit.check_durations`; ningún otro módulo suma ni elige la duración de un
chequeo. El campo falta cuando el chequeo no tiene muestra. La señal
`agentic.presubmit.duration` ya lee esa misma suma: no se agrega ni se mueve ninguna señal.

### `check_budgets`, por chequeo y nunca por nivel

El stack declara, opcional, `check_budgets: {<chequeo>: <segundos>}`, un número positivo
por nombre. Cada nombre debe ser un chequeo declarado bajo `checks`, y la clave exige
`delivery_checks`, igual que `rerun_checks`: sin el chequeo completo no hay dónde
compararla. Un presupuesto por nivel se descartó: con `parallel_checks` el tiempo de pared
de un nivel no es la suma de sus chequeos, así que mediría otra cosa.

### La comparación corre sólo en `full_checks`

El presupuesto se compara una vez por tarea, en el nodo `full_checks`, sobre la cabeza
integrada: nunca en la puerta de entrega, la de merge ni el aterrizaje. Lee
`domain/presubmit.decided_durations`, la duración de la fila que decidió el veredicto de
cada chequeo:

- si fue un flake, la del reintento verde;
- un chequeo que agotó su tiempo nunca pasa, así que nunca se compara;
- una fila sin duración se descarta, jamás cuenta como cero.

El veredicto es una función pura del dominio sobre filas registradas y presupuestos
declarados; lo escribe el sistema, nunca un rol que lee un número. Un producto que no
declara `check_budgets` produce los mismos veredictos, devoluciones, filas de hallazgos,
señales y cuerpo de pull request que antes; sus registros sólo ganan los `duration_s`.

### Adenda a ADR-0065: un pase reutilizado cuenta en el presupuesto

ADR-0065 deja fuera de `check_durations` y de la señal las filas `reused`, porque no
corrieron en esa puerta. Para el presupuesto cuentan, con la duración guardada: es la que se
midió sobre ese mismo árbol y ese mismo entorno, y lo que se compara es el costo del
chequeo, no el de esta corrida. `check_durations` y la señal siguen ignorando las filas
`reused`; sólo `decided_durations` las lee.

### El hallazgo es `over_budget`, no una fila de la tabla de hallazgos

Cuando algún chequeo pasó su presupuesto, el registro de `full_checks` lleva
`over_budget: [{name, duration_s, budget_s}]`: un nombre declarado y dos números, escritos
sólo por el sistema, y sólo si hay al menos un exceso. No devuelve nada ni bloquea nada:
ni `auto_merge_decision` ni el Pase final de importantes lo leen. No agrega valores a
`FindingCategory`, `ChangeCategory`, los enums del esquema de revisión ni a ninguna
etiqueta de telemetría. `FindingCategory` alimenta las categorías de devolución y no tiene
un valor neutro; sumarle uno para una fila que nunca puede devolver sería falso. El registro
de la última corrida es el vigente, así que una corrida posterior dentro del presupuesto lo
borra.

Se ve en dos lugares: la línea de aprobado de la sección `Full checks`, y una parte
`Checks over budget` del cuerpo del pull request, presente sólo cuando el registro vigente
trae `over_budget`.

### Ruido

El tiempo de pared se mide con hasta tres corridas compartiendo la máquina y bajo
`parallel_checks`; una duración guardada puede venir de una corrida con menos carga. No hay
margen en el código. Un exceso aislado no dice nada: sólo uno repetido entre tareas indica
que la suite creció, y el remedio es un cambio de datos en `stack.yaml` (subir el
presupuesto o recortar la suite), no de código.

### La regla de pruebas por costura

Los prompts del Developer, del integrador y de las dos revisiones del Tech Lead llevan una
misma oración, palabra por palabra: se prueba en las costuras que el plan acordó para la
subtarea, y un archivo de pruebas nuevo fuera de ellas, o en una costura más lenta que la
que el plan nombra para ese comportamiento, pide una línea en el markdown de la entrega que
diga por qué la costura acordada no lo muestra. La oración no nombra ninguna herramienta. Los
prompts del plan piden que `seams` de cada subtarea nombre esas costuras de prueba.

En la revisión, un archivo de pruebas nuevo fuera de las costuras del brief y sin esa línea
es una `observation`, categoría `standard-seams`, con la ruta del archivo y la ausencia de la
línea como evidencia. `important` y `blocking` quedan para romper una costura que el plan
nombra, como hoy: la fusión automática no pasa a depender de cómo un modelo lee las costuras.
La revisión liviana no lleva observaciones, así que sólo reporta lo segundo.

La regla es consultiva: una fila `observation` de la tabla de hallazgos no llega a ningún
pull request, y ampliar `_parts` para llevarlas queda fuera de esta decisión. En un producto
la regla sólo actúa cuando su propio `CODING_STANDARDS.md` nombra sus niveles de prueba; ese
cambio es del humano del producto.

### La huella de los prompts

Los seis prompts cambian en un solo commit, de modo que `prompts_fingerprint` se mueve una
sola vez y el corte de una Línea de base es limpio. Los dos brazos de `review_mode` reciben
la misma oración, así que difieren sólo en su interruptor.

## Consecuencias

- Los registros anteriores a este cambio no traen `duration_s` ni `over_budget` y se leen
  como siempre.
- La comparación puede ser ruidosa por la carga de la máquina; se acepta a sabiendas.
- La regla de costuras no frena nada por sí sola: informa.
