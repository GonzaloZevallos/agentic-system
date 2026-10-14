---
status: accepted
date: 2026-10-09
---

# `agentic merge` renumera el ADR cuyo número el master ya tomó

El 2026-10-08 cuatro corridas de apostrophe crearon cada una el ADR-0037 en paralelo (#160,
#159, #167 y #170). Cada rama estaba verde sola; al mergear dos, el
`tests/test_adr_numbers.py` del producto puso el master en rojo y cada PR posterior falló
hasta que se renumeró a mano. El número se elige desde el base de la tarea y la puerta de
merge (ADR-0045) no se entera de que el master ya lo tomó. Esta decisión hace imposible que
dos ADR mergeados por el poller compartan número.

## La decisión

Antes de mergear, `agentic merge` mueve al siguiente número libre todo ADR que la rama
agregó y cuyo número el master upstream tomó después del merge-base. Es la **opción 1** del
issue, hecha por el sistema; el verbo no devuelve la tarea al Developer.

**Alternativas rechazadas**

- **Opción 2, asignar el número al mergear.** El ADR se escribe, se revisa y se cita (en
  otros ADR, en el glosario, en el código y en pruebas) con su número antes de que exista
  merge. Un número que se asigna al final obliga a escribir un marcador y a reescribirlo
  todo al mergear: la misma reescritura de referencias, sin saber dónde, y con la rama
  revisada con un nombre que no es el definitivo.
- **Detectar y rechazar.** Es lo que hoy hace la prueba del producto, después de que ambos
  aterrizaron. Rechazar antes de mergear devuelve a un Developer, con otra corrida y otra
  revisión, para hacer una edición mecánica y determinista que el sistema sabe hacer solo.

## Qué cuenta como "los ADR de la rama"

Con `mb = merge-base(M, cabeza del PR)` y `M` el sha del master upstream que el verbo ya
trajo (ADR-0045, paso 3):

- Un ADR es una ruta `docs/adr/NNNN-<slug>.md`, directamente bajo el directorio (convención
  del sistema, ADR-0006 y ADR-0014; no hay clave de stack).
- Los **ADR propios** son los que existen en la cabeza y no en el árbol de `mb`. Un ADR que
  el master mergeó en la rama ya está en `mb`: nunca se renombra.
- Hay **colisión** cuando el número de un ADR propio lo tiene el master (M) y no lo tenía
  `mb`.
- Dos archivos de la cabeza con el número de un ADR propio son un **duplicado** de la rama
  consigo misma: no se renumera, se rechaza.

## Qué se reescribe

Solo líneas que la rama agregó en `mb..cabeza`, y nunca una ruta con un segmento `tests/`.
Tres formas, siempre en límite de palabra (`ADR-00480` y `10048` no coinciden):

1. dentro del archivo renombrado: su propio número en el encabezado y en una clave de
   número del frontmatter;
2. `NNNN-<slug exacto>.md` en cualquier lugar de la línea;
3. el token `ADR-NNNN` solo si la línea nombra también el slug de ese ADR, o si el archivo
   lo agregó entero la rama.

Todo otro lugar de una línea agregada que siga nombrando el número viejo, incluido lo que
esté bajo `tests/`, queda **sin reescribir** y se lista como `ruta:línea` en el comentario
`Auto-merged:` o en el de rechazo. No hay lista cerrada de artefactos derivados: un golden o
un pin que quede viejo fuera de `tests/` aparece como `checks_failed` en la cabeza nueva, con
el chequeo nombrado (GH-212). El commit de renumeración no cambia ningún archivo salvo el
rename del ADR y las líneas que reescribe la regla, y no regenera nada.

El número nuevo es `max(números en M ∪ números de los otros ADR propios) + 1`, asignado hacia
arriba en el orden de números de la rama; el slug se conserva y el número sigue de cuatro
dígitos. Las reglas puras viven en `agentic/core/process/domain/adr_numbers.py`
(`own_adrs`, `collision`, `renumber_plan`, `rewrite_line`, `unrewritten`); ahí y en
`rules.auto_merge_decision` se comparan los números y se elige el siguiente, y en ningún otro
módulo. El verbo aplica el plan en un worktree descartable, comete como el sistema, empuja a
`agentic/<tarea>` con *lease* sobre la cabeza que observó, agrega
`{from_head, to_head, map, unrewritten}` a `adr_renumber` en el documento de tarea del
checkout del producto y termina. La ronda siguiente corre la cabeza nueva por la puerta de
siempre.

## Lugar en `auto_merge_decision`

| # | Condición | Resultado |
|---|-----------|-----------|
| 1-4 | `not_ready`, `pr_not_open`, `not_approved`, `head_not_reviewed` | refuse |
| 5 | `open_findings` | refuse |
| 6-8 | `eval_set_unreadable`, `eval_set`, `guarded_path` | refuse |
| 9 | duplicado en la rama, o `ADR_RENUMBERS_MAX` renumeraciones ya hechas | refuse `adr_number_taken` |
| 10 | colisión con renumeraciones por gastar | `renumber` |
| 11 | `merge_failed` | refuse |
| 12 | la puerta del CI (`ci_*`), `checks_failed`, `attempts_exhausted` | wait / refuse |
| 13 | los chequeos locales cubren la cabeza | `run_checks` / `merge` |

`renumber` va después de `guarded_path`: una tarea que se rechazaría por el set de
evaluación o por una ruta protegida nunca recibe un push. Las rutas protegidas se juzgan
sobre el diff anterior a la renumeración, que solo escribe en archivos que la rama ya
cambió más su propio ADR bajo `docs/adr/`, así que no puede alcanzar una.

## Topes, *lease* y un solo sha M

- `ADR_RENUMBERS_MAX = 2` cuenta renumeraciones completadas (push aceptado) por tarea, la
  longitud de `adr_renumber`. Una tercera colisión es `adr_number_taken` y la decide el
  humano. Los topes por cabeza (`MERGE_ATTEMPTS_MAX`, `MERGE_CALLS_MAX`) y la espera del CI
  se reinician con la cabeza nueva, como con cualquier push.
- Un *lease* perdido o un push fallido no escribe nada ni gasta ningún tope: registra una
  línea y la ronda siguiente reintenta desde hechos frescos.
- Los hechos de la colisión, la renumeración y el merge final usan **un solo sha M**.
  `Tracker.merge` se llama solo después de un refetch que vuelve a leer M, en el camino de
  los chequeos locales y en el del CI fresco (GH-216); si el master se movió, `master_moved`,
  y la ronda siguiente decide de nuevo (y renumera otra vez si otro PR tomó el mismo número).

## La cadena de aprobación

La cabeza renumerada no es la que Tech Lead revisó. `head_not_reviewed` la acepta cuando es
el mismo commit (`same_commit`) que `reviewed_head`, o cuando el registro `adr_renumber`
**del checkout del producto** encadena `reviewed_head` con el primer `from_head`, cada
`to_head` con el siguiente `from_head` y el último `to_head` con la cabeza del PR. La cadena
parte del último registro cuyo `from_head` es `reviewed_head`: un eslabón anterior a una
revisión nueva es historia. Una copia del documento tomada de la rama del PR no la satisface
nunca, y la cadena no reemplaza a `not_approved`: la aprobación se sigue exigiendo antes.

## Credenciales

El verbo empuja con las credenciales git del operador y llama a `gh` con el usuario, como
`deliver`. No hay `GITHUB_TOKEN`, de modo que el CI arranca sobre la cabeza nueva como con
cualquier push de un usuario. El estado de revisión de GitHub no es una entrada de la
decisión.

## Medición y seguridad (§7 y §8 de los estándares)

- No se agrega ni se mueve ninguna métrica, etiqueta, atributo de span ni prompt, así que
  `prompts.fingerprint` no cambia. La única clave nueva del documento de tarea es
  `adr_renumber`, que se escribe solo si no está vacía y se lee con `.get`; un documento sin
  ella se ve byte por byte como antes.
- `agentic merge` gana un push: solo a `agentic/<tarea>`, solo con *lease* sobre la cabeza
  observada, solo ante una decisión `renumber` y solo con credenciales que `deliver` ya usa.
  El costo por colisión es un worktree descartable, un `merge-base`, tres listados de
  directorio y un push, más un ciclo de chequeos o de CI en la cabeza nueva. Una rama que no
  agrega ADR no paga nada: la detección reutiliza la lectura de `changed_paths` del verbo y no
  llama a ningún verbo git extra.

## Riesgos conocidos

- Un PR que un humano mergea desde la UI de GitHub, y uno que el poller rechaza antes de
  lanzar el verbo (set de evaluación, ruta protegida), siguen aterrizando sin renumeración.
  La prueba `tests/test_adr_numbers.py` del producto los detecta después; exigir una rama al
  día en la protección de ramas es la palanca del producto.
- Los segundos entre el refetch final y `gh pr merge` quedan abiertos: `gh` no tiene guarda
  sobre el sha base (ADR-0045).
- Un `ADR-NNNN` desnudo fuera de las tres formas, o cualquier cosa bajo `tests/`, queda
  viejo: se lista como sin reescribir para el humano y nunca se reescribe en silencio.
- Este mismo ADR toma su número del base de su tarea y el mecanismo que introduce no lo
  protege.

## Adenda (GH-243): la lectura arranca solo con un ADR añadido

La lectura de ADR (`merge_base` más tres `paths_at`) ya no arranca con cualquier ruta
`docs/adr/NNNN-*.md` cambiada, sino solo si el diff `task_base...head` *añade* una (estado
`A`, `adr_numbers.adds_adr`). Una rama que solo edita un ADR existente no paga la lectura ni
puede terminar en `adr_unreadable`. El estado lo trae `Workspace.changed_entries`
(`ChangedPath` vive en `domain/changes.py`, la letra sin el puntaje de `R100`); `merge.py` lo
pide únicamente cuando `changed_paths` ya nombró una ruta de ADR, y un fallo ahí es
`diff_unreadable`. `changed_paths` y `changed_entries` pasan `-M` explícito, así que no
dependen de `diff.renames` y nombran las mismas rutas.

Riesgo conocido: renombrar un ADR a otro número (`0012-a.md` → `0050-a.md`) sale como `R` y
no dispara la lectura. Con `-M`, un archivo que sale de una ruta protegida ya no lista su ruta
vieja (como pasaba con la configuración por defecto).
