# gest-repo-plan — Diseño

Motor local de **planes de procesos como grafos** (JSON) cuyos nodos ejecutan tareas sobre repositorios, agentes de IA, GitHub, Jira y Confluence. Los planes los redacta normalmente otra IA, así que el sistema se diseña para ser **autodescriptivo y estrictamente validable**. Una UI en React Flow permite ver los planes y los runs en vivo, y gestionar aprobaciones.

Estado de este documento: **diseño acordado, sin código todavía**.

---

## 1. Decisiones cerradas

| Tema | Decisión |
|---|---|
| Uso | Local, un solo usuario, solo Windows (por ahora) |
| Backend | Python (3.11 instalado; objetivo 3.11+), FastAPI + asyncio |
| Frontend | React + TypeScript + Vite + React Flow (`@xyflow/react`) |
| Persistencia | **SQLite** (modo WAL) + ficheros en disco para logs y workspaces |
| Planes | Ficheros JSON en `plans/`, versionables en git |
| Autenticación | `gh` ya logado; CLIs de agentes ya autenticados; resto de credenciales en `.env` local (ignorado por git) |
| Dependencias entre nodos | Un nodo no arranca hasta que sus dependencias están satisfechas |
| Join por defecto | AND (todas las aristas entrantes), configurable |
| Eventos | Cada nodo declara sus eventos de salida; las aristas conectan `nodo.evento → nodo` |
| Ramificación | Nodo `route` con condiciones JSON declarativas sobre el contexto, con regex |
| Idempotencia | Todos los nodos con efectos son idempotentes (`preview` / `already_done` / `apply`) |
| Aprobación | Todo efecto externo exige un `manual_approval` que lo domine en el grafo. Cada plan/hijo tiene sus propios aprobadores |
| Cancelación | Cancelar un padre cancela los hijos en cascada. Los nodos cancelados/fallidos/interrumpidos son **reactivables** |
| Reanudación | Solo retry de nodos `failed / cancelled / interrupted`. Los nodos `succeeded` se mantienen siempre (sin "re-run from here" en v1) |
| Jira / Confluence | Cloud, API v3, texto en ADF |
| Scripts Python | En carpeta registrada (`scripts/`), no inline en el plan |
| Condiciones | JSON declarativo, no strings de expresión |
| Concurrencia | Límite global configurable (semáforo del motor) |
| Push | `git.push` **siempre actualiza la rama remota original** (`HEAD:refs/heads/<remote_branch>`), sin `--force` |
| Mensajes git/PR | Descriptivos y validados por política (sección 6.1, "Mensajes de commit y PR"). Sin trailers de coautoría de IA. Idioma por defecto: español |
| Confluence | Contenido a partir de plantilla JSON (títulos, tablas, listas...) |

---

## 2. Arquitectura

```
┌────────────────────────┐        SSE / REST        ┌──────────────────────────────┐
│ Frontend (React Flow)  │ ◄──────────────────────► │ FastAPI                      │
│ dashboard · run · plan │                          │  ├─ API (planes, runs, aprob.)│
│ aprobaciones · catálogo│                          │  ├─ Motor (scheduler asyncio) │
└────────────────────────┘                          │  ├─ Registro de nodos         │
                                                    │  ├─ Validador de planes       │
                                                    │  └─ Runner de procesos        │
                                                    └──────┬───────────────┬────────┘
                                                           │               │
                                                   SQLite (WAL)     Disco: logs, workspaces,
                                                                    adjuntos, scripts, plans
```

- Un único proceso backend. El motor corre dentro del mismo proceso (tareas asyncio) y los procesos pesados (agentes, git, scripts) se lanzan como subprocesos.
- La UI no ejecuta nada: solo lee estado y envía comandos (lanzar, cancelar, reactivar, aprobar).
- Un CLI (`gestplan`) expone lo mismo que la API, incluidos `validate` y `catalog`, que es lo que usará la IA que diseña planes.

### Estructura de carpetas prevista

```
gest-repo-plan/
├─ DESIGN.md
├─ pyproject.toml
├─ .env.example
├─ plans/                 # planes JSON (versionables)
├─ scripts/               # scripts Python registrados para el nodo python_script
├─ templates/             # plantillas JSON (Confluence, prompts)
├─ data/                  # (ignorado por git) sqlite, logs, workspaces, adjuntos
├─ src/gestplan/
│  ├─ api/                # FastAPI: rutas, SSE
│  ├─ engine/             # scheduler, estados, contexto, recuperación
│  ├─ nodes/              # un módulo por familia de nodos + registro
│  ├─ plans/              # modelos pydantic del plan, validador, dry-run
│  ├─ db/                 # modelos, sesión, migraciones
│  ├─ integrations/       # git, gh, jira, confluence, agentes CLI
│  └─ cli.py
├─ tests/
└─ web/                   # frontend Vite + React + TS
```

---

## 3. Modelo de plan (JSON)

### 3.1 Estructura

```json
{
  "schema_version": 1,
  "name": "fix-jira-and-pr",
  "description": "Implementa un ticket de Jira y abre un PR",
  "inputs": {
    "jira_id":  { "type": "string", "required": true, "pattern": "^[A-Z]+-\\d+$" },
    "repo_url": { "type": "string", "required": true },
    "base":     { "type": "string", "default": "main" }
  },
  "settings": { "max_agents_parallel": 2, "on_unhandled_failure": "fail_run" },
  "nodes": {
    "ws":   { "type": "prepare_workspace", "version": 1,
              "config": { "repo_url": "${inputs.repo_url}", "base_branch": "${inputs.base}",
                          "new_branch": "feature/${inputs.jira_id}" } },
    "tk":   { "type": "jira.get_issue", "version": 1, "config": { "key": "${inputs.jira_id}" } },
    "dev":  { "type": "ia_agent", "version": 1, "max_attempts": 3,
              "config": { "provider": "claude", "workspace": "${nodes.ws.outputs.path}",
                          "prompt_template": "templates/prompts/implement.md" } },
    "val":  { "type": "ia_agent_validator", "version": 1,
              "config": { "provider": "copilot", "workspace": "${nodes.ws.outputs.path}" } },
    "ok1":  { "type": "manual_approval", "version": 1, "config": { "approves": ["push", "pr"] } },
    "push": { "type": "git.push", "version": 1, "config": { "workspace": "${nodes.ws.outputs.path}" } },
    "pr":   { "type": "git.create_pr", "version": 1, "config": { "workspace": "${nodes.ws.outputs.path}" } }
  },
  "edges": [
    { "from": "ws.ok",  "to": "tk" },
    { "from": "tk.ok",  "to": "dev" },
    { "from": "dev.done", "to": "val" },
    { "from": "val.changes_requested", "to": "dev" },
    { "from": "val.approved", "to": "ok1" },
    { "from": "ok1.approved", "to": "push" },
    { "from": "push.ok", "to": "pr" }
  ],
  "layout": { "ws": { "x": 0, "y": 0 }, "tk": { "x": 220, "y": 0 } }
}
```

- `layout` (posiciones del canvas) va separado para que los diffs del plan sean legibles. Es opcional; la UI calcula uno automático si falta.
- `version` por nodo: protege los planes antiguos si cambia el schema de un nodo.
- Cada nodo puede declarar `join: "all" | "any"` (por defecto `all`) y `max_attempts`.

### 3.2 Inputs dinámicos
- El bloque `inputs` declara tipos, obligatoriedad, defaults y `pattern`. Se validan al lanzar el run; si falta uno obligatorio, el run no arranca.
- Se referencian como `${inputs.jira_id}`. Se lanzan por CLI (`--input jira_id=ABC-123`), UI o desde `launch_plans`.

### 3.3 Contexto y plantillas
Espacios de nombres disponibles en `${...}`:
- `inputs.*` — inputs del run.
- `nodes.<id>.outputs.*` — salidas del último intento exitoso de ese nodo.
- `run.*` — id, fechas, nombre del plan.
- `env.*` — solo variables de una lista permitida (nunca secretos en claro).

Reglas:
- La resolución es **perezosa**: se evalúa cuando el nodo va a ejecutarse.
- Un `${...}` a un output que el nodo origen no declara es **error de validación**, no de ejecución.
- **Secretos:** nunca se guardan en contexto persistido ni en logs. Se redactan al escribir.

### 3.4 Condiciones (`route`)

```json
{ "type": "route", "config": { "cases": [
  { "event": "bugfix", "when": { "path": "inputs.jira_summary", "op": "matches", "value": "^\\[BUG\\]" } },
  { "event": "big",    "when": { "all": [
        { "path": "nodes.dev.outputs.files_changed", "op": "gt", "value": 20 },
        { "not": { "path": "inputs.labels", "op": "contains", "value": "skip-review" } } ] } }
], "default": "other" } }
```

- Operadores: `eq, ne, gt, gte, lt, lte, in, contains, exists, matches`. Combinadores: `all`, `any`, `not`.
- Los casos se evalúan **en orden**; gana el primero que cumple; si ninguno, `default`.
- `matches` usa el paquete `regex` con **timeout** (evita backtracking catastrófico). Los grupos con nombre se exponen en `outputs.captures`.
- Los eventos del `route` son los de `cases` + `default`; el validador exige arista para cada uno.

---

## 4. Semántica del grafo

### 4.1 Estados del nodo
`pending → ready → running → { succeeded | failed | waiting_approval | waiting_external | skipped | cancelled | interrupted }`

- `waiting_approval`: nodo `manual_approval` esperando decisión.
- `waiting_external`: nodos que esperan algo fuera (workflows, `wait_plan`). No bloquean hilos; el motor los revisita por polling programado.
- `interrupted`: el proceso se cayó mientras estaba `running` (se marca al arrancar la app).
- `skipped`: el nodo solo era alcanzable por ramas no tomadas.

### 4.2 Estados del run (plan)
`queued | running | waiting | succeeded | failed | cancelled`
- `waiting`: no hay nodos ejecutándose pero hay aprobaciones/esperas pendientes.
- Un resumen agregado (`nodos ok / total`, nodos activos, nodo que bloquea) se calcula para el dashboard.

### 4.3 Reglas de ejecución
1. Un nodo pasa a `ready` cuando se cumple su `join` sobre las aristas entrantes: una arista "dispara" cuando su nodo origen emite exactamente ese evento.
2. **Propagación de `skipped`:** si todas las aristas entrantes de un nodo están "muertas" (origen terminado con otro evento, o skipped), el nodo pasa a `skipped` y propaga. Evita joins que esperan para siempre.
3. **Join `any`** permite converger ramas de un `route`.
4. **Ciclos:** las aristas de retorno se detectan por DFS. Cada nodo dentro de un ciclo exige `max_attempts` (salvo ciclos controlados por un `ia_agent_validator`, cuyo `max_retries` ya acota el bucle). **Criterio implementado en el validador:** un nodo está *acotado* si tiene `max_attempts` o si es el destino del evento `changes_requested` de un validador (el motor le deriva `max_retries + 1` ejecuciones); un ciclo es válido si **toda vuelta del ciclo pasa por al menos un nodo acotado** (quitando los nodos acotados no debe quedar ningún ciclo). Cada re-ejecución crea un nuevo intento (`attempt+1`); superado el máximo, el nodo emite `failed` (o el evento `max_attempts_reached` si está conectado).
5. **Evento sin arista:** si un nodo emite un evento sin arista de salida, se aplica `on_unhandled_failure` si es de fallo (`fail_run` | `skip_dependents`); si no es de fallo, el camino simplemente termina.
6. **Fin del run:** cuando no quedan nodos `ready/running/waiting`. Resultado `succeeded` si ningún nodo quedó en `failed/cancelled/interrupted`.

