# agente-sin-api

Agente que opera por pantalla (VNC) sistemas de gestión sin API y ejecuta tareas pedidas en
lenguaje natural, incluso en apps que nunca ha visto. Reto 1 de Frontier Bogotá 2026.

> Estado: en construcción.

## Cómo correrlo

Requisitos: Python 3.12 o más reciente y [uv](https://docs.astral.sh/uv/).

```bash
uv sync                      # instala dependencias (incluye las de desarrollo)
cp .env.example .env         # y llena los valores; .env nunca se sube
uv run python -m pytest tests -q
```

Variables de `.env` (ver `.env.example`):

| Variable | Para qué |
|---|---|
| `OPENROUTER_API_KEY` | Llamadas a los modelos |
| `AGENT_MODEL`, `SUPERVISOR_MODEL` | Operador por paso (`anthropic/claude-haiku-5.5`) y supervisor (`anthropic/claude-sonnet-5.5`) |
| `TEAM_TOKEN` | Token que exige el entrypoint en `Authorization: Bearer` |
| `DESKTOP_URL`, `DESKTOP_PASSWORD`, `DESKTOP_TOKEN` | Escritorio de práctica, solo para los harness locales |
| `MEMORY`, `MEMORY_DB` | Memoria que aprende: apagada salvo `MEMORY=on`; base SQLite |

Servicio local y una tarea de prueba:

```bash
uv run uvicorn service.api:app --port 8000
curl -s localhost:8000/health
curl -s -X POST localhost:8000/tasks -H "Authorization: Bearer $TEAM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tarea": "¿Cuántas facturas vencidas hay?", "escritorio": {"url": "wss://…/websockify", "password": "…"}}'
curl -s localhost:8000/tasks/<task_id> -H "Authorization: Bearer $TEAM_TOKEN"
```

Harness de evaluación (resetea el escritorio, corre el agente y pide `__check`):

```bash
uv run python -m evals.run                    # todos los casos de evals/cases/, en proceso
uv run python -m evals.run E1 --reps 3        # un caso, varias veces
uv run python -m evals.targets <URL> E2       # el mismo caso por HTTP contra el entrypoint desplegado
uv run python -m evals.curve --n 4            # curva de aprendizaje: sin memoria y con memoria
uv run python -m evals.canary --cada 20       # canario: avisa si la app cambió
```

Deploy (Railway): el `Dockerfile` construye la imagen y `railway.json` define el chequeo de
`/health`. Se monta un volumen en `/data` para las trazas y la memoria, y se configuran las mismas
variables de `.env` salvo las del escritorio de práctica.

## Contrato del entrypoint

| Ruta | Qué hace |
|---|---|
| `GET /health` | 200 cuando el agente está listo |
| `POST /tasks` | Recibe `{tarea, escritorio: {url, password}}` y responde `{"task_id": "…"}` en menos de 2 s |
| `GET /tasks/{task_id}` | `{"estado": "en_curso" \| "terminada" \| "fallida", "resumen": "…"}` |

Todas las llamadas llevan `Authorization: Bearer <TEAM_TOKEN>`.

## Arquitectura

Ver [docs/DISENO.md](docs/DISENO.md).

## Evaluación

_Pendiente: tasa de éxito, costo por caso y latencia sobre `evals/cases/`._

## Incidente

_Se llena cuando ocurra: hora de detección, síntoma, causa, arreglo y evals antes y después._

## Límites conocidos

Ver [FALLAS.md](FALLAS.md).
