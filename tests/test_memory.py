"""Tests for the learning memory: fingerprint, safe recipes, replay with anchors and versioning.

Screens are drawn with PIL, so the tests do not depend on the practice app or on captures.
"""

import asyncio

from PIL import Image, ImageDraw

from agent.contracts import Recipe, Step, TaskResult
from agent.memory.fingerprint import locate_anchor, png_bytes
from agent.memory.recipes import safe_prefix
from agent.memory.store import SQLiteMemory

BACKGROUND = (220, 224, 230)


def screen(buttons: dict[str, tuple[int, int]], title: str = "Gestor Uno", bar=(30, 58, 95)) -> Image.Image:
    img = Image.new("RGB", (1280, 800), BACKGROUND)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 1280, 34), fill=bar)
    draw.text((12, 10), title, fill="white")
    for label, (x, y) in buttons.items():
        draw.rectangle((x - 40, y - 12, x + 40, y + 12), fill="white", outline="black")
        draw.text((x - 34, y - 6), label, fill="black")
    return img


def anchor(img: Image.Image, x: int, y: int) -> bytes:
    return png_bytes(img.crop((x - 24, y - 24, x + 24, y + 24)))


HOME = screen({"Registros": (100, 60), "Ayuda": (220, 60)})
LIST = screen({"Registros": (100, 60), "Ayuda": (220, 60), "Nuevo": (80, 120), "Exportar": (200, 120)})
FORM = screen({"Registros": (100, 60), "Ayuda": (220, 60), "Nombre": (400, 200), "Ciudad": (400, 240), "Guardar": (400, 300)})
SPEC = {"tipo": "crear", "entidad": "proveedor", "referente": "", "valores": {"Nombre": "Acme", "Ciudad": "Pereira"}, "riesgos": []}


def creation_trace(spec=SPEC) -> list[Step]:
    """What the operator emits for: menu -> New -> fill two fields -> Save."""
    v = spec["valores"]
    return [
        Step("click", 100, 60, anchor_png=anchor(HOME, 100, 60), screen_changed_px=9000, label="menú Registros"),
        Step("click", 80, 120, anchor_png=anchor(LIST, 80, 120), screen_changed_px=9000, label="botón Nuevo"),
        Step("click", 400, 200, anchor_png=anchor(FORM, 400, 200), screen_changed_px=300),
        Step("key", key="ctrl+a"),
        Step("type", text=v["Nombre"], slot="Nombre", screen_changed_px=300),
        Step("click", 400, 240, anchor_png=anchor(FORM, 400, 240), screen_changed_px=300),
        Step("key", key="ctrl+a"),
        Step("type", text=v["Ciudad"], slot="Ciudad", screen_changed_px=300),
        Step("click", 400, 300, anchor_png=anchor(FORM, 400, 300), screen_changed_px=5000, label="botón Guardar"),
    ]


class FakeDriver:
    """Clicking a button center moves to the next screen; records every action."""

    def __init__(self, start: Image.Image, transitions: dict[tuple[int, int], Image.Image]):
        self.current = start
        self.transitions = transitions
        self.actions: list[str] = []

    async def screenshot(self):
        return self.current

    async def settle(self):
        return True

    async def click(self, x, y, double=False):
        self.actions.append(f"click {x},{y}")
        for (cx, cy), nxt in self.transitions.items():
            if abs(cx - x) <= 10 and abs(cy - y) <= 10:
                self.current = nxt

    async def key(self, combo):
        self.actions.append(f"key {combo}")

    async def type_text(self, text):
        self.actions.append(f"type {text}")

    async def scroll(self, x, y, direction, clicks=3):
        self.actions.append(f"scroll {direction}")


def app_driver(start=HOME) -> FakeDriver:
    return FakeDriver(start, {(100, 60): LIST, (80, 120): FORM})


def memory(tmp_path) -> SQLiteMemory:
    return SQLiteMemory(tmp_path / "memory.db")


def test_fingerprint_same_app_other_page_and_other_app(tmp_path):
    m = memory(tmp_path)
    a = m.fingerprint(HOME)
    assert m.fingerprint(HOME) == a
    assert m.fingerprint(LIST) == a  # same title bar: same app on another page
    other = screen({"Inicio": (600, 400)}, title="Otro Sistema", bar=(120, 20, 20))
    assert m.fingerprint(other) != a


def test_first_screen_drift_is_recorded_as_lesson(tmp_path):
    m = memory(tmp_path)
    a = m.fingerprint(HOME)
    changed = HOME.copy()  # a notice banner appears on the same first screen
    ImageDraw.Draw(changed).rectangle((300, 300, 980, 360), fill=(250, 230, 120))
    assert m.fingerprint(changed) == a
    assert "pantalla inicial cambió" in m.notes(a)


