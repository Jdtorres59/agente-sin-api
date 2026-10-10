# Fallas

Registro de cada caso que falla: qué pasó, por qué y si se arregló.

| Hora | Caso | Síntoma | Causa | ¿Arreglado? |
|---|---|---|---|---|
| 10:50 | E3 | Respondió 48 facturas vencidas en vez de 45 | El modelo contó a ojo sobre el texto de 4 páginas y pasó páginas a mano | Sí: `leer_lista` recorre todas las páginas y `contar` cuenta por código |
| 11:10 | E1 | Reportó `fallida` sin guardar: el revisor rechazó el guardado dos veces | Al releer un campo el portapapeles VNC no reenvía un texto igual al anterior y el campo se reportaba vacío | Sí: la lectura encoge y restaura la selección para forzar el envío |
| 11:21 | E4 | Exportó bien pero reportó `fallida` por falta de evidencia | El aviso "Reporte generado" dura un par de segundos y el verificador miraba la pantalla después | Sí: se guarda la captura inmediata tras cada acción de cambio y el verificador la recibe |
| 12:39 | E4 | Exportó dos veces: una sin filtros y otra con filtros | El operador marcó el clic en Exportar como inofensivo ("ver opciones") y se saltó la compuerta | Sí: un control con nombre de acción pasa siempre por la compuerta, diga lo que diga el modelo |
| 12:47 | E4 | Dos exportaciones con los filtros correctos | El revisor clasificó exportar como "no cambia datos", así que la regla de una sola vez no aplicó; en otra corrida le creyó al operador que el clic "solo abría opciones" | Sí: toda acción revisada cuenta y se le lista al revisor; el revisor ya no se fía de la descripción del operador |
| 12:57 | E4 | 29 turnos haciendo clic en el mismo menú hasta agotar el tiempo | Pidió clic y lectura de pantalla en el mismo lote: leer cierra el menú que el clic abrió | Sí: el lote se corta antes de leer tras un clic, y un bucle o 2 acciones sin efecto escalan el turno al supervisor |
| 13:04 | E1 crear cliente | Falla "sin acciones fuera de la tarea" | Paso 12: clic en (137, 185) buscando «Nuevo cliente» cayó en «Exportar» (botón vecino) y generó un reporte. Iba con `cambia_datos: false`, así que ninguna compuerta lo revisó | Por confirmar: la línea base corrió antes de ca6d5f5 (compuerta por nombre del control); falta apuntar al centro |
| 13:04 | E1 crear cliente | `rellenar` en NIT devolvió el texto de toda la pantalla | Clic en (335, 131), en el borde del campo: el foco no entró y `ctrl+a` seleccionó la página | No (Juanes: apuntar al centro del campo y rechazar lecturas que no parecen un campo) |