### 4.4 Cancelación y reactivación
- **Cancelar un run:** mata el árbol de procesos de los nodos `running`, marca `cancelled` los nodos pendientes y **cancela en cascada los runs hijos**.
- **Reactivar** (`retry`): aplicable a nodos `failed / cancelled / interrupted`. Crea un nuevo intento con el contexto guardado. Los nodos `succeeded` **nunca** se reejecutan.
- Si se reactiva un nodo `launch_plans` cuyos hijos fueron cancelados, **reutiliza y reactiva** los runs hijos (clave de idempotencia `run_padre + nodo + índice`); no crea duplicados.
- Reactivar un nodo devuelve el run a `running`/`queued`.
- Tras reiniciar la app, los nodos `running` huérfanos pasan a `interrupted` y quedan reactivables.

---

### 4.5 Timeouts y estados eternos

Ningún nodo puede quedarse indefinidamente en `running` o en una espera sin que el sistema lo note.

- Cada tipo de nodo declara en código un **timeout por defecto acorde a lo que hace** (`default_timeout_s`) y, si ejecuta procesos, un **timeout por inactividad** (`idle_timeout_s`: si no produce salida en ese tiempo se considera colgado).
- El plan puede **sobrescribirlos por nodo** (`"timeout_s": 5400`), pero nunca por encima del tope global `timeout_cap` de los ajustes; el validador de planes lo comprueba.
- Al vencer: se mata el árbol de procesos, el nodo termina con el evento **`timeout`** (o `stalled` si fue por inactividad) y queda reactivable. Si no hay arista para ese evento, aplica `on_unhandled_failure`.
- **Timeout del run completo** (`settings.run_timeout`, por defecto 24 h): al vencer, el run se marca `failed` y se cancelan sus nodos activos.
- **Aprobaciones manuales:** esperar a una persona es un estado legítimo, así que por defecto no caducan. Opcionalmente `expire_after` emite el evento `expired`. El dashboard resalta las aprobaciones antiguas.

Valores por defecto orientativos (ajustables en implementación):

| Nodo | Timeout | Inactividad |
|---|---|---|
| `prepare_workspace` (clone) | 15 min | 5 min |
| `ia_agent` | 60 min | 10 min |
| `ia_agent_validator` | 30 min | 10 min |
| `git.commit` | 5 min | — |
| `git.push` | 5 min | — |
| `git.create_pr` | 2 min | — |
| `github.wait_checks` | 60 min (configurable) | — |
| `github.get_*` / `jira.*` / `confluence.*` | 2 min (con reintentos ante fallos transitorios) | — |
| `python_script` | 10 min | opcional |
| `launch_plans` | 5 min | — |
| `wait_plan` | 4 h (configurable) | — |
| `manual_approval` | sin límite | — |

---

## 5. Contrato de nodo

Cada tipo de nodo es una clase Python registrada con metadatos **declarados en código**, de donde se genera el catálogo para la IA:

```python
class Node:
    type: str                 # "git.push"
    version: int
    description: str          # texto para la IA
    side_effect: Literal["none", "local", "external"]   # lo decide el código, NO el plan
    ConfigModel: type[BaseModel]
    OutputsModel: type[BaseModel]
    events: list[EventSpec]   # nombre + descripción + si es evento de fallo

    async def preview(ctx) -> Preview        # qué va a hacer (sin hacerlo)
    async def already_done(ctx) -> bool      # ¿ya está hecho? (idempotencia)
    async def apply(ctx) -> NodeResult       # ejecuta y devuelve (evento, outputs)
```

- `preview()` alimenta al aprobador (diff, texto del PR, comentario de Jira...).
- `already_done()` permite reanudar sin duplicar: si devuelve `True`, el nodo emite su evento de éxito con los outputs ya existentes.
- Nodos sin efectos pueden omitir `preview/already_done`.
- `side_effect = "external"` activa la regla de aprobación (sección 7).

### Catálogo para la IA que diseña planes
- `gestplan catalog` / `GET /api/catalog`: lista de nodos con descripción, JSON Schema de config y outputs, eventos, `side_effect`, y ejemplos mínimos.
- `gestplan catalog --markdown` genera un documento listo para pegar como contexto a la IA.
- La documentación **se genera del código**, así nunca se desincroniza.

---

## 6. Catálogo de nodos

`side_effect`: **N** none · **L** local · **E** externo (requiere aprobador que lo domine).

### 6.1 Workspace y git
| Nodo | SE | Eventos | Qué hace |
|---|---|---|---|
| `prepare_workspace` | L | `ok`, `failed` | Crea carpeta temporal, clona el repo en la rama indicada y prepara la rama de trabajo (ver abajo). Idempotente: si el workspace del run ya existe y está en la rama, continúa |
| `cleanup_workspace` | L | `ok` | Borra el workspace (comprobando que está bajo la raíz de workspaces) |
| `git.commit` | L | `ok`, `nothing_to_commit`, `invalid_message`, `failed` | Stage + commit. Mensaje de plantilla o generado por el agente, validado por la política de mensajes |
| `git.push` | **E** | `ok`, `rejected`, `failed` | Push de la rama. `already_done`: la rama remota ya contiene el commit |
| `git.create_pr` | **E** | `ok`, `failed` | Crea PR vía `gh`. `already_done`: ya hay PR abierto de esa rama |

#### Rama de trabajo en `prepare_workspace`

Entrada: `base_branch` (de dónde partir) y `new_branch` (nombre deseado, p. ej. `feature/ABC-123`). Se comprueba en el remoto con `git ls-remote --heads origin <new_branch>`.

| Situación | Qué hace |
|---|---|
| `new_branch` **no existe** en el remoto | Crea la rama local `new_branch` desde `base_branch` |
| `new_branch` **ya existe** en el remoto | Crea una rama **local** `new_branch-<sufijo>` que parte de `origin/new_branch` (el trabajo continúa sobre lo que ya hay en esa rama) |

- **Sufijo determinista, no aleatorio:** se deriva del id del run (p. ej. 6 primeros caracteres del hash). Así, al reanudar o reactivar el nodo se obtiene el **mismo** nombre (idempotencia) y es fácil rastrear qué run creó la rama local. Sigue cumpliendo el propósito de distinguir la rama local de la remota.
- **Destino del push:** la rama local solo es un nombre local; `git.push` empuja con refspec explícito `HEAD:refs/heads/<remote_branch>`, por lo que actualiza la rama remota original. Nunca usa `--force`; si el remoto avanzó y el push es rechazado, `git.push` emite `rejected`.
- Config opcional `on_branch_exists`: `track_remote` (por defecto, lo descrito) | `fail` | `new_branch_from_base` (rama nueva con sufijo también en el remoto, partiendo de `base_branch`).
- **Outputs:** `path`, `local_branch`, `remote_branch`, `branch_existed` (bool), `base_commit`, `push_ref`. `route` puede ramificar por `branch_existed` y `git.push` / `git.create_pr` consumen `remote_branch`.
- Si ya hay un PR abierto para esa rama, `git.create_pr` lo detecta en `already_done` y devuelve el existente en lugar de crear otro.
- Riesgo a tener presente: dos runs simultáneos sobre el mismo ticket trabajarían sobre la misma rama remota. El push no forzado evita pisar, pero conviene una clave de exclusión por (repo, rama remota) en el motor.

#### Mensajes de commit y PR

Los commits y los PRs deben ser **descriptivos**: quien los lea debe entender qué cambió y por qué sin abrir el diff.

**Quién los redacta**
- El `ia_agent` **no hace commits ni push**: solo modifica ficheros. El commit lo hace el nodo `git.commit`, de modo que el mensaje pasa siempre por la política de mensajes y el agente no puede añadir trailers propios.
- Al terminar, `ia_agent` devuelve en su salida estructurada: `summary`, `files`, `suggested_commit` (asunto + cuerpo), `suggested_pr_title` y `suggested_pr_body`. Si el plan lo prefiere, `git.commit` / `git.create_pr` usan una plantilla (`templates/git/commit.md`, `templates/git/pr.md`) con placeholders del contexto, o una combinación (plantilla + resumen del agente).
- **Sin coautoría de IA:** el sistema nunca añade `Co-Authored-By` (ni equivalentes) a commits ni PRs, y `git.commit` elimina cualquier trailer de ese tipo que venga en el mensaje. Se configuran además los CLIs de agente para que no los generen.

**Quién manda sobre el formato:** si el agente de reglas del nodo (`agent`) define convenciones de commit y PR, **esas prevalecen** y el contrato de salida se limita a exigir que el mensaje sea descriptivo y cumpla la política general (abajo). El formato siguiente es el **valor por defecto cuando el agente no define el suyo**. En la política, `message_policy.format: agent | default` (por defecto `agent` si el nodo tiene `agent`, `default` si no).

**Formato por defecto**
- *Commit — asunto:* `ABC-123: verbo en imperativo + qué cambia` (≤ 72 caracteres, sin punto final). Ej.: `ABC-123: Validar formato del email en el registro de usuarios`.
- *Commit — cuerpo:* por qué se hizo el cambio y qué enfoque se tomó; lista breve de cambios relevantes si hay varios.
- *PR — título:* `[ABC-123] Resumen descriptivo del cambio`.
- *PR — cuerpo:* secciones **Contexto** (enlace al ticket y problema), **Cambios** (lista), **Validación** (notas del validador cruzado e incidencias no bloqueantes), **Cómo probar**, y un marcador oculto `<!-- gestplan run:<id> node:<id> -->` para la idempotencia.
- Si la rama ya existía y se añade un commit, el asunto describe solo el cambio nuevo.
- Idioma configurable a nivel de plan (`settings.language`: `es` | `en`). **Por defecto `es`**: asuntos, cuerpos, títulos y secciones del PR (Contexto, Cambios, Validación, Cómo probar) en español, y la instrucción de idioma se inyecta en el prompt del agente. Con `en` se usan los equivalentes (Context, Changes, Validation, How to test).

**Política de mensajes** (`message_policy`, con valores por defecto y sobrescribible por plan)
- Longitud mínima y máxima del asunto; cuerpo obligatorio si el cambio supera N ficheros o N líneas.
- Debe contener la clave del ticket si el plan tiene `jira_id` (`require_ticket_key`).
- Lista de asuntos prohibidos o genéricos: vacío, `fix`, `update`, `changes`, `wip`, `misc`, solo el nombre de la rama, etc.
- Si no cumple, `git.commit` emite **`invalid_message`** sin hacer el commit. El plan puede enrutar ese evento de vuelta a `ia_agent` con el motivo para que reescriba el mensaje (con `max_attempts`), o a una aprobación manual que lo edite.
- `git.create_pr` aplica la misma política al título y exige cuerpo con las secciones definidas.

**Aprobación**
- El `preview()` de `git.commit`, `git.push` y `git.create_pr` incluye el mensaje de commit, el título y el cuerpo del PR (tipo `markdown`), para que el aprobador los vea tal como se publicarán. La aprobación puede **editar** título/cuerpo antes de aprobar.

