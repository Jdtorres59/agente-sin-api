# Plan de implementación: Operador prudente (Juanda + Juanes)

## Contexto

El systems design cerró a las 12:00 con dos diseños individuales casi compatibles. El de Juanes
("Operador prudente") va más adelante: **ya tiene código que pasa 3 de 5 ejemplos** (US$0,01-0,03 y
35-80 s por tarea, medido a las 11:15), un harness con `__reset → agente → __check` y hallazgos
medidos (teclas a 80 ms, portapapeles VNC para leer texto exacto, Haiku ubica controles con ≤ 1 px).
El de Juanda aporta la memoria con recetas versionadas, la repetición sin modelo, el escalamiento, el
deploy y la curva de aprendizaje que pidió el organizador.

**Decisión: no reescribir desde cero.** El código de Juanes es la base del núcleo; lo de Juanda entra
como módulos con contratos fijos. Reescribir costaría 2-3 h y el incidente es a las 15:30.
Condición: en la revisión de las 12:20, si su código no corre de punta a punta, se reevalúa.

`agent/vnc.py` es el cliente RFB que escribió Juanda en el spike de diseño (probado contra el
escritorio). Queda en el área de Juanes: puede usarlo, tomar de él la espera tolerante al cursor
(`changed_pixels`, `wait_stable`) o reemplazarlo por su driver.

Objetivo: entrypoint desplegado y validado antes de las 14:00, primera corrida del set oculto
~16:30, curva de aprendizaje medida y README con incidente a las 19:45.

## Arquitectura unificada (lo que se toma de cada uno)

Base: diagrama de Juanes. Flujo: API → Runner (270 s, topes) → Comprensión (TaskSpec) →
Operador ⇄ Driver RFB → Compuertas → Verificación (contexto limpio) → Reporte.

| Pieza | Viene de | Qué se toma |
|---|---|---|
| Driver RFB, `settle()`, clic 180 ms, teclas 80 ms, portapapeles | Juanes | Su driver queda. De `agent/vnc.py` (nuestro) se porta solo la tolerancia al cursor que parpadea si su `settle()` no la tiene |
| Comprensión → `TaskSpec` (tipo, referente, valores literales, fechas, riesgos) | Juanes (= nuestro Planificador) | Tal cual, Sonnet 5.5 |
| Operador: Haiku por paso; con receta la repite sin modelo | Ambos | Se suma nuestro **escalamiento a Sonnet tras 2 acciones sin efecto** |
| Compuertas: registro único, revisión pre-guardado, guardar una vez | Juanes | Tal cual |
| Verificación con contexto limpio que relee la app | Juanes (= nuestro Juez) | Tal cual |
| El modelo transcribe filas; el código cuenta | Juanes | Tal cual |
| Reporte: plantilla, línea `respuesta:` por código, filtro de secretos | Ambos | Tal cual |
| Memoria que aprende: SQLite, mapa, recetas con anclas visuales, estadísticas | Ambos | + versiones de receta, lecciones y huella de la app (nuestro) |
| Canario cada 20 min con huella de la app | Juanes | Lo implementa el módulo de memoria (comparte la huella) |
| Servicio FastAPI + Railway + volumen para SQLite | Nuestro | Nuevo |
| Curva de aprendizaje, app de juguete, perturbación por zoom | Ambos | Nuevo |

## Reparto de responsabilidades

Regla dura: **cada archivo tiene un solo dueño.** Nadie edita archivos del otro; si necesita un
cambio, lo pide por mensaje. Archivos compartidos solo con acuerdo de los dos.

### Juanes (+ su Claude Code): el núcleo del agente
Dueño de `agent/` excepto `agent/memory/` y `agent/contracts.py`, y de `evals/run.py`.

1. **12:00-12:30** Subir su código al repo con la estructura de abajo y exponer
   `run_task(...)` según el contrato. Dejar `evals/run.py` corriendo con un comando.
2. **12:30-13:30** Emitir la traza de pasos con el formato `Step` del contrato (incluye recorte
   de 48×48 px alrededor de cada clic como ancla y de qué campo del `TaskSpec` sale cada texto).
3. **13:30-15:00** Subir de 3/5 a 5/5 y a ≥ 70% en los casos retenidos: compuertas, homónimos,
   paginación, fechas relativas, tildes, falso éxito. Sumar el escalamiento tras 2 acciones sin
   efecto.
4. **15:00** Línea base de evals. **15:30** responde al incidente en el núcleo.
5. **15:30-18:30** Integrar el hook de memoria: antes de operar, `memory.find_recipe`; si hay,
   `replay`; si falla en el paso k, el operador sigue desde ahí. Bajar falsos éxitos a 0.
6. Anotar cada falla en `FALLAS.md` (solo agrega filas).

### Juanda (+ este Claude Code): servicio, memoria que aprende, medición y documentos
Dueño de `service/`, `agent/memory/`, `agent/contracts.py`, `evals/curve.py`, `evals/canary.py`,
`evals/targets.py`, `toyapp/`, `Dockerfile`, `railway.json`, `README.md`, `docs/`.

1. **12:00-12:30** Crear el repo público, escribir `CLAUDE.md` + `docs/PLAN.md` (este plan) y
   `agent/contracts.py` con los contratos. Revisar el código de Juanes (criterio: corre de punta a
   punta).
2. **12:30-13:45** `service/api.py` (GET /health, POST /tasks < 2 s, GET /tasks/{id}, token
   Bearer, deadline 270 s, una tarea asyncio por pedido) + `Dockerfile` + deploy en Railway con
   volumen `/data` + **VALIDAR ENTRYPOINT** en la plataforma con el núcleo tal como esté.
