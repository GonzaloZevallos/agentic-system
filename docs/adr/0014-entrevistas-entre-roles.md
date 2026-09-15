---
status: accepted
date: 2026-09-13
---

# La definición y la solución técnica salen de una entrevista entre dos roles, no de un tiro

El humano decide diseño con `grill-with-docs`: rondas de preguntas con recomendación, cada
respuesta abre la siguiente ronda, y el glosario y los ADRs se escriben sobre la marcha.
Quiere esa entrevista en el sistema aunque la responda un agente. Hoy Product Analyst
escribe la definición en un tiro y PM la valida en otro; el pingpong del Tech Lead estaba
descrito (README §4) pero sin forma.

**Product Analyst entrevista a PM.** El entrevistador pregunta y recomienda (Sonnet); el
entrevistado decide (Opus), con el conocimiento de producto como fuente: es la asimetría
de §10 puesta a trabajar. El issue es el plan a afinar. Después Product Analyst escribe
la definición desde su sesión de la entrevista (sin `to-spec`: ADR-0013) y PM valida el
texto: chequea fidelidad a lo que decidió, no ratifica desde cero. El loop de
devoluciones PM→Product Analyst sigue igual sobre ese texto.

**Lo que PM no puede decidir va al humano.** Ni el issue ni el conocimiento lo resuelven:
PM lo marca como pregunta para el humano; la entrevista sigue hasta el final, para que el
humano reciba todas las preguntas juntas, y recién entonces la tarea escala con la lista,
etiqueta `needs-human` y comentario en el issue. Hasta acá el sistema escalaba solo por límite o
por fallo; nunca por "no sé", que es el motivo más honesto.

**El Tech Lead efímero entra ahora, como entrevistador.** Entrevista a Tech Lead sobre la
solución con prompt de adversario y modelo más capaz (hoy ambos corren en el modelo de
validadores; la asimetría de modelo se activa con `model_overrides` para
`tech_lead_ephemeral` y se mide como cualquier otro cambio); de ahí salen la solución, los ADRs
del producto y las subtareas con sus costuras. Es el pingpong del README con la forma de
la skill. Esta sesión lo había pospuesto para medirlo aparte; lo adelanta porque es la
entrevista que más cambia el resultado: los errores de solución técnica se componen por
toda la cadena. La regla de "cada rol se gana el lugar" se aplica igual: se mide con y
sin entrevista.

**Tope de cuatro rondas.** Al llegar, el entrevistador cierra con lo que tiene. La
transcripción entera va al documento de tarea como sección `Interview: A→B` (en inglés,
como todas las secciones del documento): es el registro crudo. Cada lado es una sesión de
Claude Code reanudada ronda a ronda con `resume`, así el entrevistado recuerda lo que ya
decidió y el autor escribe después desde su propia sesión de la entrevista.

**La entrevista escribe directo en la rama de tarea.** `grill-with-docs` edita
`CONTEXT.md` y crea ADRs mientras entrevista; pelear contra eso sale caro en prompt y en
fallos. El entrevistador tiene escritura solo sobre `CONTEXT.md` y `docs/`, en la rama de
tarea que ADR-0011 crea al arrancar. Todo termina en el PR: un término o ADR que no
sobrevive la revisión humana no se mergea. El destilado de PM a `docs/product/`
(ADR-0006) sigue igual en forma, pero escribe en ese mismo checkout y se commitea ahí:
la definición viaja en el PR con el código que la implementa.

## Opciones descartadas

- **Product Analyst se entrevista solo**: sin segundo agente no hay fuente nueva; es el
  tiro único con más pasos.
- **PM entrevista a Product Analyst**: las decisiones son del entrevistado, y el dueño
  de la definición es PM.
- **Sin tope de rondas, solo tope de costo**: una entrevista sin tope no termina, y el
  costo no dice cuándo se dejó de aprender.
- **El entrevistador solo propone términos y el sistema los escribe al aprobar**: obliga
  a un prompt que contradiga la skill y a un esquema de salida que la duplique.

## Adenda 2026-09-14: la consulta a PM durante la entrevista de solución

La primera corrida real (GH-12) escaló desde la entrevista Tech Lead efímero→Tech Lead
con siete preguntas para el humano, tres distintas. Dos ya las resolvía el glosario del
producto (el borde del vencimiento; `null` contra clave ausente es representación, del
Tech Lead) y la tercera era de PM: había dejado como vinculante "métricas vivas el día del
ship" en un repo sin infraestructura de métricas. El Tech Lead no tenía a quién
preguntar: PM ya había cerrado su turno y el prompt mandaba todo hueco de producto al
humano. El efímero incluso escribió "escalate it to the PM".

Decisión: la **consulta** (glosario del proceso). El Tech Lead entrevistado devuelve
`questions_for_pm`; al cerrar la ronda, PM las responde retomando su sesión de validación,
su respuesta entra en la transcripción y el efímero la ve en la ronda siguiente. Las
respuestas aclaran la definición: el sistema entrega de nuevo la sección de PM con las
respuestas agregadas como subsección `Clarifications` (agrega, no reescribe: PM no
reproduce su sección entera), la anterior queda como historia, y lo que cambia el
conocimiento escrito se destila otra vez (ADR-0006) en el checkout de la tarea, que la
etapa commitea al terminar la entrevista, también cuando escala: la aclaración vale aunque
otra pregunta quede para el humano. Lo destilado pasa el mismo chequeo de `## Jobs` que la
aprobación. Solo PM pregunta al humano; lo de infraestructura también, porque es quien
fijó el requisito.

