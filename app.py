"""Книга рецептов для домашней кухни: Flask + SQLite, всё хранится локально."""
import json
import os
import re
import sqlite3
import threading
import webbrowser
from datetime import datetime

from flask import (Flask, Response, abort, flash, g, redirect,
                   render_template, request, url_for)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "recipes.db")
HOST, PORT = "127.0.0.1", 5000

CATEGORIES = ["Завтраки", "Супы", "Основные блюда", "Гарниры", "Салаты",
              "Закуски", "Выпечка", "Десерты", "Напитки", "Заготовки", "Другое"]
UNITS = {"г", "кг", "мг", "мл", "л", "шт", "ст.л.", "ч.л.", "стакан", "стакана",
         "стаканов", "щепотка", "щепотки", "зубчик", "зубчика", "зубчиков",
         "пучок", "пучка", "банка", "банки", "упаковка", "упаковки", "кусок",
         "куска", "ломтик", "ломтика", "капля", "капли"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Другое',
    servings INTEGER NOT NULL DEFAULT 4,
    prep_min INTEGER NOT NULL DEFAULT 0,
    cook_min INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT '',
    steps TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    rating INTEGER NOT NULL DEFAULT 0,
    favorite INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingredients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    amount REAL,
    unit TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shopping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    amount REAL,
    unit TEXT NOT NULL DEFAULT '',
    done INTEGER NOT NULL DEFAULT 0
);
"""

app = Flask(__name__)
app.secret_key = "home-kitchen-local-only"  # приложение слушает только localhost
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # файл копии не больше 20 МБ


# ---------- база данных ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        # LIKE в SQLite не понимает регистр кириллицы, поэтому свой lower()
        g.db.create_function("PYLOWER", 1, lambda s: s.lower() if s else s)
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.executescript(SCHEMA)


# ---------- разбор и форматирование ингредиентов ----------
LINE_RE = re.compile(r"^(\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?)\s*(.*)$")


def parse_number(text):
    text = text.replace(",", ".").replace(" ", "")
    if "/" in text:
        a, b = text.split("/")
        return float(a) / float(b) if float(b) else None
    return float(text)


def parse_ingredient(line):
    """'200 г мука' -> (200.0, 'г', 'мука'); 'соль по вкусу' -> (None, '', ...)."""
    line = line.strip()
    m = LINE_RE.match(line)
    if not m:
        return None, "", line
    amount = parse_number(m.group(1))
    parts = m.group(2).strip().split(None, 1)
    if parts and parts[0].lower() in UNITS:
        unit, name = parts[0], (parts[1] if len(parts) > 1 else "")
    else:
        unit, name = "", m.group(2).strip()
    if not name:
        return None, "", line
    return amount, unit, name


def fmt_amount(x):
    if x is None:
        return ""
    x = round(x, 2)
    return str(int(x)) if x == int(x) else str(x).replace(".", ",")


def ingredient_line(row):
    return " ".join(p for p in (fmt_amount(row["amount"]), row["unit"], row["name"]) if p)


app.jinja_env.filters["num"] = fmt_amount


def to_int(value, default=0, lo=0, hi=100000):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def get_recipe_or_404(rid):
    row = get_db().execute("SELECT * FROM recipes WHERE id = ?", (rid,)).fetchone()
    if row is None:
        abort(404)
    return row


def get_ingredients(rid):
    return get_db().execute(
        "SELECT * FROM ingredients WHERE recipe_id = ? ORDER BY position", (rid,)).fetchall()


# ---------- рецепты ----------
def read_filters():
    return (request.args.get("q", "").strip(), request.args.get("category", ""),
            request.args.get("fav") == "1")


def filtered_recipes(q, category, fav, order="PYLOWER(title)", limit=None):
    sql, params = "SELECT * FROM recipes WHERE 1=1", []
    if q:
        sql += (" AND (PYLOWER(title) LIKE ? OR EXISTS (SELECT 1 FROM ingredients i "
                "WHERE i.recipe_id = recipes.id AND PYLOWER(i.name) LIKE ?))")
        params += [f"%{q.lower()}%"] * 2
    if category:
        sql += " AND category = ?"
        params.append(category)
    if fav:
        sql += " AND favorite = 1"
    sql += f" ORDER BY {order}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return get_db().execute(sql, params).fetchall()


@app.route("/")
def index():
    q, category, fav = read_filters()
    return render_template("index.html", recipes=filtered_recipes(q, category, fav),
                           q=q, category=category, fav=fav, categories=CATEGORIES)


@app.route("/random")
def random_recipe():
    """Случайный рецепт; учитывает поиск и фильтры, если они переданы."""
    rows = filtered_recipes(*read_filters(), order="RANDOM()", limit=1)
    if not rows:
        flash("Подходящих рецептов не найдено")
        return redirect(url_for("index"))
    return redirect(url_for("recipe", rid=rows[0]["id"]))


@app.route("/recipe/<int:rid>")
def recipe(rid):
    r = get_recipe_or_404(rid)
    servings = to_int(request.args.get("servings"), r["servings"], 1, 1000)
    factor = servings / r["servings"]
    items = [{"amount": i["amount"] * factor if i["amount"] is not None else None,
              "unit": i["unit"], "name": i["name"]} for i in get_ingredients(rid)]
    steps = [s.strip() for s in r["steps"].splitlines() if s.strip()]
    return render_template("recipe.html", r=r, items=items, steps=steps, servings=servings)


def insert_ingredients(db, rid, lines):
    lines = [str(ln).strip() for ln in lines if str(ln).strip()]
    for pos, line in enumerate(lines):
        amount, unit, name = parse_ingredient(line)
        db.execute("INSERT INTO ingredients (recipe_id, position, amount, unit, name)"
                   " VALUES (?,?,?,?,?)", (rid, pos, amount, unit, name))


def save_recipe(rid=None):
    f = request.form
    title = f.get("title", "").strip()
    if not title:
        return None
    data = (title, f.get("category") or "Другое", to_int(f.get("servings"), 4, 1, 1000),
            to_int(f.get("prep_min")), to_int(f.get("cook_min")),
            f.get("description", "").strip(), f.get("steps", "").strip(),
            f.get("notes", "").strip(), to_int(f.get("rating"), 0, 0, 5))
    db = get_db()
    if rid is None:
        cur = db.execute(
            "INSERT INTO recipes (title, category, servings, prep_min, cook_min, description,"
            " steps, notes, rating, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            data + (datetime.now().isoformat(timespec="seconds"),))
        rid = cur.lastrowid
    else:
        db.execute(
            "UPDATE recipes SET title=?, category=?, servings=?, prep_min=?, cook_min=?,"
            " description=?, steps=?, notes=?, rating=? WHERE id=?", data + (rid,))
        db.execute("DELETE FROM ingredients WHERE recipe_id = ?", (rid,))
    insert_ingredients(db, rid, f.get("ingredients", "").splitlines())
    db.commit()
    return rid


@app.route("/new", methods=["GET", "POST"])
def new_recipe():
    if request.method == "POST":
        rid = save_recipe()
        if rid:
            flash("Рецепт сохранён")
            return redirect(url_for("recipe", rid=rid))
        flash("Укажите название рецепта")
    blank = {"title": "", "category": "Основные блюда", "servings": 4, "prep_min": 0,
             "cook_min": 0, "description": "", "steps": "", "notes": "", "rating": 0}
    return render_template("form.html", r={**blank, **request.form.to_dict()},
                           ingredients_text=request.form.get("ingredients", ""),
                           categories=CATEGORIES, units=sorted(UNITS), editing=False)


@app.route("/recipe/<int:rid>/edit", methods=["GET", "POST"])
def edit_recipe(rid):
    r = get_recipe_or_404(rid)
    if request.method == "POST":
        if save_recipe(rid):
            flash("Изменения сохранены")
            return redirect(url_for("recipe", rid=rid))
        flash("Укажите название рецепта")
        r = {**dict(r), **request.form.to_dict()}
        text = request.form.get("ingredients", "")
    else:
        text = "\n".join(ingredient_line(i) for i in get_ingredients(rid))
    return render_template("form.html", r=r, ingredients_text=text, categories=CATEGORIES,
                           units=sorted(UNITS), editing=True)


@app.post("/recipe/<int:rid>/delete")
def delete_recipe(rid):
    get_recipe_or_404(rid)
    db = get_db()
    db.execute("DELETE FROM recipes WHERE id = ?", (rid,))
    db.commit()
    flash("Рецепт удалён")
    return redirect(url_for("index"))


@app.post("/recipe/<int:rid>/favorite")
def toggle_favorite(rid):
    get_recipe_or_404(rid)
    db = get_db()
    db.execute("UPDATE recipes SET favorite = 1 - favorite WHERE id = ?", (rid,))
    db.commit()
    return redirect(url_for("recipe", rid=rid))


# ---------- список покупок ----------
def add_to_shopping(name, amount, unit):
    db = get_db()
    row = db.execute("SELECT id, amount FROM shopping WHERE PYLOWER(name) = ? AND unit = ?"
                     " AND done = 0", (name.lower(), unit)).fetchone()
    if row and amount is not None and row["amount"] is not None:
        db.execute("UPDATE shopping SET amount = ? WHERE id = ?", (row["amount"] + amount, row["id"]))
    elif row and amount is None:
        return  # уже есть в списке
    else:
        db.execute("INSERT INTO shopping (name, amount, unit) VALUES (?,?,?)", (name, amount, unit))


@app.post("/recipe/<int:rid>/shop")
def shop_recipe(rid):
    r = get_recipe_or_404(rid)
    factor = to_int(request.form.get("servings"), r["servings"], 1, 1000) / r["servings"]
    for i in get_ingredients(rid):
        amount = i["amount"] * factor if i["amount"] is not None else None
        add_to_shopping(i["name"], amount, i["unit"])
    get_db().commit()
    flash("Ингредиенты добавлены в список покупок")
    return redirect(url_for("shopping"))


@app.route("/shopping")
def shopping():
    items = get_db().execute("SELECT * FROM shopping ORDER BY done, PYLOWER(name)").fetchall()
    return render_template("shopping.html", items=items)


@app.post("/shopping/add")
def shopping_add():
    line = request.form.get("line", "").strip()
    if line:
        amount, unit, name = parse_ingredient(line)
        add_to_shopping(name, amount, unit)
        get_db().commit()
    return redirect(url_for("shopping"))


@app.post("/shopping/<int:sid>/toggle")
def shopping_toggle(sid):
    db = get_db()
    db.execute("UPDATE shopping SET done = 1 - done WHERE id = ?", (sid,))
    db.commit()
    return redirect(url_for("shopping"))


@app.post("/shopping/<int:sid>/delete")
def shopping_delete(sid):
    db = get_db()
    db.execute("DELETE FROM shopping WHERE id = ?", (sid,))
    db.commit()
    return redirect(url_for("shopping"))


@app.post("/shopping/clear")
def shopping_clear():
    db = get_db()
    db.execute("DELETE FROM shopping WHERE done = 1" if request.form.get("only") == "done"
               else "DELETE FROM shopping")
    db.commit()
    return redirect(url_for("shopping"))


# ---------- резервная копия ----------
@app.route("/export")
def export():
    db = get_db()
    out = []
    for r in db.execute("SELECT * FROM recipes ORDER BY id"):
        item = dict(r)
        item["ingredients"] = [ingredient_line(i) for i in get_ingredients(r["id"])]
        out.append(item)
    body = json.dumps(out, ensure_ascii=False, indent=2)
    return Response(body, mimetype="application/json", headers={
        "Content-Disposition": "attachment; filename=recipes-backup.json"})


# ---------- восстановление из резервной копии ----------
@app.route("/backup")
def backup():
    count = get_db().execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    return render_template("backup.html", count=count)


def snapshot_db():
    """Копия текущей базы перед заменой; возвращает имя файла."""
    name = f"recipes-before-restore-{datetime.now():%Y%m%d-%H%M%S}.db"
    dst = sqlite3.connect(os.path.join(BASE_DIR, name))
    get_db().backup(dst)
    dst.close()
    return name


def import_recipes(data, replace=False):
    """Загружает список рецептов из JSON. Всё или ничего: при ошибке база не меняется."""
    if not isinstance(data, list):
        raise ValueError("в файле должен быть список рецептов (как в «Скачать копию»)")
    db = get_db()
    added = skipped = 0
    try:
        if replace:
            db.execute("DELETE FROM recipes")  # ингредиенты удаляются каскадом
        known = {row[0].lower() for row in db.execute("SELECT title FROM recipes")}
        for n, item in enumerate(data, 1):
            title = str(item.get("title", "")).strip() if isinstance(item, dict) else ""
            if not title:
                raise ValueError(f"рецепт №{n}: нет названия")
            if title.lower() in known:
                skipped += 1
                continue
            known.add(title.lower())
            ingredients = item.get("ingredients") or []
            if not isinstance(ingredients, list):
                raise ValueError(f"рецепт №{n}: ингредиенты должны быть списком строк")
            cur = db.execute(
                "INSERT INTO recipes (title, category, servings, prep_min, cook_min, description,"
                " steps, notes, rating, favorite, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (title, str(item.get("category") or "Другое"),
                 to_int(item.get("servings"), 4, 1, 1000), to_int(item.get("prep_min")),
                 to_int(item.get("cook_min")), str(item.get("description") or ""),
                 str(item.get("steps") or ""), str(item.get("notes") or ""),
                 to_int(item.get("rating"), 0, 0, 5), 1 if to_int(item.get("favorite")) else 0,
                 str(item.get("created_at") or datetime.now().isoformat(timespec="seconds"))))
            insert_ingredients(db, cur.lastrowid, ingredients)
            added += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    return added, skipped


@app.post("/restore")
def restore():
    file = request.files.get("backup")
    if not file or not file.filename:
        flash("Выберите файл резервной копии (.json)")
        return redirect(url_for("backup"))
    replace = request.form.get("mode") == "replace"
    try:
        try:
            data = json.loads(file.read().decode("utf-8-sig"))
        except ValueError:
            raise ValueError("файл не похож на резервную копию (ожидается .json из «Скачать копию»)")
        saved = snapshot_db() if replace else None
        added, skipped = import_recipes(data, replace)
    except ValueError as e:  # включая ошибки JSON и кодировки
        flash(f"Не удалось восстановить: {e}. База не изменена.")
        return redirect(url_for("backup"))
    msg = f"Восстановлено рецептов: {added}"
    if skipped:
        msg += f", пропущено (уже есть с таким названием): {skipped}"
    if saved:
        msg += f". Прежняя база сохранена в файле {saved}"
    flash(msg)
    return redirect(url_for("index"))


init_db()  # таблицы создаются при любом способе запуска (python app.py, flask run)

if __name__ == "__main__":
    threading.Timer(1.0, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    app.run(host=HOST, port=PORT, debug=False)
