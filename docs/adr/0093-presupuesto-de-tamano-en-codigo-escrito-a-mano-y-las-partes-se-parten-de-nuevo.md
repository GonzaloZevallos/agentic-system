---
status: accepted
date: 2026-11-11
---

# Presupuesto de tamaño en código escrito a mano y las partes se parten de nuevo

GH-592. La tarea GH-407 de apostrophe ("medios de pago", un CRUD) fue la parte 24 de 37 y aun así
declaró 175 archivos. Su pull request llegó a 263 archivos y 8.400 líneas, construirla llevó unas
14 h y aterrizarla 8 días más: cada movimiento de master obligaba a reintegrar, revisar de nuevo y
volver a correr los chequeos sobre un diff enorme. Tres reglas de ADR-0054 lo permitieron: una
parte nunca se parte de nuevo, una parte de hasta `PART_WHOLE_FACTOR` × 150 USD se construye
entera sin preguntar, y los archivos generados cuentan para el límite de 40 archivos.

La evidencia que cita el pedido: en Google la mediana de un cambio es de 24 líneas, cerca del 90 %
toca menos de 10 archivos y la mediana de revisión es de menos de 4 h (Sadowski et al., ICSE-SEIP
2018); su propia guía llama "usualmente demasiado grande" a 200 líneas en 50 archivos. En un
estudio de pull requests de agentes, cada línea o archivo extra baja alrededor de un 1 % la
probabilidad de que se mergee (arXiv 2601.15195). La guía de Devin es partir lo que a una persona
le llevaría más de unas 3 h, y el arnés de larga duración de Anthropic da una sola función a cada
sesión.

