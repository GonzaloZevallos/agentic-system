---
status: accepted
date: 2026-09-24
---

# El dashboard se publica por script, con el datasource como variable y el estado de instancia inyectado al publicar

La versión 8 del dashboard `agentic-system` (2026-09-24T00:38Z) quedó publicada, verde y
**sin un solo dato**. `observability/grafana/dashboard.json` era un template de import
(`__inputs: DS_PROMETHEUS`) y se publicó con `POST /api/dashboards/db`, que no sustituye
inputs como sí lo hace el importador de la UI: los 101 `datasource.uid` del archivo
quedaron con el literal `${DS_PROMETHEUS}`, `GET /api/datasources/uid/${DS_PROMETHEUS}`
devolvió 404, las variables quedaron sin opciones y los paneles sin series. Los 19 tests de
`tests/test_dashboard.py` pasaron sobre ese archivo: `_exprs` camina `panels[].targets[].expr`
y nunca mira `templating` ni un `datasource.uid`, así que la suite era ciega a la clase de
defecto entera.

El defecto no es de datos. El mismo día, `count by (product, system_baseline, arm)
(last_over_time(agentic_role_cost_usd[30d]))` devolvió 35 series `bl-2026-09-23/default/
agentic-system` y 342 sin label de línea de base.

## Un solo mecanismo de datasource: la variable, y `__inputs` se va

El archivo tenía dos formas de resolver el datasource y ninguna completa. Desde acá tiene
una: una variable de plantilla `DS_PROMETHEUS` de tipo `datasource`, `query: "prometheus"`,
y **`__inputs` borrado en el mismo diff**. Dejar las dos era el defecto de fondo: en un
import por UI, Grafana sustituye las 101 ocurrencias literales por el uid elegido y la
variable queda muerta, así que un guard escrito sobre la variable protegería un mecanismo
que ese camino no usa.

Se eligió sobre `POST /api/dashboards/import` por evidencia y no por preferencia. Ningún rol
de esta tarea tiene red (`DEVELOPER.tools == EXECUTE`, sin `WebFetch`), así que "el endpoint
`import` sustituye `inputs`" es hoy lo que ADR-0020 y la quinta adenda de ADR-0011 obligan a
no dar por cierto sin correrlo. Del otro camino sí hay evidencia corrida: la v9 está
publicada por `POST /api/dashboards/db` con la variable puesta, y los paneles del Scorecard
devuelven (autonomía 1.0, fallos/tarea 0.5, costo/tarea 27.45, 4 terminadas).

**Lo que la v9 prueba, acotado**: que un `current` con `text` = nombre visible del datasource
y `value` = su uid, publicado por `POST /api/dashboards/db`, resuelve `${DS_PROMETHEUS}` en
paneles y variables y devuelve series. No prueba nada sobre `text == value`, y por eso no se
publica esa forma.

## El repo no lleva estado de instancia; lo inyecta el publicador

`current` de `DS_PROMETHEUS` en el archivo del repo es exactamente
`{"selected": false, "text": "", "value": ""}`, y el guard **exige esa forma literal**: así
"el repo no lleva estado de instancia" pasa de promesa a test, y la sustitución del
publicador queda load-bearing y visible en vez de opcional. Ni `grafanacloud-prom` ni
`grafanacloud-heartyfjord3250-prom` aparecen en ningún archivo versionado.

El uid sale de `GRAFANA_DS_UID` del entorno y **el nombre sale de la instancia**, no de un
segundo valor que alguien tipea: la llamada que el publicador ya hace como puerta previa,
`GET /api/datasources/uid/$GRAFANA_DS_UID`, devuelve el objeto con su `name`. Con eso
`body(dashboard, ds_uid, ds_name)` escribe
`current = {"selected": true, "text": ds_name, "value": ds_uid}`, los mismos bytes que
devolvieron datos en la v9, con un solo insumo de entorno y sin ninguna afirmación
transferida. `GRAFANA_DS_NAME` no entra: un segundo valor tipeado es un segundo valor que
puede estar mal, y acá la instancia es la fuente de verdad.

