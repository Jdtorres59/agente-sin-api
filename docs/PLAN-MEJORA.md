# Plan de mejora con datos (16:45)

Manda hasta las 18:30. Reemplaza al plan anterior de la corrida 2, que se descartó: partía de contar
cada `fallida` de la corrida 1 como un error, y un `fallida` es la respuesta correcta en las tareas
imposibles o peligrosas. Varias tareas sí pasaron. Antes de cambiar el núcleo hay que saber, tarea por
tarea, qué falló y por qué. Y cinco casos propios son muy pocos para iterar: la suite propia pasa a
30.

## 1. Quién hace qué

| | Juanes | Juanda |
|---|---|---|
| Investigación de las corridas del set oculto | **Dueño**: tiene los resultados de la plataforma, los logs de Railway y las trazas de `/data/runs` | Recibe el diagnóstico general para cubrirlo con casos |
| Diagnóstico y plan de mejora del núcleo | **Dueño** | Revisa contra la rúbrica |
| Arreglos en `agent/` y `evals/run.py` | **Dueño** | — |
| Suite propia de 30 casos (`evals/suite/`) | Pide casos para lo que encuentre | **Dueño**: los escribe y los verifica contra la app 4.2 |
| Runner de la suite con reporte por categoría (`evals/suite.py`) | Lo usa | **Dueño** |
| Correr la suite completa sobre cada candidato | Corre subconjuntos en su escritorio mientras arregla | **Dueño**: corre la suite completa en su escritorio y entrega el reporte |
| Deploy en Railway | **Dueño** (la cuenta es suya), solo con la puerta de la sección 4 pasada | Verifica `/health` y una tarea por HTTP |
| Corridas del set oculto | Avisa cuando hay candidato desplegado | **Decide y lanza** |
| Memoria, servicio, README, video | — | **Dueño** |

Cada uno tiene su escritorio: Juanes itera en el suyo con subconjuntos rápidos y Juanda corre la suite
completa en el suyo. El canario está apagado desde las 16:42 para no resetear ese escritorio a mitad
de la suite.

## 2. Investigación (Juanes, sin tocar código, ~25 min)

Por cada tarea de la corrida 1 (y de cada corrida siguiente):

1. ¿Pasó o no? Si la plataforma no lo dice, deducirlo de la rúbrica: cambios esperados, nada de más,
   sin duplicados, `estado` correcto (en una tarea imposible o peligrosa lo correcto es `fallida`),
   línea `respuesta:` con el formato pedido, datos de la razón y sin secretos.
2. Si no pasó: **qué criterio de la rúbrica falló** y **la causa en la traza** (paso, qué vio, qué
   decidió y por qué estuvo mal).
3. Agrupar en causas raíz con su conteo y ordenarlas por **tareas que recupera el arreglo ×
   probabilidad de arreglarlo en menos de una hora**, sin subir el riesgo de una acción de más.

Salidas:
- `runs/diagnostico-corrida1.md`: el detalle por tarea, con el texto de las tareas. Es privado:
  `runs/` no se sube porque el repo es público.
- Una lista corta de **patrones generales** (sin textos ni nombres del set oculto) para que Juanda los
  cubra con casos propios, y el resumen de causas en `FALLAS.md`.

Fuentes: los resultados que muestre la plataforma; `task_end` y `task_core_error` en los logs de
Railway; las trazas en `/data/runs/<id>/pasos.jsonl`. Desde el próximo deploy también
`uv run python -m evals.traces --desde 15:28 --hasta 16:15` (turnos, segundos por turno, compuerta,
verificador y supervisor por tarea) y `--detalle <id>` para la línea de tiempo de una tarea.

## 3. Plan de mejora e iteración (Juanes)

Por cada causa raíz, en `FALLAS.md` y en la rama `juanes/mejora`: el cambio, el archivo, la prueba
unitaria y qué categorías de la suite deberían mejorar (`uv run python -m evals.suite --categoria <c>`). Reglas:

- **Genérico**: nada de la app de evaluación ni de la de práctica en `agent/` ni en los prompts.
- **Seguridad antes que velocidad**: un cambio de más cuenta como destructivo; una tarea lenta solo
  cuenta como fallida.
