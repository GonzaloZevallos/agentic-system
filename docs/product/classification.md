# Clasificación: de qué tipo es una tarea y cuándo el sistema no puede decidirlo

Qué hace PjM con un pedido que entra por la bandeja: de qué tipo lo declara, qué queda fuera
de esa decisión, y cuándo no clasificar es la respuesta correcta.

## Jobs

- **Cuando** reporto que el producto hace algo distinto de lo que su definición dice, y en el
  mismo pedido digo que se puede arreglar el código o actualizar la definición, **quiero** que
  el sistema arranque igual el camino de bug, **para** no tener que decidir yo cuál de los dos
  cede antes de que nadie haya reproducido nada.
  _Me salió bien si_: el pedido llega a Product Analyst sin que nadie me pregunte nada, y la
  decisión de quién cede me llega después, ya razonada.

- **Cuando** escribo dos pedidos con la misma forma, **quiero** que se clasifiquen igual,
  **para** poder confiar en la plantilla que uso y no aprender a evitar frases que hacen
  escalar la tarea.
  _Me salió bien si_: dos pedidos que citan una definición y un comportamiento observado
  siguen el mismo camino, aunque uno de los dos comente cómo se podría resolver.

- **Cuando** el sistema me escala una tarea porque no pudo clasificarla, **quiero** que sea
  porque de verdad no se sabe de qué tipo es, **para** que un escalado de clasificación
  signifique algo y no me haga de portero de lo que el sistema ya podía decidir.
  _Me salió bien si_: al abrir el escalado, mi respuesta aporta información que el pedido no
  tenía, en vez de repetir lo que el pedido ya decía.

## Comportamiento

### Una discrepancia documentada es un bug

Un pedido que cita **una definición** y **un comportamiento observado** que no coinciden se
clasifica `bug`, con confianza alta. Es la forma canónica de un bug: el producto hace algo
distinto de lo que se definió y la tarea es hacer que coincidan.

No hace falta que el pedido diga cuál de los dos lados está mal. **Que el pedido ofrezca las
dos salidas —arreglar el código o actualizar la definición— no cambia la clasificación y no
baja la confianza.** Elegir quién cede es exactamente lo que el camino de bug existe para
hacer: Product Analyst reproduce y dictamina, y PM valida el dictamen y, cuando la que cede
es la definición, la reescribe. Un pedido que ofrece las dos salidas no está pidiendo dos
tipos de tarea: está describiendo, con honestidad, el trabajo que el camino de bug ya tiene
repartido entre dos roles.

El caso que lo fija: gym-saas #20 (`last_check_in_day` presente como `null`, la definición
dice ausente) cerraba con "Either the key is omitted when there is no check-in, or the
definition says `null`", y esa frase sola lo mandó a `waiting_on_human` a los 2 turnos y
$0.01. Los issues #18 y #19, de la misma forma y sin esa frase, se clasificaron `bug` y
llegaron a pull request sin intervención. La frase que describe el trabajo de PM no puede ser
lo que impide que PM lo haga.

### El pedido no elige quién cede

Lo anterior vale también cuando el pedido **ya eligió**. Un pedido que dice "la definición
dice X, el producto hace Y, Y es lo que queremos, actualicen la definición" sigue siendo un
`bug`: hay una discrepancia documentada, y quién cede lo decide PM después de que Product
Analyst reproduzca, no quien escribe el pedido. Es el caso inverso al de gym-saas #20 —uno
ofrece las dos salidas, el otro propone una— y se clasifica igual, porque lo que clasifica es
la discrepancia, nunca la resolución propuesta.

Ese camino no cuesta trabajo de más: si PM le da la razón al pedido, valida el dictamen,
reescribe la definición y la tarea cierra sin construir nada. Clasificarlo feature sí
costaría, porque mandaría a construir comportamiento que el producto ya tiene.

### Pedir comportamiento nuevo no es una discrepancia

La distinción que sí decide el tipo es otra: si lo que el pedido quiere es que el producto
haga algo que **ninguna definición dice** que haga, es una feature, aunque el pedido lo
escriba con forma de queja o pida "actualizar la definición". La señal es el comportamiento
observado: sin un comportamiento observado que contradiga lo escrito, no hay discrepancia,
hay un pedido de comportamiento nuevo.

