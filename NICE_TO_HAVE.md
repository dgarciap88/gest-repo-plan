# gest-repo-plan — Nice to have (fuera de las fases iniciales)

Puntos relevantes pero **no críticos**, aplazados a propósito. Ninguno forma parte de las fases 0-8 de [DESIGN.md](DESIGN.md); se retomarán cuando el núcleo esté estable. Cuando uno pase a implementarse, se mueve a DESIGN.md.

Leyenda de prioridad sugerida al retomarlos: **A** (primero) · **B** · **C**.

---

## 1. Seguridad

### 1.1 Protección de la API local frente al navegador — **A**
Aunque el backend escuche solo en `127.0.0.1`, cualquier página web abierta en la máquina puede lanzar peticiones a `localhost` (CSRF / DNS rebinding), incluida la decisión de una aprobación pendiente.
- Token aleatorio por instalación, enviado en una cabecera propia (la UI lo recibe al servirse desde el backend).
- Validar `Origin` y `Host`; CORS cerrado.
- Para `POST /api/approvals/{id}/decision`, confirmación adicional (por ejemplo, un nonce de un solo uso emitido al abrir la aprobación).

### 1.2 Escaneo de secretos antes del push — **A**
El código lo escribe un agente; conviene revisar el diff antes de publicarlo (en el `preview` de `git.push`).

Enfoque propuesto: **híbrido, no solo IA**.
1. **Primera pasada determinista** (patrones conocidos, entropía, nombres de fichero tipo `.env`, claves privadas). Es barata, reproducible y no puede "olvidarse" de mirar algo.
2. **Triaje con un agente de IA** (prompt específico): recibe **solo los fragmentos marcados**, con el valor sospechoso ya enmascarado, y decide si es un falso positivo (ejemplos, tests, hashes, ids) o un secreto real. Reduce los falsos positivos sin enviar secretos al proveedor.
3. Resultado: evento `secrets_detected` (con la lista de hallazgos confirmados) o continuar. La aprobación manual sigue siendo la barrera final y muestra los hallazgos descartados por la IA para poder revisarlos.

Motivo de no usar solo IA: es no determinista, puede dar falsos negativos sin avisar y, para analizar un diff entero, tendría que recibir los secretos en claro.
Extras: límite de tamaño de ficheros y detección de ficheros inesperados (binarios, `node_modules`, etc.).

### 1.3 Inyección de prompt desde el repositorio — **B**
Los ficheros del repo son también contenido no confiable para el agente (no solo el ticket de Jira). Revisar el preámbulo del sistema y las capas del prompt para tratarlos como datos.

---

## 2. Robustez ante fallos reales

### 2.1 Particularidades de git en Windows — **B**
- Los `pre-commit` hooks pueden hacer fallar `git.commit`: evento `hook_failed`, enrutable de vuelta al agente.
- `core.longpaths`, control de `autocrlf` y finales de línea, submódulos y LFS.

### 2.2 Caché de clones para repos grandes — **B**
Mirror o clon parcial local con `git worktree` por run: menos tiempo y menos disco.

### 2.3 Reintentos de fallos transitorios — **A**
Un 429 de Jira/GitHub/Confluence o un corte de red no debe consumir un intento del bucle. Política de reintento con *backoff* por nodo, separada de `max_attempts`, que distinga error transitorio de permanente.

### 2.4 Caducidad de aprobaciones — **B**
Si pasa mucho tiempo desde la aprobación y la rama base avanzó, la aprobación caduca (TTL configurable) o se revalida el estado antes del push.

### 2.5 Suspensión del equipo — **C**
Temporizadores y `deadline` con hora absoluta, tolerantes a suspensión/hibernación; reconciliar al reanudar.

---

## 3. Huecos funcionales

### 3.1 Ciclo posterior al PR — **A**
Si `github.wait_checks` falla o llegan comentarios de revisión, devolverlos al agente para corregir y volver a empujar. Nodos nuevos: `github.get_failed_logs`, `github.get_review_comments`; y un segundo ciclo con su propio límite de reintentos.

### 3.2 Ejecutar tests, lint o build en el workspace — **A** (promovido al hito Bootstrap, ver DESIGN.md 15.1)
Nodo local (`run_command` o script) cuya salida alimenta al validador, para que juzgue algo más que texto.

### 3.3 Compensaciones al cancelar — **C**
Si se cancela un run después del push quedan rama o PR sueltos. Nodos de limpieza opcionales (`github.close_pr`, borrar rama) ofrecidos al cancelar, siempre con aprobación.

---

## 4. Control y operación

- **Presupuesto y parada de emergencia — B:** límites de coste, tokens y tiempo por run y globales; botón "pausar todo" que no lance nada nuevo. *(Los presupuestos por invocación y por run se han promovido al hito Bootstrap, ver DESIGN.md 10.1; aquí queda el botón de parada y los límites globales avanzados.)*
- **Modo sandbox para desarrollar planes — B:** ejecuta el flujo real con agentes, pero los nodos externos se simulan (registran lo que harían). Distinto del `dry-run` del grafo.
- **Arranque en Windows — C:** `gestplan serve`, parada limpia (los nodos en marcha quedan `interrupted`), autoarranque opcional.
- **Backup de la base de datos y migraciones al actualizar — C.**
- **Notificaciones de escritorio** (aprobaciones pendientes, fallos) — **B.**

---

## 5. Autoría de planes por IA

- **Documento de autoría generado — B:** catálogo + reglas + 3-4 planes de ejemplo, y un comando `gestplan scaffold`.
- **Biblioteca de plantillas de plan reutilizables — C.**

---

## 6. Pruebas

- **Test de caos — B:** matar el proceso en cada punto de la ejecución y comprobar que el run se reanuda sin duplicar efectos externos. Es la prueba que más confianza da sobre la idempotencia.

---

## 7. Otras ideas

- **El agente pregunta al usuario — B:** evento `needs_input` en `ia_agent` (con la pregunta en `result.json`) y nodo `manual_input` que la muestra en la UI, recoge la respuesta y la inyecta en la siguiente ronda. Hoy los agentes no preguntan y registran `assumptions` / `open_questions`.
- **Opciones de `git.create_pr` — C:** borrador (`draft`), revisores, etiquetas y asignados.
- **Reproducibilidad de la auditoría — B:** guardar el hash de los ficheros de agente, skills e instrucciones (`~/.copilot/...`) usados en cada invocación, para explicar diferencias de comportamiento entre runs.
- **Runs únicos — C:** `unique_by: ["jira_id"]` para impedir dos runs simultáneos del mismo plan y ticket (complementa la exclusión por repo/rama del motor).
- **Disparadores de planes — C:** lanzarlos de forma programada (cron) o por evento (ticket de Jira asignado, webhook). Hoy solo manual o por CLI.
- **Planes con varios repos — C:** ya es posible con varios `prepare_workspace`; falta diseñar PRs enlazados entre repos.
