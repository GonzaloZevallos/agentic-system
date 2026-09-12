# Sistema agéntico de desarrollo

Estado consolidado del diseño. Reemplaza a la conversación de origen
(`docs/conversacion-origen.md`), que se conserva solo como historial.

Convención de nombres fijada: **PjM** = Project Manager (ex "Lead"), **PM** = Product Manager.

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
2. La pingponea con un **segundo Tech Lead efímero** hasta validarla.
3. Revisa y ajusta el plan de tests funcionales.
4. Decide si la 2ª pasada necesita QA Seguridad y/o QA Performance.
5. Parte en subtareas, marca las paralelizables, prioriza por valor entregado rápido. Puede
   consultar el orden con PM.
6. Informa a PjM: plan de subtareas, Developers asignados, QAs convocados.

**Ejecución**

- Despliega un Developer por subtarea.
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

| Fuente | Rol | Reloj | Genera | Aprobación humana |
|---|---|---|---|---|
| Humano | — | — | cualquier tipo | — |
| Producción | SRE / Ops | propio | bugs | No, entran solos |
| Repositorio | Tech Lead proactivo | propio | platform enhancements | Sí |
| Competidores | PM | propio | features | Sí |
| Métricas | Data Analyst | propio | insumo para PM | — |
| Feedback de usuarios | Customer Insights | propio | insumo para PM | — |

Cada rol proactivo tiene su **propio reloj**, no hay un ciclo central que los despierte.

Cadena de producto proactiva: Data Analyst y Customer Insights dejan síntesis. PM, en su
ciclo, las lee junto a su análisis de competidores y propone features. El humano aprueba, y
recién ahí entran a PjM.

---

## 6. Transporte de información

**Un documento único por tarea.** Vive en disco o en un store. Cada rol lee lo que necesita
y escribe su sección. Se eligió sobre mensajes directos entre agentes porque da trazabilidad
total a PjM sin esfuerzo extra, permite reintentos, y cualquier rol puede releer sin pedirle
nada a nadie.

**Formato híbrido.** Markdown para el contenido que producen los agentes, porque los modelos
lo leen y escriben bien y es auditable a mano. Frontmatter YAML para lo que PjM procesa
mecánicamente.

**Contexto por rol.** Cada rol recibe por defecto **solo lo que produjo el rol previo**. Es
lo más barato en tokens y evita que el agente se distraiga. Para que no se pierda
información, cada rol escribe su salida pensando en el siguiente: incluye lo que ese rol
necesita, no todo lo que sabe. El documento único queda como respaldo consultable.

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

## 8. Pendientes

| # | Pendiente | Bloquea |
|---|---|---|
| 1 | Formato concreto de la definición de comportamiento | Implementar Product Analyst y PM |
| 2 | Fuente de verdad de producto: qué es, cómo se acumula, quién la mantiene | Product Analyst en bugs, PM |
| 3 | ADRs: quién los escribe, quién los valida, cómo los lee Tech Lead | Tech Lead |
| 4 | Frecuencia de cada reloj proactivo | SRE/Ops, PM, Data Analyst, Customer Insights, Tech Lead proactivo |
| 5 | Señales y umbrales de SRE/Ops para decidir que algo es bug, más deduplicación | SRE/Ops |
| 6 | Entradas y salidas exactas de cada rol, más allá del esqueleto de secciones | Implementar |
| 7 | UX Lead, Design Lead y Tech Writer en detalle | Menor |
| 8 | Criterio de clasificación de PjM y qué hace si duda | PjM |
| 9 | Cuántos Tech Leads existen en el sistema | Sin responder en la conversación |
| 10 | Versionado de prompts y parámetros | Mejora del proceso de PjM |
| 11 | Nada de esto está implementado: falta elegir stack y runtime de agentes | Todo |

---

## 9. Decisiones que fueron reemplazadas

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