Un pedido que cita una definición *y además* pide comportamiento nuevo sí fija dos tipos de
verdad, y ahí la confianza baja es correcta.

### Cuando el producto es el propio sistema

Una tarea sobre el sistema agéntico entra **siempre** como platform enhancement (ADR-0017),
sin PM y sin flags. Para esos pedidos la regla de la discrepancia no decide nada: da igual si
citan el README, un ADR, un glosario o el prompt de un rol, y da igual si exhiben una corrida
que no hace lo que esa documentación dice. El tipo es platform enhancement y el camino es el
de plataforma. Un pedido sobre el sistema nunca sale clasificado `bug`.

La regla de la discrepancia gobierna, entonces, a un producto con definiciones vigentes y un
PM que las mantenga, que es el único lugar donde "quién cede" es una decisión que alguien
tiene que tomar.

### Qué queda para la confianza baja

No clasificar sigue siendo una salida legítima, y el escalado por clasificación sigue siendo
una de las clases de escalado. Queda para:

- un pedido que **no cita una definición ni un comportamiento observado** concretos: no hay
  con qué decidir si algo se rompió o si se está pidiendo algo nuevo;
- un pedido cuyo **tipo** es ambiguo: bug contra feature (arriba), o trabajo sobre el producto
  contra trabajo sobre el sistema;
- un pedido que no se entiende.

No queda para la ambigüedad sobre **quién cede**, ni sobre qué capacidad toca, ni sobre cuánto
trabajo es, ni sobre cómo se implementa: nada de eso es el tipo de la tarea, y todo eso lo
deciden roles que corren después.

### La clasificación decide el camino, no la solución

La clasificación es una decisión de **tipo y de ruteo**. Todo lo que sigue —qué se reproduce,
qué cede, qué se construye— es de los roles del camino, y PjM no lo anticipa ni lo necesita
para clasificar. Por eso una clasificación correcta puede convivir con un final en el que la
que se corrige es la definición: un bug cuyo dictamen PM valida como comportamiento esperado
cierra **descartado**, y uno cuya definición PM reescribe sigue siendo un bug bien
clasificado.

### Lo que esta definición no cambia

- **El escalado por clasificación no desaparece**, ni cambia su clase de escalado, ni su
  ruteo, ni el tope de tarea ni el límite del par.
- **El caso de uso de clasificación no gana reglas propias.** Lee el tipo y la confianza que
  el rol devuelve y rutea; meter acá una regla de discrepancia mudaría al código una decisión
  que es del rol, y que esta definición ya escribe en palabras.
- **El cierre descartado** de un bug cuyo comportamiento es el esperado queda como está.

## Métricas

- **Escalados de clasificación sobre pedidos con discrepancia citada** (métrica de cabecera):
  tareas con clase de escalado de clasificación cuyo pedido citaba una definición y un
  comportamiento observado, sobre el total de pedidos con esa forma. Objetivo: cero. Hoy,
  gym-saas #20 la deja por encima.
- **Consistencia entre pedidos de la misma forma**: dos pedidos con la misma estructura
  (definición citada, comportamiento observado, pasos) se clasifican igual, ofrezcan o no una
  resolución. Se verifica sobre el set de evaluación y, cuando haya que diagnosticar,
  comparando documentos de tarea; #18 y #19 contra #20 son la evidencia de que hoy no se
  cumple.
- **Pedidos sobre el sistema clasificados como platform enhancement**: proporción de tareas
  cuyo producto es el sistema que salen con ese tipo. Objetivo: 100%, sin importar qué
  documento cite el pedido.
- **Bugs que llegan a dictamen sin intervención humana**: proporción de tareas clasificadas
  `bug` que alcanzan el dictamen de Product Analyst sin un escalado previo. Objetivo: 100%
  para los pedidos que citan definición y comportamiento.
- **Costo de un escalado de clasificación**: lo que gastó la tarea antes de pausar. Un
  escalado barato no es un escalado bueno: los $0.01 de #20 son la marca de una decisión que
  el sistema podía tomar y no tomó.
