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
_Campo_: knowledge base — estándar, pero en RAG es un corpus indexado para el agente; el nuestro es documentación que un humano lee y mantiene

**Capacidad**:
La unidad en que se organiza el conocimiento de producto: un archivo por concepto del
glosario del producto, con los jobs que sirve, su comportamiento vigente y sus métricas.
_Campo_: sin término de arte

**Job**:
El progreso que el usuario busca en una situación concreta, escrito como job story:
"Cuando [situación], quiero [motivación], para [resultado esperado]", con los criterios
con que el propio usuario juzga que le salió bien. Es lo primero que se decide en una
feature y lo que gobierna qué comportamiento entra y cuál se descarta; una capacidad
sirve a uno o más jobs, y se acumulan.
_Avoid_: persona, user story, necesidad
_Campo_: job to be done — de innovación y marketing (Christensen); la job story es de Intercom

**Destilado**:
El paso en que PM vuelca lo que una tarea dejó vigente al conocimiento de producto: la
definición de cada capacidad tocada y los términos del glosario. PM devuelve el texto, el
sistema escribe.
_Campo_: sin término de arte. `distill` está tomado: en ML, knowledge distillation es entrenar un modelo chico contra las salidas de uno grande (Hinton, Vinyals y Dean 2015)

**Vocabulario de flags**:
El conjunto cerrado de flags que un rol puede marcar sobre una tarea, con una descripción por
flag. Lo define el producto; el sistema trae un mínimo por defecto que el producto reemplaza
entero.
_Campo_: sin término de arte; es lo que un issue tracker llama label set

**Estándares de ingeniería**:
Cómo debe estar escrito el código del producto: arquitectura hexagonal, DDD táctico con el
lenguaje del glosario, SOLID, patrones solo cuando nombran un problema, tests en costuras
acordadas, tipado estricto. El sistema trae un default que el producto reemplaza entero con
un archivo en su raíz. Los leen Tech Lead y Developer; aplican al código nuevo y al que la
tarea toca, no al que ya existía.
_Avoid_: buenas prácticas, guía de estilo (el estilo lo impone el linter, no este archivo)
_Campo_: coding standards — estándar

**Stack del producto**:
Con qué tecnología se construye y se verifica este producto, declarado por el producto en su repo. Hoy son sus chequeos: los comandos con que declara que su código está sano (tests, linter, typechecker), que corre la puerta de entrega y que el revisor usa para reproducir una objeción. El sistema impone las prácticas, nunca la herramienta.
_Avoid_: checks, stack (a secas, cuando se habla del sistema), CI, validaciones
_Campo_: stack — estándar; sus comandos son los presubmit checks del producto