def test_anchor_tolerates_small_shift_and_rejects_neighbor():
    a = anchor(LIST, 80, 120)
    shifted = screen({"Registros": (100, 60), "Ayuda": (220, 60), "Nuevo": (82, 121), "Exportar": (200, 120)})
    found = locate_anchor(shifted, a, 80, 120)
    assert found is not None and (found[0], found[1]) == (82, 121)
    swapped = screen({"Registros": (100, 60), "Ayuda": (220, 60), "Exportar": (80, 120), "Nuevo": (200, 120)})
    assert locate_anchor(swapped, a, 80, 120) is None


def test_safe_prefix_stops_before_save():
    steps = safe_prefix(creation_trace(), SPEC)
    assert [s.kind for s in steps] == ["click", "click", "click", "key", "type", "click", "key", "type"]
    assert steps[4].slot == "nombre" and steps[7].slot == "ciudad"


def test_safe_prefix_rules():
    trace = creation_trace()
    # Text that is not a TaskSpec value was specific to that task: stop there.
    odd = trace[:4] + [Step("type", text="algo inventado")] + trace[5:]
    assert len(safe_prefix(odd, SPEC)) == 2
    # Enter can submit a form.
    assert len(safe_prefix(trace[:2] + [Step("key", key="enter")] + trace[2:], SPEC)) == 2
    # A step that went through the change gate is never replayed.
    gated = trace[:1] + [Step("click", 80, 120, anchor_png=trace[1].anchor_png, gated=True)] + trace[2:]
    assert len(safe_prefix(gated, SPEC)) == 1
    # Delete tasks get no recipe.
    assert safe_prefix(trace, {**SPEC, "tipo": "eliminar"}) == []


def test_modification_only_replays_the_search_by_referent():
    spec = {"tipo": "modificar", "entidad": "proveedor", "referente": "Acme", "valores": {"Ciudad": "Cali"}}
    trace = [
        Step("click", 100, 60, anchor_png=anchor(HOME, 100, 60), screen_changed_px=9000),
        Step("click", 300, 120, anchor_png=anchor(LIST, 300, 120), screen_changed_px=100),
        Step("key", key="ctrl+a"),
        Step("type", text="Acme", screen_changed_px=100),
        Step("click", 400, 240, anchor_png=anchor(FORM, 400, 240), screen_changed_px=300),
        Step("key", key="ctrl+a"),
        Step("type", text="Cali", slot="Ciudad", screen_changed_px=300),
    ]
    steps = safe_prefix(trace, spec)
    assert len(steps) == 4 and steps[-1].slot == "referente"


def test_replay_uses_new_values_and_never_saves(tmp_path):
    m = memory(tmp_path)
    recipe = Recipe("app-x", "crear:proveedor", 1, safe_prefix(creation_trace(), SPEC))
    new_spec = {**SPEC, "valores": {"nombre": "Beta SAS", "ciudad": "Cúcuta"}}
    driver = app_driver()
    result = asyncio.run(m.replay(recipe, new_spec, driver))
    assert result.ok and result.steps_done == 8
    assert "type Beta SAS" in driver.actions and "type Cúcuta" in driver.actions
    assert "click 400,300" not in driver.actions  # the save button stays for the operator's gate


def test_replay_stops_when_screen_does_not_match(tmp_path):
    m = memory(tmp_path)
    recipe = Recipe("app-x", "crear:proveedor", 1, safe_prefix(creation_trace(), SPEC))
    other = screen({"Inicio": (600, 400)}, title="Otro Sistema", bar=(120, 20, 20))
    driver = FakeDriver(other, {})
    result = asyncio.run(m.replay(recipe, SPEC, driver))
    assert not result.ok and result.steps_done == 0 and driver.actions == []


def test_replay_stops_when_slot_is_missing(tmp_path):
    m = memory(tmp_path)
    recipe = Recipe("app-x", "crear:proveedor", 1, safe_prefix(creation_trace(), SPEC))
    driver = app_driver()
    result = asyncio.run(m.replay(recipe, {**SPEC, "valores": {"nombre": "Beta"}}, driver))
    assert not result.ok and result.steps_done == 5 and "ciudad" in result.reason


def verified(steps, estado="terminada", ok=True, spec=SPEC) -> TaskResult:
    return TaskResult(estado, "hecho", steps=steps, verified=ok, spec=spec)


