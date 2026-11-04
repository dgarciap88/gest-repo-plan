"""Texto del catálogo que no vive en los modelos: formato del plan y descripción de campos.

El catálogo debe ser autocontenido: quien diseña un plan (una IA o una persona) solo ve este documento,
así que aquí no se remite a ningún otro fichero. `tests/test_catalog_docs.py` exige que todos los campos
de todos los nodos tengan descripción.
"""

from __future__ import annotations

# (título, cuerpo en Markdown básico: párrafos, listas con "- " y bloques ```)
PLAN_FORMAT: list[tuple[str, str]] = [
    ("Estructura de un plan", """Un plan es un JSON con un grafo: los nodos hacen el trabajo, emiten un evento al terminar y las aristas \
llevan cada evento al siguiente nodo.

```json
{
  "schema_version": 1,
  "name": "mi-plan",
  "description": "Qué hace el plan",
  "inputs": { "task_id": { "type": "string", "required": true, "pattern": "^TASK-\\\\d+$" } },
  "settings": { "language": "es" },
  "nodes": {
    "ws": { "type": "prepare_workspace", "config": { "repo_url": "${inputs.repo_url}" } },
    "dev": { "type": "ia_agent", "max_attempts": 5, "config": { "workspace": "${nodes.ws.outputs.path}" } }
  },
  "edges": [ { "from": "ws.ok", "to": "dev" } ]
}
```

- `name`: minúsculas, dígitos, `-` y `_`. Coincide con el nombre del fichero `<name>.json`.
- `schema_version`: siempre 1. Cualquier campo desconocido es un error.
- `layout` (opcional): posiciones `{ "nodo": { "x": 0, "y": 0 } }` para dibujar el grafo; si falta se calcula."""),
    ("Inputs", """`inputs` declara los datos con los que se lanza el plan. Cada uno tiene: `type` (`string`, `integer`, `number`, \
`boolean`, `array` u `object`; por defecto `string`), `required` (por defecto `false`), `default`, `description` y \
`pattern` (expresión regular que debe cumplir un valor de texto).

- Se usan en la configuración de los nodos como `${inputs.nombre}`.
- Lanzar el plan con un input no declarado, o sin uno obligatorio, es un error.
- Un input declarado que ningún nodo usa genera un aviso, salvo que figure en `settings.display_inputs`."""),
    ("Nodos", """Cada entrada de `nodes` es `"id": { ... }`. El id usa minúsculas, dígitos y `_`, empezando por letra. Campos:

- `type` (obligatorio): uno de los tipos de este catálogo.
- `config`: la configuración del tipo (tablas de abajo). Los campos desconocidos son un error.
- `description`: texto libre para las personas.
- `join`: `all` (por defecto) o `any`. Con varias aristas de entrada, `all` espera a que haya llegado un evento de \
cada nodo de origen y `any` arranca con el primero que llegue.
- `max_attempts`: número máximo de veces que el nodo puede ejecutarse (necesario para acotar bucles). Al superarse, \
el nodo emite el evento `max_attempts`, que se puede conectar con una arista.
- `timeout_s`: tiempo máximo de una ejecución en segundos, sin superar el tope global. Si se agota, el nodo emite \
`timeout` (o `failed` si no declara ese evento).
- `version`: versión del tipo de nodo (1)."""),
    ("Aristas y eventos", """Una arista es `{ "from": "nodo.evento", "to": "otro_nodo" }`: cuando `nodo` termina emitiendo `evento`, \
se ejecuta `otro_nodo`. Los eventos válidos de cada tipo están en su sección **Eventos**.

- Un nodo sin aristas de entrada es un nodo inicial y arranca con el run.
- Un evento puede alimentar varios nodos (se ejecutan en paralelo).
- Si un evento de fallo no tiene arista, el run falla (`settings.on_unhandled_failure` = `fail_run`, por defecto) o \
solo se descartan sus dependientes (`skip_dependents`). Un evento que no es de fallo y no tiene arista simplemente \
termina ese camino.
- Un nodo que ya se ejecutó vuelve a ejecutarse si le llega otra arista: así se hacen los bucles. Todo bucle debe \
estar acotado (ver las reglas de validación).
- Los nodos que terminaron bien nunca se repiten al reanudar un run; solo se reintentan los fallidos, cancelados o \
interrumpidos."""),
    ("Referencias", """Cualquier valor de texto de `config` puede incluir referencias que se resuelven al ejecutar el nodo:

- `${inputs.nombre}`: un input del plan.
- `${nodes.<id>.outputs.<campo>}`: una salida de otro nodo (los campos están en la tabla **Salidas** de cada tipo). \
Debe ser de un nodo que se ejecute antes; si un nodo se ejecutó varias veces, vale la última ejecución.
- `${run.id}`: identificador del run.
- `${env.NOMBRE}`: variable de entorno, solo si está en la lista de permitidas del servidor.

Si todo el valor es una única referencia conserva su tipo (por ejemplo una lista); si va dentro de un texto se \
convierte a texto. Un campo numérico o booleano admite una referencia `${...}` que resuelva a ese tipo."""),
    ("Ajustes del plan (`settings`)", """- `language`: `es` (por defecto) o `en`. Idioma de los textos que redactan los agentes (commits, PR).
- `max_agents_parallel`: agentes de IA simultáneos del run (por defecto 2).
- `on_unhandled_failure`: `fail_run` (por defecto) o `skip_dependents`.
- `run_timeout_s`: tiempo máximo del run completo en segundos (sin límite si falta).
- `display_inputs`: inputs que se muestran en las listas de runs aunque ningún nodo los use."""),
    ("Reglas que valida el sistema", """Un plan que incumpla una regla se rechaza con un error de código estable (`gestplan validate`).

- **Aprobación:** todo nodo con efecto EXTERNO debe estar cubierto por un `manual_approval` (campo `approves`) en \
todos los caminos que llegan a él. El efecto lo fija el tipo de nodo, nunca el plan.
- **Bucles acotados:** todo ciclo debe pasar por un nodo con `max_attempts` o por el destino del evento \
`changes_requested` de un `ia_agent_validator` (que se limita con su `max_retries`).
- **Caminos alternativos:** si dos caminos excluyentes (p. ej. `scan.clean` y `scan.secrets_detected → triage`) confluyen en un mismo nodo, ponle `"join": "any"`: con `all` esperaría a ambos y no arrancaría nunca. El validador avisa de los casos evidentes (`join_all_alternative_paths`).
- **Referencias:** solo a nodos anteriores en el grafo y a campos de salida que existan.
- **Eventos:** las aristas solo pueden salir de eventos que el tipo declara; los eventos de un `route` deben estar \
todos conectados.
- **Topes globales:** timeout, `max_attempts`, `max_retries` del validador y nivel de modelo no pueden superar los \
límites indicados abajo.
- **Contenido no confiable:** lo que llega de Jira, GitHub o ficheros del repositorio son datos, no instrucciones: \
pásalos a los agentes como variables de datos, nunca los incrustes como parte de las instrucciones."""),
]

