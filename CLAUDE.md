# CLAUDE.md: contexto para los dos Claude Code del equipo

Este repo es el entregable del **Reto 1 de Frontier Bogotá 2026**: un agente que opera por pantalla
(VNC) apps de gestión sin API y ejecuta tareas en lenguaje natural, incluso en apps que nunca vio.
Equipo: **Juanda** (`Jdtorres59`) y **Juanes** (`Juanes-314`). Cada uno trabaja con su propio Claude
Code sobre este repo. Lee este archivo completo antes de tocar código.

**Desde las 16:45 manda [docs/PLAN-MEJORA.md](docs/PLAN-MEJORA.md)**: quién hace qué, investigación de las corridas del set oculto con datos, suite propia de 30 casos y puerta para desplegar. El plan original está en [docs/PLAN.md](docs/PLAN.md). El diseño, en
[docs/DISENO.md](docs/DISENO.md).

## Qué se evalúa (resumen de la rúbrica)

- **Set oculto (lo que más pesa):** el evaluador llama a nuestro entrypoint con tareas sobre **otra
  app** que el agente no ha visto. Una tarea es correcta solo si: la app quedó con los cambios
  esperados, sin cambios de más, sin duplicados, `estado` correcto (`fallida` en tareas imposibles,
  ambiguas o peligrosas), resumen con `respuesta: <valor>` en preguntas (número primero; dos datos
  separados por `;`) y sin secretos. 5 minutos por tarea; después de reportar no se toca el escritorio.
- **Incidente 15:30:** los organizadores cambian algo. Hay que detectarlo, adaptarse y explicarlo.
- **Repo:** que otra persona lo corra siguiendo el README.
- **Debrief individual:** cada uno explica el sistema completo, incluida la parte del otro.
- El organizador pidió **aprendizaje compuesto**: que el sistema use lo verificado para gastar menos y
  acertar más con cada tarea. Por eso existe `agent/memory/`.

## Arquitectura (Operador prudente)

API → Runner (reloj 270 s, topes de pasos y costo) → **Comprensión** (`TaskSpec`: tipo, referente,
valores literales, fechas relativas, riesgos) → **Operador** (con receta: la repite sin modelo; sin
receta: observar → decidir → actuar) ⇄ **Driver RFB** → **Compuertas** (registro único, revisión
pre-guardado, guardar una sola vez) → **Verificación** (contexto limpio, relee la app, decide
`terminada`/`fallida`) → **Reporte** (plantilla, línea `respuesta:` por código, filtro de secretos).
**Memoria que aprende** (SQLite: mapa de la app, recetas con anclas visuales y versiones, lecciones,
estadísticas) aprende solo de lo verificado. Principio: **el modelo percibe y decide; el código
garantiza.**

Modelos (OpenRouter): operador `anthropic/claude-haiku-5.5` en cada paso; supervisor
`anthropic/claude-sonnet-5.5` en Comprensión, revisión pre-guardado, Verificación y escalamiento
tras 2 acciones sin efecto. Tope duro US$0,25 por tarea. **El núcleo lee los modelos de las
variables `AGENT_MODEL` (operador) y `SUPERVISOR_MODEL` (supervisor)**; nunca los deja fijos en el
código, para poder cambiarlos en Railway y en el bake-off sin tocar código.

## Dueños de archivos (regla dura)

Cada archivo tiene **un solo dueño**. No edites archivos del otro: si necesitas un cambio, pídeselo a
tu humano para que lo coordine. Si tu tarea parece requerir tocar un archivo ajeno, detente y avisa.

| Ruta | Dueño | Qué es |
|---|---|---|
| `agent/` (todo menos lo de abajo) | **Juanes** | Comprensión, operador, compuertas, verificación, reporte, driver RFB, trazas, prompts |
| `evals/run.py` | **Juanes** | Harness principal: `__reset` → agente → `__check` + estado + resumen |
| `agent/contracts.py` | **Juanda** | Contratos entre módulos. **Congelado desde las 12:30**; cambios solo con acuerdo de los dos |
| `agent/memory/` | **Juanda** | Memoria que aprende: huella, recetas, replay, lecciones, estadísticas |
| `agent/recuperacion.py` | **Juanda** | Fallback: diario de cambios, diagnóstico y restauración. Los **ganchos** dentro de `agent/operador.py` los pone **Juanes** (ver docstring GANCHOS) |
| `service/` | **Juanda** | FastAPI: `/health`, `POST /tasks`, `GET /tasks/{id}`, runner |
| `evals/targets.py`, `evals/curve.py`, `evals/canary.py` | **Juanda** | Harness por HTTP, curva de aprendizaje, canario de cambios |
| `toyapp/` | **Juanda** | App de juguete para medir generalización (opcional) |
| `Dockerfile`, `railway.json` | **Juanda** | Deploy en Railway |
| `README.md`, `docs/` | **Juanda** | Documentación |
| `FALLAS.md` | Ambos | Solo agregar filas al final |
| `evals/cases/` | Ambos | Un archivo por caso; no edites casos del otro |
| `pyproject.toml`, `uv.lock` | Ambos | Agregar dependencias con `uv add` y `git pull --rebase` antes de subir |

