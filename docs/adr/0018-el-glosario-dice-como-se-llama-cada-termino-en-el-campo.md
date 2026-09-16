---
status: accepted
date: 2026-09-16
---

# Cada término del glosario dice cómo se llama en el campo, y los que no tienen nombre lo dicen explícito

El glosario tiene 69 términos en tres contextos y hasta hoy no decía cómo se llama
ninguno afuera, aunque el código y los prompts están en inglés y todo término que el
código modela tiene su nombre inglés en `tests/test_language.py`. El resultado es que una
decisión de nombre no se podía revisar: nada distinguía `Devolución`, que la industria
entera llama *request changes*, de `Par`, que no se llama de ninguna manera en ningún
lado. Sin esa distinción escrita, dentro de seis meses alguien "corrige" un término propio
a algo que suena más estándar y no lo es, o defiende como propio uno que el campo ya
resolvió mejor.

**Cada entrada suma una línea `_Campo_`, junto a `_Avoid_`**, con el nombre que el término
tiene afuera y de dónde sale:

```
**Puerta de entrega**:
<definición>
_Avoid_: gate, validación, hook (el mecanismo, no el concepto)
_Campo_: presubmit check — Software Engineering at Google, cap. 20
```

**La regla de qué se escribe es cerrada, y es la parte que importa**, porque un mapeo sin
regla vuelve a ser intuición con formato:

- **estándar real** (varios frameworks o papers independientes lo usan igual): el término y
  la fuente;
- **de un solo proyecto o un solo paper**: el término **con el nombre del proyecto al
  lado**, para que se lea como préstamo y no como canon;
- **préstamo de otro dominio**: el término y de qué dominio viene, porque el que llega de
  ese dominio va a traer su definición entera;
- **sin término**: `_Campo_: sin término de arte`, escrito. Esa línea es la que protege los
  términos propios, y es la que hace falta escribir: la ausencia de la línea no distingue
  "no tiene nombre" de "nadie lo miró".

**Los términos propios se quedan propios.** Un término propio bien definido es mejor que un
préstamo que engaña: si le ponemos `pair` a nuestro Par, el que viene de afuera lee *pair
programming*, que es otra cosa. La línea `_Campo_: sin término de arte` no es una deuda a
saldar, es un resultado.

## El reparto

De los 69 términos, contando estricto: un término tiene nombre en el campo cuando el
campo nombra **nuestro concepto**, no una idea vecina.

- **19 tienen un nombre estándar de verdad**, con varias fuentes independientes: Humano
  (`human-in-the-loop`), Validador (`verifier`), Tarea (`task`), Camino (`workflow`),
  Corrida (`run`), Puerta de entrega (`presubmit check`), Revisión (`code review`),
  Devolución (`request changes`), Reanudación (`resume`), Reintento (`retry`), Handoff
  (`handoff`), Listo para revisión (`ready for review`), Pull request de tarea, Contexto de
  producto (`workspace root`), Conocimiento de producto (`knowledge base`), Estándares de
  ingeniería (`coding standards`), Stack del producto (`stack`), Muestra (`data point`) y
  Set de evaluación (`eval set`).
- **11 lo tienen en un solo proyecto o un solo paper**, y por eso la entrada nombra el
  proyecto: Rol (`role`, CrewAI), Pensador (`actor`, Reflexion), Rol proactivo (`ambient
  agent`, LangChain), Subtarea, Ola (`super-step`, LangGraph desde Pregel), Corrida de rol
  (`invoke_agent`, OpenTelemetry GenAI), Categoría de devolución (Conventional Comments),
  Pausa (`interrupt`, LangGraph), Tope por intento y Tope de tarea
  (`per_instance_cost_limit` y `total_cost_limit`, SWE-agent) y Bandeja (`inbox`).
- **9 son préstamos conscientes de otro dominio**: PjM y PM (la organización de software
  humana), Definición única (`PRD`, de producto), Rama de tarea (git), Integración
  incremental (integración continua), Hallazgo (`finding`, auditoría y seguridad de nube),
  Escalado (soporte y operaciones), Job (`job to be done`, innovación y marketing) y
  Retrabajo (`rework`, manufactura).
- **30 no tienen nombre en ningún lado**: Par, Consulta, Entrevista, Supuesto, Devolución
  interna, Motivo de devolución, Motivo de escalado, Capacidad, Flag, Sección, Ejecutar,
  Tech Lead efímero, Developer integrador, Etapa de definición, Pieza integrada, Pregunta
  para el humano, Revisión incremental, Head revisado, Límite de devoluciones, Decisión del
  humano, Fallo de rol, Cierre, Documento de tarea, Cola de escalados, Override de prompt,
  Sistema, Producto, Destilado, Vocabulario de flags y Métrica del sistema.

**Son más de los que esperábamos.** El issue #50 estimaba 17 sobre 67, y salieron 30 sobre
69. La diferencia no es que el campo tenga menos vocabulario del que creíamos: es el
criterio. Varios términos tienen una palabra *cerca* que no es la nuestra, y contarla
hubiera sido exactamente el error que este ADR existe para evitar. Tres ejemplos, y cada
uno está escrito en su entrada:

- **Documento de tarea** no es un `blackboard`: la palabra existe, pero es de la IA clásica
  (Hearsay-II; Nii 1986) y no aparece en el código de LangGraph, AutoGen, CrewAI, Google ADK
  ni el OpenAI Agents SDK.
- **Límite de devoluciones** no es `max_iter` (CrewAI) ni `max_turns` (OpenAI Agents SDK):
  esos topean el loop de un agente solo, y el nuestro cuenta el ida y vuelta entre dos.