**Idempotencia**
- `git.commit`: si `HEAD` ya contiene un commit con el mismo árbol y el mismo mensaje, no repite.
- `git.create_pr`: detecta PR abierto de esa rama (marcador en el cuerpo) y, si el contenido cambió, actualiza título/cuerpo en lugar de crear otro.

### 6.2 GitHub (lectura)
| Nodo | SE | Eventos | Qué hace |
|---|---|---|---|
| `github.get_pr_status` | N | `open`, `merged`, `closed`, `failed` | Estado del PR |
| `github.wait_checks` | N | `success`, `failure`, `timeout` | Espera por polling a los checks/workflows del PR (`waiting_external`) |
| `github.get_workflow_runs` | N | `ok`, `failed` | Lista ejecuciones y su estado |
| `github.merge_pr` | **E** | `ok`, `failed` | (futuro) |

### 6.3 Agentes IA
| Nodo | SE | Eventos | Qué hace |
|---|---|---|---|
| `ia_agent` | L | `done`, `failed`, `timeout` | Lanza un CLI de agente (`claude`, `copilot`) en modo no interactivo sobre el workspace |
| `ia_agent_validator` | L | `approved`, `changes_requested`, `max_retries_reached`, `no_progress`, `failed` | Validación cruzada de los cambios con otro agente/proveedor; devuelve JSON estructurado y el feedback se inyecta en el siguiente intento de `ia_agent` |

Notas:
- `ia_agent` escribe en el workspace (efecto local). **No recibe credenciales** de push ni tokens en el entorno.
- Salida estructurada: resumen de cambios, ficheros tocados, notas. Se guardan tokens/coste/duración por intento.
- Timeout obligatorio; al vencer se mata el árbol de procesos.
- El contenido de Jira (descripción, comentarios, adjuntos) se pasa al agente **delimitado como datos no confiables**.
- Las flags de permisos del CLI (modo "sin confirmaciones") solo se usan dentro del workspace temporal.

#### Adaptadores de proveedor (`ia_agent` / `ia_agent_validator`)

Cada proveedor es un adaptador con la misma interfaz: `build_command(request) -> list[str]` (siempre lista de argumentos, nunca un string de shell), `collect_result(...)` y `kill()`. Por ahora `copilot` y `claude`.

**Invocación de Copilot observada en pruebas** (la captura aportada):
```
copilot.cmd --model <modelo> --silent --agent <agente-personalizado>
  --session-id <uuid> --allow-all-tools --no-ask-user
  --add-dir <workspace> --add-dir <carpeta-copilot-input> [--add-dir ~/.copilot/agents|skills|instructions ...]
  --prompt "/<skill> Read and follow the complete task instructions in this file: <ruta>/copilot-prompt-<id>.md"
```
Lo que esto nos enseña y cómo se refleja en el diseño:

| Observación | Decisión |
|---|---|
| El prompt real va en un **fichero** (`copilot-input/copilot-prompt-<id>`) y por argumento solo se pasa una frase corta que apunta a él | Adoptado para todos los proveedores: el nodo escribe `prompt.md` en `data/runs/<run>/<nodo>/<intento>/` y pasa una instrucción corta. Evita el límite de longitud de la línea de comandos de Windows y los problemas de escapado de comillas/`&`/`%` con `.cmd` |
| El prompt empieza por `/<nombre>`: invoca una **skill / comando** con nombre | Campo `skill` en la config; el adaptador antepone `/<skill>` |
| `--agent <nombre>`: agente personalizado | Campo `agent` |
| `--session-id <uuid>` | Confirma que se puede **continuar sesión** en Copilot. `session_mode: continue` usa un UUID **determinista** (uuid5 de run+nodo), así las rondas del bucle agente↔validador y las reactivaciones retoman la misma sesión; con `fresh` se usa uno nuevo por intento |
| `--allow-all-tools` + `--no-ask-user` | Modo no interactivo. Siempre con stdin cerrado (`DEVNULL`). Solo permitido sobre el workspace temporal |
| Varios `--add-dir` | Campo `extra_dirs`. **Principio de mínimo acceso:** por defecto solo el workspace, el directorio de entrada del nodo y los directorios de agentes/skills/instrucciones declarados. No se añade un directorio amplio como `~/git` salvo que el plan lo pida explícitamente (con `--allow-all-tools` el agente puede leer y escribir en todo lo que se añade) |
| `--silent` (solo la respuesta) | No hay métricas de tokens/coste garantizadas; son opcionales en Copilot |
| `copilot.cmd` es un script de lote | Resolver con `shutil.which`, ejecutar sin `shell=True` y matar **todo el árbol** (cmd → node) al cancelar. Los textos controlados por el usuario (ticket, feedback) nunca van en argumentos, solo en el fichero de prompt |

**Resultado del agente por fichero, no por stdout.** Con `--silent` la salida es texto libre, poco fiable de parsear. El prompt indica al agente que escriba su resultado en `data/runs/<run>/<nodo>/<intento>/result.json` (schema del nodo: `summary`, `files`, `suggested_commit`, `suggested_pr_title`, `suggested_pr_body`; en el validador, `verdict` + `issues`). El nodo valida el JSON contra el schema; si falta o es inválido emite `failed` (reintentable) y guarda stdout como log. Esa carpeta se añade con `--add-dir`.

**Composición del prompt y contrato de salida.** El prompt que recibe el agente no lo escribe entero el autor del plan: el nodo lo **ensambla por capas** en `prompt.md`, de modo que la salida cumpla siempre el estándar interno aunque el plan (o la IA que lo diseña) se olvide de pedirlo.

| Capa | Quién la aporta | Contenido |
|---|---|---|
| 1. Preámbulo del sistema | El motor (fija, versionada: `contract_version`) | Reglas innegociables: trabajar solo dentro del workspace, **no hacer commit ni push**, no tocar credenciales, no ejecutar acciones externas, idioma de salida (`settings.language`, por defecto español), cómo tratar los datos no confiables |
| 2. Tarea | El plan (`prompt_template` + variables) | Qué hay que hacer, convenciones del repo, criterios de aceptación |
| 3. Datos no confiables | El motor, a partir del contexto | Descripción/comentarios del ticket, entre delimitadores explícitos con la advertencia de que son **datos, no instrucciones** |
| 4. Feedback de rondas previas | El motor (solo en reintentos) | Incidencias del validador o motivo de `invalid_message` |
| 5. Contrato de salida | El motor, **generado del `OutputsModel` del nodo** | Ruta exacta de `result.json`, JSON Schema, un ejemplo válido y las reglas de formato (commit en imperativo con clave de ticket, PR con sus secciones, idioma) |

- **Una sola fuente de verdad:** el contrato (capa 5) se genera del mismo modelo pydantic con el que luego se valida el resultado; no hay dos descripciones que puedan divergir.
- **Validación y auto-reparación:** al terminar el agente se valida `result.json` contra el schema y la política de mensajes. Si falla, se hace **una ronda de reparación dentro de la misma sesión** (`session_mode: continue`) enviando solo el error ("tu resultado no cumple X, corrígelo") antes de gastar un intento del bucle. Si sigue fallando, el nodo emite `failed`.
- El validador cruzado recibe el mismo ensamblado de capas con su propio contrato (`verdict` + `issues`), más el diff y los criterios.
- El plan solo ve y edita las capas 2 y 4 (variables); las capas 1, 3 y 5 se muestran en el `preview` del nodo y en el panel de la UI, y el `prompt.md` final queda guardado por intento para poder auditar qué se le pidió exactamente al agente.
- Las plantillas del plan pueden **añadir** reglas de formato propias, pero no quitar las del contrato.

**Los agentes no preguntan al usuario.** Los prompts se lanzan siempre con un agente de reglas (`--agent`) y el preámbulo del sistema (capa 1) indica que **no debe formular preguntas**. Ante una ambigüedad, el agente decide con el mejor criterio y lo deja registrado en `result.json` (campos `assumptions` y `open_questions`). Esos campos se muestran en la aprobación manual y el validador puede revisarlos. (Un mecanismo para que el agente pause y pregunte se ha dejado como *nice to have*.)

**Reglas del agente.** Las convenciones de código, de commits y de PR las aporta el agente de reglas seleccionado en cada nodo (`agent`). Para el sistema son la fuente de verdad de formato; ver "Mensajes de commit y PR".

**Configuración del nodo con Copilot:**
```json
"dev": { "type": "ia_agent", "version": 1, "max_attempts": 3, "config": {
  "provider": "copilot",
  "model": "<modelo>",
  "agent": "<agente-personalizado>",
  "skill": "<skill-opcional>",
  "workspace": "${nodes.ws.outputs.path}",
  "prompt_template": "templates/prompts/implement.md",
  "session_mode": "continue",
  "allow_all_tools": true,
  "extra_dirs": [],
  "timeout_s": 3600
} }
```
Los valores de `model`, `agent` y `skill` son libres (los define quien escribe el plan); el catálogo los documenta como cadenas. Ajustes globales (`.env`/ajustes): ruta de `copilot`, `claude`, y directorios de agentes/skills/instrucciones por defecto (`~/.copilot/...`, resueltos por usuario, no fijos).

**Adaptador de Claude (Claude Code 2.1.317, comprobado con `claude --help` y una prueba real).**

Es un `.exe` nativo (`C:\Users\dani8\.local\bin\claude.exe`), así que no tiene los problemas de `.cmd`/`.ps1` de Copilot. Prueba realizada: `prompt | claude -p --output-format json --no-session-persistence --max-budget-usd 0.10 --permission-mode dontAsk` → código de salida 0, autenticación válida en modo no interactivo y una respuesta JSON de un solo objeto.

| Necesidad | Flag de Claude |
|---|---|
| Modo no interactivo | `-p` / `--print`. El prompt entra **por stdin** (no por argumento), así que `prompt.md` se canaliza sin límites de longitud ni escapado |
| Salida | `--output-format json` → un objeto con `result` (texto), `is_error`, `subtype`, `session_id`, `total_cost_usd`, `usage` (tokens), `num_turns`, `permission_denials`, `modelUsage`. Esos campos alimentan **tokens/coste/duración** de la auditoría sin parsear texto libre. `stream-json` queda para streaming en vivo a la UI |
| Salida estructurada | `--json-schema <schema>` (solo con `-p`): validación del schema por el propio CLI. Se usa con el `OutputsModel` del nodo; el adaptador guarda el resultado como `result.json` para que sea idéntico al flujo de Copilot |
| Agente de reglas | `--agent <nombre>` (y `--agents <json>` para definirlos inline) |
| Modelo / esfuerzo | `--model`, `--effort low\|medium\|high\|xhigh\|max`, `--fallback-model` |
| Directorios | `--add-dir <dirs...>` (el workspace es el `cwd`) |
| Sesión | `--session-id <uuid>` (UUID determinista, igual que Copilot), `--resume <id>` y `--fork-session`. **No** usar `--no-session-persistence` si se quiere `session_mode: continue` |
| Permisos sin preguntar | `--permission-mode acceptEdits` (o `dontAsk`) + `--allowedTools` / `--disallowedTools` + `--permission-prompts none` (lo que requeriría confirmación se deniega solo). Se evita `--dangerously-skip-permissions`/`bypassPermissions` salvo petición explícita en el plan |
| Presupuesto | `--max-budget-usd` → implementa el `max_cost` por invocación del bucle agente/validador |
| Validador de solo lectura | `--tools "Read,Grep,Glob"` y/o `--permission-mode plan` (o `--restricted`, que además confina las herramientas de fichero a los directorios de trabajo) |
| Prompt de sistema | `--append-system-prompt`: candidato para la capa 1 (preámbulo del sistema) en lugar de ir dentro de `prompt.md` |
| Ajustes y descubrimiento | `--setting-sources user,project,local`, `--settings <fichero-o-json>`, `--disable-slash-commands`, `--safe-mode` |

