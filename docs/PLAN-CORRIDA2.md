# Plan para la corrida 2 del set oculto (16:15)

Objetivo: pasar de ~1 de 13 tareas bien en la corrida 1 a la mayoría en la corrida 2 (~17:45), sin
meter nada de la app de evaluación en el código. Este archivo manda hasta las 18:30. Si algo de aquí
choca con otro documento, gana este.

## 1. Diagnóstico de la corrida 1 (15:28-16:10, versión da361d7)

13 tareas, 1 `terminada` (mal) y 12 `fallida`. Sin errores de núcleo: el agente navega el tema nuevo y
cierra el aviso de la versión 4.2. Lo que falla es eficiencia, seguridad y dos supuestos que sacamos de
la app de práctica.

| Patrón | Tareas | Causa raíz | Tipo |
|---|---|---|---|
| Se queda sin tiempo o sin turnos | 3, 6, 7, 10 | 36-46 pasos a ~6 s por paso. 270 s alcanzan para ~40 pasos sin margen. Pasos gastados en explorar, releer y dudar | Eficiencia |
| Se rinde pronto | 11 | "No encontré cómo": no agotó menús ni búsqueda antes de `fallida` | Estrategia |
| Acción en dos pasos | 9, 12 | La app pide el motivo en un diálogo DESPUÉS de pulsar la acción; nuestra regla exige tener el dato antes de pulsar | Sobreajuste a la práctica |
| Selector que no abre o no se lee | 5 | Las opciones de una lista desplegable solo se ven en la captura con la lista abierta | Herramienta faltante |
| Acción sin compuerta | 7 | La compuerta reconoce acciones por una lista fija de verbos sacada de la práctica; «facturar», «agendar», «anular», «marcar» no estaban | Sobreajuste a la práctica |
| Cambio no pedido | 12 | Escribió texto en un campo que la tarea no mencionaba | Seguridad |
| Cambio a medias | 8 | Hizo el primer cambio y no el segundo antes de rendirse | Eficiencia y seguridad |
| Secreto en el resumen | 2 | Copió una clave leída en pantalla | Seguridad (ya arreglado) |
| Conexión VNC caída | E2 local | El escritorio se reinició y no reconectamos | Robustez |

Lo que la app 4.2 cambió (lo vimos con el canario a las 16:03 en el escritorio de práctica):
aviso al abrir; **los formularios se aplican sin cerrarse**; la barra de acciones pasó a la derecha;
nombres nuevos (Guardar → Aplicar, Volver → Cerrar, Buscar → Filtrar, Nuevo → Crear). Que el formulario
no se cierre al aplicar es una trampa de duplicados: el agente puede creer que no guardó y repetir.

## 2. Principios para cada arreglo

1. **Nada de la app de evaluación en `agent/`** (ni nombres, ni menús, ni dominio). Cada arreglo debe
   servir en cualquier app de gestión. El test de vocabulario se amplía si hace falta.
2. **Cada arreglo se mide** en el escritorio de práctica, que ya tiene el tema 4.2, antes de fusionar:
   `uv run python -m evals.run` (E1-E5, J1 y los casos nuevos J2-J4).
3. **Seguridad antes que velocidad**: un cambio de más cuenta como destructivo; una tarea lenta solo
   cuenta como fallida.
4. **Las trazas y las tareas del set oculto no van al repo** (es público). En el repo solo la causa
   general.

## 3. Juanes: núcleo (`agent/` salvo memoria, contratos y recuperación)

En este orden. Cada punto con su prueba y su medida.

**J-0 · Fusionar `juanes/incidente` y desplegar apenas termine la corrida 1.** Trae «Aplicar» en la
compuerta, credenciales → `fallida`, regla del aviso emergente, plantilla estable en `leer_lista` y
`leer_opciones`. Producción hoy no reconoce «Aplicar»: cada tarea de la app nueva corre ese riesgo.
Medida: E1-E5 + J1 en el tema 4.2.

**J-1 · Compuerta sin lista cerrada** (seguridad, tareas 7 y 12).
- La comprobación del punto (Haiku, ya existe) devuelve además `ejecuta_accion: sí/no` para el control
  bajo el punto. Si dice sí, pasa por la compuerta aunque el nombre no esté en la lista.
- Antes de aplicar, el código compara los campos editados (diario de `agent/recuperacion.py`) contra
  el `TaskSpec`: un campo cuyo valor no sale de `valores` ni del referente bloquea la acción con el
  mensaje "campo no pedido: «X»". Determinista, sin modelo.
- Formularios que no se cierran: después de aplicar, la confirmación es releer el campo o ver el aviso;
  nunca volver a aplicar. La compuerta ya lista los cambios hechos: el revisor rechaza un segundo
  aplicar sobre el mismo registro.
- Prueba: un control con nombre desconocido que ejecuta una acción pasa por la compuerta; un campo no
  pedido bloquea.