3. **13:45-15:15** `agent/memory/`: SQLite (`/data/memory.db`), huella de la app, recetas desde
   trazas verificadas (solo si Verificación dijo `terminada` y, en desarrollo, `__check` pasó),
   `replay()` con verificación de ancla por paso, lecciones y estadísticas. Pruebas propias.
4. **15:15** `evals/canary.py` listo (huella cada 20 min, alerta ruidosa y log con hora).
5. **15:30-16:30** README sección Incidente (hora, síntoma, causa, arreglo, evals antes/después).
6. **16:00-17:30** `evals/curve.py`: misma tarea-tipo con datos distintos, con y sin memoria →
   llamadas, costo y acierto por número de tarea. `evals/targets.py` para correr el harness por
   HTTP contra el entrypoint desplegado.
7. **17:30-18:30** Generalización: perturbación por zoom del navegador (ctrl+= por VNC) y, si hay
   tiempo, `toyapp/` (otra app con otra navegación) con un driver de Playwright que cumple el mismo
   contrato del driver.
8. **18:30-19:45** Prueba de jurado (clonar limpio y correr con el README), video de 2 min, entrega.

## Contratos (en `agent/contracts.py`, congelados a las 12:30)

```python
class Driver(Protocol):            # implementa Juanes
    async def connect(self, url: str, password: str) -> None: ...
    async def screenshot(self) -> Image.Image: ...
    async def click(self, x: int, y: int, double: bool = False) -> None: ...
    async def type_text(self, text: str) -> None: ...
    async def key(self, combo: str) -> None: ...
    async def scroll(self, x: int, y: int, direction: str, clicks: int = 3) -> None: ...
    async def settle(self) -> bool: ...
    async def close(self) -> None: ...

@dataclass
class Step:                        # lo emite el operador de Juanes
    kind: str                      # click | double_click | type | key | scroll
    x: int | None; y: int | None
    text: str | None               # texto literal escrito
    slot: str | None               # campo del TaskSpec de donde salió el texto (para parametrizar)
    key: str | None
    anchor_png: bytes | None       # recorte 48x48 alrededor del clic, antes de actuar
    screen_changed_px: int         # píxeles cambiados tras settle()
    model: str | None              # None si fue repetido sin modelo

@dataclass
class TaskResult:                  # lo devuelve run_task
    estado: str                    # terminada | fallida
    resumen: str
    steps: list[Step]
    cost_usd: float
    verified: bool                 # Verificación confirmó con contexto limpio

async def run_task(tarea: str, url: str, password: str, deadline: float,
                   memory: "Memory | None", trace_dir: Path) -> TaskResult: ...   # Juanes

class Memory(Protocol):            # implementa Juanda
    def fingerprint(self, img: Image.Image) -> str: ...
    def find_recipe(self, app_id: str, spec: dict) -> "Recipe | None": ...
    async def replay(self, recipe: "Recipe", spec: dict, driver: Driver) -> "ReplayResult": ...
    def learn(self, app_id: str, spec: dict, result: TaskResult) -> None: ...
    def notes(self, app_id: str) -> str: ...   # mapa + lecciones para el prompt
```

## Estructura del repo

```
agente-sin-api/
  CLAUDE.md            contexto para ambos Claude (dueños, contratos, reglas, horario)
  README.md            Juanda
  FALLAS.md            ambos (solo agregar filas)
  docs/                Juanda: DISENO.md, PLAN.md, arquitectura.png
  agent/               Juanes: comprension, operador, compuertas, verificacion, reporte, driver, trace
    contracts.py       Juanda (congelado 12:30)
    memory/            Juanda: store.py, fingerprint.py, recipes.py, replay.py
  service/             Juanda: api.py, runner.py
  evals/
    run.py             Juanes (harness principal)
    cases/             ambos agregan casos (un archivo por caso, sin editar los del otro)
    targets.py, curve.py, canary.py   Juanda
  toyapp/              Juanda (opcional)
  Dockerfile, railway.json, pyproject.toml (dependencias: agregar con rebase)
```

## Reglas para los dos (van en `CLAUDE.md` del repo)

- Ningún conocimiento de SIGECOM en `agent/` ni en prompts; chequeo en `evals/`.
- Secretos solo en `.env` / variables de Railway; el resumen nunca los repite.
- Ramas `juanes/<tema>` y `juanda/<tema>`; commits pequeños; `git pull --rebase` antes de
  subir; merge a `main` cuando corre `evals/run.py`.
- Cada falla va a `FALLAS.md` con caso, causa y si se arregló.
- Corridas del set oculto (3): ~16:30, ~18:45 y, solo si hace falta, antes de 19:45. Decide Juanda.
- 18:30 congelar funcionalidades. 19:00 cada uno le explica al otro su parte (debrief individual).

## Verificación

- 12:30: `evals/run.py` corre en el repo unificado y reproduce ≥ 3/5.
- 13:45: `curl` a /health y POST /tasks del entrypoint en Railway; botón VALIDAR ENTRYPOINT verde.
- 15:15: pruebas de memoria: una tarea repetida usa receta y baja llamadas al modelo; una receta con
  ancla rota vuelve al modelo y se versiona.
- 17:30: curva de aprendizaje con números reales (llamadas y costo en la tarea 1 vs 3 del mismo tipo).
- 18:30: prueba de jurado en carpeta limpia siguiendo solo el README.