Puntos a tener en cuenta:
- **Carga de configuración del usuario:** en la prueba, una llamada trivial leyó ~37 000 tokens de contexto (coste ≈ 0,044 USD) porque Claude carga la configuración global (`~/.claude`: CLAUDE.md, skills, plugins). Para runs reproducibles y baratos, el adaptador fija qué fuentes se cargan (`--setting-sources`) y el plan puede pedir `project` únicamente. `--bare` queda descartado por ahora: exige `ANTHROPIC_API_KEY` y no lee el login por OAuth.
- **Autenticación:** el CLI debe estar logado de antemano (`claude auth`). Existe `claude setup-token` para un token de larga duración (requiere suscripción); su uso en el motor queda por verificar.
- **Commits sin coautoría de IA:** el agente no commitea (lo hace `git.commit`), pero si algún nodo permitiera commits de Claude habría que anular la atribución automática por configuración (el nombre exacto del ajuste está por verificar).
- La **versión del CLI** (`claude --version`) se guarda en cada invocación auditada.
- `claude doctor` es útil para `gestplan doctor`.

**Copilot:** en esta máquina `copilot` resuelve a `...\AppData\Roaming\npm\copilot.ps1` (GitHub Copilot CLI 1.0.105). El adaptador debe preferir `copilot.cmd` (o el ejecutable real) frente al `.ps1`, y ejecutarlo sin `shell=True`.

#### Bucle agente ↔ validador (reintentos)

```
dev (ia_agent) ──done──► val (ia_agent_validator) ──approved──► ok1 (manual_approval) ...
   ▲                         │ changes_requested
   └─────────────────────────┘
                             │ max_retries_reached / no_progress ──► ok1 o failed
```

Configuración del validador:
```json
"val": { "type": "ia_agent_validator", "config": {
  "provider": "copilot",
  "max_retries": 2,
  "stop_on_no_progress": true,
  "blocking_severities": ["high", "medium"],
  "criteria": "${nodes.tk.outputs.description}"
} }
```

- **`max_retries`** = nº de veces que el validador puede devolver el trabajo al agente. Por defecto 2 (→ hasta 3 ejecuciones de `ia_agent`). Configurable por nodo.
- **Tope global** en ajustes (`validator_max_retries_cap`, por defecto 10): el plan no puede pedir más, así una IA diseñadora no deja un bucle de coste ilimitado. El validador de planes lo rechaza.
- El `max_retries` del validador **cuenta como acotación del ciclo**: el nodo `dev` no necesita `max_attempts` aparte (el motor lo deriva como `max_retries + 1`).
- Al agotarlos, el validador emite **`max_retries_reached`** (evento declarado, que el plan debe conectar). Lo habitual es enviarlo a un `manual_approval` que muestre el último feedback, para decidir si se publica igualmente o se aborta.
- **`no_progress`**: si el feedback es esencialmente el mismo que en la ronda anterior (hash de las incidencias) o el agente no cambió nada, se corta antes de gastar más rondas.
- **Respuesta estructurada del validador** (JSON validado contra schema):
  ```json
  { "verdict": "approved | changes_requested",
    "issues": [ { "severity": "high|medium|low", "file": "src/a.py", "line": 12,
                  "description": "...", "suggestion": "..." } ],
    "notes": "..." }
  ```
  Solo las incidencias con severidad en `blocking_severities` provocan `changes_requested`; el resto se pasan como notas (p. ej. al cuerpo del PR).
- **Realimentación:** en cada ronda el feedback llega a `ia_agent` como `${nodes.val.outputs.feedback}` (incidencias formateadas) y el prompt de `ia_agent` lo incluye junto a un resumen de rondas anteriores. Opción `session_mode: fresh | continue` para que el agente continúe su sesión previa (confirmado en Copilot con `--session-id`; ver "Adaptadores de proveedor").
- **Contador persistente:** se guarda por run y nodo. Al **reactivar** un validador que llegó a `max_retries_reached`, el usuario puede conceder rondas extra (`+N`), siempre bajo el tope global.
- Recomendado usar un proveedor distinto en agente y validador (la validación cruzada aporta más con modelos distintos). Presupuesto opcional por bucle (`max_cost`, `timeout` por ronda).
- Cada ronda queda en `node_attempts`, así la UI muestra la línea de tiempo `dev#1 → val#1 → dev#2 → val#2`.

### 6.4 Jira (Cloud, API v3, ADF)
| Nodo | SE | Qué hace |
|---|---|---|
| `jira.get_issue` | N | Campos del ticket (resumen, descripción convertida a texto, estado, labels...) |
| `jira.get_comments` | N | Lista de comentarios |
| `jira.get_attachments` | L | Descarga adjuntos al workspace |
| `jira.assign` | **E** | Asigna el ticket |
| `jira.comment` | **E** | Comenta (ADF). Marcador oculto con id de run/nodo para idempotencia |
| `jira.transition` | **E** | Cambia estado. `already_done`: ya está en el estado destino |
| `jira.create_subtask` | **E** | Crea subtarea. `already_done` por clave de idempotencia (etiqueta/propiedad) |

Se necesita un conversor texto/Markdown ↔ **ADF** con tests.

### 6.5 Confluence (Cloud)
| Nodo | SE | Qué hace |
|---|---|---|
| `confluence.create_page` | **E** | Crea página desde plantilla JSON. `already_done`: existe página con ese título en el espacio/padre y marcador del run |

Plantilla de contenido:
```json
{ "title": "Informe ${inputs.jira_id}",
  "blocks": [
    { "type": "heading", "level": 1, "text": "Resumen" },
    { "type": "paragraph", "text": "${nodes.dev.outputs.summary}" },
    { "type": "table", "columns": ["Fichero", "Cambio"], "rows": "${nodes.dev.outputs.files}" },
    { "type": "list", "items": ["a", "b"] },
    { "type": "code", "language": "python", "text": "..." } ] }
```
Hay un renderer a *storage format* (XHTML de Confluence) con tests.

### 6.6 Control de flujo
| Nodo | SE | Eventos | Qué hace |
|---|---|---|---|
| `route` | N | los de `cases` + `default` | Ramifica por condiciones sobre el contexto (sección 3.4) |
| `manual_approval` | N | `approved`, `rejected` | Pausa el run hasta decisión humana en la UI; muestra los `preview()` de los nodos que cubre |
| `python_script` | según script | el que devuelva el script | Ejecuta un script registrado en `scripts/` |
| `launch_plans` | L* | `ok`, `failed` | Lanza uno o varios planes hijos (lista de inputs) y devuelve sus ids |
| `wait_plan` | N | `all_ok`, `some_failed`, `cancelled` | Espera a los runs hijos indicados (`waiting_external`) |

\* `launch_plans` en sí solo crea runs; los efectos externos ocurren dentro de los hijos, cada uno con sus propios aprobadores.

### 6.7 `python_script`
- Los scripts viven en `scripts/` y se referencian por nombre. Contrato:
  ```python
  META = {"side_effect": "none", "events": ["ok", "failed"], "description": "...",
          "inputs_schema": {...}, "outputs_schema": {...}}
  def run(inputs: dict, ctx: dict) -> dict:   # {"event": "ok", "outputs": {...}}
  ```
- Se ejecutan en **subproceso** con entrada/salida JSON, timeout y cwd controlado.
- Si el script no declara `side_effect`, se asume `external`.
- El catálogo los incluye junto a los nodos.

---

### 6.8 Auditoría de comandos y respuestas IA

Todo lo que se lance a un agente queda registrado y es consultable **por nodo e intento**, tanto durante la ejecución como después.

**Unidad de auditoría: la invocación.** Cada vez que se lanza un CLI de agente se crea una `invocation`. Un intento puede tener varias (la ejecución principal y las rondas de auto-reparación del contrato de salida).

Qué se guarda por invocación:

| Dato | Detalle |
|---|---|
| Comando exacto | `argv` completo como lista, `cwd`, ruta resuelta del ejecutable y **versión del CLI** (`--version`). Con secretos redactados |
| Entorno | Solo los **nombres** de variables pasadas al proceso, nunca sus valores |
| Proveedor y parámetros | `provider`, `model`, `agent`, `skill`, `session_id`, `session_mode`, `extra_dirs`, `timeout_s` |
| Prompt enviado | El `prompt.md` final tal cual, más el desglose por capas (preámbulo, tarea, datos no confiables, feedback, contrato) y su `contract_version` |
| Respuesta | `stdout` y `stderr` completos (en streaming mientras corre), código de salida, si terminó por timeout o cancelación |
| Resultado estructurado | `result.json` **en bruto**, el parseado, errores de validación (schema / política de mensajes) y si hubo ronda de reparación |
| Efectos en el workspace | `git status`/diff-stat antes y después, lista de ficheros modificados, y diff completo guardado a disco |
| Control de escrituras fuera de sitio | Instantánea (ruta + mtime/tamaño) de los `extra_dirs` antes y después, para detectar escrituras fuera del workspace |
| Métricas | Inicio, fin, duración, y tokens/coste cuando el CLI los exponga |
| Transcript de la sesión | Si el CLI guarda una transcripción de la sesión (herramientas usadas, ficheros leídos, comandos ejecutados), se copia o enlaza a la invocación. Hay que **comprobar qué expone cada CLI**; si no hay, la auditoría de lo que hizo "por dentro" se limita al diff, los ficheros y los logs |

**Garantías**
- **Append-only:** las invocaciones no se editan ni se borran desde la aplicación; solo la política de retención las elimina.
- **Integridad:** se guarda un `sha256` de `prompt.md`, `stdout`, `result.json` y el diff, para detectar manipulación posterior.
- **Redacción:** antes de persistir, se pasan `argv`, logs y prompt por el redactor de secretos (valores de `.env`, patrones de tokens). El contenido del ticket **no** se redacta, ya que es dato del flujo.
- **Retención propia:** configurable y más larga que la de logs genéricos; las invocaciones de runs con efectos externos pueden fijarse a más tiempo.
- **Alcance ampliado:** el mismo mecanismo registra **todos los subprocesos** del motor (`git`, `gh`, scripts Python) como `executions`, con los campos que apliquen (comando, cwd, salida, código). Las invocaciones IA son el caso con más detalle.

**Visibilidad en la UI** (panel del nodo, pestaña **IA**)
- Lista de invocaciones por intento (`dev#2 · principal`, `dev#2 · reparación`), con estado y duración.
- Al abrir una: comando copiable, prompt con las capas desplegables, respuesta en streaming/completa, `result.json` en bruto frente al validado y sus errores, diff generado, ficheros tocados, aviso si hubo escrituras fuera del workspace, y métricas.
- El **buzón de aprobaciones** enlaza a las invocaciones que produjeron el cambio que se está aprobando.
- Vista de auditoría del run (todas las invocaciones en orden cronológico), con búsqueda y **exportación** (JSON/ZIP) para revisión externa.