## El import por UI se retira, en vez de quedar como un fallback que no funciona

`hide: 2` más `__inputs` ausente más "camino manual conservado" son tres cosas que no pueden
ser verdad juntas: un operador que importe el archivo por UI no ve ningún selector, no
recibe ninguna pregunta del importador, y se lleva un dashboard sin datos y sin síntoma —la
v8 otra vez—. Y bajar `hide` a `0` para sostener ese camino compra una compuerta que nadie
de esta tarea puede verificar (que Grafana Cloud resuelva el datasource por default) para
algo que el script ya cubre.

Entonces: el script es el único camino de publicación documentado. `observability/grafana/
README.md` (líneas 31-34) se reescribe entero y dice explícitamente que **pegar este JSON en
Dashboards → Import no funciona**, con el motivo, para que nadie reinvente la v8 por
costumbre. `hide: 2` se queda, consistente con que ninguna persona elige esa variable.

## Dónde vive la decisión, y qué la corre

`scripts/publish_dashboard.py`, en Python —el toolchain que `setup: uv sync --locked` ya
garantiza; `jq` no está en `setup` y no entra—, partido en dos:

- **La decisión, pura**: `body(dashboard, ds_uid, ds_name) -> dict` (saca `id`, fija
  `overwrite: true`, escribe el `current`) y
  `published_refusal(stored, ds_uid) -> str | None`. Las fija `tests/test_dashboard.py`,
  que corre el chequeo declarado `tests`: el cuerpo entero pinneado, el `current` con sus
  tres claves, y entradas rechazadas construidas en el test (ADR-0023) — un dashboard sin la
  variable `DS_PROMETHEUS`, lo almacenado con un `${DS_PROMETHEUS}` literal adentro (el
  estado exacto de la v8), con un uid de otra instancia, y con la variable ausente.
- **El transporte, todo lo demás**: leer entorno, las dos verificaciones de abajo, un POST,
  un código de salida.

Como `scripts/` no es paquete, el test carga el módulo por ruta con
`importlib.util.spec_from_file_location`: ningún `__init__.py` nuevo, ningún `sys.path`
tocado, ningún archivo bajo `agentic/`. Precedente de código Python operativo fuera de
`agentic/`: `observability/collector/probe.py`.

Mientras la decisión viva en un shell es la clase de defecto de ADR-0027 —un artefacto que
ningún chequeo declarado ejecuta—; por eso vive en las dos funciones puras, que `tests` sí
corre.

## Dos verificaciones contra la instancia, porque publicar 200 no es publicar bien

Un `GRAFANA_DS_UID` equivocado publicaría 200 y dejaría el dashboard vacío, que es el bug de
este issue otra vez con otro origen.

- **Antes del POST**: `GET /api/datasources/uid/$GRAFANA_DS_UID`. Cualquier respuesta que no
  sea 200 corta con código distinto de cero nombrando el uid y la URL, sin publicar nada. Es
  la misma llamada con que el issue diagnosticó el defecto, convertida en puerta en vez de en
  autopsia, y de ahí sale además el `name`.
- **Después del POST**: `GET /api/dashboards/uid/agentic-system`, y el veredicto lo da
  `published_refusal`. No compara objetos enteros —Grafana agrega `id`, `version` y
  normaliza, así que una igualdad total fallaría siempre y el chequeo terminaría borrado—:
  compara exactamente la propiedad que este issue existe para reparar, con el mismo walk
  recursivo del guard, que todo `datasource.uid` de lo almacenado sea `ds_uid` y que el
  `current.value` de `DS_PROMETHEUS` sea `ds_uid`. **Y rechaza cuando no encontró nada que
  verificar**: piso explícito de al menos una referencia de `datasource.uid` y la variable
  `DS_PROMETHEUS` presente en `templating`, porque si no "no encontré nada que objetar" y
  "está bien" serían la misma respuesta, en la única función que corre sin nadie mirando. La
  cuarta entrada rechazada del test es el **sobre** (`{"dashboard": …, "meta": …}`) pasado en
  lugar del dashboard, el error de integración del día uno y el que ningún type checker ve
  porque `scripts/` está fuera de `[tool.mypy] files`; el transporte pasa
  `stored["dashboard"]` explícitamente, así un cambio de forma de la respuesta es un
  `KeyError` ruidoso y no un `None` silencioso.

