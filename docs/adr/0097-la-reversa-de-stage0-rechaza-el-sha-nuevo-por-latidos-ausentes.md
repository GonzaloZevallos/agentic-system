---
status: accepted
date: 2026-11-14
---

# La reversa de stage0 rechaza el sha nuevo cuando faltan los latidos esperados

GH-738, paso 2 de GH-735. `agentic stage0 rotate` ya adopta un sha y escribe `switch.json` con
los pollers que se esperan de vuelta (`expected`). Este documento fija qué hace la rotación
cuando, pasada la gracia de 15 minutos, alguno de ellos no volvió.

## Contexto

Un sha que pasa el smoke puede dejar a los pollers sin arrancar: el smoke prueba el checkout, no
la flota. Sin una señal de vuelta, la flota queda en un checkout al que nunca regresó, y nadie lo
nota hasta que una persona mira. La señal existe: cada poller escribe un latido
(`<raíz>/heartbeats/<clave>.json`) con el checkout que corre; la escritura es el trabajo de
GH-736, y sin escritor la reversa no hace nada.

## Decisión

Si `rollback_due` dice que un poller esperado no tiene latido sobre el checkout nuevo, la
rotación rechaza el sha nuevo y vuelve atrás, en este orden: `reject`, `switch_current` al
anterior, `escalation.run()`. Una falla de la máquina, o una parada deliberada pasada la gracia,
da la misma señal que un commit malo, y se acepta: **un rechazo falso, que se deshace a mano
sacando la línea de `rejected`, se prefiere a una flota de pollers en un checkout al que nunca
volvió.**

- Sin `previous`, o con `previous` ausente de los stages, la corrida lo dice
  (`rollback: no previous`, `rollback: previous missing`) y sigue a adoptar.
- `switch.json` se conserva tras la reversa: protege al sha nuevo de `prune` hasta la próxima
  adopción. El registro queda inerte en cuanto `current` deja de ser `new`.
- El reloj se lee una vez por adopción, después de `read_all()`: `switched_at` y `expected` salen
  de la misma lectura. La reversa usa `now`, nunca el reloj.
- Una entrada de `heartbeats/`, o su ausencia, ahora puede causar `reject` y `switch_current`. No
  hacen falta límites nuevos: la raíz es del mismo usuario, que ya puede escribir `rejected` y
  `current`, y el adaptador valida cada valor (`Infinity`, `stop` o fuera de rango se leen como
  `Unreadable`).

## Riesgos conocidos

1. Una falla de la máquina, o una parada deliberada pasada la gracia, se ve igual que un commit
   malo. Se deshace sacando el sha de `rejected`.
2. Un poller que llegó al sha nuevo, latió una vez y murió cuenta como llegado: no se revierte.
3. Si el escritor de latidos usa otra raíz, `expected` queda vacío y la reversa no hace nada, sin
   aviso. El escritor toma la raíz de `stage0_paths`, la misma raíz resuelta que el store.
4. Una ronda del poller más larga que la gracia de 15 minutos parece un poller trabado.
5. Nada se revierte hasta que existan los latidos de GH-736.

## Consecuencias

La rotación gana un `read_all` por corrida, lineal en los pollers. No hay señal nueva: las
líneas `rollback: …` son salida de la CLI. El ADR de GH-736 fija el formato del escritor; este
solo lo lee.