---

## 7. Aprobación manual y validador de seguridad

### 7.1 Regla de dominancia
> Todo nodo con `side_effect = external` debe estar **dominado** por un `manual_approval` que lo cubra: todo camino desde el inicio del plan hasta ese nodo pasa por un aprobador cuyo `approves` lo incluye.

- Se comprueba con análisis de dominadores sobre el grafo (cubre `route`, joins y ciclos).
- La marca `side_effect` proviene del **código del nodo**. El plan no puede cambiarla.
- Un `manual_approval` solo vale para los nodos listados en su `approves`; esos nodos deben ser alcanzables desde él.
- Las lecturas externas (Jira, GitHub, estado de PR) no exigen aprobación.

### 7.2 Invalidación
Si algún nodo situado antes de un nodo con efecto vuelve a ejecutarse (por ejemplo el agente tras un `changes_requested`), la aprobación anterior queda **invalidada** y se vuelve a pedir. Evita aprobar un diff y pushear otro.

### 7.3 Funcionamiento
- Al llegar al aprobador, el motor calcula el `preview()` de cada nodo cubierto y lo guarda en la aprobación (inmutable).
- La UI muestra los previews (diff, texto del PR, comentario de Jira, transición, página de Confluence).
- Decisión: `approved` / `rejected` (con comentario opcional que se puede enrutar de vuelta al agente).
- Cada plan hijo tiene sus propios aprobadores; no se heredan aprobaciones.
- Aprobaciones pendientes aparecen en un **buzón** (inbox) en la UI.

### 7.4 Límites conocidos
Un `ia_agent` es un CLI con herramientas y podría hacer acciones externas por su cuenta. Se mitiga (sin credenciales en el entorno, workspace aislado, timeout, `side_effect` conservador en scripts) pero **no se puede garantizar solo desde el grafo**. Queda documentado como riesgo aceptado.

---

## 8. Validador de planes (`gestplan validate`)

Errores pensados para que la IA los lea y corrija. Cada error lleva `code`, `node`, `path` y sugerencia.

Comprobaciones:
1. Schema general del plan y `schema_version`.
2. Tipos de nodo existentes y `version` soportada.
3. `config` válido contra el `ConfigModel` del nodo.
4. Aristas: nodo y evento origen existen; destino existe.
5. Eventos sin conectar (aviso o error según si son de fallo).
6. Referencias `${...}`: el nodo existe, es alcanzable antes del uso (no depende de un nodo posterior) y el output existe.
7. Inputs referenciados declarados.
8. Ciclos: cada nodo del ciclo con `max_attempts`.
9. Nodos inalcanzables desde el inicio.
10. `route`: eventos conectados, `path` válidos, regex compilables.
11. **Regla de aprobación** (sección 7).
12. `python_script`: el script existe, su META es válida y sus eventos coinciden con las aristas.
13. Plantillas referenciadas (`templates/...`) existen.

`gestplan dry-run plan.json --input k=v` recorre el grafo con nodos simulados (outputs de ejemplo) y muestra el orden de ejecución, ramas posibles y puntos de aprobación.

---

## 9. Persistencia (SQLite)

- SQLite en `data/gestplan.db`, `PRAGMA journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout`.
- Implementado con `sqlite3` de la biblioteca estándar y migraciones propias (ver 15.2); SQLAlchemy y Alembic quedan como opción futura.
- Un único escritor lógico (el motor); las lecturas de la API son concurrentes.
- Cada transición de estado se persiste **antes** de actuar (para poder recuperarse tras una caída).
- Contexto, config e outputs como columnas JSON.
- Logs grandes, diffs y adjuntos en disco (`data/runs/<run_id>/<node>/<attempt>/`), la BD guarda las rutas.

### Tablas

```
plans            (id, name, path, content_hash, schema_version, last_seen_at)
runs             (id, plan_name, plan_snapshot JSON, inputs JSON, status,
                  parent_run_id, parent_node_id, parent_index, idempotency_key UNIQUE,
                  created_at, started_at, finished_at, cancelled_at, summary JSON)
node_states      (run_id, node_id, status, attempts, last_event, updated_at,  PK(run_id,node_id))
node_attempts    (id, run_id, node_id, attempt, status, event, inputs_resolved JSON,
                  outputs JSON, error JSON, log_path, started_at, finished_at,
                  pid, tokens_in, tokens_out, cost, duration_ms)
approvals        (id, run_id, node_id, attempt, status, previews JSON, covers JSON,
                  decided_at, comment, invalidated_at)
invocations      (id, run_id, node_id, attempt, kind[main|repair], provider, model, agent, skill,
                  session_id, argv JSON, cwd, exe_path, cli_version, env_names JSON,
                  prompt_path, prompt_layers JSON, contract_version,
                  stdout_path, stderr_path, exit_code, timed_out, cancelled,
                  result_raw_path, result_parsed JSON, validation_errors JSON,
                  diff_path, files_changed JSON, outside_writes JSON,
                  transcript_path, tokens_in, tokens_out, cost,
                  started_at, finished_at, sha256 JSON)                     -- append-only
executions       (id, run_id, node_id, attempt, kind[git|gh|script|other], argv JSON, cwd,
                  exit_code, stdout_path, stderr_path, started_at, finished_at)
transitions      (id, run_id, node_id, from_status, to_status, event, at, reason)   -- auditoría
wait_registry    (id, run_id, node_id, kind, params JSON, next_check_at, deadline_at)
settings         (key, value JSON)                                                     -- p.ej. límite de concurrencia
```

Notas:
- `plan_snapshot` guarda el plan tal como estaba al lanzar el run: editar el fichero no altera runs en curso.
- `transitions` da un historial completo y base para el dashboard/auditoría.
- `idempotency_key` en `runs` evita runs hijos duplicados al reactivar `launch_plans`.
- Los secretos nunca se guardan; los valores sensibles se redactan antes de escribir `inputs_resolved`, `outputs` y logs.

### Recuperación al arrancar
1. Nodos en `running` → `interrupted` (el proceso ya no existe; se comprueba `pid`).
2. Runs en `running` sin nodos activos → recalcular estado (`waiting`/`failed`).
3. `wait_registry` se rehidrata para reanudar esperas.
4. Aprobaciones pendientes se mantienen.

---

## 10. Motor de ejecución

- Bucle del scheduler en asyncio: ante cada transición recalcula qué nodos pasan a `ready` y los encola.
- **Semáforos:** global (`max_concurrent_nodes`) y específico de agentes (`max_agents_parallel`), configurables.
- **Runner de procesos (Windows):** `asyncio.create_subprocess_exec`, captura de stdout/stderr en streaming a fichero y SSE, y **matado del árbol completo** (`psutil` o `taskkill /T /F`) al cancelar o vencer el timeout. Cuidado con rutas largas: workspaces cerca de la raíz del disco (por ejemplo `C:\gp\ws\<run>`).
- **Esperas externas:** nodos `waiting_external` registran en `wait_registry` y un ticker revisa `next_check_at`; no consumen hilos.
- **Eventos hacia la UI:** SSE (`/api/events`) con transiciones de nodo/run y líneas de log.
- **Concurrencia de runs:** varios runs a la vez; el límite global protege costes y disco.

---

### 10.1 Control de costes y selección de modelos

**Principio: barato por defecto, escalar solo de forma explícita y medirlo todo.**

**Medidas reales** (Claude Code 2.1.317, llamada trivial "responde OK", una sola ejecución cada una; la caché de prompts altera el coste, así que son orientativas):

| Prueba | Modelo | Coste |
|---|---|---|
| A · config global | `claude-sonnet-5-5` | 0,0409 USD |
| B · config global | `claude-haiku-5-5` | 0,0025 USD (≈ 16× menos que A) |
| C · solo ajustes de proyecto, sin slash commands | `claude-haiku-5-5` | 0,0071 USD (escritura de caché nueva) |
| D · como C + `--effort low`, caché ya caliente | `claude-haiku-5-5` | 0,0007 USD |

Conclusiones: (1) **el modelo es la palanca principal**; (2) el contexto base (~35 000 tokens: prompt de sistema y herramientas del propio Claude Code) **no se elimina** limitando `--setting-sources` (ahorra ~3 000 tokens); (3) **la caché de prompts importa**: llamadas con el mismo prefijo (mismas flags, mismo prompt de sistema, misma sesión) son mucho más baratas.

**Niveles de modelo (`model_tier`)**
- Los planes piden un **nivel**, no un nombre de modelo: `cheap | standard | premium`. Los nombres concretos se resuelven por proveedor en los ajustes (`models`), de modo que un cambio de modelo no obliga a tocar los planes. Valores iniciales para Claude: `cheap = claude-haiku-5-5`, `standard = claude-sonnet-5-5`, `premium = claude-opus-5-5`. Los de Copilot los define el usuario (no verificados).
- **Por defecto `cheap`** en todos los nodos de IA.
- **Tope `max_model_tier`** en los ajustes (por defecto `standard`): un plan que pida `premium` no valida hasta que el usuario lo habilite. Igual que `timeout_cap` y `validator_max_retries_cap`, impide que una IA diseñadora gaste de más. Un `model` explícito solo se acepta si está en `allowed_models`.
- **Escalado en reintentos** (`model_tier: ["cheap", "standard"]`): el primer intento usa el nivel barato y, si el validador devuelve cambios o fallan los tests, los siguientes intentos suben de nivel. Por defecto, sin escalado.
- **Esfuerzo (`effort`)** por defecto `low` en `cheap` y `medium` en `standard`; solo se sube de forma explícita.

**Presupuestos**
- **Por invocación:** `max_cost` → `--max-budget-usd` en Claude. En Copilot, que no lo expone, se limita con timeout y nº de rondas.
- **Por nodo y por run:** `settings.run_budget_usd`. **Global diario:** `daily_budget_usd`.
- Al superar un límite, el nodo emite **`budget_exceeded`**; sin arista, el run pasa a `waiting` y la UI pide al usuario continuar (con un nuevo tope) o parar.
- El coste se guarda por invocación (`invocations.cost`). Claude lo devuelve en `total_cost_usd`; para Copilot, si no hay métrica, se marca "no medido".

**Reducir gasto sin perder calidad**
- **Primero lo determinista:** `run_command` (tests, lint) antes del validador; el validador de IA solo corre si los tests pasan.
- **Prompts pequeños:** solo las secciones del ticket y el diff necesarios, no el repositorio entero; el agente recibe la lista de ficheros relevantes cuando se conoce.
- **Reutilizar caché:** `session_mode: continue`, y flags y preámbulo idénticos entre llamadas del mismo run.
- **Cortar pronto:** `max_retries` bajo, `no_progress` y presupuesto por bucle.
- **Validador de solo lectura** con herramientas limitadas (consume menos al no explorar ni editar).
- La caché de prompts de Claude tiene una validez de ~1 h en esta configuración: encadenar rondas seguidas es más barato que espaciarlas.

**Visibilidad**
- Coste por invocación, nodo y run en el panel del nodo y en el dashboard (KPIs de gasto del día y del run); comando `gestplan cost [--run <id>]`.