Ninguna ruta termina en 0 sin las dos. El token nunca se imprime, ni en el mensaje de corte
ni en una traza; las URLs sí. `GRAFANA_URL`, `GRAFANA_TOKEN` y `GRAFANA_DS_UID` se nombran,
sin valor, en `observability/grafana/README.md` y **no** en `.env.example`: ese archivo
documenta lo que el sistema lee en tiempo de corrida (`AGENTIC_*`, `OTEL_*`), y una variable
que ningún código bajo `agentic/` lee, puesta ahí, le diría al próximo lector que el runtime
la usa.

## El guard: un solo spelling, verificado donde la suite era ciega

En `tests/test_dashboard.py`, con un walk recursivo que reusa `_panels` (que ya recursa y ya
tiene su aserción de contar-dos-veces) y que además camina `templating[]`, el `datasource` a
nivel de panel y el de `targets[]` —los tres lugares que `_exprs` nunca mira—:

- todo `datasource.uid` del archivo es `"${DS_PROMETHEUS}"`;
- existe exactamente una variable `DS_PROMETHEUS`, `"type": "datasource"`,
  `"query": "prometheus"`;
- `__inputs` no está, para que un re-export desde la UI que lo reponga falle acá y no en un
  panel vacío;
- ningún `current` de ninguna variable contiene un uid literal ni una cadena `bl-*`, y el de
  `DS_PROMETHEUS` es exactamente el sentinela vacío
  `{"selected": false, "text": "", "value": ""}`;
- `"editable": false`, que es la clave con que el repo deja de ser fuente de verdad por
  convención (abajo).

**El walk recursivo tiene una sola definición**, `datasource_uids(obj) -> list[str]` en el
módulo del publicador, y lo consumen los dos lados: `published_refusal` y este guard, que lo
importa por ruta. Dos walks serían dos anti-vacuidades que pueden dejar de coincidir, que es
la forma de deriva que este issue ya pagó una vez. Misma disciplina que
`tests/readme_metrics`: un lector, dos consumidores.

Entradas rechazadas construidas en el test, nunca sobre el archivo real: un uid literal
`grafanacloud-prom` en un panel anidado dentro de una fila colapsada (que prueba a la vez el
rechazo y la recursión), `${DS_PROMETHEUS}` con la variable ausente, una variable cuyo
`current.value` nombra una instancia, y `__inputs` presente. Y anti-vacuidad sobre el archivo
real: la cuenta de referencias halladas tiene que ser `>=` la de paneles hoja más la de
variables, y si da cero el mensaje nombra al test.

## El delta del Scorecard no puede leer cero cuando no hay con qué comparar

Los cuatro paneles del Scorecard tienen un `refId B` que resta `$baseline_prev`. `baseline` y
`baseline_prev` tenían la query **byte a byte idéntica** (líneas 60 y 78), así que con
`refresh: 2` resolvían a la misma primera opción, y hoy el conjunto tiene un solo elemento:
los cuatro deltas leían 0 —"no cambió nada"— en vez de "no hay comparación", que es lo
contrario de lo que ADR-0028 creó el scorecard para decir. Commitear `bl-2026-09-23` como
`current`, además, es un valor de dato que envejece con el próximo tag que el humano declare
(ADR-0030 fija `--match "bl-*"` justamente porque el hito lo declara una persona).

Decisión, en tres partes:

