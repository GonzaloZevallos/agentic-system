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
sistema escribe. En el código y en los prompts se llama `consolidate`.
_Avoid_: distill (el castellano no confunde, el inglés sí)
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

**Nombre del producto**:
Cómo se llama este producto, declarado por él en su stack. Es lo que etiqueta cada span y cada métrica de una corrida suya, así que dos productos nunca se suman en un panel. Se declara y no se deduce del checkout: el nombre del directorio cambia si alguien clona con otro nombre y el remoto cambia en un fork, mientras que un archivo versionado viaja con el repo. Es la única parte del stack que vale la del checkout y no la del commit base: dice quién es el repositorio, no qué regía en un commit.
_Avoid_: slug, id de producto, service name (el `service.name` es del sistema, y es uno solo)
_Campo_: sin término de arte; en observabilidad el vecino es una dimensión o un label

**Stack del producto**:
Con qué tecnología se construye y se verifica este producto, declarado por el producto en su repo. Son sus chequeos, los comandos con que declara que su código está sano (tests, linter, typechecker), que corre la puerta de entrega y que el revisor usa para reproducir una objeción; su preparación del entorno; su nombre; y, opcional, el testigo con que dice dónde vive el código que sus chequeos midieron (glosario de Proceso: Testigo del checkout). El sistema impone las prácticas, nunca la herramienta. Declararlo es obligatorio: un producto que no lo declara no corre, y el sistema no tiene default que una corrida pueda alcanzar.
_Avoid_: checks, stack (a secas, cuando se habla del sistema), CI, validaciones
_Campo_: stack — estándar; sus comandos son los presubmit checks del producto

**Preparación del entorno**:
Los comandos con que un producto deja listo un checkout recién creado para que se pueda trabajar y correr sus chequeos en él: crear su entorno virtual, instalar dependencias, compilar lo que haga falta. Los declara el producto en su stack y los corre el sistema una sola vez, cuando el checkout nace, antes de que entre ningún rol. Es opcional: un producto sin dependencias no prepara nada, y el sistema avisa al arrancar que entonces no puede prometer aislamiento entre subtareas.
_Avoid_: bootstrap, instalación, build (el build es del producto y puede ser un chequeo)
_Campo_: setup — estándar; la clave se llama igual en el archivo