def test_learns_only_from_verified_results_and_versions_on_mismatch(tmp_path):
    m = memory(tmp_path)
    app = m.fingerprint(HOME)
    m.learn(app, SPEC, verified(creation_trace(), ok=False))
    assert m.find_recipe(app, SPEC) is None  # not verified: nothing learned
    m.learn(app, SPEC, verified(creation_trace()))
    recipe = m.find_recipe(app, SPEC)
    assert recipe is not None and recipe.version == 1

    # The interface changed: the replay stops at step 0, the operator does it all, a new version is learned.
    spec2 = {**SPEC, "valores": {"Nombre": "Gamma", "Ciudad": "Neiva"}}
    other = screen({"Inicio": (600, 400)}, title="Gestor Uno")
    assert not asyncio.run(m.replay(recipe, spec2, FakeDriver(other, {}))).ok
    m.learn(app, spec2, verified(creation_trace(spec2), spec=spec2))
    again = m.find_recipe(app, spec2)
    assert again.version == 2
    assert "se detuvo" in m.notes(app)


def test_successful_replay_counts_and_full_trace_is_kept(tmp_path):
    m = memory(tmp_path)
    app = m.fingerprint(HOME)
    m.learn(app, SPEC, verified(creation_trace()))
    spec2 = {**SPEC, "valores": {"Nombre": "Delta", "Ciudad": "Tunja"}}
    recipe = m.find_recipe(app, spec2)
    assert asyncio.run(m.replay(recipe, spec2, app_driver())).ok
    save = [Step("click", 400, 300, anchor_png=anchor(FORM, 400, 300), screen_changed_px=5000)]
    m.learn(app, spec2, verified(save, spec=spec2))
    assert m.find_recipe(app, spec2).successes == 2
    last = m.episodes(app)[-1]
    assert last["replayed_steps"] == 8 and last["model_steps"] == 1 and last["replay_ok"] == 1


def test_failing_recipe_is_retired_and_risky_tasks_get_none(tmp_path):
    m = memory(tmp_path)
    app = m.fingerprint(HOME)
    m.learn(app, SPEC, verified(creation_trace()))
    assert m.find_recipe(app, {**SPEC, "riesgos": ["no queda claro el registro"]}) is None
    other = FakeDriver(screen({"Inicio": (600, 400)}), {})
    for _ in range(3):
        recipe = m.find_recipe(app, SPEC)
        if recipe is None:
            break
        asyncio.run(m.replay(recipe, SPEC, other))
        m.learn(app, SPEC, verified([], estado="fallida", ok=False))
    assert m.find_recipe(app, SPEC) is None


def test_notes_give_route_and_field_map(tmp_path):
    m = memory(tmp_path)
    app = m.fingerprint(HOME)
    assert m.notes(app) == ""
    m.learn(app, SPEC, verified(creation_trace()))
    notes = m.notes(app)
    assert "menú Registros → botón Nuevo" in notes
    assert "«Nombre» en (400, 200)" in notes and "1 de 1" in notes


def test_lone_search_fill_before_a_click_is_left_to_the_operator():
    spec = {"tipo": "crear", "entidad": "proveedor", "valores": {"NIT": "900111222", "Nombre": "Acme"}}
    trace = [
        Step("click", 100, 60, anchor_png=anchor(HOME, 100, 60), screen_changed_px=9000, label="menú Registros"),
        Step("click", 300, 120, anchor_png=anchor(LIST, 300, 120), screen_changed_px=100),
        Step("key", key="ctrl+a"),
        Step("type", text="900111222", slot="NIT", screen_changed_px=100),
        Step("click", 360, 120, anchor_png=anchor(LIST, 360, 120), screen_changed_px=5000, label="Buscar"),
    ]
    steps = safe_prefix(trace, spec)
    assert [s.kind for s in steps] == ["click"]


def test_replay_reports_what_it_did(tmp_path):
    m = memory(tmp_path)
    recipe = Recipe("app-x", "crear:proveedor", 1, safe_prefix(creation_trace(), SPEC))
    result = asyncio.run(m.replay(recipe, SPEC, app_driver()))
    assert "clic en «menú Registros»" in result.reason and "escribió el dato «ciudad»" in result.reason


def test_verifier_feedback_becomes_a_lesson_for_that_task_kind(tmp_path):
    m = memory(tmp_path)
    app = m.fingerprint(HOME)
    result = verified(creation_trace())
    result.feedback = ["comprobar en la lista que exista un solo registro con ese código"]
    m.learn(app, SPEC, result)
    assert "la verificación pidió: comprobar en la lista" in m.notes(app)
