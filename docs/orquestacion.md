---
roles: [pjm, tech_lead]
---

# Orquestación

Cómo trabaja la sesión que orquesta el sistema: lo que hace, lo que nunca hace y cómo se
traspasa. La memoria es del proyecto y no de quien orquesta; lo que antes vivía en una memoria
privada está acá, versionado y revisado en pull requests. Las reglas generales que el dueño
dio están en [decisiones-del-dueño.md](decisiones-del-dueño.md); esta página dice cómo se
aplican al orquestar.

## Qué hace el orquestador

- Lanza, retoma, aterriza y mergea las corridas.
- Escribe issues y edita su línea `Blocked by:`, que debe ser la primera línea del cuerpo.
- Contesta los escalados según las reglas del dueño.
- Nunca escribe código de producto ni lo delega a sub-agentes: el código de producto lo
  escribe el sistema, por issue.
- Cuando contesta un escalado con el pull request de la tarea ya abierto y la respuesta
  enuncia una regla general, además abre el issue chico que solo agrega esa regla: la regla
  dada con el pull request abierto no entra por el cierre de la tarea.

## Mergear

- Mergea un pull request cuando el Tech Lead lo aprobó y el CI declarado está verde, y
  después avisa al dueño. No espera al dueño.
- Nunca mergea los pull requests de lecciones del playbook: los cura antes, con cada lección
  de 240 caracteres o menos, y los mergea el dueño.

## Trabar una tarea

- El orquestador quita la etiqueta `agentic` y comenta el motivo y qué espera la tarea.
- La etiqueta vuelve cuando se mergea lo que bloqueaba.
- Cuando todas las partes de una tarea partida están cerradas, el orquestador cierra la tarea
  madre.

## Entorno local

- La producción (`:8000`, los datos reales del dueño) y la instancia de prueba (`:8001`) no las
  escribe ninguna corrida ni el orquestador.
- Los secretos del entorno de producción no se leen nunca.
- Un túnel público hacia producción se anuncia siempre como público.

## Stage0

- Las corridas se ejecutan desde el clon stage0, fijado a master y refrescado después de cada
  merge.
- Los pollers topan las corridas simultáneas con `AGENTIC_MAX_CONCURRENT_RUNS`; el valor que
  rige es el del `.env` de cada stage0, no un número escrito acá.
- El `.env` de stage0 lo edita el orquestador solo cuando el dueño lo pide.

## Tablero

El espejo en GitHub Project muestra Etapa (el campo Status) y Bloqueo. Una tarea trabada
aparece como Trabada, con lo que espera.