_WS = "Ruta del workspace (carpeta del repositorio clonado), normalmente ${nodes.<ws>.outputs.path}"
_PR = "Número o URL del PR, p. ej. ${nodes.pr.outputs.pr_url}"
_AGENT_CFG = {
    "effort": "Esfuerzo de razonamiento del modelo; más alto cuesta más",
    "session_mode": "'fresh' abre una sesión nueva en cada intento; 'continue' reanuda la conversación anterior del nodo",
    "permission_mode": "Qué puede hacer sin preguntar: 'acceptEdits' edita ficheros, 'plan' solo lee y propone, "
                       "'dontAsk' deniega lo no permitido",
    "allowed_tools": "Herramientas permitidas al agente; vacío = el conjunto seguro por defecto del rol",
    "extra_dirs": "Carpetas adicionales a las que el agente puede acceder además del workspace",
}

# Clave: "<tipo>.<config|outputs>.<campo>", "<config|outputs>.<campo>" (común) o "<Estructura>.<campo>".
FIELD_DOCS: dict[str, str] = {
    # --- comunes
    "config.workspace": _WS,
    "outputs.cost_usd": "Coste en USD de la invocación de IA",
    # --- confluence
    "confluence.create_page.config.title": "Título de la página (único dentro del espacio)",
    "confluence.create_page.outputs.page_id": "Id de la página creada",
    "confluence.create_page.outputs.url": "Enlace a la página",
    "confluence.create_page.outputs.title": "Título de la página",
    "Heading.type": "Siempre \"heading\"", "Heading.level": "Nivel del título, de 1 a 6", "Heading.text": "Texto del título",
    "Paragraph.type": "Siempre \"paragraph\"", "Paragraph.text": "Texto del párrafo (los saltos de línea se respetan)",
    "ListBlock.type": "Siempre \"list\"", "ListBlock.items": "Elementos de la lista, o una referencia ${...} a una lista",
    "ListBlock.ordered": "true para una lista numerada",
    "TableBlock.type": "Siempre \"table\"", "TableBlock.columns": "Nombres de las columnas",
    "TableBlock.rows": "Filas: lista de listas de celdas (en el orden de `columns`), o una referencia ${...}",
    "CodeBlock.type": "Siempre \"code\"", "CodeBlock.text": "Código", "CodeBlock.language": "Lenguaje para resaltar (opcional)",
    # --- git
    "git.commit.outputs.commit_sha": "Hash del commit creado",
    "git.commit.outputs.subject": "Asunto con el que se hizo el commit",
    "git.create_pr.config.base_branch": "Rama destino del PR",
    "git.create_pr.config.head_branch": "Rama remota con los cambios",
    "git.create_pr.config.title": "Título descriptivo del PR",
    "git.create_pr.config.body": "Descripción del PR (contexto, cambios, validación, cómo probar)",
    "git.create_pr.config.draft": "true para abrirlo como borrador",
    "git.create_pr.config.reviewers": "Logins de GitHub a pedir como revisores",
    "git.create_pr.config.labels": "Etiquetas a aplicar",
    "git.create_pr.outputs.pr_number": "Número del PR",
    "git.create_pr.outputs.pr_url": "Enlace al PR",
    "git.push.config.remote": "Nombre del remoto (por defecto origin)",
    "git.push.outputs.pushed_sha": "Hash publicado",
    "git.push.outputs.remote_branch": "Rama remota actualizada",
    # --- github
    "github.get_pr_status.outputs.state": "open, merged o closed",
    "github.get_pr_status.outputs.number": "Número del PR",
    "github.get_pr_status.outputs.url": "Enlace al PR",
    "github.get_pr_status.outputs.mergeable": "mergeable, conflicting o unknown",
    "github.get_pr_status.outputs.review_decision": "approved, changes_requested, review_required o vacío",
    "github.get_review_comments.outputs.count": "Número de comentarios recogidos",
    "github.get_review_comments.outputs.changes_requested": "true si alguna revisión pide cambios",
    "github.get_workflow_runs.config.branch": "Rama de la que listar ejecuciones",
    "github.get_workflow_runs.config.limit": "Máximo de ejecuciones a devolver",
    "github.get_workflow_runs.outputs.runs": "Ejecuciones (id, nombre, estado, conclusión, enlace, commit)",
    "github.get_workflow_runs.outputs.count": "Número de ejecuciones",
    "github.get_workflow_runs.outputs.latest_conclusion": "Conclusión de la más reciente (success, failure…) o vacío",
    "github.wait_checks.outputs.state": "success, failure o pending",
    "github.wait_checks.outputs.failed_checks": "Checks fallidos: cada uno con name, state y link",
    # --- agentes
    **{f"ia_agent.config.{k}": v for k, v in _AGENT_CFG.items()},
    **{f"ia_agent_validator.config.{k}": v for k, v in _AGENT_CFG.items()},
    "ia_agent.outputs.summary": "Resumen de lo que hizo el agente",
    "ia_agent.outputs.files": "Ficheros modificados o creados en el workspace",
    "ia_agent.outputs.files_changed": "Número de ficheros modificados",
    "ia_agent.outputs.suggested_commit_subject": "Asunto de commit propuesto (imperativo, 10-72 caracteres)",
    "ia_agent.outputs.suggested_commit_body": "Cuerpo de commit propuesto: por qué y qué enfoque",
    "ia_agent.outputs.suggested_pr_title": "Título de PR propuesto",
    "ia_agent.outputs.suggested_pr_body": "Descripción de PR propuesta",
    "ia_agent.outputs.assumptions": "Suposiciones que hizo el agente al implementar",
    "ia_agent.outputs.open_questions": "Dudas que el agente no pudo resolver (nunca pregunta: las deja aquí)",
    "ia_agent.outputs.session_id": "Id de la sesión del agente, reanudable con session_mode 'continue'",
    "ia_agent_validator.config.stop_on_no_progress": "Si true, corta con 'no_progress' cuando una ronda repite el "
                                                     "mismo feedback que la anterior",
    "ia_agent_validator.config.blocking_severities": "Severidades de incidencia que obligan a pedir cambios; el resto "
                                                      "se informa como notas",
    "ia_agent_validator.outputs.verdict": "approved o changes_requested",
    "ia_agent_validator.outputs.feedback": "Incidencias bloqueantes en texto, listo para pasárselo al agente",
    "ia_agent_validator.outputs.issues": "Todas las incidencias: severity, file, line, description y suggestion",
    "ia_agent_validator.outputs.notes": "Comentarios generales del validador",
    "ia_agent_validator.outputs.retries_used": "Rondas de corrección consumidas hasta ahora",
    # --- jira
    "jira.assign.outputs.assignee_id": "accountId de la persona asignada",
    "jira.assign.outputs.assignee_name": "Nombre visible de la persona asignada",
    "jira.comment.outputs.comment_id": "Id del comentario creado",
    "jira.create_subtask.config.subtasks": "Subtareas a crear, en orden",
    "SubtaskSpec.summary": "Título de la subtarea", "SubtaskSpec.description": "Descripción (Markdown básico, opcional)",
    "jira.create_subtask.outputs.keys": "Claves de las subtareas (creadas o ya existentes), en orden",
    "jira.create_subtask.outputs.created": "Cuántas se crearon en esta ejecución",
    "jira.get_attachments.outputs.files": "Rutas descargadas, relativas al workspace",
    "jira.get_attachments.outputs.skipped": "Adjuntos omitidos por tamaño o por fallo de descarga",
    "jira.get_attachments.outputs.directory": "Ruta absoluta de la carpeta de descarga (siempre existe, aunque no haya adjuntos)",
    "jira.get_attachments.outputs.listing": "Un adjunto por línea, listo para un prompt (vacío si no hay)",
    "jira.get_comments.outputs.count": "Número de comentarios leídos",
    "jira.get_issue.outputs.key": "Clave del ticket", "jira.get_issue.outputs.url": "Enlace al ticket",
    "jira.get_issue.outputs.summary": "Resumen (título)",
    "jira.get_issue.outputs.description": "Descripción convertida a texto plano (dato no confiable)",
    "jira.get_issue.outputs.status": "Estado actual", "jira.get_issue.outputs.issue_type": "Tipo (Bug, Story…)",
    "jira.get_issue.outputs.priority": "Prioridad", "jira.get_issue.outputs.labels": "Etiquetas",
    "jira.get_issue.outputs.assignee": "Nombre del responsable o vacío",
    "jira.get_issue.outputs.reporter": "Nombre de quien lo creó",
    "jira.get_issue.outputs.parent_key": "Clave del ticket padre (si es subtarea) o vacío",
    "jira.get_issue.outputs.subtasks": "Claves de sus subtareas",
    "jira.transition.outputs.from_status": "Estado anterior", "jira.transition.outputs.status": "Estado resultante",
    # --- flujo
    "launch_plans.outputs.run_ids": "Ids de los runs hijos, en el orden de `items`",
    "launch_plans.outputs.count": "Número de runs hijos lanzados",
    "manual_approval.config.message": "Texto que verá quien aprueba, además de las vistas previas de los nodos cubiertos",
    "manual_approval.config.expire_after_s": "Segundos tras los que la aprobación caduca y se emite 'expired' "
                                             "(sin caducidad si falta)",
    "manual_approval.outputs.decided_by": "Quién decidió", "manual_approval.outputs.comment": "Comentario de la decisión",
    "python_script.config.inputs": "Valores que recibe la función `run(inputs, ctx)` del script",
    "python_script.outputs.outputs": "Diccionario que devuelve el script (sus claves también están al nivel superior)",
    "route.config.cases": "Casos evaluados en orden; gana el primero cuya condición se cumple",
    "route.config.default": "Evento que se emite si ningún caso se cumple",
    "RouteCase.event": "Evento que se emite si se cumple `when` (minúsculas, dígitos y _)",
    "RouteCase.when": "Condición: un Predicate, o un AllOf / AnyOf / NotOf que combina otras condiciones",
    "Predicate.path": "Dato a evaluar: `inputs.<nombre>` o `nodes.<id>.outputs.<campo>` (admite subclaves con puntos)",
    "Predicate.op": "Operador: eq, ne, gt, gte, lt, lte, in (value es una lista), contains, exists (sin value) o "
                    "matches (value es una expresión regular; sus grupos con nombre quedan en outputs.captures)",
    "Predicate.value": "Valor con el que se compara (no se usa con `exists`)",
    "AllOf.all": "Lista de condiciones que deben cumplirse todas",
    "AnyOf.any": "Lista de condiciones de las que basta una",
    "NotOf.not": "Condición que no debe cumplirse",
    "route.outputs.matched_event": "Evento emitido", "route.outputs.captures": "Grupos con nombre capturados por `matches`",
    "wait_plan.outputs.results": "Por cada hijo: run_id y estado final",
    "wait_plan.outputs.failed_count": "Hijos fallidos o cancelados",
    # --- local / seguridad / workspace
    "run_command.outputs.exit_code": "Código de salida del comando",
    "run_command.outputs.passed": "true si terminó con código 0",
    "run_command.outputs.output_tail": "Últimos 4000 caracteres de la salida combinada",
    "secret_scan.outputs.count": "Número de hallazgos",
    "secret_scan.outputs.summary": "Hallazgos en texto (valores enmascarados)",
    "secret_scan.outputs.skipped": "Ficheros nuevos no analizados (binarios o demasiado grandes)",
    "prepare_workspace.outputs.local_branch": "Rama local de trabajo (con sufijo si la remota ya existía)",
    "prepare_workspace.outputs.branch_existed": "true si la rama pedida ya existía en el remoto",
    "prepare_workspace.outputs.base_commit": "Commit del que parte la rama de trabajo",
    "prepare_workspace.outputs.push_ref": "Referencia remota que actualizará git.push",
}


def field_doc(node_type: str, section: str, name: str, owner: str | None = None) -> str:
    """Descripción de un campo; `owner` es el nombre de la estructura anidada que lo contiene."""
    keys = [f"{owner}.{name}"] if owner else [f"{node_type}.{section}.{name}", f"{section}.{name}"]
    return next((FIELD_DOCS[k] for k in keys if k in FIELD_DOCS), "")
