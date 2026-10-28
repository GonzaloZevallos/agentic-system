---
status: accepted
date: 2026-10-28
---

# La documentación generada de agentic-system sale de un script y CI la valida con `check.py`

GH-400. agentic-system es también su propio producto y adopta la división de ADR-0074: lo
generado se regenera y una puerta falla si quedó viejo; lo escrito lo mantiene una persona.
Este ADR registra cómo se genera lo de este repo.

Aplica ADR-0074 (generada y escrita) y ADR-0021 (el alcance de mypy).

## Decisión

1. **Un generador en `scripts/docs/`.** `render.py` es puro y tipado (decide orden, formato,
   nombres y el límite Mermaid); `generate.py` es el borde sin tipar que arma las entradas;
   `check.py` es el paso de CI. `generate.py` queda fuera de `[tool.mypy] files` porque
   importa `render` por nombre (Python pone el directorio del script en `sys.path`) y mypy
   no lo resuelve sin un `mypy_path` ni un override, que ADR-0021 no permite. Queda como
   entrada para una mejora posterior.
2. **Dependencia nueva: `pylint==4.1.3`** en el grupo dev, fijada exacta porque la salida
   de pyreverse cambia entre versiones. Se invoca como `sys.executable -m
   pylint.pyreverse.main -o mmd -k --filter-mode PUB_ONLY -p <nombre> -d <tmp>`, con
   `PYTHONHASHSEED=0` y un entorno mínimo; escribe en un directorio temporal bajo `TMPDIR`.
   Hay un diagrama de paquetes por módulo y uno de clases por capa; una capa sin clases no
   escribe archivo y una que pasa el límite se parte en dos trozos de archivos consecutivos.
3. **"Nodos del grafo compilado" se lee con `get_graph(xray=True)`**, sobre
   `composition.graph.compile()` sin checkpointer: el generador nunca abre SQLite.
4. **Límite Mermaid**: ningún bloque pasa de 40000 caracteres ni de 400 aristas;
   `render.mermaid_limits` lo mide al generar y un test lo mide sobre lo escrito.
5. **El paso de CI es `uv run python scripts/docs/check.py`**, que arma el comando solo con
   `stack.docs` y `stack.docs_command`: el mismo texto que corre la puerta. Si falla,
   imprime la línea para regenerar y commitear. Se fija `.python-version` en 3.13 porque la
   ayuda de argparse y la salida de pyreverse dependen de la versión del intérprete.
6. **Presupuesto**: `check_budgets: {docs: 240}`. Una corrida mide unos 60 s en reposo y
   150 s con la máquina cargada (once corridas de pyreverse más el import de la CLI).
   El chequeo `docs` entra en `delivery_checks` por ADR-0074, así que corre en cada entrega
   de toda tarea futura y crece con las clases públicas de `agentic/core/`.
7. **Alternativa**: si pyreverse resultara no determinista, se reemplaza por un recorrido
   AST propio de paquetes y clases públicas.

## Consecuencias

- pyreverse 4.1.3 se cae dentro de astroid (`brain_typing.infer_typing_alias`) cuando
  `agentic/core/process/adapters/github/board.py` —que anota un local con
  `Literal["open", "closed"]`— se analiza junto a cualquier otro módulo. El generador pasa
  `--ignore board.py`: ese módulo no aparece en ningún diagrama hasta que astroid lo arregle.
- Una tarea con base anterior a este merge no tiene el bloque `docs` (el stack rige desde la
  tarea siguiente) y puede terminar con los archivos generados viejos: se resuelve con un
  pase `fix` mecánico guiado por la línea `run ... and commit` que imprime `check.py`.