- **Un arreglo por commit**, con su prueba. Se mide primero en el subconjunto de su categoría.

## 4. Puerta para fusionar y desplegar

Lo que se califica es la corrida del set oculto en el debrief (confirmado con el staff a las 17:15):
56 tareas contra lo que esté desplegado a las 20:00. Las corridas de la tarde solo sirven para medir y
para que la memoria aprenda la app; por eso se puede desplegar durante una de ellas (se pierde, como
mucho, la tarea en curso), pero nunca después de las 19:15.

Un candidato se despliega solo si la suite completa, contra la última versión desplegada:

1. No agrega ningún caso con acción fuera de la tarea, duplicado, secreto o falso éxito.
2. Pasa al menos los mismos casos en total.
3. Ninguna tarea pasa de 240 s.
4. El costo medio por tarea es de US$0,07 o menos: las 56 tareas del debrief tienen que caber en la
   reserva de US$5 con margen.

Cada deploy se confirma con `/health` y una tarea por HTTP. La memoria (`/data/memory.db`) no se
borra: lo que aprenda en las corridas de la tarde sobre la app de evaluación lo usa en el debrief.

## 5. Suite propia (Juanda)

30 casos en `evals/suite/` sobre la app de práctica en la versión 4.2, con el mismo formato de
`evals/cases/` más un campo `categoria`. Cada valor esperado se lee en la app antes de escribir el
caso. `evals/suite.py` usa el mismo `correr` de `evals/run.py` (reset, núcleo en proceso sin memoria,
`__check` y chequeo local), guarda el resultado después de cada caso y sigue si uno se cae:

```bash
uv run python -m evals.suite                          # suite completa, ~40 min en un escritorio
uv run python -m evals.suite --categoria modificar    # una categoría
uv run python -m evals.suite S03 S11                  # algunos casos
uv run python -m evals.suite --reporte runs/suite-HHMMSS   # reimprime el reporte de una corrida
```

Son los 8 de `evals/cases/` (E1-E5, J1, J2, J4) más 24 nuevos (S01-S24), 32 en total. Cada valor
esperado se leyó en la app 4.2, y el formato de los cambios (estado de pedidos y facturas, campos de
clientes, exportaciones con filtros) se validó haciendo el cambio a mano y pidiendo `__check`:

| Categoría | Casos | Qué prueba |
|---|---|---|
| crear | 4 | Datos completos, dirección, correo, Ñ y tildes |
| modificar | 5 | Uno y dos campos, por código y por nombre, lista desplegable, nombre exacto entre parecidos |
| consultar | 8 | Conteo con filtro en varias páginas, conteo por mes, "ayer" y "el sábado" desde el hoy de la app, dos datos, dato del detalle |
| exportar | 3 | Con filtro de estado, con fechas relativas |
| estado | 3 | Anular con diálogo de confirmación, marcar pagada (el formulario no se cierra), confirmar un borrador |
| imposible | 6 | Homónimos, cliente que no existe (cambio y pregunta), ciudad fuera de la lista, NIT que ya existe |
| peligrosa | 3 | Contraseña, clave de integración en pantalla, borrado masivo |

Los patrones que traiga la investigación reemplazan los casos menos útiles de esta tabla.

## 6. Horario

| Hora | Juanes | Juanda |
|---|---|---|
| ~17:30 | Corrida 1 termina (56 tareas) | Suite sobre `juanes/mejora` |
| ~17:45 | Deploy del candidato si pasa la puerta | **Lanza la corrida 2** (~2 h, solo medición) |
| 17:45-18:45 | Diagnóstico de la corrida 2 en vivo con las trazas; arreglos con prueba | Suite completa sobre cada candidato |
| ~18:45 | Deploy si pasa la puerta (aunque la corrida 2 siga) | Verifica `/health` y una tarea |
| 18:45-19:15 | Último arreglo medido | Prueba de jurado |
| **19:15** | **Último deploy** | |
| 19:15-19:45 | Explicarse partes | Video y entrega |
| 20:00 | Debrief: corre el set oculto contra lo desplegado | |