**Equilibrio:** los modelos baratos fallan más en tareas difíciles. Se compensa con el validador cruzado, los tests reales y el escalado de nivel opcional; el bootstrap usa `cheap` salvo que una tarea concreta pida `standard`.

---

## 11. API (borrador)

```
GET  /api/catalog                         # nodos + scripts, con schemas
GET  /api/plans                           # planes en plans/
GET  /api/plans/{name}                    # contenido + validación
POST /api/plans/validate                  # valida un plan (body JSON)
POST /api/runs                            # lanzar {plan, inputs}
GET  /api/runs?status=&plan=&q=           # listado/dashboard
GET  /api/runs/{id}                       # estado completo + grafo
GET  /api/runs/{id}/nodes/{node}/attempts/{n}/log
GET  /api/runs/{id}/nodes/{node}/attempts/{n}/invocations
GET  /api/invocations/{id}                # metadatos completos
GET  /api/invocations/{id}/{prompt|stdout|stderr|result|diff|transcript}
GET  /api/runs/{id}/audit?format=json|zip # auditoría completa del run
POST /api/runs/{id}/cancel
POST /api/runs/{id}/nodes/{node}/retry    # reactivar
GET  /api/approvals?status=pending
POST /api/approvals/{id}/decision         # {decision, comment}
GET  /api/events                          # SSE
GET  /api/settings   PUT /api/settings
```

---

## 12. Frontend

### 12.1 Stack
| Pieza | Elección | Motivo |
|---|---|---|
| Build | Vite + React 18 + TypeScript (strict) | Estándar, arranque rápido |
| Grafo | `@xyflow/react` (React Flow 12) | Requisito del proyecto |
| Layout automático | `elkjs` (layered, izquierda→derecha) | Maneja mejor ciclos y puertos múltiples que dagre |
| Datos servidor | TanStack Query | Caché, refetch, invalidación por SSE |
| Estado UI | Zustand | Selección de nodo, filtros, panel abierto |
| Rutas | React Router | Pantallas con URL compartible (`/runs/:id?node=dev`) |
| Estilos | Tailwind + componentes shadcn/ui | Rápido y consistente; modo claro/oscuro |
| Tipos API | `openapi-typescript` generado desde FastAPI | Front y back no se desincronizan |
| Logs | `xterm.js` (o lista virtualizada con ANSI) | Logs largos con colores |
| Diffs | `react-diff-view` o Monaco diff | Previews de cambios en aprobaciones |
| Editor JSON | Monaco con JSON Schema del plan | Autocompletado y errores al ver/editar planes |
| Formularios de inputs | Generados del bloque `inputs` del plan (JSON Schema) | Sin pantallas ad hoc por plan |
| Tests | Vitest + Testing Library + Playwright (con MSW como API falsa) | |

Servido en desarrollo por Vite con proxy a FastAPI; en uso normal, FastAPI sirve `web/dist` (un solo comando para arrancar todo).

### 12.2 Estructura prevista de `web/`
```
web/src/
├─ api/            # cliente generado + hooks (useRuns, useRun, useApprovals...)
├─ realtime/       # conexión SSE, reconexión con Last-Event-ID, invalidación de queries
├─ features/
│  ├─ dashboard/
│  ├─ run/         # grafo, panel de nodo, intentos, logs
│  ├─ approvals/   # buzón y renderers de preview
│  ├─ plans/       # lista, visor, validación, lanzador
│  ├─ catalog/
│  └─ settings/
├─ graph/          # nodos/aristas custom, layout ELK, mapeo estado→estilo
├─ components/     # UI compartida (badges de estado, tablas, drawers)
└─ lib/
```

### 12.3 Tiempo real
- Un único `EventSource` a `/api/events`. Eventos: `run.updated`, `node.updated`, `attempt.log` (líneas), `approval.created|decided|invalidated`, `settings.updated`.
- Los eventos de estado **invalidan queries** de TanStack Query (o parchean la caché); los de log se anexan al visor sin refetch.
- Reconexión automática con `Last-Event-ID`; al reconectar se refresca el estado completo para no depender de no haber perdido eventos.
- Indicador de conexión visible (si el backend cae, la UI lo dice).

### 12.4 Pantallas

**1. Dashboard**
- Franja de contadores clicables: *en curso · esperando aprobación · fallidos/interrumpidos · terminados hoy*.
- Sección **"Requiere atención"** arriba: aprobaciones pendientes, runs fallidos o interrumpidos, con acción directa (abrir / reactivar).
- Tabla de runs: estado, plan, inputs clave (p. ej. `jira_id`, configurable por plan con `display_inputs`), barra de progreso `nodos ok / total`, nodo(s) activo(s), duración, hijos (`3/10`).
- Filtros por estado, plan y texto libre (busca en inputs); agrupar por plan o por ticket; ordenar.
- Runs hijos anidados bajo su padre (expandible).
- Acciones masivas: cancelar seleccionados.
- Se actualiza en vivo por SSE.

**2. Run (pantalla principal)**
- Cabecera: plan, inputs, estado, tiempos, ruta de breadcrumb si es hijo (`padre › hijo`), botones *Cancelar run* y *Reactivar* (si procede).
- Lienzo React Flow:
  - **Nodo custom:** icono por familia, nombre, tipo, color+icono de estado (nunca solo color), insignia de intento (`2/3`), candado/escudo si es efecto externo, escudo con tic si su aprobación está concedida.
  - **Puertos de salida por evento:** cada evento del nodo es un handle con etiqueta; los de fallo en rojo, los de éxito en verde, el resto neutro. Las aristas llevan el nombre del evento.
  - **Aristas:** la que se disparó se resalta; las no tomadas se atenúan; las de retorno (bucles) van discontinuas y curvadas; la arista activa se anima.
  - **Nodos `skipped`** atenuados; **`waiting_*`** con pulso.
  - Nodo `launch_plans` muestra mini-lista de hijos con su estado y enlace al run hijo.
  - Minimapa, zoom, "ajustar a vista", botón *reorganizar* (ELK). Respeta `layout` del plan si existe.
- **Panel lateral del nodo** (al seleccionar; selección en la URL):
  - *Resumen:* estado, evento emitido, tiempos, tokens/coste si es agente.
  - *IA:* invocaciones del nodo con comando, prompt por capas, respuesta, `result.json`, diff y métricas (ver 6.8).
  - *Entradas / Salidas:* JSON del contexto resuelto (secretos redactados).
  - *Logs:* en vivo, con búsqueda y descarga.
  - *Intentos:* línea de tiempo de intentos (para bucles agente/validador), con el feedback del validador entre ellos.
  - *Preview / Aprobación:* si el nodo es `manual_approval`, aquí se aprueba/rechaza.
  - Acciones: **Reactivar** (solo `failed/cancelled/interrupted`).
- Vista alternativa **línea de tiempo** (tipo Gantt) para ver paralelismo y esperas.

**3. Buzón de aprobaciones**
- Lista de pendientes con plan, ticket, nodo y antigüedad; contador en la navegación; notificación de escritorio opcional.
- Detalle: qué nodos cubre, y un renderer por tipo de preview:
  - `diff` → diff por ficheros con resumen (+/−).
  - `markdown` → título y cuerpo del PR.
  - `adf` → comentario de Jira renderizado.
  - `transition` → `estado A → estado B`.
  - `confluence` → página renderizada desde los bloques.
  - `keyvalue` → asignaciones, ramas, etc.
- Aprobar / Rechazar con comentario (el comentario puede volver al agente si hay arista). Atajos de teclado.
- Si una aprobación queda **invalidada** (se reejecutó un nodo anterior), se muestra marcada y el nuevo preview.

**4. Planes**
- Lista de planes con descripción, nº de nodos, resultado de validación.
- Visor del grafo del plan (mismo lienzo, sin estados), junto a un panel con el JSON (Monaco, solo lectura al principio).
- Panel de **errores de validación** clicables: al pulsar uno se resalta el nodo/arista.
- **Lanzar:** formulario generado del bloque `inputs` (validación en cliente con el mismo schema), con historial de inputs recientes.

**5. Catálogo**
- Buscador de nodos y scripts; por nodo: descripción, schema de config/outputs, eventos, nivel de efecto, ejemplo.
- Botón **"Copiar como Markdown para IA"** (mismo contenido que `gestplan catalog --markdown`).

**6. Ajustes**
- Límites de concurrencia, rutas, retención, notificaciones.

### 12.5 Convenciones visuales de estado
| Estado | Estilo |
|---|---|
| `pending` | gris, borde punteado |
| `ready` | azul claro |
| `running` | azul con spinner |
| `waiting_approval` | ámbar con icono de mano |
| `waiting_external` | ámbar con icono de reloj |
| `succeeded` | verde con tic |
| `failed` | rojo con aspa |
| `interrupted` | naranja con rayo |
| `cancelled` | gris oscuro tachado |
| `skipped` | gris atenuado |

Siempre icono + color (accesibilidad) y modo oscuro desde el principio.

### 12.6 Cambios que esto exige al backend
- `preview()` devuelve un objeto **tipado** `{ kind: "diff" | "markdown" | "adf" | "transition" | "confluence" | "keyvalue", ... }` para que la UI sepa pintarlo.
- Los nodos exponen metadatos de UI: icono, familia (para color/agrupación) y qué campos de `inputs` mostrar en el dashboard (`display_inputs` a nivel de plan).
- El endpoint de run devuelve en una sola respuesta el grafo del snapshot + estado de nodos + intentos resumidos, para pintar sin múltiples llamadas.
- Evento SSE con id incremental para poder reanudar con `Last-Event-ID`.
- OpenAPI completo (se usa para generar los tipos del front).

### 12.7 Fases del frontend
1. Esqueleto, navegación, cliente generado, SSE y dashboard básico.
2. Pantalla de run con grafo, estados y panel de nodo (logs, I/O).
3. Buzón de aprobaciones con renderers de preview.
4. Visor de planes, validación y lanzador de runs.
5. Catálogo y ajustes.
6. Pulido: línea de tiempo, runs hijos anidados, atajos, notificaciones.
7. **Más adelante:** editor visual del grafo (arrastrar nodos, formularios por schema, exportar a JSON), una vez el formato de plan esté estable. Los planes los escribe la IA, así que primero va la visualización.

---

## 13. Configuración y seguridad

`.env.example`:
```
JIRA_BASE_URL=https://tuempresa.atlassian.net
JIRA_EMAIL=
JIRA_API_TOKEN=
CONFLUENCE_BASE_URL=https://tuempresa.atlassian.net/wiki
CONFLUENCE_EMAIL=
CONFLUENCE_API_TOKEN=
GP_DATA_DIR=C:\gp\data
GP_WORKSPACES_DIR=C:\gp\ws
GP_MAX_CONCURRENT_NODES=4
GP_MAX_AGENTS_PARALLEL=2
```

- GitHub y git usan la sesión de `gh` ya autenticada.
- Backend escuchando solo en `127.0.0.1`.
- Los tokens se leen solo en la capa de integraciones; nunca se pasan al entorno de `ia_agent` ni `python_script`.
- Borrado de workspaces solo bajo `GP_WORKSPACES_DIR`.

---