**J-2 · Acciones en dos pasos** (tareas 9 y 12).
- Si el dato que pide la tarea (motivo, fecha, observación) no tiene campo en la pantalla actual, el
  operador puede pulsar el botón de la acción para abrir su diálogo. Ese primer clic pasa por la
  compuerta como siempre.
- La confirmación del diálogo cuenta como **la misma** acción (no como un segundo cambio).
- Si al pulsar no apareció diálogo y el cambio ya se hizo, el diario lo registra como cambio confirmado.
- Prueba: secuencia acción → diálogo → motivo → confirmar cuenta un solo cambio.

**J-3 · Velocidad** (tareas 3, 6, 7, 10). Medido hoy: ~6 s por turno (≈4 s modelo, ≈2 s comprobación
del punto) y 4-10 s por `rellenar`.
- **Plan de subobjetivos**: Comprensión (Sonnet, misma llamada) devuelve además `plan`, 3 a 7
  subobjetivos genéricos ("ubicar el registro por su referente", "abrir su detalle", "cambiar X",
  "aplicar", "verificar"). El operador lo recibe y marca en qué subobjetivo va. Evita explorar a ciegas.
- **Más acciones por turno**: en un formulario, todos los campos en un solo turno; clic de navegación +
  clic siguiente cuando no hace falta ver la pantalla intermedia.
- **Tope de turnos 40 → 60**; el reloj sigue mandando (reserva de 30 s).
- **Probar `reasoning` del operador**: "low" contra sin razonamiento en E1-E5 + J1. Si la tasa no baja
  y el tiempo sí, se queda el más rápido.
- Medida: tiempo medio y máximo de la suite, y pasos por tarea. Meta: ninguna tarea por encima de 150 s.

**J-4 · No rendirse sin buscar** (tarea 11).
- Antes de `fallida` por "no encontré", el operador debe haber probado: buscar/filtrar por el
  referente, y abrir cada menú de primer nivel una vez. El verificador rechaza una `fallida` por
  "no encontré" si no ve ese intento en el registro y quedan más de 60 s.

**J-5 · Reconectar VNC** (E2 local).
- Si la conexión se cae (`ConnectionClosedError`), reconectar hasta 3 veces con espera de 2 s y seguir
  desde la captura actual. Prueba con un cliente falso que corta una vez.

## 4. Juanda + Claude: memoria, medición, documentos

**D-1 · Memoria con mapa de la app** (`agent/memory/`).
- Hoy solo aprende de tareas verificadas, y en la corrida 1 casi ninguna lo fue: la memoria no ayudó.
- Nuevo: de **toda** tarea (verificada o no) guarda los controles que cambiaron la pantalla, con su
  etiqueta y posición, por app. Las notas del operador incluyen "controles vistos en esta app". La
  corrida 2 usa lo que exploró la corrida 1 en la misma app; el operador confirma en la captura.
- Las recetas siguen saliendo solo de lo verificado.

**D-2 · `GET /traces`** (hecho, `ee64167`): leer las trazas y capturas de producción con el token.
Entra en el deploy de J-0. **D-3 · `evals/traces.py`**: resume cada tarea de una corrida (turnos por
herramienta, segundos por turno, rechazos del verificador, consejos del supervisor, motivo final).

**D-4 · Casos nuevos en el tema 4.2** que imitan los patrones que fallaron, sin datos de la app de
evaluación: J2 pregunta de dos saltos, J3 acción con diálogo (si la práctica la tiene), J4 pedido de una
credencial (debe fallar sin el valor).

**D-5 · Canario** (hecho): detectó el tema 4.2 a las 16:03; bases nuevas guardadas, las viejas en
`runs/canary/v3.9/`. **D-6 · README**: sección Incidente con antes y después; guion del video.

## 5. Horario

| Hora | Juanes | Juanda + Claude |
|---|---|---|
| ~16:15 | Corrida 1 termina → J-0: fusionar, desplegar, E1+E3 por HTTP | Análisis de trazas con `/traces` |
| 16:15-17:15 | J-1, J-2 (rama `juanes/corrida2`) | D-1, D-3, D-4 |
| 17:15-17:35 | J-3, J-4, J-5 lo que alcance; suite completa en tema 4.2 | Suite con memoria; README Incidente |
| 17:35 | Fusionar lo medido y desplegar | Verificar `/health` y una tarea por HTTP |
| ~17:45 | | **Juanda lanza corrida 2** |
| 18:15-18:30 | Arreglos de lo que muestre la corrida 2 | Análisis con `/traces` |
| 18:30 | Congelar funciones | Prueba de jurado |
| ~19:00 | | Corrida 3 (final, solo si hay arreglos medidos) |
| 19:15-19:45 | Explicarse partes | Video y entrega |

Reglas de deploy: nadie despliega durante una corrida del set oculto; cada deploy se confirma con
`/health` (commit, token, memoria) y una tarea por HTTP antes de lanzar una corrida.
