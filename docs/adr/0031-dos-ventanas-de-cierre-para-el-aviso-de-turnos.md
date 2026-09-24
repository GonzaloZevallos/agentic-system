# ADR-0031: dos ventanas de cierre para el aviso de turnos, y el conteo que las calibra

Revisa la adenda de ADR-0004 (2026-09-22, issue #102) sobre su propia cláusula de
calibración hacia adelante: esa adenda pidió leer `turn_notice_firings` para decidir si
el margen de 15 "abre demasiado tarde" o "demasiado temprano". La primera corrida en
producción con ese código (stage0 `8030c7f`, tarea GH-112, issue #117) contestó: 109
disparos en un intento de 80 turnos que agotó el tope igual, y 46 en el intento
reanudado que sí entregó. Un aviso que aparece en casi cada mensaje del hilo deja de
leerse como aviso -- el propio modelo tiene, en la corrida que no entregó, la mejor
evidencia de que la frase no significa *ahora*: la vio 109 veces y siguió puliendo.

## Lo que se decide

**Dos posiciones absolutas, no una.** `TURN_NOTICE_AT` (`domain/turn_notice.py`) pasa
de `dict[str, int]` a `dict[str, tuple[int, ...]]`: `{developer: (45, 65), developer_
integrator: (45, 65)}`, en la misma unidad de siempre, `AssistantMessage` de hilo
principal. `role.max_turns` sigue sin aparecer en el módulo, y ninguna resta contra él:
la adenda de 2026-09-22 ya dejó registrado que una corrida **entregó** con `turns: 98`
bajo `max_turns: 80` -- `num_turns`, este conteo y el tope que el CLI hace cumplir son
tres cantidades que no comparten unidad, y una resta las mezclaría. El pedido original
de la interview ("75% y 90% del tope") queda descartado tal cual: tomarlo literal
reintroduce exactamente esa mezcla de unidades tres días después de que la adenda
anterior la sacó, sobre evidencia que sigue de pie.

**Tres constantes con nombre, no un número redondo.** `WORST_CLOSE_OUT_TURNS = 27` es
una *cita*, no una derivación: el rango de 6-27 turnos de un cierre ordinario (commit,
después entregar) sobre un corte de `max_turns` reanudado, la evidencia propia del
issue #102. `PRESUBMIT_ALLOWANCE_TURNS = 8` es *declarada, no medida*: dos ciclos de
entrega rechazados por el presubmit, cuatro turnos cada uno (ADR-0011, segunda adenda)
-- la lectura que la revisaría es `main_thread_turns` registrado al momento de entregar,
en corridas con `presubmit_denials > 0`, una vez que esa cifra exista para leerla.
`GUARANTEED_HEADROOM_TURNS = WORST_CLOSE_OUT_TURNS + PRESUBMIT_ALLOWANCE_TURNS = 35` es
por qué la primera posición queda en 45 y no, por ejemplo, en 60: `80 - 60 = 20` queda
por debajo del peor caso de 27 turnos que la constante existe para contener, así que 60
no podría garantizar que un cierre ordinario entra en la ventana que abre; 45 deja
`80 - 45 = 35 >= 35`.

**El predicado que colapsa dos ventanas casi idénticas en una.** `windows_crossed(role,
count) -> int` reemplaza `should_notice`: cuenta cuántas posiciones de `role` están en
o por debajo de `count`, `0` para un rol que `TURN_NOTICE_AT` no cubre. El hook
(`hooks._turn_notice`) pregunta `crossed = windows_crossed(role, count)`; si `crossed >
fired`, emite **un** mensaje -- el texto de la ventana `crossed` -- y fija `fired =
crossed`, nunca `fired + 1`. Sin este colapso, un turno de solo texto o la cola de un
sub-agente (ninguno de los dos produce un evento `PostToolUse` de hilo principal) puede
dejar que el conteo salte de menos de 45 a más de 65 entre dos eventos de hilo
principal consecutivos, y un esquema que suma de a uno gastaría las dos ventanas, una
atrás de la otra, sobre el mismo par de eventos -- exactamente el modo de falla que esta
ADR corrige, ahora a dos mensajes en vez de a 109. `should_notice` desaparece: una
cuenta y un rol deciden un solo hecho, cuántas ventanas hay detrás, no dos funciones que
podrían no coincidir.

**Dos textos genuinamente distintos, nunca una frase con un número cambiado.**
`notice_context(window)` (1 o 2) no nombra ninguna cifra de turnos, ni un conteo
restante, ni `role.max_turns`: ninguna lectura del contador convierte a esas unidades
sin arriesgar la mezcla que el módulo ya evita en otro lado. La ventana 1 pide dejar de
abrir trabajo nuevo, terminar la unidad en curso y commitear lo que existe -- **no** dice
"entregá ahora", que a la posición 45 (bastante antes de la mitad del presupuesto de un
rol cubierto) sería falso, y una directiva falsa una vez se descuenta la próxima. La
ventana 2 pide entregar en este turno, en el mismo orden cerrar-entonces-entregar que
`rules.turn_cut_retry_message` ya usa para el reintento después de un corte -- acá, antes
de que el corte pase, así que la corrida nunca necesita ese reintento.

**La cantidad que el aviso mide se registra, por primera vez.** `main_thread_turns:
int`, el valor final de `TurnCount.value`, viaja por `RawResult` -> `AttemptFailed` ->
`Failure` -> `RoleRun` -> el frontmatter del documento de tarea, al lado de `turns` y
`max_turns`, sin desplazar a ninguno de los dos. Se escribe en **todo** intento, cortado
o entregado, en cada uno de los seis sitios de `raise` de `ClaudeSdkRunner.run_once` y
en el `return` que entrega -- el intento cortado es la población que importa más, porque
es la única que puede refutar la suposición de abajo. Default `.get(..., 0)`
documentado para todo documento escrito antes de este commit, el mismo tratamiento que
`denials`, `deliveries`, `turn_notice_firings` y `presubmit_denials` ya tienen.

**Lo que se escribe como suposición, no como certeza silenciosa.** Cada turno que el
CLI cuenta contra `max_turns` produce al menos un `AssistantMessage` de hilo principal,
así que `count >= consumed turns`. Nada en este repositorio verifica esa afirmación
sobre el SDK; el piso de holgura (`GUARANTEED_HEADROOM_TURNS`) solo vale bajo ella. Su
falsificador, nombrado en el docstring del módulo y acá: un intento cortado por
`max_turns` cuyo `main_thread_turns` registrado quede por debajo del `max_turns` de ese
rol la refuta, e invalida las dos posiciones declaradas arriba.

**La garantía, dicha literalmente.** "A más tardar en el primer resultado de
herramienta de hilo principal después de que el conteo llegue a 45" -- nunca "a más
tardar en el turno 45". La cola sin cobertura (un tramo final de solo texto, o
enteramente dentro de un sub-agente, después de la última ventana) no recibe nada, y
eso se acepta y se nombra igual que la adenda anterior ya lo concedía:
`additionalContext` en un `PostToolUse` es el único asiento que existe, y comprar un
segundo mecanismo para esa cola está fuera del primer invariante de este plan (issue
#117). Lo que revertiría esta ADR es un patrón de cortes que se agrupe muy por encima
de 65 en `main_thread_turns` -- ahí el remedio honesto es una tercera ventana, nunca un
reavivar sin límite del disparo, que es exactamente el mecanismo que esta ADR retira.

**Lo que se mueve junto, en un solo commit, y por qué no se atribuye después.**
Posición, cantidad de disparos y texto del mensaje cambian los tres en este mismo
commit. Ninguna lectura posterior le atribuye la caída de 109 a 2 a uno solo de los
tres: sería una lectura post-hoc sin cómo separarlos, porque los tres se movieron
juntos. En particular, `turn_notice_firings <= 2` bajo una tupla de dos posiciones vale
**por construcción** -- el propio colapso lo garantiza, `fired` nunca supera el número
de ventanas declaradas -- y por lo tanto **no es evidencia** de que el mecanismo
funciona; la única evidencia falsable que queda es
`agentic_role_failures{reason="max_turns", role="Developer"}` a cero en las próximas
cinco tareas, la métrica que #102 pidió desde el principio. 45, 65 y
`PRESUBMIT_ALLOWANCE_TURNS` son posiciones declaradas en una unidad sin ninguna cifra
propia todavía registrada -- `main_thread_turns`, una vez que existan corridas que lo
lleven, es la primera lectura real en esa unidad, y una futura recalibración es una
lectura, no una tercera adivinanza.

**`Role.max_turns` queda igual, declarado como seguimiento aparte, con sus tres
razones.** `agentic.role.turns` es `result.num_turns`, no está en la unidad del tope, y
una distribución leída de ahí no puede dimensionar `max_turns`. `prompts.fingerprint`
hashea la plantilla cruda antes de rellenarla: mover 80 cambiaría lo que cada Developer
lee sin que ningún fingerprint lo marque, y la comparación segmentada por fingerprint
de antes de este commit no mostraría el cambio -- exactamente el motivo por el que se
dice acá en vez de descubrirse en un panel. Ni `AGENTIC_MAX_BUDGET_USD` ni el límite de
`RunLimits` se mueven con más turnos, así que una corrida de Developer más larga
terminaría atada al dólar en vez de al turno. `main_thread_turns`, una vez leído, es lo
que permite dimensionar tanto el tope como los umbrales en una unidad que no necesita
conversión -- lo que esta ADR deja para una recalibración futura, no para esta.

**Ningún prompt se toca.** Ningún archivo bajo `domain/prompts/` se edita en este
commit: `{{max_turns}}` renderiza el mismo valor de siempre, `prompts.fingerprint` no
se mueve, mientras el comportamiento en tiempo de ejecución sí cambia -- posición,
disparos y texto, los tres. Esa asimetría se dice acá, y en el cuerpo del pull request,
en vez de aparecer como una sorpresa en una comparación segmentada por fingerprint.

## Lo que esta ADR no cambia

`ROLE_RETRIES`, el tope por intento, el tope de tarea, el límite del par y las clases de
escalación quedan fuera, byte a byte. `turn_notice` y `turn_notice_firings` mantienen su
nombre, su tipo y su significado registrado -- ningún `RECORDED_AS` se agrega. Ninguna
familia de métrica, serie o etiqueta se agrega, mueve o quita: `main_thread_turns` vive
solo en el documento de tarea. `.agentic/stack.yaml` y `pyproject.toml` no se tocan.

## Lo que se vigila

`agentic_role_failures{reason="max_turns", role="Developer"}` en las próximas cinco
tareas, junto con el resultado del presubmit (dos rechazos posibles por la ventana 2):
ninguna serie nueva, ninguna movida.
