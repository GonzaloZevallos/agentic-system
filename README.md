# Sistema agéntico de desarrollo

Estado consolidado del diseño. Reemplaza a la conversación de origen en claude.ai
(share: `16d37910-919c-4167-9984-48fcf32ffe1e`), cuyo volcado se borró del repo el
2026-09-12 y queda en el historial de git.

Convención de nombres fijada: **PjM** = Project Manager (ex "Lead"), **PM** = Product Manager.

Las secciones 8 a 11 salieron de una sesión de interrogatorio al diseño y son las que lo
aterrizan: qué producto, cómo se mide, con qué modelos y en qué orden se construye. La
sección 14 salió de contrastar el diseño con `langchain-ai/open_deep_research` y fija cómo
se escribe el código. Si algo en las secciones 1 a 7 las contradice, mandan las nuevas.

El vocabulario está en un `CONTEXT.md` por módulo, enumerados en `CONTEXT-MAP.md`. Las
decisiones con trade-off real están en `docs/adr/` de este repo; acá se citan, no se
re-argumentan.

---

## 1. Roles

17 roles agénticos más el humano, que actúa como aprobador y destino de escalados.

### Coordinación

| Rol | Responsabilidad |
|---|---|
| **PjM** (Project Manager) | Clasifica, enruta, registra todo, controla loops, cierra, mide el proceso y lo mejora |

### Producto

| Pensador | Validador | Alcance |
|---|---|---|
| Product Analyst | PM, luego Data Analyst | Definición de comportamiento, flags, métricas |
| UX Designer | UX Lead | Flujos e interacción |
| UI Designer | Design Lead | Visual |
| Tech Writer | — (independiente) | Textos |

PM es además validador final de producto y proponente proactivo de features.
Data Analyst valida solo la parte de métricas.

### Técnico

| Pensador | Validador | Alcance |
|---|---|---|
| Developer | Tech Lead | Implementación de una subtarea |

Tech Lead nunca codea. Tiene dos modos: por tarea, y proactivo sobre el repo.

### Calidad

| Rol | Cuándo participa |
|---|---|
| QA Lead | Siempre. Coordina y consolida un veredicto único |
| QA Funcional | Siempre, en ambas pasadas |
| QA Seguridad | 2ª pasada, si Tech Lead lo convoca |
| QA Performance | 2ª pasada, si Tech Lead lo convoca |

### Entradas automáticas

| Rol | Qué hace |
|---|---|
| SRE / Ops | Monitorea producción y crea bugs |
| Customer Insights | Lee feedback de usuarios y sintetiza para PM |

---

## 2. Tipos de tarea y caminos

PjM clasifica toda tarea entrante como **bug**, **feature** o **platform enhancement**.

### Feature

```
PjM clasifica
  → Product Analyst: competidores, definición, métricas + dashboard, flags
  → PM valida definición y flags
  → Data Analyst valida métricas
  → [si toca UI]    UX Designer define flujos    → UX Lead valida
  → [si toca UI]    UI Designer diseña sobre el flujo validado → Design Lead valida
  → [si toca textos] Tech Writer escribe sobre flujo y diseño validados
  → PM consolida la definición única
  → QA Funcional (1ª pasada): observa la definición, casos borde, casos de prueba
  → Tech Lead: análisis, pingpong, ajuste del plan de tests, subtareas
  → Developers en paralelo
  → Tech Lead revisa cada subtarea
  → Developer integrador, bajo revisión de Tech Lead
  → QA 2ª pasada (funcional + seguridad/performance si fueron convocados)
  → PM valida producto
  → PjM cierra
```

La etapa de definición es **estrictamente secuencial**. Se evaluó paralelizarla y se
descartó: el riesgo de que UI diseñe sobre un flujo que UX Lead después cambia pesa más
que la velocidad ganada.

### Bug

```
PjM clasifica
  → Product Analyst reproduce, compara contra definiciones anteriores, dictamina
      · sin definición previa → consulta a PM, que decide
  → PM valida el dictamen
      · comportamiento esperado → PjM descarta y cierra
  → [si es visual] UI Designer define el visual correcto → Design Lead valida
  → Tech Lead → Developers → QA → PM valida → PjM cierra
```

Un bug de flujo (por ejemplo, un paso del que no se puede volver atrás) es **funcional**,
no de UX: lo valida PM solo, porque las decisiones de UX ya viven en la definición que PM
posee. **UX no participa en bugs.**

Los bugs no llevan métricas ni dashboard.

### Platform enhancement

```
PjM clasifica → Tech Lead → Developers → QA verifica que el comportamiento no cambió,
                más seguridad y performance → PjM cierra
```

Sin PM, sin flags, sin métricas. Cierra solo con aprobación de QA.

Si QA detecta un cambio de comportamiento inesperado, devuelve a PjM. PjM **consulta** a PM
si el cambio es aceptable:

- Aceptable → sigue el flujo normal.
- No aceptable → vuelve a Tech Lead. Cuenta como devolución del par QA→Tech Lead.

Esa consulta no suma al par PjM→PM.

---

## 3. Reglas transversales

### Consulta vs. devolución

| | Consulta | Devolución |
|---|---|---|
| Qué es | Pregunta sin entregar la tarea | Rechazo y entrega hacia atrás |
| Cuenta para el límite | No | Sí |

### PjM lo ve todo

Toda acción relevante se comunica a PjM: creación de subtareas, asignación de Developers,
rechazos internos, consultas y entregas. PjM no necesariamente interviene, pero siempre
tiene el registro completo. Es delgado en criterio y grueso en información. Eso es lo que
lo habilita a controlar loops y a medir el proceso.

### Devoluciones internas

Los pares validador→pensador resuelven dentro del par, sin pasar por PjM:

- Tech Lead → Developer
- PM → Product Analyst
- Data Analyst → Product Analyst
- UX Lead → UX Designer
- Design Lead → UI Designer

El validador **reporta** cada devolución a PjM, y cuentan para el límite.

### Control de loops

- El conteo es **por par de roles**, no global. El par es quien devuelve y quien recibe,
  no solo validador→pensador: QA→PM y QA→Tech Lead también son pares (ADR-0009).
- Límite: **2 devoluciones** por par. También corta antes si detecta el mismo patrón
  repetido (mismo motivo entre los mismos roles).
