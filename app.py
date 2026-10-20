"""Книга рецептов для домашней кухни: Flask + SQLite, всё хранится локально."""
import json
import os
import re
import sqlite3
import threading
import uuid
import webbrowser
from datetime import date, datetime, timedelta

from flask import (Flask, Response, abort, flash, g, redirect,
                   render_template, request, url_for)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "recipes.db")
HOST, PORT = "127.0.0.1", 5000

CATEGORIES = ["Завтраки", "Супы", "Основные блюда", "Гарниры", "Салаты",
              "Закуски", "Выпечка", "Десерты", "Напитки", "Заготовки", "Другое"]
MEALS = [("breakfast", "Завтрак"), ("lunch", "Обед"), ("dinner", "Ужин")]
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
CREATE TABLE IF NOT EXISTS family_members (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);
CREATE TABLE IF NOT EXISTS meal_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meal_date TEXT NOT NULL,
    meal TEXT NOT NULL CHECK (meal IN ('breakfast', 'lunch', 'dinner')),
    person TEXT NOT NULL REFERENCES family_members(id),
    recipe_id INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
    is_dash INTEGER NOT NULL DEFAULT 0 CHECK (is_dash IN (0, 1)),
    portions INTEGER NOT NULL DEFAULT 1 CHECK (portions BETWEEN 1 AND 1000),
    UNIQUE (meal_date, meal, person)
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
        columns = {row[1] for row in db.execute("PRAGMA table_info(meal_plan)")}
        if "portions" not in columns:
            db.execute(
                "ALTER TABLE meal_plan ADD COLUMN portions INTEGER NOT NULL DEFAULT 1"
                " CHECK (portions BETWEEN 1 AND 1000)")
        db.execute(
            "INSERT OR IGNORE INTO family_members (id, name) VALUES ('wife', 'Жена'), ('husband', 'Муж')")
        table_sql = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'meal_plan'"
        ).fetchone()[0]
        if "person IN ('wife', 'husband')" in table_sql:
            db.execute("ALTER TABLE meal_plan RENAME TO meal_plan_legacy")
            db.execute(
                "CREATE TABLE meal_plan ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "meal_date TEXT NOT NULL,"
                "meal TEXT NOT NULL CHECK (meal IN ('breakfast', 'lunch', 'dinner')),"
                "person TEXT NOT NULL REFERENCES family_members(id),"
                "recipe_id INTEGER REFERENCES recipes(id) ON DELETE SET NULL,"
                "is_dash INTEGER NOT NULL DEFAULT 0 CHECK (is_dash IN (0, 1)),"
                "portions INTEGER NOT NULL DEFAULT 1 CHECK (portions BETWEEN 1 AND 1000),"
                "UNIQUE (meal_date, meal, person))")
            db.execute(
                "INSERT INTO meal_plan (id, meal_date, meal, person, recipe_id, is_dash, portions)"
                " SELECT id, meal_date, meal, person, recipe_id, is_dash, portions FROM meal_plan_legacy")
            db.execute("DROP TABLE meal_plan_legacy")


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


@app.route("/family", methods=["GET", "POST"])
def family():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        member_id = request.form.get("member_id", "")
        if action == "add":
            name = request.form.get("name", "").strip()
            if not name:
                flash("Укажите имя участника семьи.")
            elif len(name) > 80:
                flash("Имя участника не должно быть длиннее 80 символов.")
            elif db.execute(
                    "SELECT 1 FROM family_members WHERE PYLOWER(name) = PYLOWER(?)",
                    (name,)).fetchone():
                flash("Участник с таким именем уже есть.")
            else:
                db.execute("INSERT INTO family_members (id, name) VALUES (?, ?)",
                           (uuid.uuid4().hex, name))
                db.commit()
                flash(f"Участник «{name}» добавлен.")
            return redirect(url_for("family"))

        member = db.execute(
            "SELECT * FROM family_members WHERE id = ?", (member_id,)).fetchone()
        if member is None:
            abort(404)
        if action == "rename":
            name = request.form.get("name", "").strip()
            duplicate = db.execute(
                "SELECT 1 FROM family_members WHERE PYLOWER(name) = PYLOWER(?) AND id != ?",
                (name, member_id)).fetchone()
            if not name:
                flash("Имя участника не может быть пустым.")
            elif len(name) > 80:
                flash("Имя участника не должно быть длиннее 80 символов.")
            elif duplicate:
                flash("Участник с таким именем уже есть.")
            else:
                db.execute("UPDATE family_members SET name = ? WHERE id = ?",
                           (name, member_id))
                db.commit()
                flash("Имя участника обновлено.")
        elif action in ("archive", "restore"):
            active = bool(member["active"])
            if action == "archive" and active:
                active_count = db.execute(
                    "SELECT COUNT(*) FROM family_members WHERE active = 1").fetchone()[0]
                if active_count <= 1:
                    flash("Нельзя архивировать последнего активного участника.")
                    return redirect(url_for("family"))
            db.execute("UPDATE family_members SET active = ? WHERE id = ?",
                       (0 if action == "archive" else 1, member_id))
            db.commit()
            flash("Участник перемещён в архив." if action == "archive"
                  else "Участник возвращён в состав семьи.")
        else:
            abort(400)
        return redirect(url_for("family"))

    active_members = db.execute(
        "SELECT * FROM family_members WHERE active = 1 ORDER BY id IN ('wife', 'husband') DESC, name COLLATE NOCASE"
    ).fetchall()
    archived_members = db.execute(
        "SELECT * FROM family_members WHERE active = 0 ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return render_template("family.html", active_members=active_members,
                           archived_members=archived_members)


