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
  → Product Analyst: competidores, PRD, métricas + dashboard, flags
  → PM valida PRD y flags
  → Data Analyst valida métricas
  → [si toca UI]    UX Designer define flujos    → UX Lead valida
  → [si toca UI]    UI Designer diseña sobre el flujo validado → Design Lead valida
  → [si toca textos] Tech Writer escribe sobre flujo y diseño validados
  → PM consolida el PRD
  → QA Funcional (1ª pasada): observa el PRD, casos borde, casos de prueba
  → Tech Lead: análisis, pingpong, ajuste del plan de tests, subtareas
  → Developers en paralelo, cada uno con su revisión de Tech Lead pegada atrás
  → entre olas, el sistema: integración incremental y la ola siguiente
  → Developer integrador, bajo revisión de Tech Lead
  → QA 2ª pasada (funcional + seguridad/performance si fueron convocados)
  → PM valida producto
  → PjM cierra
```

La etapa de definición es **estrictamente secuencial**. Se evaluó paralelizarla y se
descartó: el riesgo de que UI diseñe sobre un flujo que UX Lead después cambia pesa más
que la velocidad ganada.

**Implementado (etapa 1)**: PjM, Product Analyst con su entrevista a PM, PM, Tech Lead con
el efímero, Developers y la revisión de Tech Lead, hasta el pull request. Data Analyst,
UX/UI, Tech Writer, QA y la validación final de PM esperan a sus etapas (§11).

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
no de UX: lo valida PM solo, porque las decisiones de UX ya viven en el PRD que PM
posee. **UX no participa en bugs.**

Los bugs no llevan métricas ni dashboard.

**Implementado (#65)**: PjM, Product Analyst, PM y desde ahí el mismo camino técnico que
feature. Product Analyst no entrevista: reproduce en el checkout de la tarea, leyendo el
código y corriendo los chequeos que el producto declara, compara contra la definición
vigente en `docs/product/` y dictamina: bug, comportamiento esperado o sin definición. Sin
definición previa no hay consulta (§3) todavía: Product Analyst entrega el dictamen como
"sin definición" con las preguntas para PM en su sección, PM decide al validarlo, y sólo una
devolución de PM (reproducción que falta, definición mal leída) cuenta contra el par. Si PM
decide que es bug, escribe ahí la definición que faltaba, para que Tech Lead tenga spec.
Comportamiento esperado → la tarea termina **descartada**: el sistema comenta el motivo en
el issue, borra el worktree y el humano cierra el issue, como hoy mergea el pull request
(ADR-0011); no se escribe definición, porque una tarea descartada no entrega nada que la
lleve. Bug → el plan, con PM consultado en la entrevista de solución como en feature,
Developers, revisión y pull request. Sin flags, métricas ni dashboard. UI Designer y Design
Lead (etapa 4) y QA (etapa 2) esperan a sus etapas (§11).

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

**Implementado (#46)**: de PjM al plan sin pasar por el PRD. El Tech Lead efímero
entrevista a Tech Lead con el issue, el conocimiento de producto y el motivo de PjM como
único insumo; como no hay PM a quien consultar, lo que sólo una persona puede decidir va al
humano. Se espera que no haya nada: una pregunta de producto en este camino es señal de
que la tarea era una feature mal clasificada. Developers y revisión, iguales que en
feature. Mientras no hay QA cierra como el camino de feature, un pull request que el humano
mergea o no (ADR-0011): que el comportamiento no cambió lo verifica hoy la revisión de
Tech Lead contra el brief y el código previo, y la devolución QA→PjM→PM queda para cuando
QA entre (etapa 2, §11). Ningún nodo de este camino exige flags, métricas ni dashboard.

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
- Cada devolución queda en el historial con su par, su **categoría de devolución**, de una
  lista cerrada, y el motivo en texto (ADR-0016). La categoría es lo que hace computable
  "el mismo motivo"; el corte anticipado todavía no está construido.
- Acción al cortar: **escalar al humano, siempre**. PjM entrega la tarea, el historial de
  devoluciones y el motivo de cada una.
- Mientras espera, la tarea queda en pausa. PjM no re-enruta.
- Cuando el humano responde, la tarea vuelve a PjM, que re-enruta aplicando esa decisión,
  y el contador de ese par se resetea.
- Si el par que llega al límite es Tech Lead→Developer, se corta esa subtarea. El resto
  sigue, salvo lo que dependa de ella.

### Tope de tarea

El acumulado de una corrida tiene tope, y crece con el plan: **30 USD** de base hasta que
hay plan; después, base más **15 USD por subtarea**, con la integración contada, nunca por
encima del techo de **250 USD** (`AGENTIC_TASK_BUDGET_BASE_USD`,
`AGENTIC_TASK_BUDGET_PER_SUBTASK_USD`, `AGENTIC_TASK_MAX_BUDGET_USD`). Un plan tiene como
mucho 12 subtareas. Los números salen de la cuarta corrida de GH-12 y se recalibran tras la
primera corrida sin la fuga de MCP (issue #18).

Se mira antes de cada intento de cualquier rol; alcanzado, el sistema escala al humano con
el total gastado y el tope como motivo, y la tarea **pausa** (sección 14). El comentario del
issue trae lo gastado por fase y la línea `agentic resume --issue N --task-budget X`: subir
el tope es decisión del humano, y `--task-budget` lo sube para esa corrida, nunca lo baja: el
techo limita la fórmula, no al humano.
No es fallo de rol ni devolución y no cuenta para ningún límite. Como el tope por
intento, es red de seguridad, bien arriba de lo que cuesta una tarea sana, no presupuesto de
trabajo. En paralelo cada rama ve el total al momento del fan-out, así que el desborde
queda acotado por el tope por intento, y el escalado sale uno solo (ADR-0004, adendas).

### Cola de escalados

El humano revisa **en lote, una vez por día**. Los escalados se acumulan y no interrumpen.
Una tarea escalada queda pausada y se retoma desde el mismo punto cuando el humano responde,
con el contador de ese par reseteado.

La cola no se construye: es la **etiqueta `needs-human`** en el issue, más el motivo, las
preguntas y cómo reanudar en un comentario. Revisar es filtrar por esa etiqueta. Responder es
dejar la decisión en un comentario y correr `agentic resume --issue N`, que saca la etiqueta,
muestra qué va a reanudar y lo gastado, y sigue. La decisión entra al documento como sección
`Human`, que lee todo rol. Con preguntas abiertas o un par en su límite, `resume` no sigue sin
decisión: resetear el contador sin nada nuevo repite la misma devolución.

Esto exige estado durable del lado del sistema: el grafo se frena, el estado sobrevive al
proceso en un checkpointer, y la respuesta del humano lo reanuda donde estaba (ADR-0003).

### Fallo de rol

Distinto de una devolución. Es el rol que se rompe: JSON inválido, salida vacía, se le
acaban los turnos.

- **Un reintento**, pasándole el error como entrada. Si el intento fallido dejó sesión
  (el tope de costo o los turnos lo cortaron, la salida no validó), el reintento la
  continúa en vez de arrancar de cero: el modelo conserva lo que leyó e hizo (ADR-0004,
  adenda).
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
"comportamiento esperado" lo cierra PjM descartado, sin estas aprobaciones (sección 2). Hoy
el sistema lo comenta en el issue con el motivo y termina la corrida; cerrar el issue es
del humano (#65).

UX Lead, Design Lead y Tech Writer **no aprueban al cierre**. Esa regla existió y fue
reemplazada: PM es el único que valida producto porque construyó el PRD junto con
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
- Cantidad y motivo de devoluciones, por categoría (ADR-0016).
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

- Valida el PRD y los flags que redacta Product Analyst.
- Delega en orden a UX Lead, Design Lead y Tech Writer según los flags.
- Consolida el PRD. Es su dueño: comportamiento, flujo, diseño y textos.
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

**Entradas**: la tarea, el conocimiento de producto (`docs/product/`, `docs/adr/` y
`CONTEXT.md` del producto, completos), acceso al producto real para explorarlo.

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

**Salida**: PRD + flags → PM.

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

**Entradas**: PRD de PM, plan de tests funcionales de QA Lead, ADRs del proyecto.

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
- Revisa **cada subtarea** al entregarse y también **la integración**, con `code-review`
  (ADR-0013). Al entregarse quiere decir apenas entrega *ese* Developer: la revisión de una
  subtarea es la segunda mitad de la rama de su Developer, no espera al resto de la ola
  (ADR-0004, adenda quinta). La primera revisión va desde la base de la subtarea con el
  brief como spec; toda revisión después de una devolución es **incremental**: desde el
  head que ya revisó, con su propia lista para el Developer como spec (ADR-0015).
- **Verifica ejecutando** (ADR-0016): no escribe nada, pero corre los chequeos que el
  producto declara (`.agentic/stack.yaml`) sobre el test que respalda cada objeción de
  comportamiento, y entrega **hallazgos** con lugar,
  afirmación, evidencia (el comando y su salida, o la cita), severidad, alcance y
  categoría. Una objeción que no reprodujo es una observación. El veredicto no lo escribe:
  devuelve si y solo si hay un hallazgo bloqueante sobre la subtarea. Un chequeo entero no
  lo repite: lo corrió la puerta de entrega y está en la sección del Developer.
- Si rechaza, vuelve **al mismo Developer**. Nunca reasigna a otro, y la vuelta pasa por el
  fan-in: la ola siguiente arranca sabiendo lo que vio toda la anterior.
- Designa un Developer integrador y revisa la integración final. El sistema ya trajo cada
  subtarea aprobada a la rama de tarea apenas se aprobó; al integrador le queda lo que el
  merge no pudo resolver solo, más el trabajo de integración que el plan declare.
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
bash y tests, **sin aprobación humana por comando** (Tech Lead corre tests al revisar, pero
no escribe ni corre otra cosa: eso no es ejecutar). La jaula es el directorio del producto,
más el tope de costo por intento que tiene todo rol: red de seguridad contra un rol
desbocado, bien arriba de lo que cuesta una subtarea (10 USD por default,
`AGENTIC_MAX_BUDGET_USD`), no un presupuesto de trabajo (ADR-0004, adendas). Por encima,
el tope de tarea (sección 3) frena el acumulado de toda la corrida. Pedir aprobación
comando por comando rompe la revisión en lote: el sistema quedaría congelado esperando al
humano en cada paso. Corre con
el entorno del sistema limpio de su propia toolchain (`VIRTUAL_ENV`, `UV_*`, su `.venv/bin`
en `PATH`), así que `pytest` y `uv run` en el producto resuelven al del producto. Sus commits firman
como el sistema, con la identidad que le llega en el entorno, y tres reglas se le niegan aun
sin aprobación: `git config`, `git push` y `git worktree` (ADR-0011, adenda). Encima corre
dentro de hooks del SDK que ven el comando parseado, no un prefijo: niegan esos tres
también detrás de `git -C` o `sh -c`, `--git-dir`, `commit --no-verify`, cambiar de rama,
editar dentro de `.git/` o fuera de su checkout, y agregar `# type: ignore` o `# noqa`
(ADR-0011, adenda segunda). La jaula incluye el entorno: un worktree no tiene uno propio y
una herramienta que busca entorno sube hasta el del producto, así que el sistema prepara
cada checkout cuando nace, con los comandos que el producto declara en `setup`, antes de
que entre ningún rol. Sin eso, el `pytest` de un Developer importa el código de la rama
que escribió ese entorno último, y un `uv pip install -e .` adentro de un worktree
repunta el de todos (ADR-0011, adenda tercera). Instalar una dependencia nueva en su
propio entorno sí es trabajo suyo.

