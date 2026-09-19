---
status: accepted
date: 2026-09-18
---

# PjM recibe el nombre declarado del producto como dato, y el test de qué producto es corre antes que el de discrepancia

La clasificación de una tarea depende de dos preguntas que hasta hoy vivían mezcladas en
`pjm.md`: si el pedido exhibe una discrepancia entre una definición y un comportamiento
observado, y de qué producto se trata. La segunda decide antes que la primera —ADR-0017
fija que una tarea sobre el propio sistema entra **siempre** como platform enhancement, y
`docs/product/classification.md` lo escribe como comportamiento: "Un pedido sobre el
sistema nunca sale clasificado `bug`"— y sin embargo era la que el prompt dejaba en manos
del modelo, derivada del vocabulario del issue ("its issues talk about roles, runs,
prompts, spans, the graph").

Dos decisiones, y ninguna es de comportamiento: el comportamiento lo gobierna
`docs/product/classification.md`, que PM mantiene. Acá va cómo se sostiene.

**El nombre del producto llega al prompt como dato, no como inferencia.** El sistema lo
tiene declarado y obligatorio desde ADR-0016 (segunda adenda) y es label de todo span y
toda métrica desde ADR-0008: `ports.stack.product()`, ya en `Ports` y ya usado por
`preflight`. `classify.py` se lo pasa a `run_role` como un placeholder más, al lado de
`{{date}}`, `{{max_turns}}` y `{{flags}}`. Es la misma clase de cosa que esos tres: un
hecho que el nodo provee para que el modelo no lo invente. Esto **no** es la "regla propia"
que `classification.md` le prohíbe al caso de uso —"meter acá una regla de discrepancia
mudaría al código una decisión que es del rol"—: ninguna línea de `classify.py` lee el
texto del pedido ni decide un tipo.

Derivarlo del vocabulario era el modo de fallo: un issue de gym-saas que hable de métricas
y de corridas cae del lado del sistema, y un issue del sistema escrito en prosa de producto
cae del otro. Como la regla del producto es la que corre primero, un antecedente adivinado
se equivoca en el paso que decide el camino entero.

**El orden queda escrito en el texto del prompt.** Primero qué producto es; después, sólo
para un producto con definiciones escritas y PM que las mantenga, el test de discrepancia
documentada ⇒ `bug`. Sin ese orden, un pedido sobre el sistema que cite un ADR y muestre
una corrida que no lo cumple satisface los dos antecedentes a la vez, que es el conflicto
entre `<Task>` y `<Hard Limits>` que produjo gym-saas #20, reproducido en el otro sentido.

## Opciones descartadas

- **Dejar el antecedente en el vocabulario del issue**: gratis en código y equivocado en
  el paso que más cuesta desandar, porque decide el camino antes de que ningún rol corra.
- **Una regla de discrepancia en `classify.py`**: lo que `classification.md` prohíbe de
  frente. El caso de uso lee `type` y `confidence` y nada más.
- **Retirar `docs/product/classification.md` a un ADR del sistema**: descartado por PM. Un
  ADR es una decisión fechada que no se edita sino que se supersede (ADR-0010); una
  definición de capacidad es lo vigente, reescrito entero (ADR-0006). Son complementarios:
  la definición dice qué rige, este ADR dice con qué se sostiene.

## Consecuencias

- `preflight.PLACEHOLDERS` suma `product`. Esa puerta renderiza el prompt de **todos** los
  roles antes de gastar un peso, y un placeholder fuera de ese set la hace fallar para
  cualquier producto, no sólo para éste.
- `prompts.fingerprint` cambia. `pjm.md` es el prompt del primer rol que corre toda tarea,
  así que la comparabilidad se corta para **toda** serie segmentada por huella, no sólo
  para el camino de esta tarea. Queda dicho acá para no descubrirlo en un panel.
- La huella se calcula sobre el texto crudo, antes de rellenar (`prompt_hash`), así que dos
  productos distintos siguen compartiendo huella: el nombre no la parte.
- Las dos métricas de cabecera de `classification.md` se leen con los labels que ya
  existen (`product` y `task.type`, ADR-0008): ninguna serie nueva.
- Desde ADR-0017 el sistema no tiene PM en su camino, así que su `docs/product/` no tiene
  quién lo destile cuando la tarea corre por plataforma. PM lo decidió como mantenimiento:
  lo escribe PM cuando hay PM, y cuando no, la tarea lo deja dicho en el pull request para
  el humano. No es una edición al pasar de ningún Developer.
