"""SQLite memory per app: fingerprints, versioned recipes, field map, lessons and episodes.

It learns only from results that the clean-context verification confirmed (estado "terminada" and
verified). Everything else only feeds the statistics. Tables:

- apps:      fingerprint thumbnails of each app seen; drift of the first screen is logged loudly.
- recipes:   (app, tipo, entidad, version) -> safe steps, successes, failures. A recipe that stops
             matching gets a new version from the next verified trace; one that fails more than it
             succeeds is no longer offered.
- fields:    where each TaskSpec field was filled, to give the operator a map in its prompt.
- lessons:   short facts learned the hard way: the interface changed at step k, or what the verifier
             demanded before accepting a task of this kind (so the operator does it the first time).
- episodes:  one row per task: cost, model steps, replayed steps. The learning curve comes from here.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

from agent.contracts import Recipe, ReplayResult, Step, TaskResult

from . import fingerprint as fp
from .recipes import dump_steps, group_actions, load_steps, normalize, safe_prefix, task_key
from .replay import replay as run_replay

SCHEMA = """
CREATE TABLE IF NOT EXISTS apps (app_id TEXT PRIMARY KEY, full BLOB, band BLOB, first_seen TEXT, last_seen TEXT, seen INTEGER);
CREATE TABLE IF NOT EXISTS recipes (app_id TEXT, tipo TEXT, entidad TEXT, version INTEGER, steps TEXT,
    successes INTEGER DEFAULT 0, failures INTEGER DEFAULT 0, created TEXT, PRIMARY KEY (app_id, tipo, entidad, version));
CREATE TABLE IF NOT EXISTS fields (app_id TEXT, entidad TEXT, slot TEXT, x INTEGER, y INTEGER, seen INTEGER, last TEXT,
    PRIMARY KEY (app_id, entidad, slot));
