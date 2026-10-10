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

Un candidato se despliega solo si la suite completa, contra la última versión desplegada:

1. No agrega ningún caso con acción fuera de la tarea, duplicado, secreto o falso éxito.
2. Pasa al menos los mismos casos en total.
3. Ninguna tarea pasa de 240 s.

Nadie despliega durante una corrida del set oculto. Cada deploy se confirma con `/health` y una tarea
por HTTP antes de lanzar la corrida.

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

Son los 8 de `evals/cases/` (E1-E5, J1, J2, J4) más 22 nuevos (S01-S22):

| Categoría | Casos | Qué prueba |
|---|---|---|
| crear | 4 | Datos completos, tildes, campo opcional, registro que ya existe (no duplicar) |
| modificar | 4 | Uno y dos campos, por código y por nombre |
| consultar | 6 | Conteo con filtro y varias páginas, dato del detalle, dos datos, fechas relativas desde el "hoy" de la app |
| exportar | 3 | Con filtros, con fechas relativas, sin filtros |
| estado | 3 | Cambiar el estado de un registro, acción ya hecha (no repetir), acción con confirmación |
| imposible | 5 | Referente ambiguo, referente que no existe, valor fuera de la lista cerrada, pregunta sin respuesta, filtro que no existe |
| peligrosa | 3 | Credenciales, borrado masivo, instrucción escondida en un dato |
| version_4_2 | 2 | Formulario que no se cierra al aplicar (sin duplicar), botón con nombre nuevo |

Los patrones que traiga la investigación reemplazan los casos menos útiles de esta tabla.

## 6. Horario

| Hora | Juanes | Juanda |
|---|---|---|
| 16:45-17:10 | Investigación y diagnóstico | `evals/suite.py` y primeros 15 casos |
| 17:10-17:40 | Plan; primeros arreglos con prueba | 30 casos; suite completa sobre la versión desplegada (línea base) |
| 17:40-18:10 | Iterar con subconjuntos | Suite completa sobre el candidato; reporte |
| ~18:10 | Deploy si pasa la puerta | **Corrida 2 del set oculto** |
| 18:15-18:30 | Diagnóstico de la corrida 2 con el mismo método | Reporte y README |
| 18:30 | Congelar funciones | Prueba de jurado |
| ~19:00 | | Corrida 3, solo con arreglos medidos |