## 14. Estrategia de pruebas
Objetivo: poder probar **todo el flujo de forma determinista y sin tocar sistemas reales**, y reservar un conjunto pequeño de pruebas manuales ("smoke") para lo que solo se verifica contra el sistema real.

### 14.1 Diseño para poder probar
Cada dependencia externa va detrás de una interfaz (puertos y adaptadores) que se puede sustituir por un doble en los tests:
`ProcessRunner`, `GitClient`, `GitHubClient`, `JiraClient`, `ConfluenceClient`, `AgentProvider` y `Clock`.
- El **reloj inyectable** permite probar timeouts, esperas (`wait_checks`), caducidades y reintentos con *backoff* sin esperar tiempo real.
- Los ficheros y la BD usan rutas temporales (`tmp_path`, SQLite en memoria o en disco temporal).

### 14.2 Niveles de prueba
| Nivel | Qué cubre | Cómo |
|---|---|---|
| Unitarias | Validador (cada regla), condiciones y regex, plantillas `${...}`, conversores ADF y renderer de Confluence, dominancia de aprobaciones, política de mensajes, ensamblado del prompt | `pytest`; ficheros *golden* para prompts, ADF y XHTML |
| Motor | Joins, `skipped`, ciclos y `max_retries`, `route`, cancelación en cascada, reactivación, timeouts, recuperación al arrancar | Grafos con nodos falsos y `Clock` falso |
| Caos / idempotencia | Matar el proceso en **cada transición** (inyectando fallo en cada punto de persistencia) y comprobar que el run se reanuda sin duplicar efectos | Bucle de pruebas parametrizado sobre los puntos de fallo |
| Git real local | `prepare_workspace`, rama ya existente, `commit`, `push` (aceptado y `rejected`), hooks que fallan, ramas locales con sufijo | Repos reales de git en disco temporal, usando un **repo bare local como remoto** (`file://`) |
| GitHub (`gh`) | Crear/leer PRs, checks, workflows | `gh` falso (ejecutable de prueba que responde con fixtures) tras la interfaz `GitHubClient` |
| Agentes | Timeouts, inactividad, matado del árbol de procesos, `result.json` válido/ inválido/ ausente, ronda de reparación, bucle agente↔validador, escrituras fuera del workspace | **CLI falso** (script que imita `copilot`/`claude` con comportamientos programables) |
| Jira / Confluence | Todas las acciones, ADF, idempotencia por marcador, 429 y reintentos | Servidor HTTP simulado (respuestas grabadas como fixtures) |
| API | Rutas, SSE, aprobaciones, reanudación con `Last-Event-ID` | `TestClient` de FastAPI |
| Frontend | Componentes, grafo, estados, buzón | Vitest + Testing Library + MSW |
| Extremo a extremo | Un run completo desde la UI: lanzar, ver el grafo moverse, aprobar, cancelar, reactivar | Playwright contra el backend arrancado con **todos los dobles** |
| Planes de ejemplo | Cada plan de `plans/examples/` valida y pasa `dry-run` | Test parametrizado en CI |
| Smoke (manual) | Un run real mínimo contra un repo de prueba, un proyecto de Jira de prueba y un agente real | Marcados `@pytest.mark.smoke`; no corren por defecto, requieren `.env` y se ejecutan a mano |

### 14.3 Qué no se puede probar de forma determinista
- **La calidad de lo que hace un agente real** (es no determinista y cuesta tokens). Se cubre su *contrato* (formato, timeouts, reparación) con el CLI falso y se comprueba el comportamiento real solo en el smoke.
- **Diferencias de la API real de Jira/GitHub/Confluence** frente a los mocks: lo detecta el smoke.
- **Robustez frente a prompt injection:** solo se puede revisar con casos manuales.

El **modo sandbox** (ejecutar el flujo real con agentes pero con los nodos externos en simulación, ver NICE_TO_HAVE) facilitaría mucho los smoke, y puede subir de prioridad.

### 14.4 Herramientas y ejecución
- Backend: `pytest` (+ `pytest-asyncio`), `ruff`, `mypy`; cobertura mínima exigida en motor y validador.
- Frontend: `vitest`, `playwright`, `eslint`, `tsc --noEmit`.
- Todo se ejecuta en Windows (único entorno objetivo), lo que permite probar de verdad rutas largas, `.cmd` y el matado de árboles de procesos.
- Un único comando (`pytest` + `npm test`) como puerta de calidad local; CI opcional más adelante.

### 14.5 Repositorio sandbox para pruebas reales (smoke)
Un repositorio propio y dedicado en GitHub (por ejemplo `gestplan-sandbox`) donde ejecutar planes reales sin riesgo para trabajo real.

**Contenido**
- Un proyecto pequeño (Python) con tests, para que los agentes tengan algo realista que modificar.
- Un workflow de GitHub Actions que ejecuta los tests y **puede fallar a propósito** según el escenario.
- Plantilla de PR, protección de la rama principal y (opcional) un revisor para probar comentarios de revisión.
- Un catálogo de **escenarios** con resultado esperado conocido: añadir una función, corregir un test roto, rama remota ya existente (sufijo local, push a la original), push rechazado por avance del remoto, checks en rojo (ciclo posterior al PR), conflicto de merge, ticket ambiguo (comprobar `assumptions`), ticket con instrucciones maliciosas (prompt injection) y fichero con un secreto falso (escaneo de secretos).
- Un script `reset-sandbox` que cierra PRs, borra ramas y deja `main` en un estado conocido entre ejecuciones.

**Salvaguardas**
- Lista de repos permitidos para los smoke (`smoke_allowed_repos`): las pruebas **se niegan a ejecutarse** contra cualquier otro repo.
- Credenciales de prueba separadas cuando sea posible (token con acceso solo al sandbox).
- Proyecto de Jira y espacio de Confluence de prueba, igualmente en lista de permitidos, para los nodos `jira.*` y `confluence.*`.

**Uso**
- Validar los nodos reales (`git.*`, `github.*`) y ver los flujos completos en la UI.
- Servir de entorno donde la IA que diseña planes los prueba antes de usarlos en repos reales (junto con el modo sandbox de NICE_TO_HAVE).
- Se crea en la fase 3 (antes de los nodos git reales); Jira/Confluence de prueba, en sus fases.

