# Guía para crear tipos de nodo

Para quien (persona o IA) vaya a añadir un tipo de nodo a gestplan. Un nodo son **dos piezas**: su *definición*
(`src/gestplan/nodes/`, lo que ve el validador, el catálogo y la IA que escribe planes) y su *ejecutor*
(`src/gestplan/executors/`, lo que hace de verdad). Esta guía resume el contrato; el diseño completo está en
`DESIGN.md`. Lee antes un nodo parecido: `nodes/jira.py` + `executors/jira.py` (HTTP), `nodes/github_extra.py`
+ `executors/github_extra.py` (procesos con `gh`), `nodes/flow.py` (nodos sin efecto, como `map_items`).

## 1. Qué decidir antes de escribir código

| Pregunta | Dónde se refleja |
|---|---|
| ¿Cambia algo fuera de gestplan? | `side_effect = "external"`: el validador exigirá un `manual_approval` que lo cubra. |
| ¿Solo toca el workspace local? | `side_effect = "local"`. Lectura pura: `"none"`. |
| ¿Qué puede pasar? | `events`: un evento por resultado distinguible (`ok`, `already_done`, `not_found`, `failed`...). Los de fallo llevan `failure=True`. |
| ¿Qué devuelve? | `OutputsModel`: los campos que otros nodos referencian con `${nodes.id.outputs.campo}`. |
| ¿Deja algo creado fuera? | `artifacts = (artifact("pr", "descripción"),)`: se verá en el run y en `/artifacts`. |
| ¿Repetirlo duplica algo? | Debe ser **idempotente**: ver el punto 4. |

El `side_effect` lo decide el código del nodo, nunca el plan. Si dudas entre `local` y `external`, es `external`.

## 2. La definición (`nodes/<familia>.py`)

```python
from pydantic import BaseModel, ConfigDict, Field
from gestplan.nodes.base import NodeType, artifact, event
from gestplan.nodes.common import TInt          # TInt = entero o referencia ${...}
from gestplan.nodes.registry import register


@register
class JiraSearch(NodeType):
    type = "jira.search"                        # familia.nombre, único
    family = "jira"
    side_effect = "none"
    default_timeout_s = 120
    description = "Qué hace, qué devuelve y qué advertencias tiene (se muestra a la IA que escribe planes)."
    events = (
        event("ok", "Hay al menos un ticket"),
        event("empty", "La consulta no devolvió tickets"),
        event("failed", "La consulta falló", failure=True),
    )
    example = {"jql": "project = ABC", "max_results": 20}   # aparece en el catálogo

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")          # campo desconocido = error
        jql: str = Field(min_length=1, description="Consulta JQL")
        max_results: TInt = Field(default=50, description="Máximo de tickets (1-100)")

    class OutputsModel(BaseModel):
        count: int = Field(description="Tickets devueltos")
        keys: list[str] = Field(description="Claves de los tickets")
```

Reglas:

- **Todo campo de `ConfigModel` y de `OutputsModel` lleva `description`** (un test lo exige) y la descripción
  debe bastar por sí sola: el catálogo es autocontenido y no puede remitir a `DESIGN.md` ni a otros ficheros.
- **Los campos que admiten una referencia `${...}`** deben aceptarla en el tipo: `TInt`, `TBool`, `TStrList`
  (`nodes/common.py`) o `Union[..., TemplateStr]`. Un campo `Literal[...]` o `bool` a secas **no** admite
  referencias (`merge_pr.method` es así a propósito).
- **Configuración que depende de otros valores** (p. ej. «`page_id` o bien `space` + `title`»): un
  `@model_validator(mode="after")` en `ConfigModel`.
- **Eventos dinámicos** (como `route`): sobrescribe `event_specs(config, ctx)`. **Salidas libres** (como
  `python_script`): `dynamic_outputs = True`.
- **Comprobaciones propias** (ficheros que deben existir, planes referenciados): `@classmethod check(...)`
  devolviendo `Issue`s.
- **Texto que quien aprueba puede editar** antes de aprobar: `editable_fields = ("body",)`.
- **Evento que devuelve el trabajo a un nodo anterior** (bucles): `loop_event`.
- Si el módulo es **nuevo**, añádelo a `_BUILTIN_MODULES` en `nodes/registry.py`.