- Acción al cortar: **escalar al humano, siempre**. PjM entrega la tarea, el historial de
  devoluciones y el motivo de cada una.
- Mientras espera, la tarea queda en pausa. PjM no re-enruta.
- Cuando el humano responde, la tarea vuelve a PjM, que re-enruta aplicando esa decisión,
  y el contador de ese par se resetea.
- Si el par que llega al límite es Tech Lead→Developer, se corta esa subtarea. El resto
  sigue, salvo lo que dependa de ella.

### Cola de escalados

El humano revisa **en lote, una vez por día**. Los escalados se acumulan y no interrumpen.
Una tarea escalada queda pausada y se retoma desde el mismo punto cuando el humano responde,
con el contador de ese par reseteado.

La cola no se construye: es la **etiqueta `needs-human`** en el issue, más el historial de
devoluciones en un comentario. Revisar es filtrar por esa etiqueta. Responder es sacarla y
dejar la decisión en un comentario.

Esto exige estado durable del lado del sistema: el grafo se frena, el estado sobrevive al
proceso, y la respuesta del humano lo reanuda donde estaba.

### Fallo de rol

Distinto de una devolución. Es el rol que se rompe: JSON inválido, salida vacía, se le
acaban los turnos.

- **Un reintento**, pasándole el error como entrada.
- Si falla de nuevo, **escala al humano**. No cuenta para el límite de devoluciones del par.

Un reintento con el error explicado arregla casi todos los casos de formato. Más reintentos
es gastar en un rol que ya se rompió; escalar al primer fallo llena la cola de cosas que se
arreglaban solas.

### Enrutamiento de devoluciones de QA

- Rechazo por comportamiento → vía PjM, a PM.
- Rechazo técnico → vía PjM, **directo al Developer de esa subtarea**, no a Tech Lead.

Esto exige que Tech Lead informe a PjM el mapa de subtareas y Developers al crearlo.

### Cierre

| Caso | Aprobaciones necesarias |
|---|---|
| Feature | PM y QA |
| Bug | PM y QA |
| Platform enhancement | QA |

El cierre es **aprobado** o **descartado**: un bug cuyo dictamen PM valida como
"comportamiento esperado" lo cierra PjM descartado, sin estas aprobaciones (sección 2).

UX Lead, Design Lead y Tech Writer **no aprueban al cierre**. Esa regla existió y fue
reemplazada: PM es el único que valida producto porque construyó la definición junto con
ellos. PM puede consultar a los leads, pero la aprobación es suya. Si el
Developer se desvía del diseño o de los textos, es PM quien lo detecta y lo devuelve.

---

## 4. Detalle por rol

### PjM — Project Manager

- Clasifica bug / feature / platform enhancement.
- Enruta, recibe toda devolución, re-enruta.
- Lleva el registro completo del sistema.
- Controla loops y escala al humano.
- Crea subtareas a pedido de Tech Lead y enruta hacia los Developers correctos.
- Cierra.
- Es el reloj del sistema para lo que no tenga reloj propio.

**Métricas que registra**

- Tiempo por etapa y por rol.
- Cantidad y motivo de devoluciones.
- Escalados al humano y su causa.
- Tamaño de tareas: subtareas, grado de paralelismo.

**Mejora del proceso**

Dispara cuando detecta un patrón negativo.

| Menores: las aplica solo | Mayores: las propone, el humano valida |
|---|---|
| Ajustar parámetros (N de loops, prioridades) | Cambiar el orden de etapas del flujo |
| Cambiar instrucciones o prompts de un rol | Agregar o quitar roles |

- Toda mejora se ata a la métrica que la motivó.
- Ventana de evaluación: las próximas **10 tareas cerradas**. Si la métrica empeoró, revierte.
- Resumen periódico cada **10 tareas cerradas**: métricas, mejoras aplicadas, revertidas y
  propuestas mayores pendientes de validación. Las mejoras menores se informan ahí, no una
  por una.
- Como las dos ventanas coinciden en 10, cada resumen trae el veredicto de las mejoras del
  ciclo anterior.
- Requiere versionado de prompts y parámetros para poder revertir: los overrides viven en
  el repo del producto y cada tarea registra el hash de los prompts que corrió (ADR-0005).

### PM — Product Manager

- Valida la definición y los flags que redacta Product Analyst.
- Delega en orden a UX Lead, Design Lead y Tech Writer según los flags.
- Consolida la definición única. Es su dueño: comportamiento, flujo, diseño y textos.
- **Destila al aprobar**: devuelve en su salida estructurada el archivo completo de cada
  capacidad que la tarea tocó (`docs/product/<capacidad>.md`) y los términos nuevos o
  corregidos del glosario del producto (`CONTEXT.md`). Los escribe el grafo; PM no tiene
  tools. Aprobar una feature sin destilar es salida inválida, o sea fallo de rol.
- Valida la implementación final de producto. Puede consultar a UX Lead y Design Lead.
- Valida que la feature salga medida.
- Evalúa el aporte al negocio de cada funcionalidad antes de aprobar.
- Analiza competidores.
- **Modo proactivo**: con reloj propio, cruza su análisis de competidores con las síntesis
  de Data Analyst y Customer Insights, y propone features nuevas. Pasan por el humano antes
  de entrar a PjM.

### Product Analyst

**Entradas**: la tarea, el conocimiento de producto (`docs/product/` y `CONTEXT.md` del
producto, completos), acceso al producto real para explorarlo.

**En feature**

