"""La guarda de borde: un clic pegado al borde de un control no se ejecuta."""

from PIL import Image, ImageDraw

from agent.escritorio import cerca_de_borde


def _dos_botones() -> Image.Image:
    """Dos botones vecinos de 22 px de alto: uno en x 14-130 y otro en x 136-218."""
    imagen = Image.new("RGB", (300, 80), (217, 221, 230))
    dibujo = ImageDraw.Draw(imagen)
    for izquierda, derecha in ((14, 130), (136, 218)):
        dibujo.rectangle((izquierda, 30, derecha, 52), fill=(240, 240, 240), outline=(120, 120, 120))
    return imagen


def test_point_next_to_a_control_border_is_flagged():
    imagen = _dos_botones()
    assert cerca_de_borde(imagen, 137, 41)  # 1 px dentro del segundo botón: el caso real de la falla
    assert cerca_de_borde(imagen, 133, 41)  # en el hueco entre los dos
    assert cerca_de_borde(imagen, 72, 32)  # pegado al borde superior del primero


def test_point_at_the_center_of_a_control_is_not_flagged():
    imagen = _dos_botones()
    assert not cerca_de_borde(imagen, 72, 41)
    assert not cerca_de_borde(imagen, 177, 41)


def test_text_strokes_and_text_caret_are_not_borders():
    imagen = Image.new("RGB", (200, 80), "white")
    dibujo = ImageDraw.Draw(imagen)
    for x in range(60, 140, 7):  # trazos verticales de 11 px, como letras
        dibujo.line((x, 35, x, 45), fill="black")
    dibujo.line((101, 32, 101, 48), fill="black")  # cursor de texto de 17 px
    assert not cerca_de_borde(imagen, 100, 40)


def test_gate_pattern_matches_action_verbs_but_not_lookalikes():
    from agent.operador import _PARECE_CAMBIO as patron

    for nombre in ("Guardar", "botón Exportar (ver opciones)", "Eliminar registro", "Sí", "Generar reporte", "Save"):
        assert patron.search(nombre), nombre
    for nombre in ("Aplicar filtro", "Datos generales", "Actualizar lista", "Envíos", "Importe total", "Buscar", "Nuevo registro"):
        assert not patron.search(nombre), nombre


def test_point_inside_a_bordered_control_is_recognised():
    from agent.escritorio import dentro_de_control

    imagen = _dos_botones()
    assert dentro_de_control(imagen, 72, 41)  # centro del primer botón
    assert dentro_de_control(imagen, 177, 41)  # centro del segundo
    assert not dentro_de_control(imagen, 260, 41)  # fondo liso a la derecha
    assert not dentro_de_control(imagen, 72, 70)  # debajo de los botones


def test_a_menu_bar_item_is_not_a_bordered_control():
    from agent.escritorio import dentro_de_control

    imagen = Image.new("RGB", (400, 100), (230, 233, 240))
    dibujo = ImageDraw.Draw(imagen)
    dibujo.rectangle((0, 0, 400, 30), fill=(31, 58, 104))  # barra de título
    dibujo.line((0, 60, 400, 60), fill=(150, 150, 150))  # línea bajo la barra de menús
    assert not dentro_de_control(imagen, 120, 45)  # hay borde arriba y abajo, pero no a los lados
