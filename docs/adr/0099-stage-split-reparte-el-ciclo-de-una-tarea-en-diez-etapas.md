---
status: accepted
date: 2026-11-14
---

# stage_split reparte el ciclo de una tarea en diez etapas

GH-751, paso 2 de GH-750. El paso 1 (`agentic/core/measurement/domain/stages.py`) convierte el
documento de la tarea en Reclamos con rango, recortados a `[creación del issue, merge]`.
`stage_split` los barre y devuelve los segundos de cada Etapa del ciclo. Nadie lo llama todavía:
este documento fija las reglas antes de que el informe las use.

## Contexto

`cycle.py` tiene `time_split` y `human_wait_seconds`, cada uno con su propia partición y sus
propios supuestos. Un informe por etapa que mezcle varias particiones suma dos veces un mismo
segundo o lo pierde. Hace falta una sola partición, con cada segundo en una sola etapa.

## Decisión

`stage_split(document, *, issue_created_at, pr_created_at, pr_merged_at)` devuelve un
`StageSplit` (segundos por `CycleStage` y por `OtherCause`, con todas las claves siempre
presentes) o el `StageUnknown` que devuelve `claims()`, sin tocarlo. Nunca lanza.

- **Límites.** `[creación del issue, merge del pull request]`, no `closed_at`: lo que ocurre después
  del merge (la reflexión, el cierre) no es ciclo, y `closed_at` lo contaría. Un merge anterior a la creación, o un pull request creado después del merge, es
  `bounds_reversed`.
- **Barrido.** Cada Reclamo aporta dos bordes; se ordenan una vez y, en cada instante, se aplican
  todos los bordes antes de atribuir el tramo siguiente. Un heap de claves abiertas da el tramo al
  Reclamo de menor `key`. O(n log n) en tiempo y O(n) en memoria. Un tramo que ningún Reclamo
  cubre es `other`/`gap`. `seconds["other"]` es la suma de `other_by_cause`, sin resta final.
- **Orden de rango**, de más fuerte a más débil: pausa humana, pausa por límite de cuenta, otras
  pausas, pull request abierto, esperas de CI, corridas y fallos, cola. Difiere de la lista del
  pedido porque una pausa tapa lo que corre debajo (un segundo esperando a una persona no es
  desarrollo) y porque el pull request abierto pesa más que las corridas que contiene. Difiere de `time_split`, que
  resta clases en cadena y deja el orden en el código de cada resta; aquí el orden es un dato
  (`key`).
- **Empates.** Con el mismo rango gana la etapa más cercana al merge (`STAGE_PRECEDENCE`), luego
  `OTHER_ORDER`. Dos Reclamos con la misma clave, dos Developers en paralelo, dan el mismo
  resultado en cualquier orden.
- **Tabla de pausas.** Humana: `human_wait`. Externa con todos sus fallos terminales en
  `account_limit`: `account_limit`, aun si la escalada es mecánica de CI. Externa por una causa de
  CI (`ci_*`): `checks`. El resto, `other`/`outage_pause`: `upstream`, `network`, y por decisión
  `delivery_transient` y `disk_full`, que no son esperas de CI ni de una persona. `account_limit`
  seguido de `upstream` es `outage_pause`; seguido de una escalada humana, `human_wait`.
- **Aterrizaje.** `landing` es pull request abierto hasta el merge, no Aterrizaje (la etapa de la
  Tarjeta): se parte después por Resultado de entrega. Una espera de `merge_ci` dentro del pull
  request abierto cuenta como `landing` (rango 4 contra 5), mientras que una pausa mecánica de CI
  en el mismo tramo va a `checks` (rango 3).
- **Una sola cifra.** `stage_split` es la cifra de segundos por etapa de los informes.
  `cycle.human_wait_seconds` se queda para `eval compare` hasta que una tarea posterior concilie
  las dos; con solo pausas humanas dentro de los límites, coinciden.
- **`StageUnknown`.** `no_issue_created`, `no_pr_created`, `no_merge` (falta un instante),
  `bounds_reversed`, `pause_open` (pausa abierta o ilegible), `failure_unbounded`, `run_unbounded`
  (un fallo o una corrida sin sus dos instantes legibles o con el fin antes del inicio),
  `ci_wait_unknown`. Un segundo desconocido es desconocido, nunca cero.

## Sesgos conocidos

Cada uno con su signo; el informe debe nombrarlos.

1. La cola sube por los reinicios: el tiempo antes de la primera corrida incluye a quien
   reinició el sistema.
2. La cola sube por el tiempo bloqueado por `Blocked by:` o esperando una etiqueta `agentic`
   tardía: el reloj corre desde la creación del issue.
3. La cola baja donde las corridas en paralelo esconden esperas de cupo: otra corrida cubre ese
   segundo con un rango más fuerte.
4. `landing` sube por la revisión del dueño en los merges humanos.
5. `landing` sube, y desarrollo y revisión bajan, porque los pases de `fix`, `re_review` y
   `sync_master` con el pull request abierto cuentan como `landing` (rango 4 gana al 6).
6. Dentro del aterrizaje `checks` baja y `landing` sube: una espera de `merge_ci` (rango 5) va a
   `landing`, una pausa mecánica de CI (rango 3) va a `checks`.
7. Una etapa anterior pierde segundos frente a una posterior donde se solapan.

## Consecuencias

El informe siguiente mide qué parte de los documentos cerrados vuelve `StageUnknown`, por causa,
antes de mostrar horas. No hay señal nueva: la función es pura y nadie la llama. El glosario
extiende la entrada **Etapa del ciclo**.
