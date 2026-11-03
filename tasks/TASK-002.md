# TASK-002 · Opción `--json` en `gestplan status`

## Contexto
La futura UI y otros scripts necesitan leer el estado de un run sin parsear texto.

## Qué hacer
- Añadir la opción `--json` a `gestplan status <run_id>` que imprima un objeto JSON con: `id`, `plan`, `status`, `cost_usd`, `nodes` (por nodo: `status`, `attempts`, `last_event`) y `children` (ids de los runs hijos).
- Sin `run_id`, `--json` imprime la lista de runs (id, plan, status).
- Reutilizar `Store`; no duplicar consultas.

## Criterios de aceptación
- [ ] La salida `--json` es JSON válido y estable (claves en el orden indicado).
- [ ] Hay tests en `tests/test_cli_run.py` y `pytest` pasa.

## Fuera de alcance
No cambiar la salida de texto actual.
