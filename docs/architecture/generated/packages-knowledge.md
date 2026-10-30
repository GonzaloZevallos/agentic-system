Esta página muestra las capas de `agentic.core.knowledge` (`domain`, `adapters`), con una flecha por cada capa que importa a otra. Cada capa tiene su página, con sus módulos y los imports que hacen.

- Páginas:
  - [knowledge: domain](packages-knowledge-domain.md)
  - [knowledge: adapters](packages-knowledge-adapters.md)

```mermaid
graph TD
    g_pkg__adapters["adapters (paquete)"]
    g_pkg__domain["domain (paquete)"]
    g_pkg__adapters --> g_pkg__domain
```
