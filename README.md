# gest-repo-plan

Motor local de **planes de procesos como grafos** (JSON) cuyos nodos ejecutan tareas sobre repositorios y agentes de IA. Diseño completo en [DESIGN.md](DESIGN.md); ideas aplazadas en [NICE_TO_HAVE.md](NICE_TO_HAVE.md).

## Estado
Hito Bootstrap (DESIGN.md 15.1) alcanzado con **Claude** como proveedor de IA: el motor ejecuta planes con workspace, agente, validador cruzado, tests, aprobación manual, commit, push y PR, y puede **lanzar planes desde planes** (`implement-tasks`). Incluye un **servidor web** (`gestplan serve`) con API, tiempo real y una UI con React Flow. Incluye nodos de Jira, Confluence y GitHub (lectura), escaneo de secretos y un adaptador de Copilot sin verificar (ver DESIGN.md 15.2).

## Servidor web y UI
```bash
cd web && npm install && npm run build && cd ..   # compila la UI a web/dist (una vez, o tras cambiarla)
gestplan serve                                      # http://127.0.0.1:8765 (API + UI + tiempo real)
```
- **Desarrollo de la UI:** con `gestplan serve` en marcha, `cd web && npm run dev` sirve la UI en `http://localhost:5173` con proxy a la API.
- El servidor mantiene vivos los runs: lanzas planes, ves el grafo moverse y apruebas desde el navegador. Al arrancar marca como interrumpidos los nodos que quedaron en ejecución tras una caída (se reactivan desde la UI).
- Escucha solo en `127.0.0.1` y rechaza `Host` ajenos, `Origin` distintos y peticiones de escritura sin la cabecera `X-Gestplan`. Con `GP_API_TOKEN` en el `.env` exige además un token: abre `http://127.0.0.1:8765/?token=<valor>` una vez.
- Con `JIRA_BASE_URL`, `JIRA_EMAIL` y `JIRA_API_TOKEN` (y opcionalmente `CONFLUENCE_*`) en el `.env` se activan los nodos de Atlassian. Mira `.env.example`.

## Puesta en marcha
```bash
python -m venv .venv
python -m pip --python .venv\Scripts\python.exe install -e ".[dev]"
.venv\Scripts\activate
pytest -q
ruff check src tests
```
Si `pip` falla con `CERTIFICATE_VERIFY_FAILED` (antivirus que intercepta HTTPS), instala con el `pip` global como arriba. Sin instalar el paquete también funciona: `PYTHONPATH=src python -m gestplan <comando>`.

## Comandos
```bash
gestplan doctor                                          # git, gh, claude, correo de git...
gestplan catalog > catalogo.md                           # catálogo de nodos para la IA que escribe planes
gestplan validate plans/examples/implement-task.json     # valida un plan (códigos de error estables)
gestplan dry-run plans/examples/implement-task.json --input task_id=TASK-001 --input repo_url=<url>

gestplan run implement-task --input task_id=TASK-001 --input repo_url=<url>   # ejecuta hasta terminar o esperar
gestplan run implement-tasks --input task_ids=TASK-001,TASK-002 --input repo_url=<url>
gestplan status [run_id]                                 # runs, nodos, aprobaciones pendientes
gestplan approvals list | show <id> | approve <id> | reject <id> [-c comentario]
gestplan resume <run_id>                                 # continúa tras aprobar, reactivar o una caída
gestplan retry <run_id> <nodo> [--drive]                 # reactiva un nodo failed/cancelled/interrumpido
gestplan cancel <run_id>                                 # cancela el run y sus hijos
gestplan logs <run_id> <nodo> [--attempt N]
gestplan cost [run_id]
gestplan serve [--host 127.0.0.1] [--port 8765]          # servidor web (ver arriba)
```
Los datos (BD SQLite, logs, workspaces) van a `./data` (`GP_DATA_DIR`, `GP_WORKSPACES_DIR`). Los planes no fusionan: un humano revisa y fusiona el PR.

## Estructura
- `src/gestplan/plans/` · modelos del plan, condiciones, referencias, grafo, validador.
- `src/gestplan/nodes/` · contrato y definición estática de cada tipo de nodo (catálogo).
- `src/gestplan/executors/` · ejecución de cada nodo (git, agente Claude, flujo...).
- `src/gestplan/engine/` · motor y ejecución de procesos; `db.py` · persistencia SQLite.
- `src/gestplan/server/` · API FastAPI, supervisor persistente y vistas JSON; `web/` · UI (Vite + React + React Flow).
- `plans/` · planes (`implement-task`, `implement-tasks`, `apply-skill`, `implement-jira`; ejemplos en `plans/examples/`); `tasks/` · tareas; `templates/` · plantillas de prompt; `scripts/` · scripts Python registrados.
- `tests/` · pruebas (`pytest`).