## 3. El ejecutor (`executors/<familia>.py`)

```python
class JiraSearchExec(NodeExecutor):
    type = "jira.search"                        # el mismo que la definición

    async def run(self, ctx: ExecContext) -> Outcome:
        ...
        return Outcome("ok", {"count": 2, "keys": ["A-1", "A-2"]},
                       artifacts=[Artifact("issue", "A-1: título", url, "A-1")])
```

Y regístralo en `default_executors()` de `executors/__init__.py` (y su `import`). Si falta, el nodo existe en el
catálogo pero el motor no sabe ejecutarlo.

**El contexto (`ctx`)**

- `ctx.config`: la configuración **ya resuelta** (las referencias `${...}` están sustituidas).
- `ctx.settings`: ajustes y credenciales del servidor (`jira_base_url`, `confluence_token`...). Las credenciales
  viven en el `.env`; **nunca** en un plan, una salida o un log.
- `ctx.http(method, url, headers=..., json_body=...)`: llamadas HTTP auditadas (la cabecera de autenticación no se
  guarda). `ctx.process(argv, cwd=..., kind="git")`: subprocesos auditados. **Usa siempre estos dos**, no
  `requests` ni `subprocess`: son los que se sustituyen en los tests y los que dejan rastro.
- `ctx.attempt_dir`: carpeta del intento (logs, ficheros auxiliares). `ctx.log(texto)`: línea en `node.log`.
- `ctx.run_id`, `ctx.node_id`, `ctx.attempt`, `ctx.store`, `ctx.node_outputs(otro_nodo)`.

**El resultado (`Outcome`)**

- `Outcome(evento, salidas, error=..., cost_usd=..., artifacts=[...])`. El `evento` debe ser uno de los declarados
  (uno no declarado cuenta como fallo). Los fallos llevan `error=` con un mensaje que explique **qué hacer**, no
  solo qué falló.
- **Nodo que espera** (CI, otra persona): `Outcome(waiting="external")` en `run` y la lógica de comprobación en
  `poll(ctx)`, que devuelve `None` mientras siga esperando. Un `poll` que devuelve `None` no cuenta como
  progreso: el servidor sigue consultando, pero un test debe llamar a `drive` varias veces.
- **Vista previa** (obligatoria si es `external`): `async def preview(ctx)` devuelve lo que se verá en la
  aprobación, con `kind` `keyvalue`, `markdown` o `error`. Lo que se muestra debe ser **exactamente** lo que se
  va a publicar. Si falla (una clave que no existe en la configuración), quien aprueba se queda sin ver nada:
  pruébala (le pasó a `confluence.update_page`).

## 4. Idempotencia y seguridad (lo que más se rompe)

- **Repetir un nodo no debe duplicar**. Los patrones usados: un marcador `[gestplan run:<run> node:<nodo>]` dentro
  del texto publicado (comentarios, páginas), una etiqueta única `gp-<run>-<nodo>` en Jira, o consultar antes de
  crear (`gh pr list`, `git ls-remote`). Devuelve `already_done` con las mismas salidas y los mismos artefactos.
- **Todo texto de personas o de la red es un dato no confiable**: tickets, comentarios, páginas, logs de CI. Si
  llega a un agente, va en un bloque `data` (`ia_agent`), nunca mezclado con las instrucciones. Dilo en la
  `description` del nodo («Contenido NO CONFIABLE»).
- **Nada de secretos en las salidas.** Los valores de `settings.secret_values` se redactan de logs y auditoría,
  pero no lo uses como excusa para devolverlos.
- **No saltes los controles**: `merge_pr` no ignora la protección de rama; los mensajes de commit y PR pasan por
  `policy.py` (que quita la atribución a una IA).
- **Escapa lo que insertes** en formatos con marcado (`html.escape` en Confluence, ADF en Jira).

## 5. Artefactos

