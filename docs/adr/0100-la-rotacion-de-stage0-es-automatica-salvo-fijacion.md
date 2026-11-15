---
status: accepted
date: 2026-11-14
---

# La rotación de stage0 es automática salvo que el dueño la fije

GH-634, parte 4 de GH-622. Hasta ahora stage0 se movía a mano: `scripts/stage0.sh` con un sha, o
`agentic stage0 rotate` ejecutado por una persona. Este documento hace automático el segundo
camino y enmienda la adenda del 2026-09-17 de [ADR-0002](0002-repos-separados.md) y
[ADR-0017](0017-el-sistema-es-su-propio-segundo-producto.md), que decían que actualizar stage0 es
un acto explícito en que se nombra el commit. Cómo revierte y cómo el poller sigue el cambio ya
está en [ADR-0097](0097-la-reversa-de-stage0-rechaza-el-sha-nuevo-por-latidos-ausentes.md) y
[ADR-0098](0098-el-poller-sigue-la-rotacion-de-stage0-con-un-re-exec-y-el-rotador-revierte-lo-que-nadie-adopto.md);
no se repite.

## Decisión

**Qué cambia en la regla.** Lo que ADR-0002 protege sigue igual: el sistema nunca escribe en el
repositorio desde el que corre, y stage0 sigue siendo un clon fijado a un sha. Lo que cambia es
quién lo mueve. Ya no hace falta que una persona nombre el commit: lo hace una regla pura
(`target`), siempre hacia un commit con CI verde de master, nunca hacia una rama ni hacia lo
que haya a mano. "Explícito" pasa a querer decir que la regla es explícita, y que el dueño
puede anularla con un pin.

**La rotación corre sola.** El temporizador de usuario `agentic-stage0-rotate` ejecuta
`agentic stage0 rotate` 2 minutos después del arranque (`OnBootSec`) y luego cada 5 minutos
(`OnUnitActiveSec`). Es una sola unidad por máquina, no una por producto, y no tiene
Persistent=: una ronda perdida no se recupera, la siguiente llega en menos de 5 minutos.

**A dónde se mueve.** Sin pin, `target` elige el commit verde más nuevo que desciende
estrictamente de `current`, que no fue rechazado, dentro de una ventana de 10 commits del
historial de primer padre de master. Si no hay uno así, el resultado es `Keep` con
`no_green_in_window` o `up_to_date`. El pin `AGENTIC_STAGE0_PIN` se rige por las reglas 1 a 4
de `target`:

1. un valor que no es hexadecimal de 7 a 40 caracteres da `unreadable`;
2. un valor igual a `current` da `pinned` (quedarse no es adoptar);
3. un valor rechazado da `rejected`;
4. cualquier otro valor válido mueve stage0 a ese sha, también hacia atrás: es lo único que va
   hacia atrás.

Un pin vacío en el `.env` significa sin pin, porque el CLI lo lee con or None. Por eso el pin
no "sostiene la rotación" en general: solo detiene el movimiento cuando `current` ya es el pin;
mientras no lo es, la rotación lo lleva allí.

**Dónde vive el pin.** El pin y el resto del `.env` viven en `<root>/current/.env`, que es
donde el servicio tiene su `WorkingDirectory`. `FilesystemStage0Store` copia ese `.env` a cada
stage nuevo (`copy_env`), así que editar el `.env` de un stage viejo, o el de
`~/.agentic-stage0`, no tiene efecto: el siguiente rotate no lo lee.

**Las corridas vivas terminan en su stage0.** `prunable` nunca borra un stage en uso, ni
`current` ni `previous`, y conserva los 2 más nuevos (`keep`). Una corrida iniciada antes de
la rotación sigue ejecutando su propio código hasta terminar.

**El poller sigue a `current`.** Hace re-exec sobre la ruta real resuelta, nunca por el
enlace: `reexec_due` decide el destino, `reexec_argv` y `reexec_env` arman argv y entorno, y
`reexec_blocked` corta el ciclo (ADR-0098). Un stage0 instalado como enlace conserva la ruta del
enlace en la unidad del poller.

## Riesgos conocidos

- **Una reanudación toma `current`.** No queda atada al stage0 en que empezó la tarea. La
  comparabilidad es entonces por invocación (`system_version` en los spans), y una tarea puede
  cruzar dos líneas de base.
- **El documento de tarea guarda solo la última.** `FilesystemTaskDocuments.save` estampa
  `system_version` y `system_baseline` en cada escritura, así que el documento conserva los
  valores de la última invocación. eval compare atribuye a la última línea de base la tarea
  que cruzó una, como ya pasaba tras un movimiento manual. Registrarlos por invocación y decidir
  cómo cuenta eval compare una tarea así queda como trabajo aparte.
- **La reversa puede dejar una reanudación en el código anterior.** Pasados 15 minutos desde el
  cambio, si un poller de `expected` en el `SwitchRecord` no volvió al sha nuevo en su latido,
  `rollback_due` lo revierte (ADR-0097). Una reanudación que arranque entre el cambio y la
  reversa corre sobre el código nuevo; una que arranque después, sobre el anterior.
- **La primera rotación no se puede revertir**, porque los pollers viejos no escriben latidos
  (ADR-0098).
- **Un pin inválido detiene la rotación**: da `unreadable` en cada ronda hasta que se corrija.

## Transición, a cargo del dueño

Hay que reinstalar cada producto con scripts/install-poller.sh <producto>
~/.agentic-stage0s/current --rotate y reiniciar cada poller una vez con systemctl --user
restart. enable --now no reinicia una unidad que ya está activa (es comportamiento de
systemd), así que sin el reinicio el poller viejo sigue en su código. Las corridas
sobreviven al reinicio porque la unidad del poller usa `KillMode=process`.

## Consecuencias

- ADR-0002 y ADR-0017 no se editan; esta enmienda es la que lee quien llegue desde ellos.
- `scripts/stage0.sh` sigue fijando a mano un directorio con nombre; la rotación bajo
  `~/.agentic-stage0s` es de `agentic stage0 rotate`.
- El glosario del contexto `process` define **Rotación de stage0** y **Fijación de stage0**.
