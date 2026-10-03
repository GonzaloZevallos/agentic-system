---
status: accepted
date: 2026-10-03
---

# La etapa 4 entra por UX y UI, con la validación final de PM adelantada para las tareas que tocan UI

El front de apostrophe salió de seis tareas del sistema (GH-19, GH-20, GH-5, GH-4, GH-9 y
GH-7) y no se parece a su diseño: el humano lo diseñó en Claude Design, el repo guarda las
28 pantallas como HTML estático en `docs/product/design-system/pantallas/`, y lo construido
tomó los tokens (paleta y fuente) pero no la estructura ni los componentes. Nueva venta mide
1037 px de ancho en un viewport de 390, los botones caen en Arial y los formularios usan
controles nativos donde el diseño tiene filas y controles segmentados.

Cuatro causas, todas medidas sobre los documentos y los transcripts de esas tareas:

- **Nadie es dueño de lo visual.** UI Designer, Design Lead, UX Designer y UX Lead esperan a
  la etapa 4 (§11); el flag `touches_ui` se marca y no dispara nada, y ningún prompt de rol
  menciona pantallas ni diseño.
- **El diseño se pierde entre el issue y el Developer.** Los issues nombran las pantallas;
  los planes del Tech Lead no mencionan `pantallas/` ni una vez y ningún brief nombra un
  archivo de pantalla.
- **Los Developers casi no las abrieron.** GH-20, que construyó Ventas, Nueva venta y el
  detalle de una venta, no abrió ninguno de esos tres archivos.
- **Nada lo verifica.** Los chequeos son de comportamiento, lint y tipos; la revisión del
  Tech Lead confirma que el CSS usa tokens, no que la pantalla se parezca a la suya.

## Decisión

Decidido con el humano en un grill el 2026-10-03.

**Alcance.** Entran UX Designer y UX Lead, UI Designer y Design Lead, solo en el camino de
feature y solo cuando la tarea tiene `touches_ui`; una sola marca prende las dos ramas.
Quedan para después UI en bugs visuales y Tech Writer; UX no entra en bugs (§13).

**En la definición**, después de que PM valida el PRD y en el orden de la sección 2:

1. UX Designer escribe el **flujo** de cada job (glosario de Proceso). Si la tarea no cambia
   el flujo, lo dice.
2. UX Lead lo valida con un **recorrido** del prototipo en un navegador. Donde el flujo pasa
   por una pantalla que el prototipo no tiene, evalúa ese tramo sobre el texto y lo marca
   "sin prototipo".
3. UI Designer escribe el **diseño de pantallas** sobre el flujo validado; Design Lead lo
   valida.
4. PM consolida el PRD. Lo que UX marcó como "impide cumplir el job" es pregunta para el
   humano; la fricción es **observación al diseño**, que llega en el pull request. El sistema
   nunca cambia el diseño del humano por su cuenta.

**En el desarrollo**, el brief de cada subtarea de front nombra sus pantallas, y el Developer
mira sus **comparaciones** antes de entregar.

**Al final**, solo en tareas con `touches_ui`, se adelanta la validación final de PM y mira
solo dos cosas: el recorrido de UX Lead sobre la **app de muestra** y las comparaciones que
juzga Design Lead, con fidelidad igual a ojo (misma estructura, jerarquía, tipografía,
colores, espaciado y componentes). PM devuelve a Tech Lead. Los leads no aprueban al cierre
ni devuelven al Developer: la regla de §13 sigue.

**Lo que declara el producto**: un bloque `design` opcional en `.agentic/stack.yaml` con el
prototipo, el comando que levanta la app de muestra y el que saca las comparaciones (glosario
de Conocimiento de producto: Diseño del producto). Sin el bloque, la tarea corre sin
recorrido real ni comparaciones y su documento lo dice.

**Modelos**: los de §10, pensadores en Sonnet y validadores en Opus.

## Alternativas descartadas

- **Un control mínimo en la revisión del Tech Lead**, sin roles nuevos: más barato, pero deja
  lo visual en manos de un validador cuyo oficio es el código.
- **Design Lead que devuelve directo al Developer**: feedback más temprano, pero reintroduce
  lo que §13 reemplazó (los leads no validan al cierre ni devuelven).
- **Comparación pixel a pixel** con umbral: se rompe con datos, fuentes y largos de contenido
  distintos, y no ve la estructura.
- **Heurísticas sin navegar** como fuente de UX Lead: con el mismo texto que el pensador,
  ratifica en vez de validar (§10).
- **Toda fricción como pregunta para el humano**: pausaría cada tarea por detalles que no
  impiden el job.
- **La validación final de PM completa** desde ahora: sin QA, PM no tiene evidencia propia
  del comportamiento y ratificaría la revisión del Tech Lead.

## Consecuencias

- §11 cambia: parte de la etapa 4 y una parte de la validación final de PM entran antes que
  la etapa 2. El resto de la validación de PM sigue esperando a QA.
- Cada rol nuevo se gana el lugar (§11) en la primera corrida que lo usa: el rediseño de
  Ventas en apostrophe como feature, las mismas pantallas que GH-20 hizo sin estos roles.
  Cada hallazgo, devolución y observación lleva su rol, y se cuenta cuántos cambiaron el
  resultado por rol, junto con el veredicto del humano sobre las comparaciones.
- Una tarea con UI cuesta más en la definición y suma una validación al final. El tope de
  tarea tiene que absorberlo.
