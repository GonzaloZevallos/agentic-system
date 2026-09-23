---
status: accepted
date: 2026-09-23
---

# Un fallo externo tiene su propio motivo: `account_limit` y `upstream` salen de `sdk`

En catorce días `agentic.role.failures` mostró 49 muestras con `reason=sdk` y cero con
`reason=limit`, mientras el programa de mejora acumulaba escalaciones por
`You've hit your session limit` y por `individual spend limit` (HTTP 429) y tres más por el
incidente de Anthropic del 2026-09-21 22:13-23:10 (HTTP 500/529). Todas cayeron en `sdk`.
La cuota de una cuenta ajena no es un fallo del sistema, y la segunda métrica del scorecard
(ADR-0028) —fallos propios por tarea— excluye lo externo por el motivo del fallo: hoy no
tiene nada que excluir y mide la cuota del proveedor.

Esta decisión obedece la regla de ADR-0008, del addendum del 2026-09-16: **las series y los
nombres persistidos se agregan, nunca se renombran**. Todo lo que sigue es aditivo: dos
valores nuevos en un conjunto cerrado, ni una serie renombrada, ni una clave de documento
movida, ni un label borrado.

## `limit` queda congelado, y por qué redefinirlo sería invisible

`limit` significa hoy el tope por intento (issue #66): `_reason_for_subtype` devolvía
`"limit" if "budget" in subtype else "sdk"`. Ese valor ya está escrito en
`failures[].reason` de documentos de tarea y ya es valor de label en Grafana Cloud. Cambiarle
el significado —de "nuestro tope" a "la cuota del proveedor"— es un renombre que ninguna
herramienta puede ver: las muestras viejas y las nuevas comparten la cadena y ningún panel
distingue qué quiso decir cada una. Así que `limit` se queda byte a byte y con su
significado intacto, y lo externo entra con nombre propio.

El glosario ya obligaba a que los dos hechos fueran separables. La entrada **Scorecard** de
Medición dice "fallos propios por tarea (sin los de límite de cuenta)", y la entrada **Clase
de escalado** de Proceso dice que el tope por intento "para la tarea es un fallo de rol, no
un escalado": es nuestro. Un solo valor no puede ser a la vez nuestro y ajeno y seguir
contestando lo único que el scorecard le pregunta.

La cuarta consecuencia de ADR-0028 se cita entera porque es lo que esta decisión descarga:

> La clasificación de fallos tiene un bug que el scorecard hace visible: 49 fallos `sdk` y
> cero `limit` en catorce días, cuando el vocabulario ya tiene `limit`. Hasta que se
> arregle, "fallos propios por tarea" mide la cuota de Anthropic.

"El vocabulario ya tiene `limit`" nombra el **bug**, no prescribe el balde. De ahí que lo
que se entrega no sea lo que el pedido pide literalmente (`reason=limit` en un 429) sino
`reason=account_limit`, y que la métrica de aceptación del pedido se lea contra esa serie.
Queda dicho acá y en el cuerpo del pull request para que el próximo lector no tome la letra
del issue como una contradicción que alguien pasó por alto.

**El conjunto de exclusión del scorecard tiene un solo enunciado normativo**: la entrada
**Scorecard** de `agentic/core/measurement/CONTEXT.md`, que este mismo cambio actualiza. Acá
va el razonamiento de por qué cada valor cae adentro o afuera, nunca una segunda lista: es
la disciplina de ADR-0021 con el registro de mypy —se nombra el lugar que lleva el alcance,
no se restatean sus contenidos—, y es lo que evita que dos documentos digan la misma regla y
deriven. ADR-0008 y ADR-0028 no se editan (ADR-0010: los ADRs aceptados se superseden, no se
editan); el camino de vuelta hasta acá lo llevan los dos archivos que el diff ya toca, el
comentario de `FailureReason` en `task.py` y la línea del enum en `observability/README.md`,
cada uno nombrando este ADR.

## El clasificador es una función pura del dominio, sobre el mismo objeto que el motivo

`_reason_for_subtype` vivía en el adaptador y veía un solo campo, `subtype`. Desaparece: su
cuerpo se muda a `rules.failure_reason(report: ReportedFailure) -> FailureReason`, pura, en
`core/process/domain/rules.py`, al lado de `failure_motive`, alimentada por el **mismo**
`ReportedFailure` con que el motivo se compone. Un objeto, una lectura, un lugar. Los dos
sitios de error llaman `failure_reason(_reported_error(exc))` y
`failure_reason(_reported_result(result))`.

Tres razones, y la tercera es la que decide. El motivo y el motivo-como-dato dejan de poder
discrepar, porque salen del mismo valor. La función queda en `core/process/domain`, que está
en el piso de mypy de ADR-0021, así que nace tipada y chequeada. Y es la única forma de que
exista el test que el pedido pide —de dominio, con el texto real de los errores— sin SDK y
sin CLI.

**La precedencia, en este orden, escrita en el docstring porque en principio dos cláusulas
pueden aplicar a la vez**: primero un subtype de presupuesto (`"budget" in subtype`), porque
nuestro propio tope es un hecho sobre nuestra configuración y es definitivo; después las
reglas por código de estado; después la cláusula de salida estructurada; y `sdk` al final. La
regla no lee `exit_code`: `_reported_result` estructuralmente no lleva ninguno.

## El predicado es el código de estado, nunca la prosa

`account_limit` es `api_error_status == 429`. `upstream` es `500 <= api_error_status < 600`.
Nada más.

No se parsea prosa, y no es una preferencia: el sexto addendum de ADR-0004 lo prohíbe —
`reason` es la llamada del sitio que la levanta y nunca se adivina parseando el error — y la
evidencia muestra el texto del proveedor variando entre corridas (`session limit · resets
7:30pm` contra `individual spend limit · run /usage-credits …`). La sub-causa (sesión, gasto
o tasa) **no se modela**: el humano la lee del motivo, que ya lleva la prosa verbatim en el
primer tramo de `failure_motive`.

**Lo que hace forzada la elección del campo, y no meramente preferible**: las ~20 fallas de
límite de cuenta registradas en `.agentic/tasks/` (GH-28 ×6, GH-31, GH-69, GH-70, GH-71,
GH-74, GH-85, GH-91) llevan todas `HTTP 429`, `terminal reason: api_error` y `exit code 1` en
su motivo. El `exit code` prueba que llegaron por el sitio de `ResultError`, el único que lo
aporta; y ninguna muestra un `subtype` en su metadata, que `_metadata_core` omite solamente
cuando vale `success`. O sea que esta población llega con **`subtype="success"`**, la misma
forma que el incidente del 2026-09-17 dejó canonizada en `docs/product/role-failures.md`:
una regla con llave en `subtype` no podía haberla visto nunca. La que sí la ve es
`api_error_status`, que estaba poblado en todas.

**`upstream` va por rango y no por enumeración** (`{500, 502, 503, 529}`): una enumeración es
una lista que reclasifica en silencio el primer estado que el proveedor devuelva y nadie
anotó, que es la misma clase de hueco que esta tarea existe para cerrar. El rango es igual de
estructural y no tiene ese modo de falla.

## La cláusula de salida estructurada, y el `sdk` que queda

`subtype == "error_max_structured_output_retries"` o
`terminal_reason == "structured_output_retry_exhausted"` ⇒ `schema`. La evidencia es
`.agentic/tasks/GH-32.md`, que registra `reason: sdk` para `Failed to provide valid
structured output after 5 attempts (subtype=error_max_structured_output_retries, terminal
reason: structured_output_retry_exhausted, exit code 1)`: el CLI agotando sus propios
reintentos de salida estructurada, que es el hecho que la entrada **Fallo de rol** del
glosario llama "la salida estructurada falta o es inválida" y que `schema` ya nombra en el
`Literal`. Es aplicar un término como está escrito, no abrir un valor. Van los **dos**
disyuntos, porque la población de 429 mostró que un `subtype` puede venir en `success` y no
se apuesta a que esta otra siempre lo pueble.

Sin esta cláusula, este ADR diría que `sdk` es lo nuestro con un contraejemplo registrado
debajo. Con ella, **`sdk` pasa a significar "el harness se rompió de una forma que ninguno de
los hechos nombrados cubre"**, y un payload con la forma de GH-32 lo deja.

Reclasificar hacia un valor que además dirigiera el control seguiría siendo comportamiento,
no etiquetado: por eso el diff muestra que nada en `application/` ramifica sobre
`reason == "schema"` —hoy la única rama sobre `reason` en toda la capa es la selección del
mensaje de reintento por `max_turns` (`run_role.py:438`)— y eso es verificable sobre el diff.

## Un `subtype` nulo pasaba a crash y ahora clasifica

El sitio de `ResultError` llamaba `_reason_for_subtype(exc.subtype)` con un cuerpo
`"budget" in subtype`, mientras el sitio del `ResultMessage` de error se protegía con
`result.subtype or ""`. Con `subtype=None`, el primero levantaba `TypeError` **adentro** del
`except ResultError`: ningún `AttemptFailed`, ninguna falla registrada, ningún costo
capturado, la excepción escapando de `run_once`. La regla nueva toma `report.subtype or ""`
—la protección del sitio del `ResultMessage`, verbatim, no una nueva— así que ese camino
clasifica en vez de romper.

Es una rotura latente que este diff saca como consecuencia de unificar los dos sitios, no un
arreglo que nadie pidió, y es el único camino donde este cambio es observablemente mejor y no
sólo distinto de etiqueta. Queda escrito para que un bisect no se lo cargue al cambio de
label.

## `other` es alcanzable sólo por omisión

`AttemptFailed.__init__` tiene `reason: FailureReason = "other"`. El default se queda —
sacarlo toca diez sitios y la firma del port, alcance que esta tarea no tiene por qué
comprar— y la disciplina se vuelve verificable sobre el diff: todo sitio que levanta
`AttemptFailed` acá pasa un `reason` explícito. De ahí la lectura que importa en un panel:
**`other` es alcanzable sólo por omisión, así que un `other` distinto de cero en una ventana
es un argumento que falta, nunca una categoría.**

## El chequeo de registro es el test de dominio

Ningún rol de esta tarea puede producir una muestra de `account_limit`: la serie se mueve con
el próximo 429 posterior al merge, nadie puede provocar uno a pedido, y el revisor no puede
reproducirlo. Es la misma situación estructural que ADR-0021 —una tarea cuyo tema es un
chequeo que nada en su propio loop puede ejercitar— y se resuelve igual, diciéndolo en vez de
dejar que un `Verdict: approved` insinúe una cobertura que el revisor no tuvo.

**El test de dominio es el chequeo de registro de esta tarea.** Pincha el **par**
`(failure_reason(report), failure_motive(report))` sobre un `ReportedFailure` por payload
real —el 429 de GH-31, el 529 de GH-73, la salida estructurada agotada de GH-32 y
`error_max_budget_usd`— leídos verbatim de los documentos de tarea, nunca retipeados desde
una transcripción. Y una entrada rechazada por cláusula: prosa de límite con
`api_error_status` ausente ⇒ `sdk`; `499` ⇒ `sdk`; `600` ⇒ `sdk`; `subtype=None` con 429 ⇒
`account_limit`; `subtype=None` sin nada ⇒ `sdk`; `529` con subtype de presupuesto ⇒ `limit`,
que fija la precedencia en vez de dejarla como afirmación del docstring. ADR-0023 es directo
al punto: un artefacto de enforcement cuyos tests nunca ejercitan una entrada rechazada es un
hallazgo bloqueante, y este clasificador es uno.

El Developer pega en su sección el comando y su salida verbatim; un conteo sin salida pegada
no es evidencia. Y el brief le dice al revisor que la ausencia de un 429 vivo no es motivo de
devolución (ADR-0016 ya decide esa forma: una objeción que no se reprodujo es una
observación), y qué sí puede bloquear leyendo dos archivos y un diff: una cláusula sin entrada
rechazada, cualquier lectura de prosa en el clasificador, un cambio en la aserción
`error_max_budget_usd → limit`, una rama nueva sobre `reason`, un valor nuevo en
`EscalationClass`, o uno de los tres archivos de vocabulario moviéndose sin los otros.

La métrica de aceptación del pedido se lee con `product="agentic-system"`: la consulta del
issue no lleva selector de producto y `agentic.role.failures` lleva `product` desde el
addendum del 2026-09-17 de ADR-0008, así que sus 49 mezclan los dos productos contra 34
`reason: sdk` registrados acá.

## Lo que esta decisión no cambia

- **Ningún prompt de `domain/prompts/` nombra el vocabulario de fallos**, verificado sobre el
  árbol: `prompts.fingerprint` no se mueve y las muestras de esta tarea siguen comparables con
  la línea de base vigente. Vale decirlo en el mismo commit que le cambia el significado a una
  serie de motivos.
- **El dashboard no se edita**: su único panel de fallos es
  `sum by (system_version, reason) (sum_over_time(agentic_role_failures{product=~"$product",
  system_version=~"$version"}[1w]))`, que recibe valores nuevos por construcción y ya filtra
  producto y versión.
- **`EscalationClass` no gana ningún valor y ningún texto de escalado cambia.**
  `escalation_class` lo escribe `rules.escalation` junto con `escalated` y dirige el
  comentario del issue y el camino de `resume`: una clase nueva es comportamiento, y un
  platform enhancement no cambia ninguno.
- **`ROLE_RETRIES`, el tope por intento, el tope de tarea y el límite del par** no se tocan.
  Un corte por límite de cuenta sigue siendo un fallo de rol y su segunda ocurrencia sigue
  escalando, byte a byte.
- **Las muestras ya emitidas no se reescriben ni se rellenan**, y ningún documento escrito se
  reclasifica.

## Riesgos conocidos

- **La serie nueva es un piso, no un censo.** El `except Exception` genérico y el sitio de
  `result is None` no tienen `ReportedFailure` y siguen en `sdk`; meter la mano en el texto de
  una excepción para sacarle un código de estado es la prohibición de arriba con otro
  sombrero. Un 429 que nunca produjo un result message se registra `sdk`, así que un primer
  429 posterior al merge que siga cayendo ahí es este residual y no una regresión. Una lectura
  estructurada del atributo de estado de la excepción del SDK es un spike declarado aparte.
- **La pausa sigue etiquetada como nuestra.** Una pausa por límite de cuenta o por caída del
  proveedor sigue siendo `escalation_class="failure"`; la serie de motivos es el único lugar
  donde la externalidad se ve, así que una pausa de ésas se diagnostica cruzando
  `agentic.task.paused{escalation_class="failure"}` con
  `agentic.role.failures{reason="account_limit"}` sobre la misma ventana y el mismo `product`.
  Seis de las escalaciones que el pedido cita como evidencia son exactamente esto. El
  vocabulario de escalado queda para su propio issue, nombrado en el cuerpo del pull request.
- **"Fallos propios por tarea" es incomparable de un lado al otro de este commit**, incluida
  la cifra de cohortes 3.6 → 2.5 de ADR-0028, que se computó bajo la definición que este
  commit cambia. Es el primer lugar donde un lector va a mirar.
- **Nadie reclasifica la historia.** Lo único que podría hacerlo es la prosa de
  `failures[].error`, que es justo lo que la regla de arriba prohíbe en tiempo de ejecución: de
  ahí que ningún código de evaluación, comparación o scoring derive un motivo del texto de un
  motivo registrado. Sin esa frase, el parser que `rules` no puede tener aparece en
  `eval compare`, donde ningún invariante estaba mirando.

## Opciones descartadas

- **`reason=limit` para el 429, como pide la letra del issue**: un solo valor para "nuestro
  tope" y "la cuota ajena" deja de contestar lo único que el scorecard le pregunta, y
  redefinirlo es un renombre que ninguna herramienta puede ver.
- **Un octavo valor de `EscalationClass`** para la pausa externa: es comportamiento
  (`rules.escalation`, el comentario del issue, el camino de `resume`) y esta tarea no cambia
  ninguno. Se entrega el residual escrito y un issue de seguimiento.
- **Enumerar los 5xx** en vez del rango: reclasifica en silencio el primer estado que nadie
  anotó.
- **Distinguir sesión, gasto y tasa dentro del 429**: exige leer prosa, que el sexto addendum
  de ADR-0004 prohíbe, contra un texto del proveedor que ya varió entre corridas.
- **Clasificar en el adaptador**, dejando `_reason_for_subtype` con un solo campo: el motivo y
  el motivo-como-dato quedarían leyendo la misma falla por dos caminos, y el test de dominio
  que el pedido pide no tendría dónde vivir.
- **Rellenar o reclasificar las muestras y los documentos anteriores**: son eventos; ADR-0008
  las agrega, no las corrige hacia atrás.
- **Editar ADR-0008 o ADR-0028**: los ADRs aceptados se superseden, no se editan (ADR-0010).
  Los back-links viajan en los dos archivos que el diff ya toca.
