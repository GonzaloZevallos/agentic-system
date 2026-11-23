---
status: accepted
date: 2026-11-23
---

# El acceso a GitHub es un objeto y la espera puede salir de las cabeceras

Enmienda ADR-0050 (toda llamada a `gh` tiene un límite de tiempo y su adenda de reintentos),
sin editarlo. GH-1071, parte 1 (GH-1090 trajo el módulo puro, GH-1091 lo conecta). Este ADR
registra lo construido en `agentic/core/process/domain/github_access.py` y en
`agentic/core/process/adapters/github/run.py`, y la dirección de las partes 2 a 8, que lo
enmiendan si cambian algo de lo que fija acá.

## El asiento `GithubAccess`

`GithubAccess(gate, cache, budget)` es cómo un proceso llega a GitHub. Hoy sus tres campos son
marcadores opacos, todos `None`; las partes siguientes los tipan como los puertos de la
compuerta, el caché y el presupuesto. `run_gh` recibe `access: GithubAccess | None = None`.
`GhTracker` y `GhBoard` lo reciben en el constructor, junto a `retry`, y lo pasan a su
`run_gh`.

- **Con `access=None`, `run_gh` sigue el camino de siempre**: mismo argv, mismos kwargs de
  `run`, mismas líneas de log y misma excepción (clase, mensaje, `status` y `resets_at`).
- **La composición arma uno por llamada a `_ports`**, o sea uno por nodo, y lo pasa al
  `GhTracker` con `retry` y al `GhBoard`. `backfill_gh_read` arma el suyo, que no comparte
  presupuesto con ningún nodo. `inbox`, `label_timeline`, `format_fix_reads` y el `GhTracker`
  sin `retry` no lo reciben. La parte que necesite estado compartido decide su vida útil y
  enmienda este ADR.

## `split_response` y `wait_from`

`split_response(stdout)` lee la salida de `gh api -i` como una `HttpResponse` (estado,
cabeceras en minúscula y cuerpo) y lanza `NotAnHttpResponse` si no lo es. `wait_from(response,
now, backoff)` devuelve cuánto esperar, con esta precedencia: un `retry-after` legible; si no,
el tiempo hasta un `x-ratelimit-reset` legible y posterior a `now`; si no, el `backoff`. Un
valor ilegible cae a la fuente siguiente. No recorta nada: recortar es de quien duerme.

## El camino de las cabeceras

Corre solo bajo `access`, y solo cuando la clase del error es `transient` o `rate_limited`;
`timed_out` y `unreachable` nunca leen cabeceras. `_response_of` devuelve `None` si el stdout
no es texto o no es HTTP, y atrapa únicamente `NotAnHttpResponse`: sin cabeceras la espera cae
al backoff de siempre.

- **transient** con cabeceras espera `wait_from(response, now, backoff)`.
- **rate_limited** lee primero `gh api rate_limit`, sin cambios. `resets_at` y `last_reset`
  salen solo de esa lectura; una espera sacada de una cabecera nunca los fija. Solo si la
  lectura no devuelve nada, la espera sale de `wait_from`.
- **timed_out** espera siempre el backoff: no hay stdout.
- `_retry_wait` es el único lugar que decide esperar o rendirse y el que arma todos los
  mensajes de rendición; nunca recibe `access`.
- La clase se sigue decidiendo por stderr (`_retryable_kind` no cambia).

## La lectura anidada de `rate_limit`

`_rate_limit_reset` llama a `run_gh` con `access=None`, explícito. Esa lectura decide la espera:
nunca se encola en la compuerta, ni se serializa, ni gasta presupuesto, ni se cachea, sea cual
sea el acceso de quien la pidió.

## El mensaje de rendición por cabecera

Una espera tomada de una cabecera que supera `retry.max_wait_s` no duerme ni se recorta: lanza
el error de la clase, con `status`, y no escribe la línea `retrying`:

`gh <llamada> <frase>; GitHub asked to wait N s, over the M s bound`

Un `TrackerRateLimited` así lleva `resets_at=None` si ninguna lectura de `rate_limit` lo fijó.

## Límites conocidos

- **El camino de las cabeceras es inalcanzable en producción** hasta que una parte pase
  `gh api -i`: hoy ningún llamador lo hace, y se prueba solo contra fakes (`gh_response`).
- **Un 403 secundario o un 429 sigue siendo `TrackerRefused`**: la clase sale de stderr y no se
  capturó ningún stderr de esos casos con el `gh` fijado (2.100.0). Un marcador sin captura es
  un riesgo que ADR-0050 ya descartó.
- **Sin recorte, un hijo con tope `GH_EXEMPT_WAIT_MAX_S` (60 s) se rinde de inmediato** ante un
  `retry-after` mayor, donde hoy reintenta tras el backoff. Se hará visible cuando llegue `-i`.
- **`retry-after: 0` reintenta sin esperar**, acotado solo por `attempts`.

## Dirección de las partes 2 a 8 de GH-1071

Invariantes que atan a las partes siguientes; la que cambie una enmienda este ADR:

- **REST primero**: las lecturas pasan a `gh api -i` sobre REST, donde hay cabeceras y
  validación condicional.
- **Escrituras serializadas**: las escrituras pasan por una compuerta que las serializa.
- **Presupuesto por cuenta**: un presupuesto por `(host, hash del token)`, no por proceso.
- **Caché revalidado**: un caché de lecturas que se revalida contra GitHub antes de servirse.
