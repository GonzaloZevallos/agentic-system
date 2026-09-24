---
status: accepted
date: 2026-09-24
---

# La verificación del publicador acepta el literal `${DS_PROMETHEUS}` que Grafana realmente almacena, no un `uid` sustituido que ningún camino correcto produce

`scripts/publish_dashboard.py` (ADR-0032, PR #124) publica bien y siempre termina en
rojo. `published_refusal` exigía que todo `datasource.uid` del dashboard **almacenado**
fuera igual a `GRAFANA_DS_UID` (ADR-0032, "El guard: un solo spelling" / "Dos
verificaciones contra la instancia": *"que todo `datasource.uid` de lo almacenado sea
`ds_uid`"*). Esa cláusula describe un Grafana que no existe: `POST
/api/dashboards/db` no sustituye nada (el propio docstring de `body()` ya lo decía, y
la corrida real del 2026-09-24 sobre master `c60b8ef` lo confirma), así que
`body(dashboard, ds_uid, ds_name)` escribe únicamente `DS_PROMETHEUS.current`; los 49
paneles y targets guardan el literal `${DS_PROMETHEUS}` tal cual está en el archivo. La
cláusula vieja pedía una igualdad que ningún camino correcto de este script puede
producir — rojo estructural, no ambiental.

## Lo que cae de ADR-0032

> "el `current.value` de `DS_PROMETHEUS` sea `ds_uid`" — **se mantiene** (clause 3
> abajo).
> "que todo `datasource.uid` de lo almacenado sea `ds_uid`" — **cae**: se reemplaza por
> "que todo `datasource.uid` de lo almacenado sea el literal `${DS_PROMETHEUS}`".

ADR-0032 no se edita (ADR-0010, ADR-0029: un ADR aceptado se sucede, nunca se
reescribe); esta es la sucesora de esa única cláusula.

## El predicado, tres cláusulas sobre `stored`

`published_refusal(stored, ds_uid) -> str | None` devuelve `None` sólo cuando las tres
valen:

1. `set(datasource_uids(stored)) == {"${DS_PROMETHEUS}"}`, no vacío;
2. existe al menos una variable `DS_PROMETHEUS` en `templating.list`;
3. **toda** variable con ese nombre tiene `current.value == ds_uid`.

El disyuntor `uid == ds_uid` se retira: nuestro único camino correcto no puede
producirlo, y una rama de aceptación que ningún camino correcto puede satisfacer es
código muerto que ningún test puede ejercitar honestamente (ADR-0023, en su forma más
severa: no sólo sin cobertura, sino inalcanzable).

`type: "datasource"` y la unicidad de la variable quedan **fuera a propósito** de este
predicado: son nuestras — `body()` nunca las toca, y el file guard de
`tests/test_dashboard.py` ya las fija sobre el archivo que enviamos. Repetirlas sobre la
copia del servidor no compraría ninguna discriminación que las cláusulas 1 y 3 no
tengan ya, y sí compraría un modo permanentemente rojo si Grafana algún día normalizara
ese campo. La cláusula 3 es universal (`for v in ds_variables`, no `ds_variables[0]`)
precisamente para que la unicidad pueda quedar fuera sin que el predicado haga una
elección arbitraria.

## Tabla de atestación

Cada fila lee **atestada**, con el fragmento verbatim de la corrida del 2026-09-24
sobre master `c60b8ef` que la respalda. Ninguna cláusula queda asumida.

| Cláusula | Estado | Evidencia verbatim |
| --- | --- | --- |
| 1. `datasource.uid` == literal `${DS_PROMETHEUS}` | atestada | `published but not verified: stored dashboard has datasource.uid(s) != 'grafanacloud-prom': ['${DS_PROMETHEUS}']` — la lista de "wrong" que la corrida real devolvió contiene un único elemento, el literal, y nada más. Grafana no agregó ninguna referencia propia al guardar; por eso la cláusula puede ser una igualdad (`== {"${DS_PROMETHEUS}"}`) y no sólo una contención. |
| 2. variable `DS_PROMETHEUS` presente en `templating.list` | atestada | La v10 publicada (la misma corrida) trae `templating.list[0]` con `"name": "DS_PROMETHEUS"`: la variable sobrevive el POST tal como el archivo la manda, sin que Grafana la elimine ni la renombre. |
| 3. `current.value == ds_uid` de esa variable | atestada | `templating.list[0].current.value = "grafanacloud-prom"`, y `GET /render/d-solo/agentic-system?panelId=204` de esa misma v10 respondió 200 con datos: el `current` que `body()` escribe es exactamente lo que hace que los paneles resuelvan. |

## La asimetría que gobierna los tests

Una rama de **aceptación** sólo puede admitir una forma que `body()` seguido de `POST
/api/dashboards/db` pueda producir — ese es el conjunto de lo que "correcto" significa
acá. Una rama de **rechazo** sólo tiene que nombrar un estado que el mundo pueda dejar
en Grafana, un conjunto estrictamente más amplio, porque `published_refusal` juzga el
contenido almacenado y no nuestra intención. Por esto el test que hoy se llamaba
`test_published_refusal_rejects_the_v8_state_with_ds_prometheus_left_literal` estaba
mal nombrado: su entrada (`${DS_PROMETHEUS}` en el panel, `current.value` ya resuelto)
es la forma que Grafana devuelve en **todo** publish exitoso, no el v8 real. Se
renombra y se re-apunta a `current` en el sentinela del repo
(`{"selected": false, "text": "", "value": ""}`) sobre un literal correctamente
resuelto: eso es el v8 real, y no tenía test hasta esta tarea.

Los controles positivos derivan de `body()`, nunca se tipean a mano: una creencia
tipeada a mano sobre la respuesta de Grafana es exactamente lo que produjo este bug.
Uno compone sobre `_minimal_valid_dashboard()`; el otro compone sobre el archivo real
(`_load()`), la única aserción del repo que dice "el dashboard que publicamos pasa el
chequeo con el que lo publicamos", re-probada por `checks.tests` en cada entrega.

## Cobertura del rechazo (invariante corregida)

"Exactamente una entrada rechazada por rama" se vuelve **al menos** una: con esa regla
literal la entrada de dos variables (clause 3 universal, la segunda nombrando otra
instancia) quedaría prohibida, y es la única entrada que falla bajo una implementación
`ds_variables[0]` y pasa bajo la universal — la que prueba el cambio, no sólo lo
acompaña.

## Taxonomía de mensajes en `main()`

Los códigos de salida no cambian (0/1/2). Todo corte **antes** del POST abre con
`not published:` (nada quedó escrito: reintentar es seguro); todo corte **después**
abre con `published but not verified:` (algo quedó escrito y puede no servir:
inspeccionar lo vivo y, si hace falta, restaurar la versión anterior desde el
historial de Grafana, nunca reintentar a ciegas) — el prefijo que la rama de rechazo ya
usaba, extendido ahora también al `GET` de verificación fallido. La rama de rechazo
concatena el motivo de `published_refusal`, que nombra la cláusula.

## Un solo hogar normativo

El criterio se dice una sola vez, con sus tres cláusulas numeradas, en el docstring de
`published_refusal`. Este ADR lo cita y no lo repite; `observability/grafana/README.md`
borra la frase falsa ("corta si queda alguna referencia a `${DS_PROMETHEUS}` sin
resolver") y apunta al docstring en vez de enumerar. Misma disciplina que
`CODING_STANDARDS.md` §6 / ADR-0021 y que ADR-0029 sobre el conjunto de exclusión del
scorecard: nombrar el lugar que carga el alcance, nunca repetir su contenido.

## Opción B descartada (sustituir el literal antes del POST)

Hace no portable el dashboard publicado, lo vuelve no comparable byte a byte con el
archivo que `editable: false` existe para hacer autoritativo, deshace "un solo
mecanismo de datasource" (ADR-0032) un día después de esa decisión, y compra una
igualdad que no prueba nada que la cláusula 1 no pruebe ya.

## Riesgos conocidos

- **La atestación describe una respuesta de un servidor en una fecha.** Grafana Cloud
  se actualiza sin aviso, y el script corre rara vez. Un rojo futuro en una cláusula
  nombrada es **primero** una hipótesis sobre el servidor y sólo después sobre el
  archivo. Orden de diagnóstico: leer el dashboard almacenado (`GET
  /api/dashboards/uid/agentic-system`), compararlo contra esta tabla cláusula por
  cláusula, y sólo entonces tocar el predicado — en tarea propia, con su propio
  registro corrido, nunca como un aflojamiento al momento de la incomodidad.
- **Nada en el loop ejercita el transporte.** Ningún rol de esta tarea tiene red; las
  dos funciones puras (`body`, `published_refusal`) son lo único que `tests` corre.
- **`scripts/` queda fuera de `[tool.mypy] files` por construcción** (ya registrado en
  ADR-0032): el publicador nace tipado y nadie lo chequea.
- **Entre dos publicaciones nada detecta drift.** `published_refusal` sólo corre al
  publicar; una edición manual del dashboard vivo entre dos corridas del script no la
  ve nadie hasta la próxima publicación.

## Opciones descartadas

- **Restaurar `type: "datasource"` y la unicidad al predicado sobre `stored`**: no
  compran discriminación que las tres cláusulas no tengan, y agregan un modo
  permanentemente rojo si Grafana normaliza esos campos algún día.
- **Indexar la cláusula 3 en `ds_variables[0]`**: hace que "toda variable" y "la
  primera" coincidan mientras haya una sola, y deja de distinguirlas cuando hay dos —
  exactamente el caso que este defecto necesitaba cubrir.
