# Sistema agéntico de desarrollo

Estado consolidado del diseño. Reemplaza a la conversación de origen
(`docs/conversacion-origen.md`), que se conserva solo como historial.

Convención de nombres fijada: **PjM** = Project Manager (ex "Lead"), **PM** = Product Manager.

Las secciones 8 a 11 salieron de una sesión de interrogatorio al diseño y son las que lo
aterrizan: qué producto, cómo se mide, con qué modelos y en qué orden se construye. Si algo
en las secciones 1 a 7 las contradice, mandan las nuevas.

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

- El conteo es **por par de roles**, no global.
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

UX Lead, Design Lead y Tech Writer **no aprueban al cierre**. Esa regla existió y fue
reemplazada: PM es la única fuente de verdad de producto porque construyó la definición
junto con ellos. PM puede consultar a los leads, pero la aprobación es suya. Si el
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
- Requiere versionado de prompts y parámetros para poder revertir.

### PM — Product Manager

- Valida la definición y los flags que redacta Product Analyst.
- Delega en orden a UX Lead, Design Lead y Tech Writer según los flags.
- Consolida la definición única. Es su dueño y la fuente de verdad de producto:
  comportamiento, flujo, diseño y textos.
- Valida la implementación final de producto. Puede consultar a UX Lead y Design Lead.
- Valida que la feature salga medida.
- Evalúa el aporte al negocio de cada funcionalidad antes de aprobar.
- Analiza competidores.
- **Modo proactivo**: con reloj propio, cruza su análisis de competidores con las síntesis
  de Data Analyst y Customer Insights, y propone features nuevas. Pasan por el humano antes
  de entrar a PjM.

### Product Analyst

**Entradas**: la tarea, las definiciones anteriores, acceso al producto real para explorarlo.

**En feature**

- Analiza competidores para tener criterio sobre el comportamiento.
- Redacta la definición de comportamiento.
- Define qué métricas de negocio aporta la funcionalidad y cómo se obtienen.
- Marca los flags. Los flags no son una lista fija: PM define por tarea cuáles aplican.
- Consulta a PM si la tarea es ambigua.

**En bug**

1. Reproduce en el producto real.
2. Compara contra las definiciones anteriores.
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
rol lee lo que necesita y escribe su sección. Se eligió sobre mensajes directos entre agentes
porque da trazabilidad total a PjM sin esfuerzo extra, permite reintentos, y cualquier rol
puede releer sin pedirle nada a nadie.

**GitHub issues es la bandeja de entrada, no el documento.** Las tareas entran como issues
del repo del producto. El sistema toma solo los que llevan la etiqueta `agentic`, que pone
un humano: es el interruptor que evita que el sistema se meta en cualquier issue suelto.

PjM sincroniza hacia el issue lo que le sirve a un humano: la etiqueta de clasificación, la
etiqueta de estado, un comentario de resumen al cerrar, y el cierre. El detalle de trabajo
no va al issue.

Se eligió el archivo sobre el issue como documento porque cada rol lee **secciones por
nombre**, y parsear eso de comentarios de GitHub es frágil y cuesta una llamada de API por
lectura. El archivo se grepea, se versiona y entra en el checkpoint. La contra asumida es
que hay sincronización que se puede desfasar; el issue es siempre el lado derivado.

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
timestamps por etapa, escalados, subtareas.

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

Cada sección cierra con un bloque fijo **"Para el siguiente rol"**. Eso es lo que lee el
siguiente por defecto.

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
| Definiciones acumuladas (fuente de verdad de producto) | Caminos y ruteo |
| ADRs | Conteo de devoluciones y control de loops |
| Acceso a métricas | Documento único y sus secciones |

**Forma de la costura**: un **directorio de contexto de producto** que el sistema recibe
como parámetro. Se eligió sobre un adaptador con funciones porque todavía no se sabe qué
varía de verdad entre productos, y no se puede generalizar sobre un solo ejemplo. El
adaptador se extrae cuando aparezca el segundo producto.

**Repositorios separados.** El sistema agéntico vive en su propio repo. El producto vive en
otro. El sistema nunca escribe dentro de su propio repo cuando trabaja: todo lo que produce
(documentos de tarea, definiciones acumuladas, ADRs, código) se escribe en el repo del
producto. Si no fuera así, la historia de un producto ensuciaría el repo del sistema y un
segundo producto se mezclaría con el primero.

Forma del repo del producto:

| Ruta | Contenido | Quién escribe |
|---|---|---|
| código de la app | El producto | Developer |
| `docs/adr/` | ADRs numerados | Humano el primero, después Tech Lead |
| `.agentic/tasks/` | Documento único por tarea | Los roles, sección por sección |
| `.agentic/producto/` | Definiciones acumuladas, la fuente de verdad de producto | PM |

Los ADRs viven con el código, no bajo `.agentic/`, porque son del producto y los lee
cualquiera. Los valida el Tech Lead efímero en el pingpong, y el humano los ve igual al
revisar el diff antes de mergear.

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
definiciones acumuladas. Las primeras tareas del sistema son todas features.

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
| 1 | Formato concreto de la definición de comportamiento | Implementar Product Analyst y PM |
| 2 | Fuente de verdad de producto: vive en `.agentic/producto/` y la mantiene PM. Falta el formato y cómo se acumula | Product Analyst en bugs, PM |
| 3 | ~~ADRs~~ **resuelto**: `docs/adr/`, primero el humano, después Tech Lead, valida el efímero | — |
| 4 | Frecuencia de cada reloj proactivo | Etapa 5 |
| 5 | Señales y umbrales de SRE/Ops para decidir que algo es bug, más deduplicación | SRE/Ops, etapa 5 |
| 6 | Lista de secciones que lee cada rol, una por rol | Implementar |
| 7 | UX Lead, Design Lead y Tech Writer en detalle | Etapa 4 |
| 8 | Criterio de clasificación de PjM y qué hace si duda | PjM |
| 9 | Cuántos Tech Leads existen en el sistema | Sin responder |
| 10 | Versionado de prompts y parámetros | Mejora del proceso de PjM y línea de base del set |
| 11 | Las cinco tareas del set de evaluación, todavía sin escribir | Medir cualquier cosa |
| 12 | El v0 del gimnasio, todavía sin construir | Etapa 1 en adelante |

**Resueltos desde la versión anterior**: el stack y el runtime de agentes (LangGraph para el
flujo entre roles, Claude Agent SDK como harness de cada rol, autenticado con suscripción),
dónde vive la fuente de verdad de producto, y qué producto se usa de prueba.

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