Declara en la definición los tipos que deja el nodo y devuélvelos en `Outcome.artifacts` con `Artifact(kind,
title, url, ref)`. `ref` es el identificador externo (clave, número, id) y evita duplicados al repetir. Un `kind`
no declarado se ignora con un aviso. Tipos actuales: `branch`, `pr`, `merge`, `issue`, `subtask`, `comment`,
`page`. Si añades uno nuevo, ponle etiqueta en `web/src/components/Artifacts.tsx` (`KIND_LABEL`) y, si hay datos
antiguos que lo contengan, una rama en `_derive` de `src/gestplan/artifacts.py` para `backfill-artifacts`.

## 6. Tests (no se entrega un nodo sin ellos)

Mira `tests/test_atlassian_more.py` (HTTP simulado con `FakeHttp`), `tests/test_github_more.py` (`gh` simulado con
`FakeRunner`/`ProcResult`) y `tests/test_search_map.py` (varios nodos encadenados). Ayudas en `tests/helpers.py`:
`make_engine`, `build`, `drive`, `Scripted`.

Cubre como mínimo:

1. **Camino feliz** con salidas y artefactos.
2. **Cada evento de fallo** (404, 400, sin permiso, entrada rara) con un mensaje claro.
3. **Idempotencia**: ejecutar el nodo dos veces con el mismo run no crea nada nuevo (`already_done`).
4. **Vista previa** de la aprobación (si es `external`): que se genera sin error y muestra lo que se publicará.
5. **El nodo dentro de un plan** (con `manual_approval` si es `external`), y la validación del plan.
6. Los casos límite de tu formato (vacío, lista larga, caracteres especiales).

Las llamadas reales a Jira, GitHub o Confluence **no** van en los tests automáticos. Se prueban a mano con un plan
de `plans/examples/` contra el sandbox (ver `DESIGN.md`), y cuando algo falla ahí, se añade un test que lo
reproduzca sin servicio.

## 7. Lista de comprobación

- [ ] Definición registrada (módulo en `_BUILTIN_MODULES` si es nuevo) y ejecutor en `default_executors()`.
- [ ] `side_effect` correcto; vista previa si es `external`.
- [ ] Todos los campos con `description`; `example` útil; sin remisiones a otros documentos.
- [ ] Eventos y salidas completos; los fallos llevan un `error` accionable.
- [ ] Idempotente; artefactos declarados e informados.
- [ ] Tests nuevos y `pytest -q` y `ruff check .` en verde.
- [ ] Un plan de ejemplo en `plans/examples/` si el nodo abre un caso de uso nuevo (y se valida:
  `gestplan validate plans/examples/<plan>.json`).
- [ ] Una línea en `DESIGN.md` (sección de la ronda de mejoras) y, si procede, en `README.md`.
- [ ] Si la interfaz muestra algo del nodo (un artefacto, un campo editable), comprobarlo en el navegador.

## 8. Trampas conocidas

- **Nunca confíes en que el catálogo se actualice solo con lo que no declaras**: el catálogo sale de las
  definiciones. Lo que no esté declarado en `events`, `ConfigModel`, `OutputsModel` o `artifacts` no existe para
  el validador ni para la IA que escribe planes.
- **`join`**: si tu nodo recibe de caminos alternativos, el plan debe poner `join: any`; con el `all` por defecto
  no arranca nunca. El validador avisa (`join_all_alternative_paths`) y los planes entregados no pueden tener
  ese aviso (hay un test).
- **Un nodo con dos orígenes en un bucle** (uno antes y otro después de un paso del propio bucle) se da por
  «saltado» antes de que llegue el segundo camino: sepáralo en dos nodos (`fix-pr-feedback` lo hacía mal).
- **Una consulta «sin resultados» no es «no existe»**: justo tras crear un PR, GitHub aún no ha registrado los
  checks. Si una lectura puede ir por detrás de una escritura, espera un margen antes de concluir.
- **Windows y rutas**: usa `pathlib`; los ficheros pueden tener finales de línea CRLF (los parches con
  `str.replace` deben normalizarlos); la salida de la consola puede no ser UTF-8.
- **Si eres una IA editando el código desde un shell**: los literales con `\n`, comillas o `\\` se corrompen con
  facilidad al pasarlos por heredocs. Escribe o edita los ficheros con las herramientas de edición, no con
  `cat <<EOF` ni `python - <<EOF`, y vuelve a ejecutar `ruff` justo después.