@app.route("/meal-plan", methods=["GET", "POST"])
def meal_plan():
    week_value = request.values.get("week", "")
    try:
        selected_day = date.fromisoformat(week_value) if week_value else date.today()
        monday = selected_day - timedelta(days=selected_day.weekday())
    except ValueError:
        monday = date.today() - timedelta(days=date.today().weekday())
        flash("Некорректная дата недели. Показана текущая неделя.")

    days = [monday + timedelta(days=offset) for offset in range(7)]
    db = get_db()
    family_members = db.execute(
        "SELECT id, name FROM family_members WHERE active = 1 ORDER BY"
        " id IN ('wife', 'husband') DESC, name COLLATE NOCASE").fetchall()
    if request.method == "POST":
        copy_to_week = request.form.get("copy_to_week")
        target_monday = None
        if copy_to_week:
            try:
                target_day = date.fromisoformat(request.form.get("target_week", ""))
            except ValueError:
                flash("Укажите корректную дату недели, куда копировать план.")
                return redirect(url_for("meal_plan", week=monday.isoformat()))
            target_monday = target_day - timedelta(days=target_day.weekday())
            if target_monday == monday:
                flash("Выберите другую неделю для копирования.")
                return redirect(url_for("meal_plan", week=monday.isoformat()))

        clear_day = request.form.get("clear_day")
        if clear_day:
            if clear_day not in {day.isoformat() for day in days}:
                abort(400)
            db.execute("DELETE FROM meal_plan WHERE meal_date = ?", (clear_day,))
            db.commit()
            flash("План на день очищен")
            return redirect(url_for("meal_plan", week=monday.isoformat()))

        choices = []
        try:
            for day in days:
                for meal, _meal_label in MEALS:
                    for member in family_members:
                        person = member["id"]
                        field = f"{day.isoformat()}_{meal}_{person}"
                        if field not in request.form:
                            raise ValueError("Форма плана заполнена не полностью.")
                        value = request.form[field]
                        try:
                            portions = int(request.form[f"{field}_portions"])
                        except (KeyError, ValueError):
                            raise ValueError("Укажите количество порций целым числом.")
                        if not 1 <= portions <= 1000:
                            raise ValueError("Количество порций должно быть от 1 до 1000.")
                        if value in ("", "dash"):
                            choices.append((day.isoformat(), meal, person, None,
                                            1 if value == "dash" else 0, portions))
                            continue
                        try:
                            recipe_id = int(value)
                        except ValueError:
                            raise ValueError("Выбрано неизвестное блюдо.")
                        if not db.execute("SELECT 1 FROM recipes WHERE id = ?",
                                          (recipe_id,)).fetchone():
                            raise ValueError("Выбранный рецепт не найден.")
                        choices.append((day.isoformat(), meal, person, recipe_id, 0, portions))
        except ValueError as error:
            flash(f"Не удалось сохранить план: {error}")
            return redirect(url_for("meal_plan", week=monday.isoformat()))

        for meal_date, meal, person, recipe_id, is_dash, portions in choices:
            if recipe_id is None and not is_dash:
                db.execute(
                    "DELETE FROM meal_plan WHERE meal_date = ? AND meal = ? AND person = ?",
                    (meal_date, meal, person))
            else:
                db.execute(
                    "INSERT INTO meal_plan (meal_date, meal, person, recipe_id, is_dash, portions)"
                    " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(meal_date, meal, person)"
                    " DO UPDATE SET recipe_id = excluded.recipe_id, is_dash = excluded.is_dash,"
                    " portions = excluded.portions",
                    (meal_date, meal, person, recipe_id, is_dash, portions))

        if target_monday:
            target_sunday = target_monday + timedelta(days=6)
            db.execute(
                "DELETE FROM meal_plan WHERE meal_date BETWEEN ? AND ?"
                " AND person IN (SELECT id FROM family_members WHERE active = 1)",
                (target_monday.isoformat(), target_sunday.isoformat()))
            shift_days = (target_monday - monday).days
            copied = db.execute(
                "INSERT INTO meal_plan (meal_date, meal, person, recipe_id, is_dash, portions)"
                " SELECT date(meal_date, ?), meal, person, recipe_id, is_dash, portions"
                " FROM meal_plan WHERE meal_date BETWEEN ? AND ?"
                " AND (recipe_id IS NOT NULL OR is_dash = 1)"
                " AND person IN (SELECT id FROM family_members WHERE active = 1)",
                (f"{shift_days} days", days[0].isoformat(), days[-1].isoformat())).rowcount
            db.commit()
            if copied:
                flash(f"План скопирован на неделю с {target_monday:%d.%m.%Y}: записей {copied}.")
            else:
                flash(f"План на неделю с {target_monday:%d.%m.%Y} очищен: исходная неделя пустая.")
            return redirect(url_for("meal_plan", week=target_monday.isoformat()))

        db.commit()
        flash("План питания сохранён")
        return redirect(url_for("meal_plan", week=monday.isoformat()))

    stored = db.execute(
        "SELECT meal_date, meal, person, recipe_id, is_dash, portions FROM meal_plan"
        " WHERE meal_date BETWEEN ? AND ?"
        " AND person IN (SELECT id FROM family_members WHERE active = 1)",
        (days[0].isoformat(), days[-1].isoformat())).fetchall()
    values = {}
    for entry in stored:
        key = (entry["meal_date"], entry["meal"], entry["person"])
        values[key] = {
            "recipe": "dash" if entry["is_dash"] else (
                str(entry["recipe_id"]) if entry["recipe_id"] is not None else ""),
            "portions": entry["portions"],
        }

    week_days = []
    weekday_names = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница",
                     "Суббота", "Воскресенье")
    for day in days:
        meals = []
        for meal, meal_label in MEALS:
            meals.append({
                "key": meal,
                "label": meal_label,
                "people": [{
                    "key": person,
                    "label": person_label,
                    "field": f"{day.isoformat()}_{meal}_{person}",
                    "value": values.get((day.isoformat(), meal, person), {}).get("recipe", ""),
                    "portions": values.get((day.isoformat(), meal, person), {}).get("portions", 1),
                } for person, person_label in [(member["id"], member["name"])
                                               for member in family_members]],
            })
        week_days.append({
            "iso": day.isoformat(),
            "label": weekday_names[day.weekday()],
            "date": day.strftime("%d.%m"),
            "meals": meals,
        })

    recipes = db.execute("SELECT id, title FROM recipes ORDER BY PYLOWER(title)").fetchall()
    return render_template(
        "meal_plan.html", days=week_days, recipes=recipes, week=monday.isoformat(),
        previous=(monday - timedelta(days=7)).isoformat(),
        next_week=(monday + timedelta(days=7)).isoformat(),
        copy_week=(monday + timedelta(days=7)).isoformat(),
        current=(date.today() - timedelta(days=date.today().weekday())).isoformat())


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
    export_data = {
        "title": r["title"],
        "category": r["category"],
        "servings": servings,
        "prep_min": r["prep_min"],
        "cook_min": r["cook_min"],
        "rating": r["rating"],
        "description": r["description"],
        "ingredients": [ingredient_line(item) for item in items],
        "steps": steps,
        "notes": r["notes"],
    }
    return render_template("recipe.html", r=r, items=items, steps=steps,
                           servings=servings, export_data=export_data)


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
    recipes = []
    for r in db.execute("SELECT * FROM recipes ORDER BY id"):
        item = dict(r)
        item["ingredients"] = [ingredient_line(i) for i in get_ingredients(r["id"])]
        recipes.append(item)
    family_members = [{
        "id": row["id"],
        "name": row["name"],
        "active": bool(row["active"]),
    } for row in db.execute("SELECT id, name, active FROM family_members ORDER BY id")]
    meal_plan = [{
        "date": row["meal_date"],
        "meal": row["meal"],
        "member_id": row["person"],
        "person_name": row["person_name"],
        "recipe_title": row["recipe_title"],
        "is_dash": bool(row["is_dash"]),
        "portions": row["portions"],
    } for row in db.execute(
        "SELECT mp.meal_date, mp.meal, mp.person, fm.name AS person_name,"
        " r.title AS recipe_title,"
        " mp.is_dash, mp.portions"
        " FROM meal_plan mp JOIN family_members fm ON fm.id = mp.person"
        " LEFT JOIN recipes r ON r.id = mp.recipe_id"
        " WHERE mp.is_dash = 1 OR mp.recipe_id IS NOT NULL"
        " ORDER BY mp.meal_date, mp.meal, mp.person")]
    body = json.dumps({
        "format": "my_recipes_backup",
        "version": 3,
        "recipes": recipes,
        "family_members": family_members,
        "meal_plan": meal_plan,
    }, ensure_ascii=False, indent=2)
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
    """Загружает рецепты и планы из JSON. Всё или ничего при ошибке."""
    if isinstance(data, list):
        recipes_data, meal_plan_data, members_data, version = data, [], [], 0
    elif isinstance(data, dict) and data.get("format") == "my_recipes_backup":
        version = data.get("version")
        if version not in (1, 2, 3):
            raise ValueError("неподдерживаемая версия резервной копии")
        recipes_data = data.get("recipes")
        meal_plan_data = data.get("meal_plan")
        members_data = data.get("family_members", []) if version >= 3 else []
        if (not isinstance(recipes_data, list) or not isinstance(meal_plan_data, list)
                or not isinstance(members_data, list)):
            raise ValueError("в копии должны быть списки рецептов и планов питания")
    else:
        raise ValueError("неизвестный формат резервной копии")

    validated_members = []
    member_ids = set()
    member_names = set()
    for n, item in enumerate(members_data, 1):
        if not isinstance(item, dict):
            raise ValueError(f"участник семьи №{n}: ожидался объект")
        member_id = item.get("id")
        name = item.get("name")
        active = item.get("active")
        if not isinstance(member_id, str) or not member_id.strip() or member_id in member_ids:
            raise ValueError(f"участник семьи №{n}: некорректный или повторяющийся идентификатор")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"участник семьи №{n}: не указано имя")
        if name.strip().casefold() in member_names:
            raise ValueError(f"участник семьи №{n}: имя уже используется")
        if not isinstance(active, bool):
            raise ValueError(f"участник семьи №{n}: некорректный статус")
        member_ids.add(member_id)
        member_names.add(name.strip().casefold())
        validated_members.append((member_id, name.strip(), active))
    if version >= 3 and not any(member[2] for member in validated_members):
        raise ValueError("в копии должен быть хотя бы один активный участник семьи")
    member_names_by_id = {member_id: name for member_id, name, _active in validated_members}

    validated_plan = []
    for n, entry in enumerate(meal_plan_data, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"запись плана №{n}: ожидался объект")
        try:
            meal_date = date.fromisoformat(entry.get("date", "")).isoformat()
        except (TypeError, ValueError):
            raise ValueError(f"запись плана №{n}: некорректная дата")
        meal = entry.get("meal")
        if version >= 3:
            person = entry.get("member_id")
            person_name = entry.get("person_name")
            if (not isinstance(person, str) or person not in member_ids
                    or not isinstance(person_name, str) or not person_name.strip()):
                raise ValueError(f"запись плана №{n}: участник отсутствует в копии")
            if person_name.strip() != member_names_by_id[person]:
                raise ValueError(f"запись плана №{n}: имя участника не совпадает с составом семьи")
        else:
            person = entry.get("person")
            person_name = {"wife": "Жена", "husband": "Муж"}.get(person)
        recipe_title = entry.get("recipe_title")
        is_dash = entry.get("is_dash")
        portions = entry.get("portions", 1)
        if meal not in {key for key, _label in MEALS}:
            raise ValueError(f"запись плана №{n}: неизвестный приём пищи")
        if version < 3 and person_name is None:
            raise ValueError(f"запись плана №{n}: неизвестный участник")
        if not isinstance(is_dash, bool):
            raise ValueError(f"запись плана №{n}: некорректное значение прочерка")
        if (not isinstance(portions, int) or isinstance(portions, bool)
                or not 1 <= portions <= 1000):
            raise ValueError(f"запись плана №{n}: количество порций должно быть от 1 до 1000")
        if is_dash:
            if recipe_title is not None:
                raise ValueError(f"запись плана №{n}: прочерк не может содержать рецепт")
        elif not isinstance(recipe_title, str) or not recipe_title.strip():
            raise ValueError(f"запись плана №{n}: не указано название рецепта")
        validated_plan.append((meal_date, meal, person, person_name, recipe_title,
                               is_dash, portions))

    db = get_db()
    added = skipped = 0
    try:
        if replace:
            db.execute("DELETE FROM meal_plan")
            db.execute("DELETE FROM recipes")  # ингредиенты удаляются каскадом
            if version >= 3:
                db.execute("DELETE FROM family_members")
        member_mapping = {}
        for member_id, name, active in validated_members:
            existing = db.execute(
                "SELECT id FROM family_members WHERE PYLOWER(name) = PYLOWER(?)",
                (name,)).fetchone()
            if existing:
                local_id = existing["id"]
                if replace:
                    db.execute("UPDATE family_members SET active = ? WHERE id = ?",
                               (int(active), local_id))
            else:
                local_id = member_id
                if db.execute(
                        "SELECT 1 FROM family_members WHERE id = ?", (local_id,)).fetchone():
                    local_id = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO family_members (id, name, active) VALUES (?, ?, ?)",
                    (local_id, name, int(active)))
            member_mapping[member_id] = local_id
        if version < 3:
            member_mapping = {"wife": "wife", "husband": "husband"}
        known = {row["title"].lower(): row["id"]
                 for row in db.execute("SELECT id, title FROM recipes")}
        for n, item in enumerate(recipes_data, 1):
            title = str(item.get("title", "")).strip() if isinstance(item, dict) else ""
            if not title:
                raise ValueError(f"рецепт №{n}: нет названия")
            if title.lower() in known:
                skipped += 1
                continue
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
            known[title.lower()] = cur.lastrowid
            added += 1
        for meal_date, meal, person, person_name, recipe_title, is_dash, portions in validated_plan:
            if version >= 3:
                member_id = member_mapping[person]
            else:
                member_id = person
                existing = db.execute(
                    "SELECT id FROM family_members WHERE id = ?", (member_id,)).fetchone()
                if existing is None:
                    db.execute(
                        "INSERT INTO family_members (id, name) VALUES (?, ?)",
                        (member_id, person_name))
            recipe_id = None
            if recipe_title is not None:
                recipe_id = known.get(recipe_title.strip().lower())
                if recipe_id is None:
                    raise ValueError(
                        f"план на {meal_date}: рецепт «{recipe_title}» отсутствует в копии")
            db.execute(
                "INSERT INTO meal_plan (meal_date, meal, person, recipe_id, is_dash, portions)"
                " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(meal_date, meal, person)"
                " DO UPDATE SET recipe_id = excluded.recipe_id, is_dash = excluded.is_dash,"
                " portions = excluded.portions",
                (meal_date, meal, member_id, recipe_id, int(is_dash), portions))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return added, skipped, len(validated_plan)


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
        added, skipped, plan_entries = import_recipes(data, replace)
    except ValueError as e:  # включая ошибки JSON и кодировки
        flash(f"Не удалось восстановить: {e}. База не изменена.")
        return redirect(url_for("backup"))
    msg = f"Восстановлено рецептов: {added}"
    if skipped:
        msg += f", пропущено (уже есть с таким названием): {skipped}"
    if plan_entries:
        msg += f". Записей плана питания восстановлено: {plan_entries}"
    if saved:
        msg += f". Прежняя база сохранена в файле {saved}"
    flash(msg)
    return redirect(url_for("index"))


init_db()  # таблицы создаются при любом способе запуска (python app.py, flask run)

if __name__ == "__main__":
    threading.Timer(1.0, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    app.run(host=HOST, port=PORT, debug=False)