**Entrega**. Una entrega es código commiteado en un árbol limpio con los chequeos del
producto en verde. La **puerta de entrega** lo mira cuando el Developer entrega, dentro de
su turno: si el HEAD no se movió, si queda algo sin commitear o si un chequeo falla,
rechaza la entrega con el motivo y el modelo vuelve a entregar. Rechaza como mucho dos
veces por corrida, reintento incluido; después deja pasar, y el resultado de los chequeos abre la sección del Developer,
escrito por el sistema. En la corrida 4 un fix quedó en el árbol de trabajo y la suite pasó
contra código que la rama no tenía. El commit y el árbol limpio se chequean otra vez al
terminar: faltar es fallo de rol y se reintenta con el error como entrada.

### QA Lead y los tres QA

**1ª pasada, antes de desarrollar.** Solo QA Funcional, coordinado por QA Lead.

- Revisa claridad y completitud del PRD.
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

Consecuencia asumida: seguridad y performance se evalúan solo sobre código, nunca sobre el
PRD. Es un trade-off para arrancar. Si PjM ve muchos rechazos de seguridad tardíos,
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
documento que lee. No recibe solo lo del rol previo: Tech Lead lee el PRD de PM y los
casos de QA, no el resumen del resumen.

La lista es declarativa y no la decide el modelo en tiempo de ejecución. Así PjM la puede
registrar, y cuando un rol falla se puede ver si le faltaba información.

Cada rol igual cierra con un bloque **"Para el siguiente rol"**, que sigue siendo útil como
handoff explícito, pero ya no es lo único que viaja.