1. `current` de `baseline` y de `baseline_prev` vuelve a `""`: el `current` vacío era síntoma
   del 404, no un defecto propio, y las dos variables repueblan con `refresh: 2`.
2. `baseline_prev` deja de compartir query: suma `system_baseline!="$baseline"` a su
   `label_values(...)`.
3. **Todo selector que filtre `system_baseline` con una variable interpolada suma
   `system_baseline!=""`**: `$baseline` o `$baseline_prev`, `refId A` o `refId B`, en las
   cuatro filas. La regla es sobre la etiqueta y no sobre el lado. La variable vacía
   interpola a `system_baseline=~""`, que en Prometheus matchea las series **sin la clave**
   —las 342 que el pedido cuenta—, y ese razonamiento no distingue lados. Del lado `prev`
   evita que el delta dé un número equivocado en silencio en vez de quedar sin dato; del lado
   `$baseline` evita algo peor y alcanzable con un clic, porque esa variable resuelve de una
   query que depende de `$product`: elegir `gym-saas` —lo que el README de la línea 27 invita
   a hacer— deja el conjunto vacío (las 35 series con `bl-2026-09-23` son todas
   `agentic-system`) y todo panel con eje de línea de base pasaría a leer la historia
   pre-baseline como si fuera la línea de base elegida, con números plausibles. El matcher no
   agrega una regla: hace verdadera una que ADR-0028 ya afirma —"un panel con una línea de
   base concreta elegida no la ve"—, hoy falsa cuando la variable resuelve vacía. Con la
   variable elegida, los dos matchers conviven sin cambiar nada.
4. Elegir `gym-saas` pasa a leer vacío en toda fila con eje de línea de base, y eso es
   correcto: ese producto no tiene ninguna serie con la clave. Se dice en
   `observability/grafana/README.md`, en la misma sección reescrita, junto con cuál es la
   lectura de la historia pre-baseline (`agentic eval compare`, donde ADR-0028 pone la
   cohorte `pre-baseline`). Un vacío declarado es un resultado; un vacío sorpresa es un
   ticket.

Los cuatro `description` dicen qué muestra el target B cuando las dos variables resuelven al
mismo valor, y que un target B vacío significa "no hay línea de base anterior", nunca "no
cambió nada".

`arm` conserva `{"text": ["All"], "value": ["$__all"]}`: no es un valor de dato, es el
literal estructural de Grafana, y es la forma que `product` y `version` ya tienen en este
mismo archivo. La regla que queda: ningún valor de una serie (`bl-*`, un nombre de producto,
un sha, un uid de instancia) se escribe como `current`; un literal estructural sí.

## El chequeo de registro, y qué puede bloquear el revisor

Ningún rol del loop tiene red: el revisor no puede correr el script, no puede pegarle a
Grafana y no puede reproducir un byte de la evidencia de la v9. Sin decirlo, ese revisor tiene
dos salidas malas —bloquear "no probaste que el POST funciona", un hallazgo que la jaula hace
imposible de respaldar, o aprobar dejando que el `Verdict: approved` insinúe una cobertura que
estructuralmente no tuvo—. Se dice, con la forma de ADR-0021 y ADR-0029.

**El chequeo de registro de esta tarea son los tests de `body()`, `published_refusal` y el
guard del archivo**, corridos por el chequeo declarado `tests` (`uv run pytest -m "not llm"`),
que además es la única ruta que cubre su propio diff en la primera entrega. El Developer pega
en su sección el comando y su salida verbatim: un conteo sin salida pegada no es evidencia.

El brief le dice al revisor que **no puede ejercitar la instancia y que esa imposibilidad no
es motivo de devolución** (ADR-0016: una objeción que no se reprodujo es una observación), y
enumera lo que sí puede bloquear leyendo dos archivos y un diff: una cláusula del guard sin
entrada rechazada; un uid literal o una cadena `bl-*` en `dashboard.json`; `__inputs` de
vuelta; un `current` de `DS_PROMETHEUS` distinto del sentinela vacío; un selector de
`system_baseline` con variable interpolada sin su `!=""`; una ruta del publicador que sale 0
sin las dos verificaciones; `published_refusal` sin su piso; y el comando del README distinto
del `USAGE` del script.

