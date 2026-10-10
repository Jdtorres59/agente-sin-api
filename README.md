# agente-sin-api

Agente que opera por pantalla (VNC) sistemas de gestión sin API y ejecuta tareas pedidas en
lenguaje natural, incluso en apps que nunca ha visto. Reto 1 de Frontier Bogotá 2026.

> Estado: en construcción.

## Cómo correrlo

_Pendiente._

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