**Excepción: el Developer y el revisor de una subtarea no leen las ajenas.** Cada uno
declara `Tech Lead` entera en su lista, pero lo que arma el prompt (`run_role.context_for`)
recorta esa sección a su propia subtarea: bajo `### Subtasks` recibe su `brief` propio
(`title`, `brief`, `seams`, `depends_on`, tomados de `state["subtasks"]`, nunca de un
`#### <id>` de ese Markdown) y un índice de una línea con las demás, solo id y título. El
resto de la sección —`### Solution`, `### Standards`, `### Invariants`, `### Integration`,
`### For the next role`— llega igual, byte a byte. Sin subtarea propia (el integrador, o
un id que el plan no tiene) recibe la sección entera, como hoy (issue #71). `Tech Lead:
findings` nunca se recorta: cruza lo que a cada subtarea le importa de las demás.

### Esqueleto del documento

**Frontmatter, mantenido por PjM**: id, tipo, estado, rol actual, flags, contadores por par,
timestamps por etapa, escalados, subtareas (con el head que revisó el último pase), hash de
prompts, corridas por rol (`runs`: rol, sección, `session_id`, modelo, costo, costo por
modelo con los subagentes, turnos) y fallos con su texto.

**Secciones**

| Sección | Contenido |
|---|---|
| Product Analyst | Competidores, definición de comportamiento, métricas y dashboard, flags propuestos. En bug: reproducción, comparación, dictamen |
| PM | Validación, ajustes, decisiones ante consultas, PRD |
| Data Analyst | Validación de métricas |
| UX Designer / UX Lead | Flujos y validación |
| UI Designer / Design Lead | Visual y validación |
| Tech Writer | Textos |
| QA Lead / Funcional (1ª) | Observaciones al PRD, casos borde, casos de prueba |
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

## 8. Los dos productos y la costura

El sistema es **agnóstico al producto**. Lo que cambia por producto entra como parámetro;
lo que no cambia es el proceso. Hay **dos productos**: el SaaS de gimnasios, que es el de
prueba y la base del set de evaluación de §9, y el propio sistema, que desde ADR-0017 se
desarrolla a sí mismo.

| Del producto (parámetro) | Del sistema (fijo) |
|---|---|
| Repositorio | Roles y sus prompts |
| Conocimiento de producto | Caminos y ruteo |
| ADRs | Conteo de devoluciones y control de loops |
| Acceso a métricas | Documento único y sus secciones |

**Forma de la costura**: un **directorio de contexto de producto** que el sistema recibe
como parámetro. Se eligió sobre un adaptador con funciones porque todavía no se sabe qué
varía de verdad entre productos, y no se puede generalizar sobre un solo ejemplo. El
adaptador se extrae cuando aparezca el segundo producto, que ya apareció: es el sistema
(ADR-0017). Los ports de la arquitectura
hexagonal (ADR-0007) no son ese adaptador: los define lo que el sistema necesita, tienen
una sola implementación sobre este directorio, y este sigue siendo el único parámetro.

**Repositorios separados** (ADR-0002, adendas). Cada producto vive en su repo, y la regla
no es que el sistema no escriba en el suyo sino que **nunca escribe en el checkout desde el
que corre**. La corrida la ejecuta `stage0`, un checkout aparte fijado a un sha; lo que se
edita y lo que se ejecuta son dos copias. Lo que hay que congelar durante una corrida es el
código que la ejecuta, y eso es un checkout, no un repositorio.

Se dice sobre el checkout y **se cobra sobre el repositorio** (ADR-0002, segunda adenda): dos
checkouts de un mismo repo comparten `.git`, así que la rama de tarea y los worktrees de un
producto que fuera un worktree de stage0 nacerían adentro del repo que ejecuta. Por eso
stage0 es un clon, que sí es otro repositorio, y por eso el chequeo compara el
`--git-common-dir` de los dos lados.

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
| `.agentic/stack.yaml` | Lo que el producto declara de sí mismo: `product`, el nombre con que se etiqueta cada corrida suya (#47); `checks`, los comandos con que declara que su código está sano; y `setup`, opcional, lo que prepara un checkout recién creado. Obligatorio: sin él el sistema no arranca (ADR-0016, adendas; ADR-0011, adenda tercera) | Humano |
| `.agentic/state.sqlite` | Checkpointer del grafo, fuera del control de versiones (ADR-0003) | El sistema |
| `.agentic/worktrees/` | Un worktree por subtarea, fuera del control de versiones (ADR-0011) | El sistema |
| rama `agentic/<tarea>` | La rama de tarea: entrevistas, ADRs, código integrado. Termina en un pull request (ADR-0011) | Los roles |

### El sistema como segundo producto

Auto-hospedado: los artefactos de una corrida sobre el sistema caen en **este mismo repo**,
con la forma de arriba y cuatro diferencias (ADR-0017):

| | El gimnasio | El sistema |
|---|---|---|
| Quién ejecuta | Cualquier checkout | Sólo `stage0`, fijado a un sha: `scripts/stage0.sh`, y `preflight` rechaza el resto (§14) |
| Tipo de tarea | feature, bug o platform enhancement | Siempre **platform enhancement** (#46): sin PM, sin flags, sin métricas de negocio |
| Quién es el usuario | El dueño del gimnasio | El humano que escribe el issue, y el issue es el PRD |
| Set de evaluación | Es la base de §9 | No puntúa: no tiene métricas de negocio que discutir |

El glosario del producto, acá, es multi-contexto: `CONTEXT-MAP.md` y un `CONTEXT.md` por
módulo bajo `agentic/core/`, no un archivo en la raíz. El sistema lo sabe leer: cuando el
producto tiene mapa, un rol recibe el mapa primero (sus *Relationships* dicen qué contexto
conoce a cuál, y eso no está en ningún glosario) y después cada `CONTEXT.md` que enlaza. Al
escribir, **un término pertenece a un contexto**: `GlossaryTerm` lo declara y una destilación
escribe en tantos glosarios como módulos tocó la tarea. Y una reescritura conserva las líneas
de la entrada que no le pertenecen, `_Campo_` entre ellas: el que escribe una definición no
es dueño de todo lo que la entrada carga (ADR-0017, ADR-0018).

El conocimiento de producto (definiciones, ADRs, glosario) vive con los docs humanos, no
bajo `.agentic/`, porque es del producto y lo lee cualquiera; `.agentic/` es maquinaria del
sistema (ADR-0006). Los tres llegan enteros en el prompt de todo rol que lee conocimiento,
ADRs incluidos: un rol sin tools no tiene otra forma de saber qué decidió un ADR, y para PM
un ADR cierra la pregunta, nunca va al humano (ADR-0006, adenda segunda). Los ADRs los
valida el Tech Lead efímero en el pingpong, y el humano ve todo el conocimiento igual al
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

**Línea de base.** El set entero se re-corre solo en hitos: un rol nuevo, un cambio de
estructura de revisión, un cambio de modelo por rol. Un cambio de prompt o de parámetro
corre solo la tarea de humo que la declaración del set nombra (`agentic eval run`,
sección 14) — el puntaje binario de cinco tareas no distingue un ajuste de prompt de
una tarea más fácil, pero sí distingue un rol o una estructura, y correr las cinco a
cada ajuste de prompt es lo que el costo hace inviable con suscripción. Una tarea de
humo que escala a mitad de corrida es **sin puntaje**, no un peor puntaje: comparar
contra la línea de base exige la misma profundidad de corrida en las dos, y una
escalada corta menos que la corrida completa que dejó el puntaje anterior.

**El loop humano.** `agentic eval run --budget <USD> --yes` corre el set (o, en una
tarea de humo, la única del set que la declaración marca) desde cero, una tarea a la
vez; el humano mergea cada pull request o no; `agentic close --issue N` por cada tarea
del set registra el veredicto -- se niega con `"no metrics endpoint configured;
nothing recorded"` si no hay endpoint de métricas (`cli.py`, `_close`), la línea que
alguien lee seis semanas después cuando la línea de base son cinco nulos; `agentic eval
score --out <archivo>.jsonl` escribe una línea por tarea,
leída entera del documento de esa tarea, y se niega si el archivo de salida ya existe.
Requiere versionado de prompts, que ya era condición para poder revertir.

---

## 10. Modelos por rol

Un validador que comparte modelo, prompt e información con el pensador **ratifica, no
valida**. La regla es que todo validador traiga una fuente que el pensador no tuvo.

| | Modelo | Por qué |
|---|---|---|
| Pensadores | Sonnet | Son los que más llamadas hacen |
| Validadores | Opus | Asimetría más barata de implementar y de medir |
| Subagentes de cualquier rol | Sonnet | Leen y resumen para un padre que razona en el suyo; en Opus eran 5-10 USD por revisión (ADR-0013, adenda) |

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

**Consecuencia asumida**: los tres caminos existen (#46, #65), pero el de bug dictamina
contra el conocimiento de producto escrito, y el v0 no documentó todo lo que hace. Lo que
sigue faltando es la definición previa: un bug sobre una capacidad sin definición no se
frena, Product Analyst lo dice y PM decide; si decide que es bug, escribe ahí la definición
que faltaba. Si decide que es comportamiento esperado, la definición sigue sin escribirse
(una tarea descartada no entrega nada que la lleve) y el siguiente reporte del mismo caso
vuelve a llegar sin definición: escribirla ahí es deuda de la etapa 2. El sistema como
producto propio entra siempre por platform enhancement (§8, ADR-0017).

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
| 8 | ~~Criterio de clasificación de PjM y qué hace si duda~~ **resuelto** (#46): el criterio es qué notaría un usuario del producto, no quién escribió el issue ni cuán técnico suena; vive en el prompt de PjM. Si encaja en dos tipos o en ninguno, `confidence: low` escala | — |
| 9 | ~~Cuántos Tech Leads existen~~ **resuelto**: un rol, una instancia por tarea; el modo proactivo es el mismo rol. El efímero no es un rol de la lista, es el entrevistador de Tech Lead (ADR-0014) | — |
| 10 | ~~Versionado de prompts~~ **resuelto**: `.md` por rol con overlay en el producto, hash por tarea (ADR-0005). Falta el versionado de **parámetros** de `Configuration` | Mejora del proceso de PjM |
| 11 | Las cinco tareas del set de evaluación, todavía sin escribir. El humano las escribe como issues en gym-saas (GH-12 más cuatro de las que se postergaron armando el v0) y las declara en `.agentic/eval-set.yaml` del producto; el runner que las corre ya existe (`agentic eval run`/`agentic eval score`, sección 14) y no las necesita para funcionar, solo para tener algo que correr | Medir cualquier cosa |
| 12 | ~~El v0 del gimnasio~~ **resuelto**: existe, escrito a mano y plano. Los estándares de ingeniería aplican al código nuevo y al que cada tarea toca (ADR-0012) | — |
| 13 | ~~Campos de `Configuration` y esquemas~~ **resuelto**: `agentic/shared/configuration.py` y `agentic/core/process/domain/schemas.py`, uno por rol implementado. Los roles que faltan agregan su esquema al entrar | — |
| 14 | ~~Script que corre el set de cinco tareas y escribe el JSONL de resultados. Sin costo: eso ya lo da Grafana~~ **resuelto**, con la premisa del costo corregida en el mismo diff: `agentic eval run` / `agentic eval score` (sección 14). Grafana no puede dar el costo por tarea porque ningún span ni ninguna métrica lleva id de tarea y ninguno lo hará (ADR-0008, segunda adenda; cardinalidad cerrada) — el costo sale de `cost_usd` en el documento de la tarea, que ya lo tenía | — |
| 15 | Versionado de **parámetros** de `Configuration` como label de las métricas; hoy solo van el SHA del sistema y la huella de prompts | Comparar corridas que cambian solo un parámetro |
| 16 | El camino como dato (`Workflow`, solo la ida, con la tabla de devoluciones aparte) y `Status` en cinco valores más `outcome`, ya modelados en ADR-0009. Hoy el ruteo es una función por nodo en `rules.py` | Etapa 2: se implementa al entrar QA Lead, el segundo par |
| 17 | Verificación real de que la mejora del sistema no depende del producto: el mismo set de prompts rindiendo sobre un segundo producto, no solo gym-saas (adenda del pedido de GH-27, punto 6) | Hito posterior a la etapa 2; hasta entonces el set de cinco es de gym-saas porque es el único producto |

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
| Product Analyst escribe el PRD en un tiro y PM lo valida en otro | Product Analyst entrevista a PM con `grilling`; lo que PM no puede decidir va al humano como pregunta (ADR-0014) |
| El PRD arranca por el comportamiento | Arranca por el job to be done: primera ronda de la entrevista, primera sección del PRD, primera sección de cada capacidad en `docs/product/`; PM devuelve lo que no sirve al job |
| El pingpong del Tech Lead no tiene forma fija | Es una entrevista del efímero a Tech Lead, con tope de cuatro rondas, y de ahí salen los ADRs y las subtareas (ADR-0014) |
| Los roles no tienen skills; solo prompt y esquema | Skills de Matt Pocock fijadas como submódulo; solo las invocables por un agente, dos obligatorias: `tdd` y `code-review` (ADR-0013) |
| La corrida termina cuando el último rol entrega | Termina en un pull request del producto, lista para revisión; el issue recibe el link (ADR-0011) |
| El tope de costo por intento es solo del Developer | Es de todo rol, `AGENTIC_MAX_BUDGET_USD`; los subagentes de cualquier rol corren en Sonnet (ADR-0004 y ADR-0013, adendas) |
| El único tope de costo es por intento | Hay también un tope de tarea sobre el acumulado, que escala en vez de seguir (ADR-0004, adenda tercera) |
| Tope de tarea fijo en 100 USD, y escalar termina la corrida | Base más proporcional por subtarea con techo, y alcanzarlo pausa: el humano sube el tope y reanuda (ADR-0004, adenda cuarta; ADR-0003, adenda) |
| Cada revisión del Tech Lead arranca desde la base de la subtarea | La primera sí; después de una devolución es incremental, desde el head ya revisado (ADR-0015) |
| Los Developers corren en paralelo y después Tech Lead revisa todas las entregas | Cada Developer y su revisión son una rama del fan-out: la revisión arranca cuando entrega *su* Developer (ADR-0004, adenda quinta) |
| La integración es una subtarea final del integrador | Incremental en el fan-in, apenas se aprueba cada subtarea; el integrador y la revisión de la rama entera siguen al final (ADR-0004, adenda quinta) |
| En paralelo el desborde del tope se acepta acotado por las ramas | Con la revisión adentro de la rama el número dejó de ser aceptable: el gasto del proceso lo comparten todas las ramas, sin estado compartido (ADR-0004, adenda quinta) |
| Tech Lead revisa de solo lectura, sin correr tests, y escribe el veredicto con un motivo libre | Corre los tests que respaldan cada objeción; entrega hallazgos con evidencia y categoría, y el veredicto se deriva de los bloqueantes (ADR-0016) |
| El revisor corre `pytest` con reglas fijas y el Developer copia el resultado de la suite en su sección | El producto declara su stack en `.agentic/stack.yaml`; la puerta de entrega los corre y escribe el resultado, y el revisor los usa para reproducir (ADR-0016, adenda) |
| Un producto sin `.agentic/stack.yaml` corre con el default del sistema | No corre: `preflight` corta y muestra la plantilla. El default quedó como plantilla, inalcanzable para una corrida (ADR-0016, adenda segunda) |
| El worktree de una subtarea la aísla | La aísla en disco, no en entorno: sin la clave `setup` que lo prepare, todos los checkouts comparten el `.venv` del producto (ADR-0011, adenda tercera) |
| El sistema nunca escribe en su repo | Nunca escribe en el **checkout** desde el que corre: el sistema es su propio segundo producto y sus artefactos caen en este repo (ADR-0017; ADR-0002, adenda) |
| El glosario de Proceso agrupa 27 términos bajo "Interacciones" | Cuatro secciones que dicen qué momento del proceso nombran (cómo se piden cosas, cómo se entrega y se revisa, cómo se rechaza trabajo, cómo termina una corrida), y tres términos fundidos en el que ya los contenía (#55) |
| La devolución se llama `Return` en el código | `ChangesRequested`, que es *request changes*, uno de los tres valores de `event` de una review de GitHub. La métrica `agentic.task.returns`, el label `return.category`, el atributo `role.return_categories`, el valor `returned` y las claves del documento de tarea **no** siguen: son dato ya escrito, y renombrarlas parte las series y rompe la reanudación de una tarea pausada (ADR-0008, adenda) |
| La puerta de entrega se llama `DeliveryGate` en el port y `CheckoutGate` en la implementación | `Presubmit` y `CheckoutPresubmit`: es palabra por palabra el presubmit check de Software Engineering at Google, cap. 20, y ninguno de los dos nombres viejos decía lo que la cosa es (ADR-0018) |
| Los dos `kind` de rol se llaman `thinker` y `validator` en el código | `actor` y `verifier`, del par Actor/Evaluator de Reflexion y del verifier de Cobbe 2021 y Lightman 2023. Corresponde verifier y no judge: el judge opina, el verifier ejecuta (ADR-0016). El castellano Pensador y Validador se queda (ADR-0018) |
| El camino como dato se llama `Route` | Se llama `Workflow`, el término que usan Microsoft Agent Framework, OpenAI, Google ADK, Temporal y CrewAI Flows; `Graph` chocaría con el `StateGraph` de LangGraph, que es el adaptador (ADR-0009, adenda) |
| El paso de PM al conocimiento se llama `distill()` en el código | `consolidate()`: en inglés `distill` se lee como knowledge distillation, comprimir un modelo grande en uno chico. El castellano "Destilado" se queda (ADR-0018) |
| La copia del sistema que ejecuta una corrida se llama "runtime" | `stage0`, del bootstrapping de compiladores, con `stage1` y `stage2` gratis; "runtime" ya es el harness de cada rol (ADR-0018) |
| Todo lo que ni el pedido ni el conocimiento resuelven va al humano, y una pregunta registrada no se retira | Va solo lo que deja el PRD indecidible; el resto es un supuesto en la respuesta. El dueño de la decisión retira por número lo que una ronda posterior resuelve (ADR-0014, adenda tercera) |
| Este repo lee el default de estándares del sistema porque describe bien lo que el repo ya hace (ADR-0017) | ADR-0012's addendum replaces the ADR-0017 bullet that had this repo reading the system default because it described what the repo already does; from here the default is practices and this repo materialises them in its own `CODING_STANDARDS.md` |
| El set entero se re-corre cada vez que se cambia un prompt o un parámetro | Solo en hitos (rol nuevo, cambio de estructura de revisión, cambio de modelo por rol); un cambio de prompt o de parámetro corre solo la tarea de humo que la declaración del set nombra, y una que escala a mitad de corrida queda sin puntaje, no con uno peor (sección 9; GH-27) |
| El costo por tarea del set de evaluación sale de Grafana | Grafana no puede: ningún span ni ninguna métrica lleva id de tarea y ninguno lo hará (ADR-0008, segunda adenda). Sale de `cost_usd`, ya en el documento de cada tarea; `agentic eval score` lo lee de ahí, nunca de una métrica (sección 12, pendiente 14; GH-27) |

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
| `shared/` | `configuration.py`, `environment.py` (el entorno que se le pasa a lo que se spawnea: sin la toolchain propia y sin la configuración propia), `stage0.py` (dónde está el checkout que ejecuta) | Nada del proyecto |
| `core/process/` | `domain/`: `roles.py` (`Role` con tools, reglas de permiso y skills; `Pair`; `Interview`; `describe_flags`) y `prompts/`, `task.py` (estado, subtareas y reducers), `document.py`, `schemas.py`, `rules.py` (límite por par, versionado por sección, la ola que despacha el fan-in, la tabla de hallazgos y los invariantes, ruteo, tope de tarea, reanudación), `presubmit.py` (qué le falta a una entrega). `application/`: `ports.py` (`RoleRunner`, `Presubmit`, `TaskDocuments`, `ProductKnowledge`, `Prompts`, `FlagVocabulary`, `ProductStack`, `Commands`, `Inbox`, `Workspace`, `Tracker`), `interview` (rondas entre dos roles, ADR-0014), `presubmit` (la puerta de entrega sobre los ports, `CheckoutPresubmit`) y `limits.py` (lo que una corrida puede hacer a la vez y gastar, compartido por las ramas) y un caso de uso por nodo: `classify`, `define`, `validate_definition`, `plan`, `develop`, `review`, `integrate` (el fan-in), `deliver`, `escalate`, sobre `run_role`, más `resume` (qué hace una reanudación antes de gastar). `adapters/`: `claude_sdk/` (harness con plugin de skills, jaula por rol con `hooks.py`, `Printer`), `shell/` (corre los chequeos del producto), `filesystem/` (documentos y overlay de prompts), `git/` (rama de tarea y árboles de trabajo, ADR-0011), `github/` (bandeja y escritura: PR, comentarios, etiquetas), `langgraph/` (el grafo: nodos, ruteo, el subgrafo `develop → review` con `Send` por subtarea y el fan-in, abre y cierra los spans; `pause` con el `interrupt()`, ADR-0003) y `telemetry.py`, el port por el que entra el tracing | `shared` |
| `core/knowledge/` | `domain/`: `knowledge.py` (cómo se muestra, cómo se edita el glosario, qué debe traer una aprobación), `flags.py` (el vocabulario), `stack.py` (el stack del producto y sus chequeos). `adapters/filesystem/`: implementa `ProductKnowledge` y `FlagVocabulary` sobre el repo del producto; `adapters/git/`: `ProductStack`, como lo declara el commit base | `shared`, `process` |
| `core/measurement/` | `domain/`: `metrics.py` (retrabajo, devoluciones, huella de prompts). `adapters/otel/`: spans y métricas del sistema, implementa `Telemetry` | `shared`, `process` |
| raíz | `composition.py` arma los ports reales, el checkpointer SQLite e inyecta la telemetría; `cli.py` es el adaptador de entrada (`run`, `resume`) | Todo |

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

**El sistema no sabe nada de Python (issue #38).** Los prompts de rol dejan de dar por
sentado un stack: `developer.md` ya no afirma que el sistema rechaza un `# type: ignore` o
un `# noqa`, algo falso fuera de Python — se lo dice `forbidden`, lo que el producto
declara en `.agentic/stack.yaml`, con el motivo que el propio producto redacta;
`tech_lead_review.md` pierde los ejemplos de sintaxis de Python de la categoría
`standard-typing-silenced`, reemplazados por una descripción agnóstica de la misma
categoría, sin agregar ningún ejemplo nuevo; y el resto de los prompts pierde los nombres
de herramienta y de archivo que se le habían colado (`conftest.py`, `pyproject.toml`, un
`python3 -` de ejemplo). Qué herramienta cumple cada práctica lo dice el
`CODING_STANDARDS.md` del producto (ADR-0012, adenda), nunca un prompt del sistema.
`tests/core/process/application/test_prompts_stack_flavour.py` guarda la propiedad —
ningún prompt de rol nombra un token de stack — nunca los bytes exactos de un prompt.
Este es el único diff de la tarea que toca `agentic/core/process/domain/prompts/`, así
que es el que mueve `prompts.fingerprint`: la huella de antes de este commit deja de ser
comparable para cualquier serie segmentada por huella de prompts.

### Salida estructurada

Lo que PjM procesa mecánicamente (clasificación con confianza, veredicto con motivo, flags,
mapa de subtareas) se define como esquema Pydantic y se pasa al SDK como `json_schema`. El
Markdown de la sección sigue siendo texto libre, pero viaja como campo `markdown` del mismo
esquema: el SDK termina el turno en cuanto el modelo llama a la herramienta de salida
estructurada, así que una respuesta de texto aparte nunca llega. El runner corta la sesión
en el primer resultado que trae salida estructurada: un subagente que el rol dejó en
background reabre la sesión al volver y el modelo entrega de nuevo, un resultado por
notificación (issue #16); el corte termina el proceso con lo que tuviera corriendo y lo
avisa en el log. Una salida estructurada
ausente o inválida, o un `markdown` vacío, es fallo de rol, no un diccionario vacío. La
revisión (`Review`) no trae veredicto: trae `findings`, cada uno con su categoría cerrada, y
el caso de uso deriva el veredicto; un bloqueante sin evidencia o con alcance de plan no
cuenta como tal: el sistema lo baja y lo anota, sin fallo de rol (ADR-0016). Las
listas de strings de primer nivel (`questions_for_pm`, `questions_for_human`,
`assumptions`) llegan al modelo como un string de líneas y vuelven como lista: con un array
en el esquema el modelo las escribía como texto suelto o `<item>` y el JSON no parseaba
(issue #8, 20 casos en 9 sesiones de GH-12).

### Estado del grafo

- `TaskInput` mínimo como `input` schema: id, título, descripción.
- Contadores por par con un reducer de override: resetear el par cuando el humano responde es
  un update explícito, no un dict copiado a mano. La clave la deriva `Pair.key`; la del
  par Tech Lead→Developer incluye el id de subtarea.
- `subtasks` es un mapa por id con reducer de override por clave: cada Developer patchea la
  suya. `branch`, `pull_request` y `questions_for_human` son campos planos. La conversación
  interna de cada Developer no entra al estado: queda en su sesión (ADR-0004).
- `thread_id` es el id de la tarea. Checkpointer `AsyncSqliteSaver` en
  `.agentic/state.sqlite` del producto (ADR-0003).

### Pausa y reanudación

Escalado con `interrupt()`, reanudación con `Command(resume={"decision": texto})`
(ADR-0003 y su adenda). El `interrupt()` vive en `pause`, un nodo sin efectos después de
`escalate`: LangGraph reanuda un nodo desde su comienzo, así que dentro de un caso de uso
volvería a pagar el rol y dentro de `escalate` volvería a comentar el issue. Con la
respuesta, `pause` devuelve el patch de reanudación (`rules.resumed`) y el ruteo va a donde el
estado dice que se frenó (`rules.resume_target`).

`agentic resume --issue N [--task-budget X] [--from-document] [--dry-run] [--yes]` usa el
checkpoint; toma como decisión todos los comentarios del issue posteriores al último del sistema,
juntos, y sin decisión sale con 1 si hay preguntas pendientes o un par llegó a su límite.
Antes de gastar muestra la tarea, dónde se frenó, el motivo, lo gastado por fase, el tope, la
decisión entera, qué reanuda, las sesiones y los worktrees, y pide confirmación. `--from-document` es el rescate: siembra el estado desde el documento,
cruzado con git, como si `escalate` acabara de correr. `agentic run` se niega a arrancar
sobre una tarea con estado salvo `--fresh`, que ya no reusa las ramas donde estaban
(ADR-0011, adenda sexta): antes de soltar el checkpoint, `_run` archiva lo que dejó la
corrida anterior, primera sentencia dentro del lock. Si `agentic/<tarea>` sigue viva,
commitea lo que ningún Developer llegó a commitear en cada worktree de la tarea
(`Workspace.worktrees`), copia ahí el documento (`TaskDocuments.write_into`), saca los
worktrees y renombra la rama de tarea y las de sus subtareas, la de tarea primero, bajo
`archive/run<N>-<commit del sistema>/`; `N` es uno más que el mayor archivado de esa
tarea, o el que ya tenía si la rama de tarea ya no está viva pero alguna de subtarea sí
—el archivado se cortó a la mitad, y el reintento lo termina en el mismo prefijo en vez
de abrir otro. Nada se borra, así que una corrida vieja se puede leer después. Un
`GitError` en cualquier renombre sube como `CannotStart`: el thread sobrevive, y el
reintento retoma donde se cortó porque cada renombre es un no-op si el destino ya
existe y el origen no. `_run` imprime una línea antes de invocar el grafo, diga o no
algo nuevo esta vez, y el issue recibe `Started over: previous run archived as
archive/run<N>-<sha>/`, un comentario del sistema como `Escalated:` o `Resumed:`; si el
documento decía que la tarea estaba pausada, o no se pudo leer, también se le quita
`needs-human`. Una tarea sin ramas ni documento no toca git ni el tracker. En la etapa
5 un poller con reloj propio llama a
`resume`, que es el mismo mecanismo que necesitan los roles proactivos. Antes de invocar el
grafo, `run`, `resume` y `--fresh` toman `.agentic/runs/<tarea>.lock` (pid, host, hora y
comando): un pid vivo en este host se niega, uno de otro host se niega por nombre sin
probarlo, y uno muerto se toma, avisándolo; `--dry-run` no lo toma, solo lo muestra. Protege
invocaciones de la CLI entre sí, no un `langgraph dev`, que mantiene todos los hilos en un
solo proceso. Un SIGTERM o un SIGHUP cancelan la invocación en curso: se vacía la
telemetría, se libera el lock y el proceso sale con código distinto de cero, sin escribir
en el documento (el único `TaskState` que la CLI tiene en ese momento es el de entrada, y
pisarlo borraría lo que los nodos de esta invocación ya escribieron) ni un comentario nuevo
en el issue (`Resumed: ` gana una cláusula que nombra el rol interrumpido, en la próxima
reanudación de una corrida que se cruzó con uno de estos, en vez de un prefijo nuevo). Un
SIGKILL no se puede atrapar: el lock queda huérfano y `resume` lo toma avisándolo.
`--detach` (en `run` y en `resume`, este último exigiendo `--yes` y rechazando
`--dry-run` al parsear) reejecuta la misma invocación sin `--detach`, en `setsid`
—nunca un fork, porque un hijo forkeado después de `setsid` sigue sin terminal para su
propio `print` y porque `telemetry.configure` ya abrió hilos en background que un fork
no llevaría—, con stdin cerrado y stdout/stderr redirigidos al log antes de correr
ningún rol; el padre solo hace el trabajo de sólo lectura (preflight, la pila
declarada, si el lock ya está vivo, y en `resume` el resumen y la confirmación),
imprime el pid del hijo —el que toma el lock, nunca el padre— y la ruta del log, y sale.
Sin TTY y sin `--detach` avisa una vez que el proceso muere con su sesión; no se
desacopla solo, porque CI, los tests y `agentic eval` (#27) necesitan el código de
salida.

### Fallo de rol

Mecánicamente es una de cuatro cosas: excepción del SDK, `max_turns` agotado sin resultado,
salida estructurada ausente o inválida, texto vacío. El port `RoleRunner` devuelve la salida
cruda o lanza `AttemptFailed`, con el costo y la sesión que el intento dejó; la validación y
el reintento viven en `application.run_role`: atrapa, reintenta una vez con el error como
entrada (reanudando la sesión del intento fallido si la hay, o agregado al prompt de una
sesión nueva si no), y si vuelve a fallar lanza
`RoleFailure`, que el caso de uso convierte en escalado. Se descartó `RetryPolicy` de LangGraph porque reintenta el
nodo sin pasarle el error al modelo. Todo fallo, reintentado o no, queda en el estado para las
métricas de PjM.

Lo que se puede frenar antes de que sea un fallo se frena adentro del turno, con hooks del
SDK (ADR-0011, adenda segunda). Un rol que ejecuta corre con `PreToolUse` sobre Bash, Edit y
Write, que niegan con motivo lo que un prefijo no ve. La puerta de entrega es un
`PreToolUse` sobre `StructuredOutput`: el runner recibe un `Presubmit` y lo pregunta cada
vez que el modelo entrega. `Stop` no sirve para eso: con `output_format` dispara después de
la entrega, y un `block` ahí no la frena. Un deny no es fallo de rol ni reintento: el modelo
recibe el motivo y sigue en la misma sesión.

### Fan-out

La etapa técnica es una ola de ramas alrededor de un nodo de fan-in (ADR-0004, adenda
quinta). Una rama es el subgrafo `pipeline`, `develop → review` para **una** subtarea,
despachado con un `Send(estado + subtask_id)` por subtarea lista: así la revisión arranca
apenas entrega su Developer en vez de esperar al más lento de la ola (corrida 4: 693 s en
serie contra 179 s en paralelo). Entra por la revisión si la subtarea ya está entregada, que
es donde reanuda una corrida frenada entre las dos. Cada rama corre en su propio árbol de
trabajo (ADR-0011); `status`, `current_role` y `escalated` llevan reducer "último gana".

Un subgrafo como nodo devuelve su estado final, y el padre lo vuelve a plegar con los mismos
reducers: el envoltorio devuelve el delta (`task.delta`), o `cost_usd` se cuenta dos veces y
`runs`, `failures` y `return_history` se duplican.

El nodo `integrate` es el fan-in, del sistema, sin roles. El plan le entrega la primera ola y
cada rama vuelve a él; una devolución es una ola más, así que la segunda arranca viendo lo
que vio toda la primera. Corre solo, que es para lo que está: crea los checkouts de a uno
(cuatro `worktree add` sobre el mismo `.git` se pisaban el lock), mergea cada subtarea
aprobada a la rama de tarea apenas se aprueba (**integración incremental**; un merge que no
aplica queda nombrado para el integrador, y `Workspace.merge` devuelve el motivo en vez de
tirar), agrega la subtarea de integración cuando toca, guarda el documento una vez con el
estado de todas las ramas, y fija el `status` de una tarea que escaló, que una hermana feliz
podía pisar. El escalado se le cuenta al humano una sola vez, desde acá.

Cuántos roles corren a la vez lo limita `AGENTIC_MAX_CONCURRENT_ROLES` (default 4), y el
gasto de todas las ramas lo lleva el mismo objeto: el tope de tarea se mira contra la mayor
de las dos vistas, la del estado y la del proceso, y sólo impide **arrancar** un intento
(`application/limits.py`).

### Entrevistas

Un caso de uso `interview(estado, Interview)` corre las rondas (ADR-0014): cada lado es una
sesión del SDK reanudada con `resume`, el entrevistador entrega `InterviewTurn` (`done`,
`markdown`), el entrevistado `Answers` (`markdown`, `questions_for_human`, `withdrawn`). La
transcripción
es una sección `Interview: A→B` del documento y el autor escribe después reanudando su
propia sesión. Preguntas para el humano al final de la entrevista = escalado. Cada una
se plantea una sola vez: el entrevistado devuelve solo las nuevas de la ronda, ve las ya
registradas bajo `## With the human` en el prompt siguiente y el caso de uso descarta la
repetición literal al acumular (ADR-0014, adenda segunda). Solo va lo que deja la definición
indecidible; lo que no la cambia es un supuesto en la respuesta. Una pregunta registrada se
retira cuando una ronda posterior la resuelve: el dueño de la decisión devuelve en `withdrawn` los
números de la lista que recibió, el caso de uso las saca antes de sumar las nuevas, y el
entrevistador también ve la lista para traer el conocimiento que la cierre (ADR-0014,
adenda tercera).

La entrevista de solución tiene **consultado** (glosario: Consulta, ADR-0014 adenda): el
Tech Lead entrevistado devuelve `TechAnswers` (`markdown`, `questions_for_pm`) y nunca
pregunta al humano. Con preguntas, PM responde en la misma ronda (`Consultation`:
`markdown`, `questions_for_human`, `withdrawn`, `definitions`, `glossary`) retomando su sesión de
validación; la respuesta entra en la transcripción y en el prompt siguiente del
entrevistador, la sección `PM` se entrega de nuevo con `### Clarifications` (la anterior
queda como `PM (vN)`) y lo que cambia el conocimiento se destila otra vez.

### Observabilidad

Cuatro capas, de más inmediata a más durable. `agentic run` y `agentic resume` escriben
siempre su log en `<producto>/.agentic/logs/<tarea>-<hora UTC>.log` (tool calls, la línea
`done:` de cada rol y el aviso de sub-agentes en background), fuera del `.gitignore` del
producto; `--verbose` es aditivo: además de sumar al log los deltas de generación (texto,
thinking) según van llegando, refleja las mismas líneas por stderr con prefijo de rol, la
única forma de verlas en vivo. Claude Code exporta OpenTelemetry nativo; un collector en
`observability/collector/` lo reenvía a un Langfuse local, porque el exportador de Claude
Code no puede mandar un header `Authorization: Basic` entero. El grafo abre un span por
tarea y uno por rol y pasa `TRACEPARENT` al subproceso, así una tarea es un trace con los
roles anidados; al cerrar cada span el adaptador le estampa lo que el caso de uso decidió
(costo, modelo, versión de la entrega, resultado) leyendo el patch, sin que la aplicación
lo sepa. Las métricas del sistema (glosario de Medición: costo por tarea, retrabajo,
duración, devoluciones, escalados) salen del mismo adaptador y el collector las manda,
junto con los spans del sistema y sin los de Claude Code, a Grafana Cloud, con la versión
del sistema y la huella de prompts como labels (ADR-0008, `observability/README.md`);
`agentic.task.returns` deja una muestra por categoría de devolución (ADR-0016). Todo span y
toda métrica llevan además el **producto** sobre el que corrió la tarea, con el nombre que el
propio producto declara en su `.agentic/stack.yaml`. Lo que se mide es el sistema, no el
producto: el eje de todo panel es la versión del sistema y el producto queda como filtro y
como covariable (la mezcla de tareas de cada versión), nunca como eje ni como suma
(ADR-0008, adenda; #47, #24). Por último, el `session_id` de cada
corrida queda en el documento de tarea: `claude -r <id>` en
el repo del producto reabre la sesión del rol.

Los roles no ven la configuración MCP del usuario: el runner pasa `strict_mcp_config` sin
servidores, porque ningún rol declara una tool MCP y los conectores heredados de la cuenta
(claude.ai, `~/.claude.json`) sumaban ~233k tokens releídos en cada llamada, el 67% del
costo de la corrida 4 (issue #18). `tests/llm/test_context_size.py` vigila el contexto por
llamada de un rol mínimo; solo muerde en una cuenta con conectores.

**Medición, dashboard v3 (issue #24).** Lo que `agentic.role.cost_usd` y
`agentic.role.duration` agregaban por nodo ahora se ve por **corrida de rol**: una muestra
por entrada de `runs` de la patch, con `role.slug` (los 17 valores de `Role.slug`, así que
`tech_lead`, `tech_lead_review`, `tech_lead_interviewee` y `tech_lead_ephemeral` dejan de
compartir `role="Tech Lead"`) y `phase` (`rules.phase_of`) como labels nuevos, más un
`agentic.role.run_duration` medido dentro del cupo de concurrencia y un `agentic.role.
queue_wait` aparte para la espera. Sobre la misma corrida: tokens por tipo, llamadas,
turnos, herramientas usadas y lo gastado en sub-agentes (`agentic.role.tokens/.calls/
.turns/.tool_calls/.subagent_cost_usd`), y el motivo de un intento fallido en vez de un
conteo sin explicación (`agentic.role.failures{reason}`). Los hallazgos de una revisión y
los chequeos de una entrega quedan como eventos propios (`agentic.review.findings`,
`agentic.presubmit.checks/.duration`), igual que las rondas, preguntas e invariantes de una
entrevista (`agentic.interview.*`). Cada invocación (`agentic run`/`agentic resume`) emite
lo que ella gastó —no el acumulado de la tarea— como `agentic.invocation.*`, lo que corta la
doble cuenta de una tarea reanudada; y el motivo de una escalada deja de ser texto para
parsear: `escalation_class` viaja en el estado, escrito por un solo constructor de dominio
junto con `escalated`, y `agentic.task.paused` lo lleva como label. El dashboard "Sistema
agéntico" (v3) suma paneles por fase, por `role.slug`, tokens y $ por 1k tokens de salida,
herramientas, hallazgos, entregas rojas, entrevistas, pausas por motivo y fallas por motivo;
el detalle completo, métrica por métrica, vive en `observability/README.md`.

### Tooling

`pyproject.toml` con `uv.lock`, ruff con `E F I UP` (sin `D`: docstrings obligatorios en 17
roles es ruido ahora), pytest, GitHub Actions corriendo ruff y pytest en cada PR,
`langgraph.json` para `langgraph dev` y Studio, `.env.example`. El grafo se exporta sin
compilar desde `agentic.composition`, el composition root; la CLI lo compila con el checkpointer.

`scripts/stage0.sh <ref> [dir]` crea o actualiza **stage0**, el checkout desde el que corre
el sistema, fijado a un sha y con su propio entorno (`~/.agentic-stage0` por default).
Actualizarlo es un acto explícito: nombrás el commit. Es un clon y no un `git worktree`,
que compartiría el repositorio con el checkout que se está editando. El sistema se niega a
arrancar cuando el producto está en el mismo repositorio que el paquete que corre —compara
el `--git-common-dir` de los dos lados, así que un worktree del propio repo también cae— y
el mensaje dice qué usar y cómo crearlo. La regla vive en `preflight`, la puerta de arranque,
y la pregunta también `default_ports`, porque el grafo se invoca sin la CLI desde `langgraph
dev`, Studio y la API. Falla antes de gastar un peso. Un stage0 con cambios sin commitear no
se rechaza, se avisa: la corrida sale `+dirty` y no compara contra nada, pero no está mal.
Corriendo desde stage0, `system_version()` da el sha de stage0, limpio, con el producto en
cualquier estado.

El sistema tampoco le pasa su propia configuración a lo que spawnea: las `AGENTIC_*` del
humano se le sacan a los chequeos del producto y se le anulan a los roles
(`shared/environment.py`). Con el sistema como producto, sus chequeos leen esas mismas
variables, y un `AGENTIC_PRODUCT_DIR` heredado habla de la corrida de afuera.

### Evaluación

Langfuse y Grafana no puntúan. El puntaje sigue siendo el de la sección 9: binario, humano.
El set de tareas vive en `.agentic/eval-set.yaml` del producto (como `stack.yaml`,
`flags.yaml` y `BRIEF.md`): los números de issue y cuál de ellos es la tarea de humo, nada
más. `agentic eval run --budget <USD> --yes` corre cada tarea del set (o, con `--dry-run`,
solo la muestra sin gastar nada) desde cero -- un `agentic run --fresh --issue N` por
tarea, esperado antes de lanzar el siguiente, nunca en paralelo -- y se niega antes de
lanzar el primero si la declaración no es válida, si el nombre del producto no se puede
leer, si el pull request o el lock de alguna tarea del set siguen abiertos, o si el propio
lock de `eval` está tomado. `agentic eval score --out <archivo>.jsonl` escribe una línea
JSON por tarea, leída entera del documento de esa tarea -- nunca de una métrica ni de
`gh` -- y se niega si `<archivo>.jsonl` ya existe, para que ninguna corrida pueda pisar una
línea de base. Cada línea lleva lo que ninguna métrica puede dar porque ningún span lleva
id de tarea (ADR-0008, segunda adenda): hash de prompts, el mapa `prompts` completo,
`system_version`, el resultado binario y la nota (siempre `null`: la carga el humano a
mano). El costo por tarea sale de `cost_usd`, ya en el documento; Grafana nunca lo tuvo por
tarea y nunca lo tendrá. Además, evals de nodo intermedio como tests de pytest ("PjM
clasifica estas descripciones así", "PM devuelve esta definición incompleta"), marcados
`llm` y **excluidos de CI**: el SDK se autentica con la suscripción, que GitHub Actions no
tiene. CI corre solo tests puros: parser del documento, reducers, configuración, ruteo con
salidas grabadas.

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
