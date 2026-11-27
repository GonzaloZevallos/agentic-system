---
status: accepted
date: 2026-11-21
---

# Un rechazo de formato es un fallo propio del sistema, nunca una pregunta para el dueño

GH-1011, GH-1016, GH-1017 y GH-1018. Cuando la salida estructurada de un rol no pasa el
esquema o el chequeo, quien falló es el sistema que la pidió, no el dueño. Este ADR enmienda la
adenda de reintentos del ADR-0004 y registra las decisiones que ya están construidas y las
que todavía no.

## Decisión

**1. Es un fallo del sistema.** Un rechazo de formato es un fallo de la salida estructurada
(`Failure.reason` `schema` o `check`, con una `rule`). Nunca se convierte en una pregunta para
el dueño: el sistema lo reintenta, lo repara cuando puede y, si no puede, abre su propio issue.
La cota de intentos la decide solo `rules.attempts_for`: un rechazo de `schema` o `check` nunca
tiene menos de `FORMAT_ATTEMPTS` (tres) intentos, y cualquier otro motivo conserva
`reintentos + 1`.

**2. Reparación y reintento.** Antes de gastar un intento, una regla con reparación registrada
en `REPAIRS` se repara sobre la salida cruda y la cadena entera se juzga de nuevo desde el
esquema. Hoy son dos: `reason_truncated` (`depends_on_reason_too_long`) y
`parts_dropped_when_fits` (`parts_when_fits`). Cada regla se repara una sola vez por intento; un
segundo rechazo de la misma regla falla el intento. El reintento que sigue a un rechazo sin
reparación lleva la regla, el campo, el valor y la causa, y el valor sale siempre por
`refusal_retry.clean_value`: una línea, cortada a `VALUE_MAX_CHARS`. El tamaño del valor entero
se mide antes de ese corte (`format_issue.value_len`, que llama `run_role._refused`) y queda en
`Failure.value_len`, solo bajo la misma condición que escribe `rule`.

**3. Reglas de apertura.** El sistema abre un issue por regla de rechazo, no por rechazo.
`format_issue.due` dice si el último fallo es uno que se archiva (`schema` o `check` con
`rule`). `format_issue.marker` fija la última línea del cuerpo y `format_issue.matches` la lee,
comparando la regla exacta; `format_issue.find` elige, entre los issues abiertos que coinciden,
el de menor número. Si hay uno, el sistema comenta ahí; si no, abre uno nuevo con la etiqueta
que fija el propio sistema (`needs-triage`). Una escritura que no llega se registra en
`tracker_failures` con `target` `issue` (la apertura) o `comment` (el comentario sobre uno que
ya existe), y la escalada que sigue nunca cambia.

**4. Evidencia en un repositorio público.** El issue vive en un repositorio público bajo el
login del dueño. Su texto sale solo de `format_issue.evidence`, que lee campos cerrados: la
regla, el campo, el rol, el producto (reducido a un slug o `unknown`), el número del issue de
la tarea, `value_len`, los intentos y las reglas anteriores. Nunca lee `value`, `error` ni
`detail`: son texto del propio rol. El contrato de lectura y escritura es `add_format_issues`
(una entrada por regla, gana la primera escritura) y `format_issue_for`, el único lector.

**5. Retoma y retención.** Decidido, y hoy el sistema no lo hace: cuando una regla ya tiene su
issue abierto, las tareas que chocan con ella no se escalan al dueño sino que esperan retenidas,
y se retoman cuando el issue se cierra y el arreglo corrió (se construye en: parte 6 (GH-1018)).
Hasta entonces, un rechazo que agotó sus intentos escala como siempre, y el issue que abre el
sistema no retiene nada.

## Consecuencias

- El dueño recibe un issue por regla, con evidencia cerrada, y no una pregunta por tarea.
- Lo que el sistema escribe en el repositorio público no puede filtrar lo que el rol escribió.
- La retoma y la retención quedan registradas como decididas pero no construidas, para que
  nadie las cite como comportamiento actual.

## Adenda GH-1020: la liberación de una retención

El sondeo libera una retención ya anotada (`noted_at`, con `issue_url`) en `release_fixed_holds`.
La regla es `rules.format_hold_released`: el issue de rechazo está cerrado y su commit de cierre
corre en el HEAD del stage0 que ejecuta el sistema, o está cerrado sin commit (una persona lo cerró
a mano, `NOT_PLANNED` incluido), y ese par `(issue_url, commit)` no se liberó antes. Una lectura que
falló mantiene la retención. Las lecturas son solo locales: el issue por `gh`, y la pertenencia
del commit con `git` sobre el checkout de stage0, sin fetch y sin escribir en él. El paso nunca
reemplaza un lock que no sea su propio `format_release` muerto, y no hace ninguna llamada a un
puerto con el lock tomado.

Riesgos conocidos:

- Una tarea retenida con un lock muerto de otro comando espera a una persona.
- Si stage0 vuelve atrás después de una liberación, el mismo par no vuelve a liberar la retención.
- Entre una rotación y el siguiente re-exec, el sondeo puede decir «not in stage0 yet» una ronda
  de más.
- Un cierre `NOT_PLANNED` libera una vez y puede gastar una retoma.
- Un stage0 superficial (shallow) no contiene el commit: se lee como `stage0 unreadable`.