Umbral del Tech Lead: la representación es suya (forma del JSON, nombres, empates); el
significado de un término es del glosario y se aplica tal cual; consulta solo cuando un
comportamiento observable no está definido o la definición contradice el código o los
datos. Sin la regla, la consulta se vuelve el nuevo escalado.

Fuera por ahora: el Developer no consulta; lo que encuentra al implementar va en su
entrega y el Tech Lead lo resuelve o consulta en la revisión. Una consulta abierta en
medio de una subtarea rompe el tope de dinero y turnos.

Costo: a lo sumo un turno de PM por ronda, dentro del tope de rondas; sin límite propio.

Opciones descartadas: PM como tercer participante de cada ronda (triplica turnos); PM
responde todo al final (obliga a planificar sobre huecos); tratar la consulta como
devolución Tech Lead→PM (contaría contra el límite de 2 y devolvería la tarea).

## Adenda 2026-09-14: una pregunta para el humano se plantea una sola vez

Las siete preguntas de GH-12 eran tres porque el sistema acumulaba por ronda lo que el
entrevistado devolvía en `questions_for_human`, y nadie responde al humano durante la
corrida: el entrevistado volvía a listar en la ronda N+1 lo que dejó abierto en la N, a
veces reformulado. La consulta lo quitó de la entrevista de solución; en la de definición
(Product Analyst→PM) seguía, y en la consulta podía volver por el otro lado: el Tech Lead
re-consultando lo que PM ya mandó al humano.

Decisión, en dos capas. El prompt: PM entrevistado y PM consultado devuelven solo lo nuevo
de la ronda; el Tech Lead no vuelve a consultar lo que PM derivó al humano. Para que la
regla no dependa de la memoria de cada sesión, lo ya registrado va numerado en el prompt
siguiente de los tres, bajo `## With the human`: es el único lugar donde el Tech Lead lo
ve, porque de la consulta solo recibe la prosa de PM. El grafo: al acumular descarta la
repetición literal (espacios y mayúsculas aparte), en el único punto por el que pasan las
dos entrevistas, y el motivo del escalado cuenta la lista ya depurada; lo acumulado llega
al estado también cuando un lado falla después, para que el escalado por fallo no lo
pierda. Una reformulación pasa: la defensa contra eso es el prompt, y se acepta antes que
deduplicar por significado.

Descartado: que el entrevistado devuelva cada ronda la lista vigente completa y el grafo
la reemplace en vez de extender. Permitiría retirar una pregunta resuelta después, pero
una pregunta olvidada al re-listar se perdería en silencio, que es peor que un duplicado.

## Adenda 2026-09-15: solo lo que deja la definición indecidible, y se retira lo que se resuelve

La tercera corrida de GH-12 escaló desde la entrevista de definición con dos preguntas
que no tenían que llegar al humano. Una no cambiaba la definición: PM lo escribió en la
misma respuesta ("it is a question for the human only insofar as it affects rollout
messaging") y la registró igual, porque el prompt mandaba al humano todo lo que ni el
pedido ni el conocimiento resolvían. La otra la respondió la ronda siguiente: el Product
Analyst mostró que un ADR del producto la decidía, pero una pregunta acumulada no tenía
camino de vuelta, y la lista no vacía escala al cerrar. La corrida anterior, con el mismo
pedido y el mismo conocimiento, se aprobó a la primera: la diferencia era muestreo.

Decisión, en dos partes. **El umbral**: una pregunta va al humano solo si una respuesta
distinta produciría una definición distinta y nada en el pedido ni en el conocimiento
elige una. Lo que no cambia la definición PM lo decide y lo deja escrito en su respuesta
como supuesto (glosario); el Product Analyst lo lleva a la definición desde su sesión.
**El retiro**: el dueño de la decisión (PM entrevistado, PM consultado) devuelve en
`withdrawn` los números, de la lista `## With the human` que recibió en esa ronda, de las
preguntas que esa ronda resolvió, con la respuesta en su prosa. El caso de uso las saca de
la lista antes de sumar las nuevas de la ronda, así los números son los que el prompt
mostró; un número fuera de la lista no retira nada. Escala solo lo que sigue abierto. Para
que el retiro ocurra a propósito y no por azar, el entrevistador también recibe
`## With the human` antes de las respuestas, y su prompt le pide traer el conocimiento que
cierre una pregunta, citando la fuente; retirar sigue siendo del dueño, porque las
decisiones son del entrevistado.

Esto no es lo que la adenda anterior descartó. Ahí el entrevistado re-listaba todo y el
grafo reemplazaba, y una pregunta olvidada se perdía en silencio. Acá el retiro es
explícito, por número y con respuesta escrita: lo que no se nombra se queda.

Descartado: que el entrevistador retire (el que pregunta decidiría); que el retiro sea por
texto (una reformulación no coincidiría); un campo estructurado para los supuestos (la
prosa de la respuesta ya llega al autor de la definición, que escribe desde su sesión).
