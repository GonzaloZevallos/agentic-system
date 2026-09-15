# Conocimiento de producto

Lo destilado y vigente sobre el producto: dónde vive en el repo del producto, cómo se le
muestra a un rol y qué debe dejar una aprobación. Provee al proceso lo que el proceso le
pide; no lo dirige.

## Language

**Conocimiento de producto**:
Lo destilado y vigente sobre el producto, en el repo del producto y fuera de `.agentic/`: una
definición por capacidad (métricas incluidas), los ADRs y el glosario. Lo mantiene PM, salvo
los ADRs, que escribe Tech Lead.
_Avoid_: fuente de verdad, specs

**Capacidad**:
La unidad en que se organiza el conocimiento de producto: un archivo por concepto del
glosario del producto, con los jobs que sirve, su comportamiento vigente y sus métricas.

**Job**:
El progreso que el usuario busca en una situación concreta, escrito como job story:
"Cuando [situación], quiero [motivación], para [resultado esperado]", con los criterios
con que el propio usuario juzga que le salió bien. Es lo primero que se decide en una
feature y lo que gobierna qué comportamiento entra y cuál se descarta; una capacidad
sirve a uno o más jobs, y se acumulan.
_Avoid_: persona, user story, necesidad

**Destilado**:
El paso en que PM vuelca lo que una tarea dejó vigente al conocimiento de producto: la
definición de cada capacidad tocada y los términos del glosario. PM devuelve el texto, el
sistema escribe.

**Vocabulario de flags**:
El conjunto cerrado de flags que un rol puede marcar sobre una tarea, con una descripción por
flag. Lo define el producto; el sistema trae un mínimo por defecto que el producto reemplaza
entero.

**Estándares de ingeniería**:
Cómo debe estar escrito el código del producto: arquitectura hexagonal, DDD táctico con el
lenguaje del glosario, SOLID, patrones solo cuando nombran un problema, tests en costuras
acordadas, tipado estricto. El sistema trae un default que el producto reemplaza entero con
un archivo en su raíz. Los leen Tech Lead y Developer; aplican al código nuevo y al que la
tarea toca, no al que ya existía.
_Avoid_: buenas prácticas, guía de estilo (el estilo lo impone el linter, no este archivo)

**Chequeos del producto**:
Los comandos con que el producto declara que su código está sano (tests, linter, typechecker). Los corre la puerta de entrega, y el revisor los usa para reproducir una objeción.
_Avoid_: checks, CI, validaciones
