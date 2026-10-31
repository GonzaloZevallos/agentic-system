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
sistema escribe. Ocurre al aprobar el PRD y, en una feature con UI, otra vez en la validación
final de PM, cuando las capacidades se reconcilian con lo entregado (ADR-0075). Toda la prosa
va en español rioplatense. En el código y en los prompts se llama `consolidate`.
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
arranque; una entrega o un merge que lo hace crecer por encima del tope se rechaza. Ver
ADR-0055 y ADR-0061.
_Avoid_: guía de estilo, design system (el sistema de diseño es el código; esto son los criterios)
_Campo_: design standards

**Estándares de escritura**:
Los criterios con que el producto escribe sus textos de cara a una persona: voz, tono, registro, vocabulario (las palabras del glosario que van en pantalla), límites de largo y patrones por clase de texto, y el catálogo de mensajes si lo tiene. Viven en `WRITING_STANDARDS.md`, en la raíz del producto. Los leen enteros solo Tech Writer y su revisión; el sistema no trae un default y el archivo queda fuera del índice de conocimiento. Sin él, Tech Writer lo dice y propone uno inicial como seguimiento en vez de inventar reglas. Ver ADR-0058.
_Avoid_: guía de estilo, voz de marca (es un documento de criterios, no una identidad)
_Campo_: content style guide / writing standards

**Nombre del producto**:
Cómo se llama este producto, declarado por él en su stack. Es lo que etiqueta cada span y cada métrica de una corrida suya, así que dos productos nunca se suman en un panel. Se declara y no se deduce del checkout: el nombre del directorio cambia si alguien clona con otro nombre y el remoto cambia en un fork, mientras que un archivo versionado viaja con el repo. Es la única parte del stack que vale la del checkout y no la del commit base: dice quién es el repositorio, no qué regía en un commit.
_Avoid_: slug, id de producto, service name (el `service.name` es del sistema, y es uno solo)
_Campo_: sin término de arte; en observabilidad el vecino es una dimensión o un label

**Stack del producto**:
Con qué tecnología se construye y se verifica este producto, declarado por el producto en su repo. Son sus chequeos, los comandos con que declara que su código está sano (tests, linter, typechecker), que corre la puerta de entrega y que el revisor usa para reproducir una objeción; su preparación del entorno; su nombre; y, opcionales, sus escenarios de rendimiento (`performance`: comandos `seed` y `measure`, volumen y límites absolutos, que `agentic perf` mide y que nunca son chequeos), sus `delivery_checks` (el nivel de la puerta de entrega: los chequeos que corre cada entrega; el resto corre una vez por tarea sobre la cabeza integrada), sus `check_budgets` (los segundos que admite para cada chequeo, que el chequeo completo compara; glosario de Proceso: Presupuesto de chequeo), sus `ci_covers` (el job de CI que corre la misma suite que un chequeo local y el patrón de una línea fallida en su salida: si ese chequeo agota su tiempo tras un retroceso de la CI sin veredicto, se difiere a ese job en vez de fallar; ADR-0064, adenda GH-453), el testigo con que dice dónde vive el código que sus chequeos midieron (glosario de Proceso: Testigo del checkout) y `docs` (el comando que regenera la documentación generada y las rutas que posee; suma el chequeo reservado `docs`, ver Documentación técnica). El sistema impone las prácticas, nunca la herramienta. Declararlo es obligatorio: un producto que no lo declara no corre, y el sistema no tiene default que una corrida pueda alcanzar.
_Avoid_: checks, stack (a secas, cuando se habla del sistema), CI, validaciones
_Campo_: stack — estándar; sus comandos son los presubmit checks del producto

**Documentación técnica**:
Los documentos que explican cómo funciona un producto, en Markdown y Mermaid dentro de su repo, en español, con cada afirmación citando el archivo o el símbolo en que se apoya. Hay dos clases: la generada (derivada del código por el comando `generate` que el producto declara en `docs:`, bajo `docs/architecture/generated/`, nunca editada a mano y verificada por el chequeo `docs`, que falla si regenerarla cambia algo) y la escrita (vistas C4 y secuencias de flujos clave bajo `docs/architecture/`, planificada por Tech Lead y escrita por un Developer en la subtarea que cambia el límite). `docs/README.md` («Cómo funciona») es el índice y Tech Lead mantiene al día sus secciones técnicas. Opcional: sin `docs:` nada cambia. Ver ADR-0074.
_Avoid_: wiki, manual, documentación de usuario (esa es de Tech Writer), diagramas (son una parte)
_Campo_: technical documentation / docs-as-code; la clave se llama `docs` en el archivo

**Presupuesto de diagrama**:
Lo máximo que puede tener un diagrama Mermaid, generado o escrito, bajo `docs/architecture/`: 20 nodos y 30 aristas en un `flowchart`/`graph`, 15 clases en un `classDiagram`. No se miden los `sequenceDiagram` ni los C4. Pasarse (estrictamente más que el número) se arregla partiendo el diagrama por significado en páginas `.md` que dicen qué muestran y listan a su padre y sus hermanas, nunca por cantidad sola. En el código es `DIAGRAM_BUDGET`.
_Avoid_: presupuesto de chequeo (eso son los segundos de un chequeo)
_Campo_: diagram size limit; sin término de arte

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

**Decisiones del dueño**:
Las reglas generales que el dueño dio al contestar un escalado o por mensaje, guardadas como criterio en un archivo por alcance: `docs/product/decisiones-del-dueño.md` en cada producto y `docs/decisiones-del-dueño.md` en agentic-system. Todo rol las lee enteras, solo las vigentes, antes que el resto del conocimiento. Una regla vigente decide la pregunta que cubre sin escalar; contra un ADR que toca, gana la fechada después. Cada entrada tiene un id por contenido, solo se agrega al final y corregir es reemplazar.
_Avoid_: memoria del orquestador, preferencias
_Campo_: decision log — estándar en ingeniería (ADR); acá es del dueño y se lee entero, no se busca
