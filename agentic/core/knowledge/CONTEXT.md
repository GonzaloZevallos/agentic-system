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

**Estándares de diseño**:
Los criterios de diseño del producto: reglas de iconos y texto, captura de diseño, lo que
UX y UI deben respetar. Viven en `DESIGN_STANDARDS.md`, en la raíz del producto. Los leen
entero los cuatro roles de diseño (UX Designer, UX Lead, UI Designer y UI Lead). El sistema
no trae un default: sin el archivo no se entrega nada. Pasar de 300 líneas se avisa en el
PR y el preflight, nunca se rechaza ni se reescribe. Ver ADR-0055.
_Avoid_: guía de estilo, design system (el sistema de diseño es el código; esto son los criterios)
_Campo_: design standards

**Nombre del producto**:
Cómo se llama este producto, declarado por él en su stack. Es lo que etiqueta cada span y cada métrica de una corrida suya, así que dos productos nunca se suman en un panel. Se declara y no se deduce del checkout: el nombre del directorio cambia si alguien clona con otro nombre y el remoto cambia en un fork, mientras que un archivo versionado viaja con el repo. Es la única parte del stack que vale la del checkout y no la del commit base: dice quién es el repositorio, no qué regía en un commit.
_Avoid_: slug, id de producto, service name (el `service.name` es del sistema, y es uno solo)
_Campo_: sin término de arte; en observabilidad el vecino es una dimensión o un label

**Stack del producto**:
Con qué tecnología se construye y se verifica este producto, declarado por el producto en su repo. Son sus chequeos, los comandos con que declara que su código está sano (tests, linter, typechecker), que corre la puerta de entrega y que el revisor usa para reproducir una objeción; su preparación del entorno; su nombre; y, opcional, el testigo con que dice dónde vive el código que sus chequeos midieron (glosario de Proceso: Testigo del checkout). El sistema impone las prácticas, nunca la herramienta. Declararlo es obligatorio: un producto que no lo declara no corre, y el sistema no tiene default que una corrida pueda alcanzar.
_Avoid_: checks, stack (a secas, cuando se habla del sistema), CI, validaciones
_Campo_: stack — estándar; sus comandos son los presubmit checks del producto

**Diseño del producto**:
Lo que el producto declara en su stack para que UX y UI Lead vean pantallas: dónde está su prototipo, cómo se levanta su app de muestra y cómo se sacan sus comparaciones. Es opcional, como la preparación del entorno: un producto sin diseño declarado corre igual, pero sin recorrido real ni comparaciones, y el documento de la tarea lo dice.
_Avoid_: design system (es una parte del diseño, la de tokens y componentes), mockups
_Campo_: sin término de arte; la clave se llama `design` en el archivo

**Prototipo**:
Las pantallas del diseño del humano, estáticas y enlazadas entre sí, que se pueden recorrer en un navegador sin la app. Es el diseño que manda: el sistema nunca lo cambia por su cuenta.
_Avoid_: mockup, maqueta, lienzo (el lienzo es donde el humano lo diseñó; el prototipo es lo que el producto guarda en su repo)
_Campo_: clickable prototype — estándar en diseño de interacción

**App de muestra**:
La app construida corriendo con datos de muestra, aparte de la del humano y sin tocar sus datos, que el producto sabe levantar con un comando que dice la dirección y el usuario. Es lo que UX Lead recorre en la validación final.
_Avoid_: demo, staging (staging es un entorno desplegado; esta vive y muere con la tarea)
_Campo_: sin término de arte

**Brief**:
Lo que el humano escribe una vez por producto, antes de la primera tarea: el problema, los
jobs y las restricciones. No es un PRD: no describe comportamiento.
_Avoid_: visión, PRD
_Campo_: sin término de arte

**Preparación del entorno**:
Los comandos con que un producto deja listo un checkout recién creado para que se pueda trabajar y correr sus chequeos en él: crear su entorno virtual, instalar dependencias, compilar lo que haga falta. Los declara el producto en su stack y los corre el sistema una sola vez, cuando el checkout nace, antes de que entre ningún rol. Es opcional: un producto sin dependencias no prepara nada, y el sistema avisa al arrancar que entonces no puede prometer aislamiento entre subtareas.
_Avoid_: bootstrap, instalación, build (el build es del producto y puede ser un chequeo)
_Campo_: setup — estándar; la clave se llama igual en el archivo