CREATE TABLE IF NOT EXISTS lessons (id INTEGER PRIMARY KEY, ts TEXT, app_id TEXT, text TEXT, scope TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS episodes (id INTEGER PRIMARY KEY, ts TEXT, app_id TEXT, tipo TEXT, entidad TEXT, estado TEXT,
    verified INTEGER, cost REAL, model_steps INTEGER, replayed_steps INTEGER, recipe_version INTEGER, replay_ok INTEGER);
"""
MAX_NOTE_LINES = 8


@dataclass
class _Replayed:
    spec_json: str
    recipe: Recipe
    result: ReplayResult
    executed: list[Step]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(event: str, **fields) -> None:
    print(json.dumps({"event": event, "ts": _now(), **fields}, ensure_ascii=False), flush=True)


class SQLiteMemory:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.db.commit()
        self._replays: dict[int, _Replayed] = {}  # keyed by id(spec): the core passes the same dict to learn

    # --- who is this app -------------------------------------------------------------------

    def fingerprint(self, img: Image.Image) -> str:
        full, band = fp.thumbnails(img)
        structured = fp.has_structure(band)
        best: tuple[float, str, bytes] | None = None
        for app_id, known_full, known_band in self.db.execute("SELECT app_id, full, band FROM apps"):
            d_full = fp.distance(full, known_full)
            same = fp.distance(band, known_band) <= fp.SAME_APP_BAND if structured else d_full <= fp.SAME_APP_FULL
            if same and (best is None or d_full < best[0]):
                best = (d_full, app_id, known_full)
        if best is not None:
            d_full, app_id, _ = best
            if fp.DRIFT < d_full <= fp.SAME_APP_FULL:  # more than that is just another start page
                # Same app, different first screen: the loud signal the incident detector looks for.
                text = f"La pantalla inicial cambió un {d_full:.1%} respecto a la conocida ({_now()[11:16]})."
                self._lesson(app_id, text)
                _log("app_changed", app_id=app_id, changed=round(d_full, 4))
                self.db.execute("UPDATE apps SET full = ?, band = ? WHERE app_id = ?", (full, band, app_id))
            self.db.execute("UPDATE apps SET last_seen = ?, seen = seen + 1 WHERE app_id = ?", (_now(), app_id))
            self.db.commit()
            return app_id
        app_id = "app-" + hashlib.sha1(full).hexdigest()[:8]
        self.db.execute("INSERT OR REPLACE INTO apps VALUES (?, ?, ?, ?, ?, 1)", (app_id, full, band, _now(), _now()))
        self.db.commit()
        _log("app_new", app_id=app_id)
        return app_id

    # --- recipes -----------------------------------------------------------------------------

    def _latest(self, app_id: str, tipo: str, entidad: str):
        return self.db.execute(
            "SELECT version, steps, successes, failures FROM recipes WHERE app_id = ? AND tipo = ? AND entidad = ?"
            " ORDER BY version DESC LIMIT 1",
            (app_id, tipo, entidad),
        ).fetchone()

    def find_recipe(self, app_id: str, spec: dict) -> Recipe | None:
        if not app_id or not spec or spec.get("riesgos"):
            return None  # an ambiguous or dangerous task gets no shortcut: it may have to fail untouched
        tipo, entidad = task_key(spec)
        row = self._latest(app_id, tipo, entidad)
        if row is None:
            return None
        version, steps, successes, failures = row
        if failures >= 2 and failures > successes:
            return None
        return Recipe(app_id, f"{tipo}:{entidad}", version, load_steps(steps), successes, failures)

    async def replay(self, recipe: Recipe, spec: dict, driver) -> ReplayResult:
        result, executed = await run_replay(recipe, spec, driver)
        self._replays[id(spec)] = _Replayed(json.dumps(spec, sort_keys=True, default=str), recipe, result, executed)
        _log("recipe_replay", app_id=recipe.app_id, recipe=recipe.task_type, version=recipe.version,
             ok=result.ok, steps=result.steps_done, reason=result.reason)
        return result

    # --- learning ----------------------------------------------------------------------------

    def learn(self, app_id: str, spec: dict, result: TaskResult) -> None:
        if not app_id:
            return
        spec = spec or {}
        run = self._replays.pop(id(spec), None)
        if run is not None and run.spec_json != json.dumps(spec, sort_keys=True, default=str):
            run = None
        tipo, entidad = task_key(spec)
        success = result.estado == "terminada" and result.verified
        executed = run.executed if run else []
        trace = executed + list(result.steps)

        self.db.execute(
            "INSERT INTO episodes (ts, app_id, tipo, entidad, estado, verified, cost, model_steps, replayed_steps,"
            " recipe_version, replay_ok) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), app_id, tipo, entidad, result.estado, int(result.verified), result.cost_usd, len(result.steps),
             len(executed), run.recipe.version if run else None, None if run is None else int(run.result.ok)),
        )
        if run is not None:
            column = "successes" if success and run.result.ok else "failures" if not run.result.ok else None
            if column:
                self.db.execute(
                    f"UPDATE recipes SET {column} = {column} + 1 WHERE app_id = ? AND tipo = ? AND entidad = ? AND version = ?",
                    (app_id, tipo, entidad, run.recipe.version),
                )
            if not run.result.ok:
                self._lesson(app_id, f"La receta v{run.recipe.version} de «{tipo} {entidad}» se detuvo: {run.result.reason}.")

        if success:
            steps = safe_prefix(trace, spec)
            current = self._latest(app_id, tipo, entidad)
            stale = current is not None and (
                (run is not None and not run.result.ok) or (current[3] >= 2 and current[3] > current[2])
            )
            if steps and (current is None or stale):
                version = 1 if current is None else current[0] + 1
                self.db.execute(
                    "INSERT INTO recipes (app_id, tipo, entidad, version, steps, successes, created) VALUES (?, ?, ?, ?, ?, 1, ?)",
                    (app_id, tipo, entidad, version, dump_steps(steps), _now()),
                )
                _log("recipe_learned", app_id=app_id, recipe=f"{tipo}:{entidad}", version=version, steps=len(steps))
            self._map_fields(app_id, entidad, trace, spec)
            # Self-feedback: what the verifier asked for before it accepted. Next time it goes in the prompt.
            for objection in getattr(result, "feedback", None) or []:
                self._lesson(app_id, f"Antes de aceptar, la verificación pidió: {objection}", scope=f"{tipo} {entidad}")
        self.db.commit()

    def _map_fields(self, app_id: str, entidad: str, trace: list[Step], spec: dict) -> None:
        names = {normalize(k): str(k) for k in (spec.get("valores") or {})}
        for action in group_actions(trace):
            if len(action) != 3:
                continue
            slot = action[2].slot and normalize(action[2].slot)
            if not slot or slot not in names:
                continue
            self.db.execute(
                "INSERT INTO fields VALUES (?, ?, ?, ?, ?, 1, ?) ON CONFLICT (app_id, entidad, slot)"
                " DO UPDATE SET x = excluded.x, y = excluded.y, seen = seen + 1, last = excluded.last",
                (app_id, entidad, names[slot], action[0].x, action[0].y, _now()),
            )

    def _lesson(self, app_id: str, text: str, scope: str = "") -> None:
        self.db.execute("INSERT INTO lessons (ts, app_id, text, scope) VALUES (?, ?, ?, ?)", (_now(), app_id, text[:300], scope))

    # --- what the operator reads -------------------------------------------------------------

    def notes(self, app_id: str) -> str:
        if not app_id:
            return ""
        lines = []
        rows = self.db.execute(
            "SELECT tipo, entidad, SUM(estado = 'terminada' AND verified), COUNT(*) FROM episodes WHERE app_id = ?"
            " GROUP BY tipo, entidad ORDER BY MAX(id) DESC LIMIT ?",
            (app_id, MAX_NOTE_LINES),
        ).fetchall()
        for tipo, entidad, ok, total in rows:
            line = f"- {tipo} {entidad}: {ok} de {total} tareas verificadas."
            fields = self.db.execute(
                "SELECT slot, x, y FROM fields WHERE app_id = ? AND entidad = ? ORDER BY seen DESC LIMIT 8", (app_id, entidad)
            ).fetchall()
            recipe = self._latest(app_id, tipo, entidad)
            route = [s.label for s in load_steps(recipe[1]) if s.kind in ("click", "double_click") and s.label] if recipe else []
            if route:
                line += " Ruta que funcionó: " + " → ".join(route[:8]) + "."
            if fields:
                line += " Campos vistos: " + ", ".join(f"«{s}» en ({x}, {y})" for s, x, y in fields) + "."
            demanded = self.db.execute(
                "SELECT text FROM lessons WHERE app_id = ? AND scope = ? ORDER BY id DESC LIMIT 2", (app_id, f"{tipo} {entidad}")
            ).fetchall()
            line += "".join(f"\n  {t}" for (t,) in demanded)
            lines.append(line)
        lessons = self.db.execute(
            "SELECT text FROM lessons WHERE app_id = ? AND scope = '' ORDER BY id DESC LIMIT 3", (app_id,)
        ).fetchall()
        if not lines and not lessons:
            return ""
        text = "MEMORIA DE ESTA APLICACIÓN (de tareas anteriores; confírmalo todo en la captura, las posiciones pueden haber cambiado):\n"
        text += "\n".join(lines)
        if lessons:
            text += "\nLecciones:\n" + "\n".join(f"- {t}" for (t,) in lessons)
        return text

    # --- for the learning curve and the debrief ------------------------------------------------

    def episodes(self, app_id: str | None = None) -> list[dict]:
        query = "SELECT * FROM episodes" + (" WHERE app_id = ?" if app_id else "") + " ORDER BY id"
        cursor = self.db.execute(query, (app_id,) if app_id else ())
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, row)) for row in cursor]

    def close(self) -> None:
        self.db.close()


def default_path() -> str:
    return "/data/memory.db" if Path("/data").is_dir() else "runs/memory.db"