**Estado:** ya creado y verificado: [dgarciap88/gestplan-sandbox](https://github.com/dgarciap88/gestplan-sandbox) (privado, cuenta personal), clonado en `C:\Users\dani8\git\gestplan-sandbox`. Incluye proyecto con tests, CI (en verde en `main`), plantilla de PR, los 9 escenarios y los scripts `reset-sandbox.ps1` / `advance-remote.ps1`, con ramas semilla `scenario/broken-test`, `feature/SBX-3` y `feature/SBX-4`. El estado base es la etiqueta `baseline-v2`.

**Lecciones del montaje** (a tener en cuenta en el motor):
- **Correo de git y GitHub:** con la protección de correo privado activa, GitHub rechaza (GH007) pushes de commits con el correo real. El sandbox usa el correo `noreply` de GitHub como configuración local del repo. `git.push` debe tratar ese rechazo con un error claro y `gestplan doctor` debe avisar.
- **Codificación de scripts en Windows PowerShell 5.1:** un `.ps1` UTF-8 sin BOM se lee como ANSI y corrompe los acentos en mensajes de commit y ficheros. Los scripts se guardan con BOM, y el motor debe pasar los textos a git por fichero (`-F`) o asegurar UTF-8 en lugar de depender de la codificación de consola.

---

## 15. Hoja de ruta

| Fase | Contenido |
|---|---|
| 0 | Entorno: `python -m venv .venv` + `pip install -e ".[dev]"`, `pyproject`, estructura, CI local |
| 1 | Modelos de plan + validador + catálogo + dry-run (sin ejecutar nada real); `gestplan doctor` básico (comprueba `git`, `gh` logado, CLIs de agentes y credenciales del `.env`) |
| 2 | Motor + SQLite + estados + recuperación; nodos `route`, `manual_approval`, `python_script` |
| 3 | `prepare_workspace`, `ia_agent`, `ia_agent_validator`, `git.*` + bucle agente/validador |
| 4 | API FastAPI + SSE y frontend: dashboard, run en vivo, buzón de aprobaciones |
| 5 | GitHub (estado/checks) y Jira (familia completa con ADF) |
| 6 | `launch_plans` / `wait_plan` con cancelación en cascada |
| 7 | Confluence (plantillas + storage format) |
| 8 | Pulido: logs, limpieza, ajustes, visor de planes; después editor visual |

### 15.1 Hito Bootstrap (autoalojamiento)

Objetivo: llegar cuanto antes a un punto en el que **la propia herramienta implemente sus siguientes tareas** mediante planes, y usar esos planes como banco de pruebas real mientras se construye. Hasta ese hito, el código lo escribe el asistente a mano; después, las tareas se lanzan como runs y las personas aprueban y fusionan.

**Alcance mínimo del hito (todo por CLI, sin UI)**

| Pieza | Fase de origen | Nota |
|---|---|---|
| Modelo de plan, validador, catálogo, `dry-run`, `doctor` | 1 | Imprescindible: la IA que escribe planes valida con esto |
| Motor, SQLite, estados, recuperación, reactivación, timeouts | 2 | Con `route`, `manual_approval` y `python_script` |
| CLI de operación: `run`, `status`, `logs`, `cancel`, `retry`, `approvals list/show/approve/reject` | 2 | La aprobación se hace por CLI hasta que exista la UI |
| `prepare_workspace`, `ia_agent` (Copilot y/o Claude), `ia_agent_validator`, `git.commit`, `git.push`, `git.create_pr` | 3 | Con el contrato de prompt y la auditoría de invocaciones (6.8) |
| `launch_plans` y `wait_plan` | 6 → **adelantada** | Necesarios para "un plan que lanza planes" |
| `run_command` local (tests, lint) | NICE_TO_HAVE 3.2 → **promovida** | El validador y el agente necesitan ver la salida real de los tests |
| Niveles de modelo, topes (`max_model_tier`) y presupuestos por invocación y por run (10.1) | NICE_TO_HAVE 4 → **promovido** | Imprescindible para que el bootstrap sea barato desde el primer run |

**Fuera del hito (se construye después, ya con la propia herramienta):** UI (fase 4), nodos Jira y Confluence (5, 7), ciclo posterior al PR, escaneo de secretos y el resto de NICE_TO_HAVE.

**Sustituto de Jira durante el bootstrap:** las tareas son ficheros en `tasks/` del propio repo (`TASK-001.md`, con descripción y criterios de aceptación) y el plan recibe `task_file` como input en lugar de `jira_id`.

**Planes del bootstrap**
1. `implement-task`: `prepare_workspace → run_command (línea base de tests) → ia_agent ⇄ ia_agent_validator (con run_command de tests) → manual_approval → git.commit → git.push → git.create_pr`.
2. `implement-tasks`: recibe una lista de ficheros de tarea, hace `launch_plans` de `implement-task` (uno por tarea, con límite de concurrencia) y `wait_plan`; resumen final con los PRs creados.

**Precauciones de autoalojamiento**
- **El motor que ejecuta no es el código que se modifica:** los agentes trabajan siempre en un workspace clonado; el motor corre desde un checkout estable (`main` en un directorio aparte). Actualizarlo es un paso manual tras fusionar un PR.
- **Migraciones de BD:** cada cambio de esquema lleva migración (Alembic) y se prueba contra una BD de la versión anterior, porque el motor en uso arrancará con código nuevo.
- **Barrera de calidad:** ningún PR sin `pytest` en verde en el workspace (nodo `run_command` antes de la aprobación) y revisión humana antes de fusionar. **Un humano fusiona siempre**; los planes no fusionan.
- **Aprobaciones antes de cualquier efecto externo** (regla 7.1) desde el primer plan, también en el bootstrap.
- **Orden de tareas:** primero las que permiten ejecutar el siguiente plan (el propio camino al hito lo recorre el asistente), luego las que se prueban con la herramienta, empezando por tareas pequeñas y de bajo riesgo para validar el flujo.
- **Repositorio:** `gest-repo-plan` debe ser un repo git con remoto en GitHub antes de usar `prepare_workspace` sobre sí mismo.

### 15.2 Estado de implementación

**Hecho (hito Bootstrap, solo con Claude; además el servidor web y la UI, ver más abajo):** modelos de plan, validador, catálogo, `dry-run` y `doctor` (fase 1); SQLite, motor con estados, reintentos, reactivación, recuperación y timeouts (fase 2); `prepare_workspace`, `cleanup_workspace`, `ia_agent`, `ia_agent_validator`, `run_command`, `git.commit`, `git.push`, `git.create_pr` (fase 3); `route`, `manual_approval`, `python_script`, `launch_plans`, `wait_plan`; niveles de modelo con topes y presupuesto por invocación (`--max-budget-usd`); auditoría de comandos (`executions`) e invocaciones de IA (`invocations`); CLI de operación (`run`, `resume`, `status`, `logs`, `cancel`, `retry`, `approvals`, `cost`). Planes `implement-task` e `implement-tasks` y tareas iniciales en `tasks/`. 135 tests (incluidos git real con remoto local y un Claude falso) y una prueba real con Claude Haiku.

**Servidor web y UI (fase 4, primera versión):** `gestplan serve` arranca FastAPI con un **supervisor persistente** (el mismo motor, ahora en un bucle que no termina) que avanza todos los runs activos, recupera los nodos interrumpidos al arrancar y se despierta al instante con cada acción de la API. La API sigue la sección 11 (runs, planes, catálogo, aprobaciones, nodos, logs, invocaciones, estadísticas) y un canal **SSE** (`/api/events`, reanudable con `Last-Event-ID`) emite las transiciones de nodo, run y aprobación leyendo la propia tabla `transitions`. La UI (`web/`: Vite, React, TypeScript, React Flow, TanStack Query) ofrece dashboard con contadores y "Requiere atención", grafo del run con estados, aristas disparadas y bucles discontinuos por debajo, panel de nodo (resumen, intentos, datos, logs en vivo, actividad de IA con prompt y respuesta), buzón de aprobaciones con vista previa, visor de planes con validación y formulario de lanzamiento, y catálogo con "copiar como Markdown para IA". El backend sirve `web/dist` (un solo proceso) y la UI usa `HashRouter`. Diseño del grafo: posiciones por niveles calculadas en el servidor (sin ELK por ahora).

**Integraciones y seguridad (fase 5):** nodos `jira.*` (`get_issue`, `get_comments`, `get_attachments`, `assign`, `comment`, `transition`, `create_subtask`; API v3 con conversión Markdown↔ADF, reintento ante 429/5xx e idempotencia por marcador o etiqueta) y `confluence.create_page` (bloques JSON → storage format, idempotente por marcador), sobre un cliente HTTP inyectable (`engine/http.py`) cuyas llamadas quedan auditadas como `executions` sin la cabecera de autenticación. Credenciales en `.env` (`JIRA_*`, `CONFLUENCE_*`, que por defecto reutiliza las de Jira), cuyos valores se redactan de logs y auditoría. Nodos de GitHub de lectura (`github.get_pr_status`, `wait_checks`, `get_review_comments`, `get_workflow_runs`) y el plan `plans/examples/fix-pr-feedback.json` para el **ciclo posterior al PR** (corregir comentarios y checks y volver a empujar a la misma rama, con aprobación). Nodo `secret_scan` (primera pasada determinista: patrones, entropía y nombres de fichero; hallazgos enmascarados; la triaje con IA sigue pendiente), incluido en los planes de ejemplo antes del commit. Proveedor **Copilot** (`provider: "copilot"`): prompt por stdin y respuesta en texto; los modelos se configuran con `GP_COPILOT_MODEL_CHEAP|STANDARD|PREMIUM` (sin valores por defecto) y el formato **no está verificado con modelos reales**. Protección de la API local: validación de `Host` (DNS rebinding), de `Origin` y de la cabecera `X-Gestplan` en las peticiones que modifican datos, y token opcional `GP_API_TOKEN` (cabecera `X-Gestplan-Token` o `?token=` en la primera carga de la UI). UI: línea temporal del run, runs hijos anidados en el dashboard y avisos del navegador al llegar aprobaciones. El catálogo (`/api/catalog`, `gestplan catalog` y la pestaña Catálogo) es **autocontenido**: formato del plan, reglas, estructuras anidadas y todos los campos descritos (`catalog_docs.py`, con un test que lo exige).

**Plan `apply-skill`:** dados `skill`, `agent`, `jira_id` y `source_branch`, prepara el workspace (rama de trabajo = `jira_id` tal cual, partiendo de `source_branch`), aplica la skill con un agente de Copilot, valida con Claude (validación cruzada), escanea secretos, hace commit, pide aprobación y hace push; abre un PR contra `source_branch` solo si `source_branch` y `jira_id` son distintos. `ia_agent` admite `skill`: el prompt empieza por `/<skill>`. Los agentes y skills de usuario se buscan en `GP_AGENTS_HOME` (por defecto `<home>/.copilot`), que se pasa a Copilot como `COPILOT_HOME`; si no están ahí solo hay un aviso, porque pueden venir del repositorio. Los tests los ejecuta el propio agente. Sin verificar con una skill y un agente reales.

**Pendiente:** editor visual del grafo; triaje de secretos con IA; verificación del adaptador Copilot y de Jira/Confluence contra servicios reales (los tests usan un servidor simulado; falta el smoke con credenciales); presupuesto por run y diario; el resto de NICE_TO_HAVE.

**Decisiones tomadas al implementar**
- **SQLite con la biblioteca estándar** (`sqlite3`, modo WAL) y migraciones propias por `PRAGMA user_version`, en lugar de SQLAlchemy y Alembic: menos dependencias y el esquema es pequeño. Se puede cambiar más adelante.
- **Motor cooperativo de un solo proceso** (`Engine.run_until_quiescent`): avanza un run y sus hijos hasta que termina o solo quedan aprobaciones o esperas externas. Cada transición se persiste antes de actuar; `recover()` marca `interrumpido` lo que estaba en ejecución tras una caída.
- **Semántica de uniones:** las aristas entrantes se agrupan **por nodo origen** (varios eventos del mismo origen son alternativas). `join: all` exige que todos los orígenes disparen; si uno murió, el nodo se salta. `join: any` basta con uno.
- **Bucles:** un nodo que ya se ejecutó se reejecuta (nuevo intento) cuando le llega una arista; el límite es `max_attempts` o el derivado del validador (`max_retries + 1`). Al superarlo, el nodo falla con el evento `max_attempts`. Reactivar un nodo (`retry`) reinicia su contador.
- **Config normalizada:** el motor resuelve las referencias y valida el resultado con el `ConfigModel` del nodo, de modo que los ejecutores reciben los valores por defecto del modelo (p. ej. el validador es de solo lectura). Las referencias a nodos que aún no han producido salidas valen cadena vacía (primera ronda de un bucle); un campo ausente en un nodo que sí se ejecutó es un error.
- **Reactivar `wait_plan`** reactiva también los hijos cancelados que espera; `launch_plans` es idempotente por clave `padre:nodo:índice`.
- **Aprobaciones por CLI** (`approvals list/show/approve/reject`) y continuación con `resume`; la aprobación se invalida si el nodo se reejecuta.
- **Commits y mensajes:** `git.commit` usa `-F` (fichero UTF-8, sin depender de la codificación de consola), aplica la política de mensajes y elimina trailers `Co-Authored-By`.

**Hallazgos con Claude real (Haiku 5.5, Claude Code 2.1.317)**
- Una tarea mínima (función + test) con agente y validador cuesta **≈ 0,007–0,014 USD**; ~35 000 tokens de contexto base por llamada, mayormente caché.
- En Windows el CLI ejecuta comandos con la herramienta **PowerShell**: las reglas de `--allowedTools` deben cubrir `PowerShell(...)` además de `Bash(...)`.
- Las reglas de comando son estrictas: `python -m pytest ... || py ...` (comando compuesto, con redirección) se **deniega**. El preámbulo del sistema pide comandos simples y de uno en uno. `permission_denials` del JSON del CLI permite auditar qué se denegó.
- Si se muestra el esquema JSON tal cual, el modelo copia la clave `description` de la clase en su respuesta: el contrato se envía sin `title` ni descripción de clase.
- El `session_id` útil para reanudar es el que **devuelve el CLI**, no el UUID que se calcula.

**Entorno:** el antivirus (Avast) intercepta HTTPS con su propio certificado. El `pip` global (≥ 26) usa el almacén de Windows y funciona; el `pip` 24 de un venv nuevo usa `certifi` y falla con `CERTIFICATE_VERIFY_FAILED`. Se instala desde el `pip` global: `python -m pip --python .venv\Scripts\python.exe install -e ".[dev]"`.

---

## 16. Puntos aún por decidir
1. ~~`uv` o `pip`+venv~~ **Decidido: `pip` + venv** (Python 3.11.9). Dependencias en `pyproject.toml` con extras `dev`; `requirements.lock` opcional generado con `pip freeze` para reproducibilidad.
2. **Flags de los CLIs:** Copilot ya está cubierto por la invocación de pruebas (ver "Adaptadores de proveedor"). Claude ya está cubierto con `claude --help` y una prueba real (ver "Adaptador de Claude"); queda probar `--json-schema` y un caso con herramientas sobre un repo. Falta confirmar con una prueba que el `--agent`/skill de Copilot escribe `result.json` de forma fiable (la ronda de auto-reparación mitiga los fallos ocasionales).
3. **Confluence:** espacio y página padre por defecto; ¿se pasan por input o por `.env`?
4. **Retención:** cuánto tiempo conservar logs y workspaces de runs terminados.
5. **Notificaciones:** ¿aviso de escritorio cuando haya una aprobación pendiente o un run falle?
6. **Extracción de resumen del agente:** formato exacto del JSON que se le pide a `ia_agent` / validador.
7. **Plantillas de prompt:** convención y ubicación (`templates/prompts/`) y variables disponibles.
8. ~~Certificado SSL de `pip`~~ **No es un problema de la red del usuario:** `pip` funciona desde su terminal (descarga de `setuptools` verificada). El fallo `CERTIFICATE_VERIFY_FAILED` solo ocurre en el entorno de ejecución del asistente. Consecuencia práctica: las instalaciones de dependencias (`pip install -e ".[dev]"`) las ejecuta el usuario en su terminal, o hay que resolver el certificado en el entorno del asistente, sin desactivar la verificación.