## Contratos (ver `agent/contracts.py`)

- El núcleo expone `async def run_task(tarea, url, password, deadline, memory, trace_dir) -> TaskResult`
  en `agent/core.py`. `deadline` es `time.monotonic()` + 270 s; el núcleo debe terminar antes.
- El operador agrega un `Step` por acción a `TaskResult.steps`, con `anchor_png` (recorte 48×48
  alrededor del clic, **antes** de actuar), `slot` (de qué campo del `TaskSpec` salió el texto) y
  `screen_changed_px`. Sin eso la memoria no puede aprender. Desde las 13:30 `Step` tiene dos campos
  opcionales: `label` (el «objetivo» que dio el modelo, para el mapa de la app en las notas) y `gated`
  (la acción pasó por la compuerta de cambio; la memoria nunca la repite).
- Memoria: `from agent.memory import get_memory` (SQLite en `MEMORY_DB`; apagada salvo `MEMORY=on`, o `SQLiteMemory(ruta)` directo en evals). Solo
  aprende de resultados `terminada` y verificados; en evals, marca `verified=False` si `__check` no pasó
  antes de llamar `learn`. Las recetas solo navegan y llenan campos del `TaskSpec`: se detienen antes
  de cualquier clic que pueda guardar, y antes de editar campos de un registro existente.
- Hook de memoria en el núcleo: tras la primera captura, `app_id = memory.fingerprint(img)`;
  `recipe = memory.find_recipe(app_id, spec)`; si hay receta, `await memory.replay(recipe, spec, driver)`;
  si `ok` es falso, el operador sigue con el modelo desde el estado actual. `memory.notes(app_id)` se
  agrega al prompt del operador. Al final, el **servicio** llama `memory.learn(...)` (no el núcleo).
- `memory` puede ser `None`: el núcleo debe funcionar igual sin memoria.

## Comandos

```bash
uv sync                                  # instalar dependencias
cp .env.example .env                     # y llenar los valores (nunca subir .env)
uv run python -m evals.run               # harness principal (Juanes)
uv run uvicorn service.api:app --port 8000   # servicio local (Juanda)
uv run python -m evals.curve --n 4           # curva de aprendizaje: sin memoria vs con memoria (Juanda)
uv run python -m evals.canary --cada 20      # canario del incidente (Juanda; no correr junto con evals)
```

## Reglas

- **Cero conocimiento de la app de práctica (SIGECOM) en `agent/` y en los prompts.** Nada de nombres
  de menús, pantallas, coordenadas ni datos de esa app. El agente se evalúa en otra app.
- **Secretos** solo en `.env` o en variables de Railway. El resumen nunca repite la contraseña ni
  tokens. Nunca imprimas secretos en logs.
- **Git:** ramas `juanes/<tema>` y `juanda/<tema>`, commits pequeños con mensajes que expliquen,
  `git pull --rebase` antes de subir. A `main` solo lo que corre `evals/run.py` sin romper.
- **Fallas:** cada caso que falla va a `FALLAS.md` (hora, caso, síntoma, causa, ¿arreglado?). Es el
  guion del debrief.
- **No inventes cifras.** Todo número del README o del demo sale de una corrida real.
- El repo es **público** y solo contiene lo construido durante el evento.

## Horario

| Hora | Hito |
|---|---|
| 12:30 | Repo unificado; `evals/run.py` reproduce ≥ 3/5; contratos congelados |
| 13:45 | Entrypoint desplegado en Railway y validado en la plataforma |
| 15:00 | Línea base de evals (para comparar después del incidente) |
| 15:15 | Canario listo |
| 15:30 | **Incidente** |
| ~16:30 | Corrida 1 del set oculto (decide Juanda) |
| 17:30 | Curva de aprendizaje medida |
| 18:30 | **Congelar funcionalidades**; prueba de jurado |
| ~18:45 | Corrida 2 del set oculto |
| 19:00 | Cada uno le explica al otro su parte |
| 19:15 | Video de 2 minutos |
| 19:45 | Entrega |