La verificación de punta a punta contra Grafana va nombrada en el cuerpo del pull request, con
su comando exacto, como acto del humano posterior al merge: nunca insinuada como hecha.

## Lo que esta decisión no cambia

Cero archivos bajo `agentic/`: `prompts.fingerprint` no se mueve (ADR-0008), ninguna familia
de métricas se agrega, mueve ni renombra, y ninguna fila de labels de `observability/README.md`
cambia. Los 19 tests de hoy quedan byte a byte —que sigan verdes no prueba nada sobre este
defecto, porque pasaron sobre la v8 vacía, pero que no haya que tocarlos es la prueba de que
el resto del contrato no se movió—. En particular,
`test_versioned_families_are_grouped_and_filtered_by_system_version` y
`test_delivery_panels_are_grouped_and_filtered_by_system_version` siguen verdes sin editarse:
su `re.search` de `system_baseline=~"$baseline"` lo satisface la ocurrencia del lado A, y
`_queried_by_metric` ya reconoce matchers negativos, que es para lo que existe
`test_negative_matcher_is_recognised_as_a_queried_label`.

De nivel raíz del JSON se tocan exactamente dos claves, `__inputs` (que se borra) y `editable`
(que pasa a `false`); ninguna otra.

Lo que sí vuelve a cambiar de superficie observable es un panel con datos, que es el estado
anterior a la v8: esta tarea restituye comportamiento, no lo cambia.

## Riesgos conocidos

- **`scripts/` queda fuera de `[tool.mypy] files` por construcción**
  (`tests/test_stack.py::test_every_mypy_file_is_inside_agentic` sólo admite rutas bajo
  `agentic/`): el publicador nace tipado y nadie lo chequea. Dicho en el cuerpo del PR, no
  arreglado al pasar.
- **`editable: false` cierra el camino de un clic, no todos.** Es una marca del dashboard y no
  un permiso de la API: una publicación por API o un editor con permisos de administrador
  todavía pueden pisar el vivo, y `published_refusal` corre sólo al publicar y compara sólo
  las referencias de datasource, así que entre dos publicaciones nada detecta una divergencia.
  Lo que se cierra es el camino que efectivamente ocurrió —así se separaron la v8 y la v9 del
  archivo—. El README escribe el único loop: editar el archivo, correr el script; y que la
  exploración ad hoc va a **Explore**, no a editar paneles, para que la restricción no se lea
  como una capacidad perdida que alguien revierta sin saber por qué.
- **Ningún rol del loop puede correr el script**: no hay red en esta tarea, así que la
  verificación de punta a punta contra Grafana es del humano, después del merge.

## Opciones descartadas

- **`POST /api/dashboards/import` con `inputs`**: probablemente correcto y hoy no verificable
  sin red; se eligió el camino con evidencia corrida.
- **Dejar `__inputs` junto a la variable**: dos mecanismos para una cosa, y la variable muerta
  después de un import por UI.
- **Commitear el uid o el nombre del datasource**: estado de una instancia en un archivo
  versionado; lo inyecta el publicador.
- **`GRAFANA_DS_NAME` como segundo insumo de entorno**: un segundo valor tipeado que puede
  estar mal, cuando la instancia ya lo dice.
- **Bajar `hide` a 0 y conservar el import por UI**: sostiene con una compuerta no verificable
  un camino que el script ya cubre.
- **Comparar objetos enteros después del POST**: Grafana normaliza, así que fallaría siempre y
  el chequeo terminaría borrado.
- **`jq` para armar el cuerpo**: dependencia de herramienta que ningún `setup` declara.
- **Las variables de Grafana en `.env.example`**: ese archivo es lo que el runtime lee.