- **Handoff** sí es `handoff`, pero por poco: allá es la transferencia de control a otro
  agente y acá es el bloque de texto que se le deja al que sigue. Queda en la lista de los
  estándar con la divergencia escrita, que es lo que hace falta para no importar la
  definición ajena sin darse cuenta.

## Cuatro trampas

Son errores que un recién llegado comete solo, y por eso quedan dichos en la entrada del
término que cada uno amenaza, no sólo acá:

1. **`guardrail` no significa presupuesto** en ningún framework: en el OpenAI Agents SDK
   es validación de input y output con *tripwire* (al dispararse corta la ejecución con una
   excepción), y en CrewAI es validación del output de una task. Está en `_Avoid_` de
   **Tope por intento**, que es el término que un recién llegado querría llamar así.
2. **`self-hosting` está tomado.** En compiladores es el programa que se construye a sí
   mismo; en Temporal y Kubernetes es "operás vos la infra". Son dos acepciones homónimas
   sin relación, y la segunda es la que va a leer cualquiera que venga de infraestructura.
   Está en **Sistema**.
3. **`distill` se lee como destilación de modelos**: en ML es entrenar un modelo chico
   contra las salidas de uno grande (Hinton, Vinyals y Dean 2015), que es otra cosa entera
   y del mismo campo. Está en **Destilado**, y es lo que #51 renombra en el código.
4. **`blackboard` no existe** en ningún framework moderno. Está en **Documento de tarea**.

## Lo que el campo tiene y nosotros no usamos

Términos que el campo tiene bien resueltos y que nuestro glosario todavía no nombra. No
son deuda: son el vocabulario del que sacar nombres cuando hagan falta, ya verificado.

- **`trajectory`**: la secuencia de acciones que un agente hace antes de responder (Google
  ADK; LangSmith la evalúa por *trajectory match*). Viene de RL. Es lo que vamos a querer
  nombrar cuando midamos algo más fino que la corrida de rol.
- **`pass@k`**: la probabilidad de que, generando k muestras por problema, al menos una pase
  los tests (Kulal et al. 2019; el estimador insesgado es de Chen et al. 2021, el paper de
  Codex, que suele llevarse el crédito del término). Para el set de evaluación (#27).
- **`cost-of-pass`**: el costo monetario esperado de generar una solución correcta (Erol et
  al., arXiv:2504.13359, 2025). Es exactamente la pregunta de §9 del README, y tiene además
  *frontier cost-of-pass*, el mínimo alcanzable entre los modelos disponibles y el humano.
- **`context engineering`**: curar lo que entra a la ventana de contexto (Karpathy y Lütke,
  mediados de 2025; definición operativa de Anthropic, septiembre de 2025). Es lo que hace
  `Role.reads` sin llamarlo así.
- **`intrinsic self-correction`**: auto-corregirse sin feedback externo. *Large Language
  Models Cannot Self-Correct Reasoning Yet* (Huang et al. 2023, ICLR 2024) muestra que
  degrada el resultado. Es el respaldo de por qué un validador tiene que traer una fuente
  propia, y por eso #52 lo lleva al prompt del revisor.

## Opciones descartadas

- **Renombrar todo lo que el campo nombra distinto**: el glosario está en castellano a
  propósito y el nombre del campo casi siempre es inglés. Lo que se alinea es el nombre del
  código, y sólo cuando el nombre propio confunde (issues #51, #52, #53); el castellano se
  queda.
- **Poner el nombre del campo en `_Avoid_`**: `_Avoid_` es lo que no hay que escribir en
  este repo. El nombre del campo es lo contrario: es lo que hay que escribir cuando uno sale
  a leer afuera. Mezclarlos borra las dos señales.
- **Una tabla de mapeo aparte del glosario**: se desincroniza. La línea vive en la entrada
  porque se edita cuando se edita la entrada.
- **Identificadores en `_Campo_`**: `CONTEXT.md` no lleva identificadores (ADR-0009). El
  nombre del campo es lenguaje, igual que el término; el mapeo a código sigue en
  `tests/test_language.py`.
- **Dejar en blanco los que no tienen nombre**: es el caso que más protección necesita.
- **Llevar el formato al glosario del producto** (el que PM destila con `upsert_terms`): ahí
  los términos son del negocio del producto, no del campo agéntico. `_Campo_` no tendría qué
  decir.

## Consecuencias

- Las tres `CONTEXT.md` suman una línea `_Campo_` por entrada. Cada término nuevo la trae
  desde el primer commit; una entrada sin ella es una entrada a medias.
- `CONTEXT-MAP.md` ya no dice que el producto vive "en otro repo": dice que es el que el
  sistema recibe como parámetro, que es lo que el sistema sabe.
- Los issues #51, #52, #53 y #55 escriben sus entradas contra este formato.
- El número 0017 queda para #43, que lo reserva: este ADR es "el que sigue" según #50.
- El reparto de arriba cuenta los 69 términos del día en que se escribió. La poda de #55
  fundió tres de ellos (Motivo de escalado y Pausa en Escalado, Reintento en Fallo de rol) y
  el glosario quedó en 67. Los nombres de campo no cambiaron: se mudaron adentro de la
  entrada que los absorbió.
- Este ADR es el registro de la evidencia, no la lista viva: la lista viva es el glosario.
  Cuando un término cambia de nombre en el campo, cambia la entrada; este ADR queda como
  estaba, igual que cualquier decisión fechada.
