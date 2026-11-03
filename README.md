# gest-repo-plan

Motor local de **planes de procesos como grafos** (JSON) cuyos nodos ejecutan tareas sobre repositorios, agentes de IA, GitHub, Jira y Confluence. Diseño completo en [DESIGN.md](DESIGN.md); ideas aplazadas en [NICE_TO_HAVE.md](NICE_TO_HAVE.md).

## Estado
Fase 1 del hito Bootstrap (DESIGN.md 15.1): modelos de plan, validador, catálogo de nodos, `dry-run` y `doctor`. El motor de ejecución todavía no existe.

## Puesta en marcha
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
```

## Comandos
```bash
gestplan doctor                                   # comprueba git, gh, claude, correo de git...
gestplan catalog > catalogo.md                    # catálogo de nodos para la IA que escribe planes
gestplan validate plans/examples/implement-task.json
gestplan dry-run plans/examples/implement-task.json --input task_id=TASK-1 --input repo_url=<url>
```
Sin instalar el paquete también funciona: `PYTHONPATH=src python -m gestplan <comando>`.

## Estructura
- `src/gestplan/plans/` · modelos del plan, condiciones, referencias, grafo, validador.
- `src/gestplan/nodes/` · tipos de nodo (contrato, registro y definiciones).
- `plans/examples/` · planes de ejemplo; `templates/` · plantillas de prompt; `scripts/` · scripts Python registrados.
- `tests/` · pruebas (`pytest`).