- Entrevista a PM (ADR-0014). La primera ronda es siempre el **job to be done**: en qué
  situación el usuario recurre a esto, qué progreso busca, cómo juzga que le salió bien,
  qué hace hoy sin la feature. Sale como job story ("Cuando [situación], quiero
  [motivación], para [resultado]") con criterios de éxito, y ninguna ronda posterior lo
  reabre.
- Analiza competidores para tener criterio sobre cómo sirven ese job.
- Redacta la definición de comportamiento: el job primero, y cada caso sirve al job; lo
  pedido que no lo sirve queda fuera, dicho explícitamente.
- Define qué métricas de negocio aporta la funcionalidad y cómo se obtienen.
- Marca los flags. El vocabulario de flags es cerrado y lo define el producto en
  `.agentic/flags.yaml`, con un default mínimo del sistema; Product Analyst marca cada uno
  sí/no y el set validado por PM reemplaza al propuesto.
- Consulta a PM si la tarea es ambigua.

**En bug**

1. Reproduce en el producto real.
2. Compara contra la definición vigente de la capacidad en `docs/product/`.
3. Con definición previa, dictamina bug o comportamiento esperado.
4. Sin definición previa, consulta a PM, que decide.

**Reclasificación**: si la tarea no es lo que PjM clasificó, lo sugiere a PM, PM se lo pide
a PjM, y PjM reclasifica.

**Salida**: definición + flags → PM.

### Data Analyst

- Validador de Product Analyst en métricas: revisa que midan valor de negocio y que sean
  obtenibles.
- Valida **después** de PM, no en paralelo.
- No valida al final.
- **Modo proactivo**: con reloj propio, analiza las métricas de features ya lanzadas y deja
  una síntesis de qué funcionó y qué no, que alimenta a PM.

### UX Lead / UX Designer

- UX Designer define flujos e interacción. UX Lead valida.
- Participan solo cuando el flag "toca UI" está marcado.
- **No participan en bugs.**
- No validan al cierre. No devuelven a Developer.

### Design Lead / UI Designer

- UI Designer define lo visual, sobre un flujo ya validado. Design Lead valida.
- Participan cuando el flag "toca UI" está marcado, incluso en bugs visuales.
- No validan al cierre. No devuelven a Developer.

### Tech Writer

- Independiente, no tiene validador propio.
- Participa cuando el flag "toca textos" está marcado.
- Escribe sobre flujo y diseño ya validados. Es el último de la etapa de definición.
- No valida la implementación ni devuelve.

### Tech Lead

Uno por tarea. **Nunca codea, siempre delega.**

**Entradas**: definición única de PM, plan de tests funcionales de QA Lead, ADRs del proyecto.

**Análisis**

1. Propone la solución técnica.
2. La pingponea con un **segundo Tech Lead efímero** hasta validarla. El efímero corre con
   modelo más capaz y **prompt de adversario**: su trabajo es encontrar el fallo, no validar.
   Sin esas dos asimetrías el pingpong solo ratifica.
3. Revisa y ajusta el plan de tests funcionales.
4. Decide si la 2ª pasada necesita QA Seguridad y/o QA Performance.
5. Parte en subtareas, marca las paralelizables, prioriza por valor entregado rápido. Puede
   consultar el orden con PM.
5b. Escribe un ADR en `docs/adr/` cuando una decisión técnica sobrevive a la tarea. El
   efímero lo valida en el mismo pingpong.
6. Informa a PjM: plan de subtareas, Developers asignados, QAs convocados.

**Ejecución**

- Despliega un Developer por subtarea. Cada Developer es un **nodo del grafo**, no un
  subagente escondido dentro de la llamada de Tech Lead: PjM tiene que saber qué Developer
  hizo qué subtarea para poder enrutar los rechazos técnicos de QA.
- Responde consultas de Developers y escala a PM o QA si hace falta.
- Revisa **cada subtarea** al entregarse y también **la integración**.
- Si rechaza, vuelve **al mismo Developer**. Nunca reasigna a otro.
- Designa un Developer integrador y revisa la integración final.
- Entrega la pieza integrada a PjM → QA 2ª pasada.

**Modo proactivo**: con reloj propio, revisa el repositorio en busca de deuda técnica y
dependencias, y genera platform enhancements. Es el mismo rol para que comparta criterio
técnico y ADRs con los Tech Leads por tarea.

### Developer

- Recibe una subtarea de Tech Lead.
- Acceso completo al repositorio, lo explora por su cuenta.
- Implementa código y tests: unitarios, de integración y e2e según sea necesario.
- Consulta solo a Tech Lead. Tech Lead escala si hace falta.
- Entrega código, resumen de cambios, decisiones técnicas y supuestos tomados.

**Herramientas y permisos**. Es el único rol que ejecuta: lectura y escritura de archivos,
bash y tests, **sin aprobación humana por comando**. La jaula es el directorio del producto,
más un tope de costo por llamada. Pedir aprobación comando por comando rompe la revisión en
lote: el sistema quedaría congelado esperando al humano en cada paso.

### QA Lead y los tres QA

**1ª pasada, antes de desarrollar.** Solo QA Funcional, coordinado por QA Lead.

- Revisa claridad y completitud de la definición.
- Busca casos borde.
- Escribe los casos de prueba funcionales.
- QA Lead consolida y entrega a PjM → Tech Lead.

**2ª pasada, después de desarrollar.**

- QA Funcional ejecuta los casos, reporta, y explora casos no previstos. En features valida
  que las métricas se registren y que el dashboard muestre lo definido.
- QA Seguridad y QA Performance, si Tech Lead los convocó, definen sus casos sobre el código
  real, ejecutan, reportan y exploran.
- QA Lead consolida **un solo veredicto** con todos los hallazgos, aunque unos aprueben y
  otros rechacen, y enruta cada hallazgo: comportamiento → PM, técnico → Developer de la
  subtarea.

Consecuencia asumida: seguridad y performance se evalúan solo sobre código, nunca sobre la
definición. Es un trade-off para arrancar. Si PjM ve muchos rechazos de seguridad tardíos,
es señal de adelantarlos.

### SRE / Ops

- Reloj propio. Monitorea producción.
- Genera **bugs**, que entran directo a PjM sin aprobación humana.

### Customer Insights

- Reloj propio. Lee feedback de usuarios y lo sintetiza para PM.
- No genera tareas por sí mismo.

---

## 5. Entradas del sistema

Todo lo que genera una tarea la genera como **issue de GitHub con la etiqueta `agentic`**.

| Fuente | Rol | Reloj | Genera | Aprobación humana |
|---|---|---|---|---|
| Humano | — | — | cualquier tipo | — |
| Producción | SRE / Ops | propio | bugs | No, entran solos |
| Repositorio | Tech Lead proactivo | propio | platform enhancements | Sí |
| Competidores | PM | propio | features | Sí |
| Métricas | Data Analyst | propio | insumo para PM | — |
| Feedback de usuarios | Customer Insights | propio | insumo para PM | — |

Los roles proactivos que necesitan aprobación humana crean el issue **sin** la etiqueta
`agentic`. Ponerla es la aprobación.

Cada rol proactivo tiene su **propio reloj**, no hay un ciclo central que los despierte.

Cadena de producto proactiva: Data Analyst y Customer Insights dejan síntesis. PM, en su
ciclo, las lee junto a su análisis de competidores y propone features. El humano aprueba, y
recién ahí entran a PjM.

---

## 6. Transporte de información

**Un documento único por tarea.** Un archivo en `.agentic/tasks/` del repo del producto. Cada
rol lee lo que necesita y escribe su sección. Por qué un archivo y no mensajes ni el issue:
ADR-0001. Es el **registro crudo** del trabajo: guarda también lo que una devolución pisó y
las corridas de cada rol. Lo vigente se destila de ahí al conocimiento de producto
(`docs/product/`, ADR-0006); ningún rol lee tareas viejas para saber cómo se comporta el
producto.

**GitHub issues es la bandeja de entrada, no el documento.** Las tareas entran como issues
del repo del producto. El sistema toma solo los que llevan la etiqueta `agentic`, que pone
un humano: es el interruptor que evita que el sistema se meta en cualquier issue suelto.

PjM sincroniza hacia el issue lo que le sirve a un humano: la etiqueta de clasificación, la
etiqueta de estado, un comentario de resumen al cerrar, y el cierre. El detalle de trabajo
no va al issue.

**Formato híbrido.** Markdown para el contenido que producen los agentes, porque los modelos
lo leen y escriben bien y es auditable a mano. Frontmatter YAML para lo que PjM procesa
mecánicamente.

**Contexto por rol.** Cada rol declara en su definición la **lista fija de secciones** del
documento que lee. No recibe solo lo del rol previo: Tech Lead lee la definición
consolidada de PM y los casos de QA, no el resumen del resumen.

La lista es declarativa y no la decide el modelo en tiempo de ejecución. Así PjM la puede
registrar, y cuando un rol falla se puede ver si le faltaba información.

Cada rol igual cierra con un bloque **"Para el siguiente rol"**, que sigue siendo útil como
handoff explícito, pero ya no es lo único que viaja.

### Esqueleto del documento

**Frontmatter, mantenido por PjM**: id, tipo, estado, rol actual, flags, contadores por par,
timestamps por etapa, escalados, subtareas, hash de prompts, corridas por rol (`runs`: rol,
sección, `session_id`, modelo, costo, turnos) y fallos con su texto.

**Secciones**

| Sección | Contenido |
|---|---|
| Product Analyst | Competidores, definición de comportamiento, métricas y dashboard, flags propuestos. En bug: reproducción, comparación, dictamen |
| PM | Validación, ajustes, decisiones ante consultas, definición consolidada |
| Data Analyst | Validación de métricas |
| UX Designer / UX Lead | Flujos y validación |
| UI Designer / Design Lead | Visual y validación |
| Tech Writer | Textos |
| QA Lead / Funcional (1ª) | Observaciones a la definición, casos borde, casos de prueba |
| Tech Lead | Solución técnica, resultado del pingpong, plan de tests ajustado, QAs convocados, subtareas |
| Developer (una por subtarea) | Resumen, decisiones y supuestos, tests |
| Tech Lead (revisión) | Hallazgos por subtarea y de la integración |
| QA 2ª pasada | Resultados por especialidad, exploratorio, veredicto consolidado |
| PM (final) | Validación de producto |
| PjM | Cierre y métricas de la tarea |

Cada sección cierra con un bloque fijo **"Para el siguiente rol"**, un handoff explícito
que acompaña a las secciones completas que el siguiente rol declara leer.

Cuando un rol vuelve a entregar tras una devolución, su sección anterior queda como
`## <Rol> (vN)` y la vigente sin sufijo. Los roles leen solo las vigentes; las versiones
son para PjM al escalar con historial y para comparar corridas.

---

## 7. Medición de features

Toda feature sale medida. **No está terminada si no sale medida.**

- Product Analyst define las métricas de negocio y el dashboard.
- Data Analyst valida que midan valor real y que sean obtenibles.
- Las métricas y el dashboard son parte de la definición única, así que Tech Lead las parte
  en subtareas como cualquier otra cosa.
- QA Funcional valida que se registren y que el dashboard muestre lo definido.
- PM valida al cierre que la feature salga medida.

No aplica a bugs ni a platform enhancements.

---

## 8. Producto de prueba y costura

El sistema es **agnóstico al producto**. Lo que cambia por producto entra como parámetro;
lo que no cambia es el proceso.

| Del producto (parámetro) | Del sistema (fijo) |
|---|---|
| Repositorio | Roles y sus prompts |
| Conocimiento de producto | Caminos y ruteo |
| ADRs | Conteo de devoluciones y control de loops |
| Acceso a métricas | Documento único y sus secciones |

**Forma de la costura**: un **directorio de contexto de producto** que el sistema recibe
como parámetro. Se eligió sobre un adaptador con funciones porque todavía no se sabe qué
varía de verdad entre productos, y no se puede generalizar sobre un solo ejemplo. El
adaptador se extrae cuando aparezca el segundo producto. Los ports de la arquitectura
hexagonal (ADR-0007) no son ese adaptador: los define lo que el sistema necesita, tienen
una sola implementación sobre este directorio, y este sigue siendo el único parámetro.

**Repositorios separados** (ADR-0002). El sistema vive en su propio repo, el producto en
otro, y el sistema nunca escribe dentro del suyo cuando trabaja.

Forma del repo del producto:

| Ruta | Contenido | Quién escribe |
|---|---|---|
| código de la app | El producto | Developer |
| `docs/product/` | Conocimiento de producto: una definición vigente por capacidad, métricas incluidas | PM al aprobar, vía el grafo |
| `docs/adr/` | ADRs numerados | Humano el primero, después Tech Lead |
| `CONTEXT.md` | Glosario del producto | Humano el primero, después PM y las entrevistas (ADR-0014) |
| `CODING_STANDARDS.md` | Estándares de ingeniería del producto, pisa el default del sistema (ADR-0012) | Humano |
| `.agentic/tasks/` | Documento único por tarea, registro crudo del trabajo | Los roles, sección por sección |
| `.agentic/prompts/` | Overrides de prompts por rol, si los hay (ADR-0005) | PjM |
| `.agentic/flags.yaml` | Vocabulario de flags del producto, pisa el default del sistema | Humano, después PM |
| `.agentic/state.sqlite` | Checkpointer del grafo, fuera del control de versiones (ADR-0003) | El sistema |
| `.agentic/worktrees/` | Un worktree por subtarea, fuera del control de versiones (ADR-0011) | El sistema |
| rama `agentic/<tarea>` | La rama de tarea: entrevistas, ADRs, código integrado. Termina en un pull request (ADR-0011) | Los roles |

El conocimiento de producto (definiciones, ADRs, glosario) vive con los docs humanos, no
bajo `.agentic/`, porque es del producto y lo lee cualquiera; `.agentic/` es maquinaria del
sistema (ADR-0006). Los ADRs los valida el Tech Lead efímero en el pingpong, y el humano ve
todo el conocimiento igual al revisar el diff antes de mergear.

### El producto de prueba

**SaaS B2B para dueños de gimnasios**: socios, suscripciones, asistencia y métricas de
negocio. Se eligió porque tiene métricas reales y discutibles (churn, asistencia promedio,
recupero de inactivos, ingreso recurrente), lo que hace que la regla de "no está terminada
si no sale medida" se pueda probar de verdad y no quede como teatro.

**Stack**: Python, FastAPI, SQLite, pytest. **Solo API, sin frontend.**

Sin frontend, UX Designer, UX Lead, UI Designer y Design Lead no tienen nada que hacer. Eso
es deliberado: entran cuando haya interfaz, no antes.

### La versión cero la construye el humano

El sistema **mantiene y hace crecer** un producto; no lo pare. En greenfield la mitad de los
roles no tiene insumo: no hay definiciones anteriores contra las cuales comparar un bug, no
hay producción que monitorear, no hay usuarios que dejen feedback.

Por eso el v0 lo escribe el humano a mano: **socios, suscripciones y asistencia, con tests y
CI**. Las métricas quedan **afuera a propósito**, para que la primera tarea real del sistema
sea el dashboard y ejercite la regla de medición.

Los tests y el CI van en el v0 porque son el criterio de éxito del sistema. Sin ellos no hay
nada que medir.

---

## 9. Criterio de éxito y evaluación

Sin esto, la mejora del proceso de PjM no es mejora, es deriva.

**Set fijo de cinco tareas.** Salen de lo que el humano vaya postergando mientras arma el
v0: tareas reales, escritas por alguien con el problema en la mano. Se re-corren idénticas
contra cada versión de los prompts. Cinco tareas distintas cada vez no miden nada, porque no
se puede separar si mejoró el prompt o si las tareas eran más fáciles.

**Puntaje binario y estricto.** Una tarea cuenta solo si el diff pasa los tests y el humano
lo mergea **sin tocar**. No hay crédito parcial: es lo único que no se corre con el humor
del día.

**Nota aparte.** Cada tarea deja anotado qué le faltó. No puntúa, pero dice qué prompt tocar
sin ensuciar el número.

**Línea de base.** Cada vez que se cambia un prompt o un parámetro, se re-corre el set
entero. Requiere versionado de prompts, que ya era condición para poder revertir.

---

## 10. Modelos por rol

Un validador que comparte modelo, prompt e información con el pensador **ratifica, no
valida**. La regla es que todo validador traiga una fuente que el pensador no tuvo.

| | Modelo | Por qué |
|---|---|---|
| Pensadores | Sonnet | Son los que más llamadas hacen |
| Validadores | Opus | Asimetría más barata de implementar y de medir |

Las otras fuentes de asimetría, cuando existen, valen más que el modelo: QA Funcional
ejecuta tests contra el producto real, QA Seguridad y Performance leen código real, Data
Analyst consulta métricas reales. Esos validadores se ganan el lugar solos.

Los que no traen información nueva (Tech Lead efímero, PM sobre Product Analyst) dependen
enteramente de la asimetría de modelo y de prompt. Si no la tienen, sobran.

---

## 11. Orden de implementación

Los 17 roles quedan en el diseño. Lo que se ordena es el orden en que se construyen.

**Regla**: cada rol nuevo tiene que mostrar que las cinco tareas salen **mejor con él que
sin él**. Es lo que convierte la intuición de organigrama en una hipótesis que se puede
refutar.

| Etapa | Roles | Por qué ahí |
|---|---|---|
| 1 | PjM, Product Analyst, PM, Tech Lead, Developer | Mínimo para llegar a un diff medible |
| 2 | QA Lead, QA Funcional | Primer validador con información nueva real |
| 3 | Data Analyst, QA Seguridad, QA Performance | Cuando haya métricas y código que valga auditar |
| 4 | UX, UI, Design Lead, Tech Writer | Cuando haya frontend |
| 5 | SRE/Ops, Customer Insights | Cuando haya producción y usuarios |

**Consecuencia asumida**: el camino de bug no se puede ejercitar hasta que el producto tenga
conocimiento de producto escrito. Las primeras tareas del sistema son todas features.

**Segunda consecuencia asumida**: en la etapa 1 no hay QA, así que Tech Lead arranca sin el
plan de tests funcionales y Developer escribe los tests desde la definición y las costuras
que el plan declara. La tarea termina **lista para revisión**: un pull request que el humano
mergea o no (ADR-0011). El Tech Lead efímero entra en la misma etapa, como entrevistador
de Tech Lead (ADR-0014).

**Riesgo conocido de la estructura por roles.** Un organigrama humano existe porque las
personas tienen contexto limitado, no se pueden clonar, y alguien rinde cuentas. Los agentes
no tienen ninguna de las tres restricciones. Copiar la forma sin el motivo trae dos costos
reales: los errores se componen a lo largo de la cadena de handoffs, y con muchos roles no
se puede atribuir cuál arruinó el resultado. La regla de que cada rol se gane el lugar
existe justamente para pagar ese costo solo donde compre calidad.

---

## 12. Pendientes

| # | Pendiente | Bloquea |
|---|---|---|
| 1 | ~~Formato concreto de la definición de comportamiento~~ **resuelto**: job to be done (job story y criterios de éxito), competidores, comportamiento caso por caso, métricas derivadas de los criterios del job, flags, handoff. Vive en el prompt del Product Analyst | — |
| 2 | ~~Fuente de verdad de producto~~ **resuelto**: conocimiento de producto en `docs/product/`, un archivo por capacidad, destilado por PM al aprobar (ADR-0006). Las definiciones del v0 las escribe el humano | — |
| 3 | ~~ADRs~~ **resuelto**: `docs/adr/`, primero el humano, después Tech Lead, valida el efímero | — |
| 4 | Frecuencia de cada reloj proactivo | Etapa 5 |
| 5 | Señales y umbrales de SRE/Ops para decidir que algo es bug, más deduplicación | SRE/Ops, etapa 5 |
| 6 | ~~Lista de secciones que lee cada rol~~ **resuelto**: `Role.reads`, la entrada del rol son esas secciones completas. Cada rol nuevo declara la suya al entrar | — |
| 7 | UX Lead, Design Lead y Tech Writer en detalle | Etapa 4 |
| 8 | Criterio de clasificación de PjM y qué hace si duda | PjM |
| 9 | ~~Cuántos Tech Leads existen~~ **resuelto**: un rol, una instancia por tarea; el modo proactivo es el mismo rol. El efímero no es un rol de la lista, es el entrevistador de Tech Lead (ADR-0014) | — |
| 10 | ~~Versionado de prompts~~ **resuelto**: `.md` por rol con overlay en el producto, hash por tarea (ADR-0005). Falta el versionado de **parámetros** de `Configuration` | Mejora del proceso de PjM |
| 11 | Las cinco tareas del set de evaluación, todavía sin escribir | Medir cualquier cosa |
| 12 | ~~El v0 del gimnasio~~ **resuelto**: existe, escrito a mano y plano. Los estándares de ingeniería aplican al código nuevo y al que cada tarea toca (ADR-0012) | — |
| 13 | ~~Campos de `Configuration` y esquemas~~ **resuelto**: `agentic/shared/configuration.py` y `agentic/core/process/domain/schemas.py`, uno por rol implementado. Los roles que faltan agregan su esquema al entrar | — |
| 14 | Script que corre el set de cinco tareas y escribe el JSONL de resultados (sección 14). Sin costo: eso ya lo da Grafana | Medir |
| 15 | Versionado de **parámetros** de `Configuration` como label de las métricas; hoy solo van el SHA del sistema y la huella de prompts | Comparar corridas que cambian solo un parámetro |
| 16 | El camino como dato (`Route`, solo la ida, con la tabla de devoluciones aparte) y `Status` en cinco valores más `outcome`, ya modelados en ADR-0009. Hoy el ruteo es una función por nodo en `rules.py` | Etapa 2: se implementa al entrar QA Lead, el segundo par |

**Resueltos desde la versión anterior**: el stack y el runtime de agentes (LangGraph para el
flujo entre roles, Claude Agent SDK como harness de cada rol, autenticado con suscripción),
dónde vive el conocimiento de producto, y qué producto se usa de prueba.

---

## 13. Decisiones que fueron reemplazadas

Se anotan para no reintroducirlas por error.

| Decisión original | Estado actual |
|---|---|
| El orquestador se llama "Lead" | Renombrado a Project Manager (PjM) |
| Lead marca los flags de alcance | Los marca Product Analyst, los valida PM |
| UX, UI y TW aprueban al cierre | Solo PM valida producto |
| Lead integra el código | Un Developer integra, bajo revisión de Tech Lead |
| Developer hace el análisis técnico y pingponea con otro Developer | Lo hace Tech Lead con un segundo Tech Lead efímero |
| Developer consulta directo a PM o QA | Consulta a Tech Lead, que escala |
| Los pensadores de producto trabajan en paralelo | Secuencial, por el riesgo de rediseño |
| UX entra en bugs de flujo | Un bug de flujo es funcional, lo valida PM |
| PM y Data Analyst validan en paralelo | PM primero, Data Analyst después |
| Tech Writer queda bajo PM | Tech Writer es independiente |
| Cada rol recibe solo lo que produjo el rol previo | Cada rol declara la lista de secciones que lee |
| El pingpong del Tech Lead es con un igual | El efímero usa modelo más capaz y prompt de adversario |
| Los 17 roles se construyen todos y después se mide | Se construyen de a uno y cada uno se gana el lugar |
| Las definiciones acumuladas viven en `.agentic/producto/` | Conocimiento de producto en `docs/product/`, con los docs humanos (ADR-0006) |
| El documento de tarea es "fuente de verdad" y no guarda historial | Es el registro crudo del trabajo: guarda entregas pisadas y corridas por rol. Lo vigente se destila |
| LangSmith para tracing | Langfuse local, alimentado por el OpenTelemetry nativo de Claude Code |
| Los spans del sistema llevan solo id, título y rol; el JSONL de evaluación guarda el costo | Los spans llevan costo, modelo, versión y resultado; las métricas del sistema van a Grafana Cloud con la versión del sistema como label (ADR-0008) |
| Paquete plano, un módulo por tema, con las reglas dentro de los nodos del grafo | Hexagonal: dominio, aplicación con ports, adaptadores; LangGraph es un adaptador (ADR-0007) |
| El par es un validador y su pensador | Quien devuelve y quien recibe; QA→PM y QA→Tech Lead cuentan para el límite (ADR-0009) |
| El cierre requiere siempre las aprobaciones del tipo | Aprobado o descartado: el bug de "comportamiento esperado" cierra descartado, sin aprobación (ADR-0009) |
| Un solo `domain/` hasta que duela, y un solo `CONTEXT.md` | Tres módulos bajo `agentic/core/` (`process`, `knowledge`, `measurement`), cada uno hexagonal y con su glosario; `process` no importa a los otros (ADR-0010) |
| Product Analyst escribe la definición en un tiro y PM la valida en otro | Product Analyst entrevista a PM con `grilling`; lo que PM no puede decidir va al humano como pregunta (ADR-0014) |
| La definición arranca por el comportamiento | Arranca por el job to be done: primera ronda de la entrevista, primera sección de la definición, primera sección de cada capacidad en `docs/product/`; PM devuelve lo que no sirve al job |
| El pingpong del Tech Lead no tiene forma fija | Es una entrevista del efímero a Tech Lead, con tope de cuatro rondas, y de ahí salen los ADRs y las subtareas (ADR-0014) |
| Los roles no tienen skills; solo prompt y esquema | Skills de Matt Pocock fijadas como submódulo; solo las invocables por un agente, dos obligatorias: `tdd` y `code-review` (ADR-0013) |
| La corrida termina cuando el último rol entrega | Termina en un pull request del producto, lista para revisión; el issue recibe el link (ADR-0011) |

---

## 14. Arquitectura del código

Sale de una sesión de interrogatorio contra `langchain-ai/open_deep_research` (ODR), un
sistema de investigación multi-agente sobre LangGraph. Se adoptan sus patrones de grafo, no
su setup: ODR no tiene tests unitarios, ni CI, ni ADRs.

### Paquete

Tres módulos bajo `agentic/core/`, cada uno hexagonal por dentro (ADR-0007) y con
dirección fija entre ellos (ADR-0010), las dos reglas vigiladas por un test sobre los
imports:

| Módulo | Contenido | Importa |
|---|---|---|
| `shared/` | `configuration.py` | Nada del proyecto |
| `core/process/` | `domain/`: `roles.py` (`Role` con tools, reglas de permiso y skills; `Pair`; `Interview`; `describe_flags`) y `prompts/`, `task.py` (estado, subtareas y reducers), `document.py`, `schemas.py`, `rules.py` (límite por par, versionado por sección, subtareas listas, ruteo). `application/`: `ports.py` (`RoleRunner`, `TaskDocuments`, `ProductKnowledge`, `Prompts`, `FlagVocabulary`, `Inbox`, `Workspace`, `Tracker`), `interview` (rondas entre dos roles, ADR-0014) y un caso de uso por nodo: `classify`, `define`, `validate_definition`, `plan`, `develop`, `review`, `deliver`, `escalate`, sobre `run_role`. `adapters/`: `claude_sdk/` (harness con plugin de skills y jaula por rol, `Printer`), `filesystem/` (documentos y overlay de prompts), `git/` (rama de tarea y árboles de trabajo, ADR-0011), `github/` (bandeja y escritura: PR, comentarios, etiquetas), `langgraph/` (el grafo: nodos, ruteo, `Send` por subtarea, abre y cierra los spans; mañana `interrupt()` y checkpointer) y `telemetry.py`, el port por el que entra el tracing | `shared` |
| `core/knowledge/` | `domain/`: `knowledge.py` (cómo se muestra, cómo se edita el glosario, qué debe traer una aprobación), `flags.py` (el vocabulario). `adapters/filesystem/`: implementa `ProductKnowledge` y `FlagVocabulary` sobre el repo del producto | `shared`, `process` |
| `core/measurement/` | `domain/`: `metrics.py` (retrabajo, devoluciones, huella de prompts). `adapters/otel/`: spans y métricas del sistema, implementa `Telemetry` | `shared`, `process` |
| raíz | `composition.py` arma los ports reales e inyecta la telemetría; `cli.py` es el adaptador de entrada | Todo |

Dentro de cada módulo: el dominio no importa nada del proyecto salvo `shared` y otros
dominios, la aplicación solo dominios y aplicaciones, y solo los adaptadores ven
adaptadores, nunca los de otro módulo. `process` es el dominio central: define en sus
ports lo que necesita del conocimiento y del tracing, y `knowledge` y `measurement` lo
implementan sin que `process` los conozca.

LangGraph es un adaptador: los casos de uso son funciones async puras que reciben
`Configuration` y `Ports`, y el grafo solo cablea. El escalado es un dato del patch que
devuelve el caso de uso; pausar sobre él es del adaptador. Los tests espejan el árbol:
`tests/core/<módulo>/<capa>/`, `tests/shared/`, `tests/llm/`.

El dominio es funcional (ADR-0009): no hay clase agregado, las invariantes son funciones
sobre `TaskState`. Los términos del glosario que el código modela tienen un solo nombre en
inglés, y el mapeo vive en `tests/test_language.py`, no en los `CONTEXT.md`. El camino
todavía es ruteo por nodo en `rules.py`; pasa a `Route` con QA Lead (pendiente 16).

### Configuración

Una clase `Configuration` Pydantic con `from_sources(overrides, env)`. Precedencia: variable de
entorno > `config["configurable"]` > default. Cada nodo la lee al inicio. Campos iniciales:
modelo de pensadores, modelo de validadores, máximo de devoluciones por par (2), reintentos
por fallo de rol (1), ventana de evaluación (10), directorio de contexto de producto. Cada
`Role` declara `kind` (pensador o validador) y `Configuration` mapea kind → modelo, con
override por rol. Sin metadata de UI: no se usa Open Agent Platform. `product_dir` es obligatorio: sin él
la CLI falla, porque el sistema no tiene un lugar sano donde escribir por defecto (ADR-0002).

### Prompts

Un `agentic/core/process/domain/prompts/<rol>.md` por rol, con placeholders para fecha y límites: el
prompt es parte de la definición del rol, como `Role.reads`. Convenciones
tomadas de ODR: secciones con tags XML (`<Task>`, `<Hard Limits>`, `<Show Your Thinking>`),
presupuestos numéricos explícitos y criterios de parada escritos. El validador recibe en el
prompt las **devoluciones restantes de su par**: un validador que sabe que es su última
devolución cierra el motivo para el humano en vez de pedir otro ajuste. El corte sigue siendo
de PjM, en código, nunca del modelo. Overlay y versionado: ADR-0005.

### Salida estructurada

Lo que PjM procesa mecánicamente (clasificación con confianza, veredicto con motivo, flags,
mapa de subtareas) se define como esquema Pydantic y se pasa al SDK como `json_schema`. El
Markdown de la sección sigue siendo texto libre, pero viaja como campo `markdown` del mismo
esquema: el SDK termina el turno en cuanto el modelo llama a la herramienta de salida
estructurada, así que una respuesta de texto aparte nunca llega. Una salida estructurada
ausente o inválida, o un `markdown` vacío, es fallo de rol, no un diccionario vacío.

### Estado del grafo

- `TaskInput` mínimo como `input` schema: id, título, descripción.
- Contadores por par con un reducer de override: resetear el par cuando el humano responde es
  un update explícito, no un dict copiado a mano. La clave la deriva `Pair.key`; la del
  par Tech Lead→Developer incluye el id de subtarea.
- `subtasks` es un mapa por id con reducer de override por clave: cada Developer patchea la
  suya. `branch`, `pull_request` y `questions_for_human` son campos planos. La conversación
  interna de cada Developer no entra al estado: queda en su sesión (ADR-0004).
- `thread_id` es el id de la tarea. Checkpointer SqliteSaver en el producto (ADR-0003).

### Pausa y reanudación

Escalado con `interrupt()`, reanudación con `Command(resume=decisión)` (ADR-0003). La CLI
`agentic resume` busca tareas pausadas cuyo issue ya no tiene `needs-human`, lee el último
comentario y reanuda. En la etapa 5 la reemplaza un poller con reloj propio, que es el mismo
mecanismo que necesitan los roles proactivos.

### Fallo de rol

Mecánicamente es una de cuatro cosas: excepción del SDK, `max_turns` agotado sin resultado,
salida estructurada ausente o inválida, texto vacío. El port `RoleRunner` devuelve la salida
cruda o lanza `AttemptFailed`; la validación y el reintento viven en `application.run_role`:
atrapa, reintenta una vez con el error agregado al prompt, y si vuelve a fallar lanza
`RoleFailure`, que el caso de uso convierte en escalado. Se descartó `RetryPolicy` de LangGraph porque reintenta el
nodo sin pasarle el error al modelo. Todo fallo, reintentado o no, queda en el estado para las
métricas de PjM.

### Fan-out

Developers en paralelo con `Send` (ADR-0004): la arista condicional de `plan` y de
`review` devuelve un `Send("develop", estado + subtask_id)` por cada subtarea lista (sin
dependencias pendientes). Cada uno corre en su propio árbol de trabajo y rama (ADR-0011);
`status`, `current_role` y `escalated` llevan un reducer "último gana" para que dos
Developers en el mismo paso no choquen. La integración es una subtarea más, del integrador,
sobre la rama de tarea.

### Entrevistas

Un caso de uso `interview(estado, Interview)` corre las rondas (ADR-0014): cada lado es una
sesión del SDK reanudada con `resume`, el entrevistador entrega `InterviewTurn` (`done`,
`markdown`), el entrevistado `Answers` (`markdown`, `questions_for_human`). La transcripción
es una sección `Interview: A→B` del documento y el autor escribe después reanudando su
propia sesión. Preguntas para el humano al final de la entrevista = escalado.

La entrevista de solución tiene **consultado** (glosario: Consulta, ADR-0014 adenda): el
Tech Lead entrevistado devuelve `TechAnswers` (`markdown`, `questions_for_pm`) y nunca
pregunta al humano. Con preguntas, PM responde en la misma ronda (`Consultation`:
`markdown`, `questions_for_human`, `definitions`, `glossary`) retomando su sesión de
validación; la respuesta entra en la transcripción y en el prompt siguiente del
entrevistador, la sección `PM` se entrega de nuevo con `### Clarifications` (la anterior
queda como `PM (vN)`) y lo que cambia el conocimiento se destila otra vez.

### Observabilidad

Cuatro capas, de más inmediata a más durable. `agentic run --verbose` vuelca a stderr el
stream del SDK (texto, thinking si el rol lo tiene, tool calls) con prefijo de rol: es la
única forma de ver generar. Claude Code exporta OpenTelemetry nativo; un collector en
`observability/collector/` lo reenvía a un Langfuse local, porque el exportador de Claude
Code no puede mandar un header `Authorization: Basic` entero. El grafo abre un span por
tarea y uno por rol y pasa `TRACEPARENT` al subproceso, así una tarea es un trace con los
roles anidados; al cerrar cada span el adaptador le estampa lo que el caso de uso decidió
(costo, modelo, versión de la entrega, resultado) leyendo el patch, sin que la aplicación
lo sepa. Las métricas del sistema (glosario de Medición: costo por tarea, retrabajo,
duración, devoluciones, escalados) salen del mismo adaptador y el collector las manda,
junto con los spans del sistema y sin los de Claude Code, a Grafana Cloud, con la versión
del sistema y la huella de prompts como labels (ADR-0008, `observability/README.md`). Por
último, el `session_id` de cada corrida queda en el documento de tarea: `claude -r <id>` en
el repo del producto reabre la sesión del rol.

### Tooling

`pyproject.toml` con `uv.lock`, ruff con `E F I UP` (sin `D`: docstrings obligatorios en 17
roles es ruido ahora), pytest, GitHub Actions corriendo ruff y pytest en cada PR,
`langgraph.json` para `langgraph dev` y Studio, `.env.example`. El grafo se exporta sin
compilar desde `agentic.composition`, el composition root; la CLI lo compila con el checkpointer.

### Evaluación

Langfuse y Grafana no puntúan. El puntaje sigue siendo el de la sección 9: binario, humano.
Un script corre el set de cinco tareas y escribe un JSONL versionado en este repo con lo que
ninguna métrica puede dar: hash de prompts, `Configuration` completa, resultado binario y
nota. El costo ya está en Grafana bajo la misma huella de prompts. Además, evals de nodo
intermedio como tests de pytest ("PjM clasifica estas descripciones así", "PM devuelve esta
definición incompleta"), marcados `llm` y **excluidos de CI**: el SDK se autentica con la
suscripción, que GitHub Actions no tiene. CI corre solo tests puros: parser del documento,
reducers, configuración, ruteo con salidas grabadas.

### Descartado de ODR

| Práctica de ODR | Por qué no |
|---|---|
| `asyncio.gather` dentro de un nodo para el fan-out | Esconde a los Developers de PjM (ADR-0004) |
| Terminar el turno para esperar al humano | La pausa puede caer en cualquier par (ADR-0003) |
| Jueces LLM como puntaje | El puntaje es binario y humano (sección 9) |
| Metadata `x_oap_ui_config` en la configuración | No hay UI de plataforma |
| `think_tool` como herramienta de reflexión | El harness de cada rol ya razona |
| Reglas ruff `D` de docstrings obligatorios | Ruido en esta etapa |
| Carpeta `legacy/` ejecutable | No hay versiones anteriores que conservar |
| Ausencia de tests y CI | No es una práctica, es un hueco |