Decisión del dueño (issue #592, `Owner's rule:`): una tarea se dimensiona para integrarse en
horas, el presupuesto cuenta archivos y líneas escritos a mano y los generados nunca cuentan; y una
parte que sigue por encima del presupuesto se parte de nuevo, construir entera una parte demasiado
grande ya no es una opción. La línea de 150 USD queda como chequeo secundario.

## Decisión

- **El presupuesto.** Un plan declara como máximo 15 archivos escritos a mano que no sean tests y
  400 líneas agregadas a mano que no sean tests; los tests se cuentan aparte, con su propio tope
  de 600 líneas agregadas. Los números viven solo en `domain/size_budget.py` (`HAND_FILES`,
  `HAND_LINES`, `TEST_LINES`); ningún otro módulo, prompt ni mensaje los repite: el texto que lee
  el planificador lo renderiza `size_budget.budget_text`. `rules.estimate_fits` sigue siendo la
  única puerta de entrada y compara contra `size_budget`; en `rules` quedan solo la línea de costo
  (`SPLIT_COST_USD`, secundaria, sobre el mayor entre el costo declarado y la referencia del
  historial como hasta ahora) y el factor del tope (`ESTIMATE_CAP_FACTOR`).
- **Qué es escrito a mano.** `size_budget.classify` es una regla cerrada. Un camino es *generado* si
  su nombre es un lockfile (`LOCKFILES`) o encaja con un glob generado del producto; es *test* si
  un directorio suyo es uno de `tests`, `test`, `__tests__`, `e2e` o su nombre encaja con
  `test_*`, `*_test.*`, `*.test.*`, `*.spec.*`; todo lo demás es escrito a mano. Documentación,
  ADRs, README, glosarios y los `.md` de los prompts son archivos escritos a mano y gastan el
  presupuesto.
- **El conjunto generado.** Es la unión de tres fuentes, leídas del commit base de la tarea: los
  lockfiles, que decide el sistema porque son salida de un gestor de paquetes en cualquier
  producto; `docs.paths` del stack; y la clave opcional nueva `generated:`. La diferencia de la
  propia tarea nunca agrega al conjunto contra el que se la juzga (una tarea no se declara
  generada a sí misma). `generated:` pasa por la misma negativa de rutas que `docs.paths` y un
  glob que encaje con todo se rechaza. No hay chequeo de frescura: un glob de más sube el
  presupuesto de la tarea que lo escribe solo si llega al commit base, y eso pasa por revisión.
- **Medición.** `Workspace.numstat(upstream, head)` corre `git diff --numstat -M -z
  <upstream>...<head>` y devuelve filas por camino. La detección de renombres y la base de tres
  puntos viven solo ahí: lo que master ganó no entra en la cuenta y un renombre puro cuenta 0/0.
  `Workspace.changed_lines` no cambia. Solo un archivo movido entero es gratis; extraer una
  función a un archivo nuevo cuenta como líneas agregadas.
- **Planeado contra real.** Al entregar, el sistema escribe `planned_size` y `actual_size` en el
  frontmatter del documento de tarea. La forma del registro y las claves las define solo
  `size_budget` (`PlanSize`, `ActualSize`). El historial y `eval compare` imprimen cuánto divergen
  (real / planeado por campo; un campo planeado en 0 no tiene cociente). Un pull request que
  mergea un humano con `agentic close` no deja `actual_size`: solo lo mide la entrega del sistema.
- **Piso contra el recorte.** Mientras menos de 10 tareas cerradas (`PROVEN_PAIRS`) tengan los dos
  registros, los archivos y las líneas a mano declarados se juzgan por 1,5 (`UNPROVEN_RATIO`),
  redondeado hacia arriba; los tests no llevan factor. Desde 10 tareas, el p90 del cociente real
  por campo reemplaza al 1,5, con 1,0 de mínimo. Como el piso del costo, sube la cifra juzgada y
  nunca rechaza por sí mismo. El 1,5 sale de cocientes de archivos totales de cinco tareas
  (GH-589, GH-544, GH-521, GH-576 y GH-535); aplicarlo a archivos y líneas escritos a mano no está
  probado, y mientras no haya historial el tope efectivo es de unos 10 archivos y 266 líneas.
- **Compatibilidad.** Un plan o un documento escrito antes de este cambio no tiene campos de
  tamaño: se juzga exactamente como antes, con la línea de costo y 40 archivos sobre
  `estimated_files` (`LEGACY_FILES`, que solo ese camino lee). Un `fit_waived: true` ya registrado
  conserva su efecto.
- **Las partes se parten de nuevo.** Se borra el camino de construcción entera
  (`PART_WHOLE_FACTOR`, `part_builds_whole`, `part_whole_line`). Una parte que no cabe escribe
  `parts` como cualquier tarea, hasta profundidad 2 (`rules.MAX_SPLIT_DEPTH`). El marcador del
  issue gana `depth D`; uno anterior, sin profundidad, se lee como profundidad 1, y todo
  comentario que el sistema publicó antes sigue registrado en `resume.SYSTEM_COMMENTS`
  (`PART_BUILT_WHOLE` queda como entrada heredada). Pasada la profundidad 2 la tarea escala con la
  clase `estimate` que ya existe, conservando su plan, y la pregunta ya no ofrece "construirla
  entera".
- **La parte terminal cierra a su padre.** Una parte que se parte debe tener exactamente una parte
  terminal, la que ninguna otra declara en `depends_on`. El pull request de esa hija cierra
  también el issue de la parte padre (marcador `agentic-closes`), así que los issues bloqueados
  por el padre se liberan cuando la cadena aterriza. Una tarea de profundidad 0 que se parte
  conserva su lista de comprobación para el humano.
- **Rebanadas verticales.** Cada parte declara su tamaño, un `kind` (`change` o `migration`) y un
  `guard`: cómo queda inalcanzable lo que está sin terminar (fuera de la navegación o detrás de
  una bandera) hasta la última rebanada. Una migración que convierte o mueve datos existentes es
  siempre su propia parte, y una parte `migration` tiene otra parte antes o después en
  `depends_on`: una partición con una migración aislada se rechaza (`schemas.parts_check`). El
  orden de una migración es escribir en ambas formas, convertir, cambiar los lectores y quitar la
  forma vieja.
- **El fixture de GH-407.** `tests/fixtures/split/gh407_plan.yaml` pasa el plan real de GH-407
  por la regla nueva: toda rebanada que produce cabe en el presupuesto y la conversión de los
  pagos existentes es su propia rebanada.
- **Por qué no hace falta un hold para aterrizarlo.** `schemas` se importa de forma ansiosa, así
  que el código nuevo entra completo con la rama y ninguna tarea en vuelo lee a medias el
  vocabulario nuevo.
- **Observabilidad y seguridad.** Se agregan dos campos al documento (`planned_size`,
  `actual_size`); no se agrega atributo de span, métrica ni etiqueta. `generated:` no ensancha
  nada: se lee del commit base y pasa por la misma negativa que `docs.paths`. El costo es un
  `git diff --numstat` por entrega, lineal en el diff.

## Riesgos conocidos

1. El 1,5 viene de cocientes de archivos totales de cinco tareas; aplicarlo a archivos y líneas
   escritos a mano no está probado.
2. Cuatro controles son juicio, no mecánica, y los carga el revisor: que master siga verde tras
   cada rebanada, que un `guard` de verdad deje inalcanzable lo inconcluso, que una parte `change`
   no esconda una conversión de datos, y que se respete el orden de la migración.
3. Extraer una función a un archivo nuevo cuenta como líneas agregadas; solo los archivos movidos
   enteros son gratis.
4. El sistema ahora cierra el issue de una parte que se partió; una tarea de profundidad 0 que se
   parte conserva su lista para el humano.
5. Documentación, ADRs, README, glosarios y los `.md` de los prompts son archivos escritos a mano y
   gastan el presupuesto.
6. Esta misma tarea la habría rechazado el presupuesto que agrega: es la última construida bajo la
   regla vieja.
7. El fixture de GH-407 prueba la clasificación y el chequeo sobre rutas reales; no prueba que el
   modelo sepa rebanar bien.

## Qué reemplaza

ADR-0054 queda enmendado (ver su adenda del 2026-11-11): su adenda de "una parte se construye
entera" y la línea de 40 archivos para planes nuevos quedan reemplazadas. El tope de dos veces la
estimación, la clase `estimate` y la línea de 150 USD siguen.

## Addendum (GH-719, 2026-11-13): las reglas del carve-out (D-b111a2ac)

Una parte de profundidad 2 cuyo plan sigue por encima del presupuesto mueve subtareas enteras a
un issue de seguimiento en lugar de escalar. Esta primera parte de GH-719 deja solo las reglas
puras (`domain/carve_out.py`), `open_issue` con varias etiquetas y los seguimientos como destino
del barrido de partidas. Nada en `plan` lo llama todavía.

- **Marcador:** `<!-- agentic-carve-out: <tarea> of #<n> -->`, donde `<n>` es el número de issue
  de la parte que extrae, nunca su índice. `rules.split_part_of` no lo lee: el seguimiento no es
  una partida y no tiene profundidad.
- **Línea de bloqueo:** exactamente `Blocked by: #<parte>`, compuesta por el sistema.
- **Sección:** `## Carved-out steps` copia el brief y los invariantes de cada subtarea extraída,
  textuales.
- **Etiquetas:** el seguimiento lleva `agentic` y copia las de la parte salvo `needs-human`,
  `agentic-hold`, la de parada, la de partida y las que empiezan con `path:`.
- **Rechazo en dos niveles, sin limpiar nunca:** el texto del modelo (`text_refused`) rechaza
  `<!--`, `-->`, `Blocked by:`, `Closes`/`Fixes`/`Resolves` y un salto de línea en el título; los
  briefs e invariantes copiados (`validate`) rechazan `<!--`, `-->`, `Blocked by:` y una palabra
  de cierre de GitHub seguida de una referencia a un issue.
- **Re-apunte:** `append_blocker` recibe `targets=[L, *seguimientos]` y agrega los que faltan
  justo después de `#L` en la línea `Blocked by:`.
- **Tamaño:** lo decide solo la función `fits` inyectada (`rules.estimate_fits` o
  `schemas.plan_fits`); el módulo no tiene ningún número.

La parte 3 debe: persistir la clave `carve_out` y su serialización, escrita antes de cada
escritura al tracker; escribir `outcome` solo al cerrar; parametrizar el comentario de `_repoint`
(hoy dice que la tarea se partió en partes); y no escribir en el tracker en corridas de
evaluación.
