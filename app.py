import os
import re
import sqlite3
from urllib.parse import quote
from functools import wraps
from datetime import datetime
from io import BytesIO

from werkzeug.utils import secure_filename
from flask import (
    Flask,
    g,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    session,
)

# Pillow (redimensionar imagens)
try:
    from PIL import Image, ImageOps
except Exception:
    Image = None
    ImageOps = None

# Postgres (Railway)
try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:
    psycopg = None
    dict_row = None


APP_NAME = "Distribuidora de Bebidas Nova Cidade"
BASE_DIR = os.path.dirname(__file__)

# Se existir DATABASE_URL (Railway Postgres), usa Postgres. Senão, usa SQLite local.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# SQLite path (se usar SQLite)
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "database.sqlite3"))

# Uploads (no Railway some sem Volume — mas funciona enquanto você não der deploy / não reiniciar)
DEFAULT_UPLOAD = os.path.join(BASE_DIR, "static", "uploads")
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", DEFAULT_UPLOAD)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

# WhatsApp inicial (seed) - depois você altera no admin
STORE_WHATSAPP_NUMBER = os.getenv("STORE_WHATSAPP_NUMBER", "5531999999999")

# Admin fixo
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "NovaCidade@2026")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-me")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024  # 12MB


# =========================
# DB (SQLite ou Postgres)
# =========================
def using_postgres() -> bool:
    return bool(DATABASE_URL)


def get_db():
    """
    Retorna conexão e guarda em g:
      - SQLite: sqlite3.Connection (row_factory Row)
      - Postgres: psycopg.Connection (row_factory dict_row)
    """
    if "db" not in g:
        if using_postgres():
            if psycopg is None:
                raise RuntimeError(
                    "psycopg não instalado. Adicione no requirements.txt: psycopg[binary]==3.2.6"
                )
            g.db = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        else:
            g.db = sqlite3.connect(DB_PATH)
            g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass


def db_execute(db, sql: str, params=()):
    cur = db.cursor()
    cur.execute(sql, params)
    return cur


def db_fetchone(cur):
    return cur.fetchone()


def db_fetchall(cur):
    return cur.fetchall()


def db_commit(db):
    try:
        db.commit()
    except Exception:
        pass


def init_db():
    db = get_db()

    if using_postgres():
        db_execute(
            db,
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """,
        )

        db_execute(
            db,
            """
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 1
            );
            """,
        )

        db_execute(
            db,
            """
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                price_cents INTEGER NOT NULL DEFAULT 0,
                image_url TEXT,
                category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                category TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                is_promo INTEGER NOT NULL DEFAULT 0,
                promo_price_cents INTEGER
            );
            """,
        )

        db_commit(db)

        # seed settings whatsapp
        cur = db_execute(db, "SELECT value FROM settings WHERE key=%s;", ("whatsapp_number",))
        row = db_fetchone(cur)
        if row is None:
            db_execute(
                db,
                "INSERT INTO settings (key, value) VALUES (%s, %s);",
                ("whatsapp_number", STORE_WHATSAPP_NUMBER),
            )
            db_commit(db)

        # seed categorias
        cur = db_execute(db, "SELECT COUNT(*) AS c FROM categories;")
        c = db_fetchone(cur)["c"]
        if int(c) == 0:
            base_cats = [("Cervejas", 1), ("Refrigerantes", 1), ("Águas", 1), ("Outros", 1)]
            for name, active in base_cats:
                db_execute(
                    db,
                    "INSERT INTO categories (name, is_active) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING;",
                    (name, active),
                )
            db_commit(db)

    else:
        # SQLite
        db_execute(
            db,
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """,
        )

        db_execute(
            db,
            """
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 1
            );
            """,
        )

        db_execute(
            db,
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                price_cents INTEGER NOT NULL DEFAULT 0,
                image_url TEXT,
                category_id INTEGER,
                category TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                is_promo INTEGER NOT NULL DEFAULT 0,
                promo_price_cents INTEGER
            );
            """,
        )
        db_commit(db)

        # seed settings whatsapp
        cur = db_execute(db, "SELECT value FROM settings WHERE key=?;", ("whatsapp_number",))
        row = db_fetchone(cur)
        if row is None:
            db_execute(db, "INSERT INTO settings (key, value) VALUES (?, ?);", ("whatsapp_number", STORE_WHATSAPP_NUMBER))
            db_commit(db)

        # seed categorias
        cur = db_execute(db, "SELECT COUNT(*) AS c FROM categories;")
        c = db_fetchone(cur)["c"]
        if int(c) == 0:
            base_cats = [("Cervejas", 1), ("Refrigerantes", 1), ("Águas", 1), ("Outros", 1)]
            for name, active in base_cats:
                db_execute(db, "INSERT OR IGNORE INTO categories (name, is_active) VALUES (?, ?);", (name, active))
            db_commit(db)


@app.before_request
def _ensure_db():
    init_db()


# =========================
# AUTH
# =========================
def is_admin_logged_in() -> bool:
    return bool(session.get("is_admin"))


def admin_required(view_func):
    @wraps(view_func)
    def _wrapped(*args, **kwargs):
        if not is_admin_logged_in():
            flash("Faça login para acessar o admin.", "error")
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return _wrapped


# =========================
# SETTINGS
# =========================
def get_setting(key: str, default: str = "") -> str:
    db = get_db()
    if using_postgres():
        cur = db_execute(db, "SELECT value FROM settings WHERE key=%s;", (key,))
    else:
        cur = db_execute(db, "SELECT value FROM settings WHERE key=?;", (key,))
    row = db_fetchone(cur)
    if not row:
        return default
    # dict_row (postgres) => row["value"]; sqlite Row => row["value"]
    val = row["value"]
    return default if val is None else str(val)


def set_setting(key: str, value: str) -> None:
    db = get_db()
    if using_postgres():
        db_execute(
            db,
            """
            INSERT INTO settings (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value;
            """,
            (key, value),
        )
    else:
        db_execute(
            db,
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value;
            """,
            (key, value),
        )
    db_commit(db)


def normalize_whatsapp(raw: str) -> str:
    digits = re.sub(r"\D+", "", raw or "")
    if not digits:
        return ""
    if len(digits) == 11:
        digits = "55" + digits
    return digits


# =========================
# UTILS
# =========================
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def money_br(price_cents: int) -> str:
    v = (price_cents or 0) / 100.0
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def parse_price_to_cents(raw: str) -> int:
    s = (raw or "0").strip()
    s = s.replace("R$", "").replace("r$", "").strip()

    if s.isdigit():
        return int(s) * 100

    if "," in s:
        s = s.replace(".", "").replace(",", ".")

    value = float(s)
    return int(round(value * 100))


def fetch_categories(active_only=True):
    db = get_db()
    if active_only:
        sql = "SELECT id, name, is_active FROM categories WHERE is_active=1 ORDER BY name;"
    else:
        sql = "SELECT id, name, is_active FROM categories ORDER BY is_active DESC, name;"
    cur = db_execute(db, sql)
    rows = db_fetchall(cur)
    return [dict(id=r["id"], name=r["name"], is_active=bool(r["is_active"])) for r in rows]


def fetch_products(active_only=True):
    db = get_db()
    if active_only:
        sql = """
        SELECT p.*, c.name AS category_name
        FROM products p
        LEFT JOIN categories c ON c.id = p.category_id
        WHERE p.is_active = 1
        ORDER BY COALESCE(c.name, p.category, 'Outros'), p.name;
        """
    else:
        sql = """
        SELECT p.*, c.name AS category_name
        FROM products p
        LEFT JOIN categories c ON c.id = p.category_id
        ORDER BY p.is_active DESC, COALESCE(c.name, p.category, 'Outros'), p.name;
        """
    cur = db_execute(db, sql)
    rows = db_fetchall(cur)

    products = []
    for r in rows:
        cat = r.get("category_name") or r.get("category") or "Outros"
        base_cents = int(r.get("price_cents") or 0)
        promo_cents = int(r.get("promo_price_cents") or 0) if r.get("promo_price_cents") is not None else 0
        is_promo_ok = bool(r.get("is_promo")) and promo_cents > 0
        effective_cents = promo_cents if is_promo_ok else base_cents

        products.append(
            dict(
                id=r["id"],
                name=r["name"],
                description=r.get("description") or "",
                price_cents=base_cents,
                price=money_br(base_cents),
                promo_price_cents=(promo_cents if promo_cents > 0 else None),
                promo_price=(money_br(promo_cents) if promo_cents > 0 else ""),
                is_promo=is_promo_ok,
                effective_price_cents=effective_cents,
                effective_price=money_br(effective_cents),
                image_url=r.get("image_url") or "",
                category=cat,
                category_id=r.get("category_id"),
                is_active=bool(r.get("is_active")),
            )
        )
    return products


def unique_webp_name(original_filename: str) -> str:
    base = secure_filename(os.path.splitext(original_filename or "img")[0]) or "img"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{base}_{stamp}.webp"


def process_and_save_image(file_storage) -> str:
    if not Image:
        raise RuntimeError("Pillow não está instalado. Coloque no requirements.txt: Pillow")

    data = file_storage.read()
    file_storage.seek(0)

    img = Image.open(BytesIO(data))
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg

    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((800, 800), Image.Resampling.LANCZOS)

    fname = unique_webp_name(file_storage.filename)
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], fname)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    img.save(save_path, "WEBP", quality=82, method=6)
    return f"/static/uploads/{fname}"


# =========================
# ROTAS PÚBLICAS
# =========================
@app.get("/")
def index():
    products = fetch_products(active_only=True)
    grouped = {}
    for p in products:
        grouped.setdefault(p["category"], []).append(p)

    return render_template(
        "index.html",
        app_name=APP_NAME,
        grouped=grouped,
        is_admin=is_admin_logged_in(),
    )


@app.get("/checkout")
def checkout():
    store_number = get_setting("whatsapp_number", STORE_WHATSAPP_NUMBER)
    return render_template(
        "checkout.html",
        app_name=APP_NAME,
        store_whatsapp=store_number,
        is_admin=is_admin_logged_in(),
    )


@app.post("/api/whatsapp_link")
def api_whatsapp_link():
    data = request.get_json(force=True)
    customer_name = (data.get("customer_name") or "").strip()
    address = (data.get("address") or "").strip()
    phone = (data.get("phone") or "").strip()
    payment_method = (data.get("payment_method") or "").strip()
    change_for = (data.get("change_for") or "").strip()
    items = data.get("items") or []

    if not customer_name or not address or not phone or not payment_method or not items:
        return jsonify({"error": "Dados incompletos."}), 400

    total_cents = 0
    lines = []
    for it in items:
        qty = int(it.get("qty") or 0)
        if qty <= 0:
            continue
        price_cents = int(it.get("price_cents") or 0)
        name = (it.get("name") or "Item").strip()
        subtotal = qty * price_cents
        total_cents += subtotal
        lines.append(f"• {qty}x {name} — {money_br(subtotal)}")

    if not lines:
        return jsonify({"error": "Carrinho vazio."}), 400

    pay_line = payment_method
    if payment_method.lower() == "dinheiro" and change_for:
        pay_line += f" (troco para {change_for})"

    msg = (
        f"🛒 *Pedido — {APP_NAME}*\n\n"
        f"👤 *Nome:* {customer_name}\n"
        f"📍 *Endereço:* {address}\n"
        f"📞 *WhatsApp/Telefone:* {phone}\n"
        f"💳 *Pagamento:* {pay_line}\n\n"
        f"📦 *Itens:*\n" + "\n".join(lines) + "\n\n"
        f"💰 *Total:* {money_br(total_cents)}\n\n"
        f"✅ Pedido confirmado."
    )

    store_number = get_setting("whatsapp_number", STORE_WHATSAPP_NUMBER)
    link = f"https://wa.me/{store_number}?text={quote(msg)}"
    return jsonify({"link": link})


# =========================
# LOGIN / LOGOUT
# =========================
@app.get("/login")
def login():
    if is_admin_logged_in():
        return redirect(url_for("admin"))
    next_url = request.args.get("next") or url_for("admin")
    return render_template("login.html", app_name=APP_NAME, next_url=next_url, is_admin=is_admin_logged_in())


@app.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    next_url = request.form.get("next") or url_for("admin")

    if username == ADMIN_USER and password == ADMIN_PASSWORD:
        session["is_admin"] = True
        session["admin_user"] = username
        flash("Login realizado com sucesso!", "success")
        return redirect(next_url)

    flash("Usuário ou senha inválidos.", "error")
    return redirect(url_for("login", next=next_url))


@app.get("/logout")
def logout():
    session.clear()
    flash("Você saiu do admin.", "success")
    return redirect(url_for("index"))


# =========================
# ADMIN
# =========================
@app.get("/admin")
@admin_required
def admin():
    products = fetch_products(active_only=False)
    categories = fetch_categories(active_only=True)
    store_number = get_setting("whatsapp_number", STORE_WHATSAPP_NUMBER)

    return render_template(
        "admin.html",
        app_name=APP_NAME,
        products=products,
        categories=categories,
        store_whatsapp=store_number,
        is_admin=is_admin_logged_in(),
    )


@app.post("/admin/settings/whatsapp")
@admin_required
def admin_update_whatsapp():
    raw = (request.form.get("store_whatsapp") or "").strip()
    digits = normalize_whatsapp(raw)
    if not digits:
        flash("Informe um número válido (somente números). Ex: 5531999999999", "error")
        return redirect(url_for("admin"))

    set_setting("whatsapp_number", digits)
    flash("WhatsApp da loja atualizado!", "success")
    return redirect(url_for("admin"))


# =========================
# ADMIN - CATEGORIAS (ESSA ROTA ESTAVA FALTANDO)
# =========================
@app.get("/admin/categories")
@admin_required
def admin_categories():
    categories = fetch_categories(active_only=False)
    return render_template(
        "categories.html",
        app_name=APP_NAME,
        categories=categories,
        is_admin=is_admin_logged_in(),
    )


@app.post("/admin/categories/add")
@admin_required
def admin_categories_add():
    name = (request.form.get("name") or "").strip()
    is_active = 1 if request.form.get("is_active") == "on" else 0

    if not name:
        flash("Nome da categoria é obrigatório.", "error")
        return redirect(url_for("admin_categories"))

    db = get_db()
    try:
        if using_postgres():
            db_execute(db, "INSERT INTO categories (name, is_active) VALUES (%s, %s);", (name, is_active))
        else:
            db_execute(db, "INSERT INTO categories (name, is_active) VALUES (?, ?);", (name, is_active))
        db_commit(db)
        flash("Categoria adicionada!", "success")
    except Exception:
        flash("Essa categoria já existe (ou erro ao salvar).", "error")

    return redirect(url_for("admin_categories"))


@app.post("/admin/categories/toggle/<int:cid>")
@admin_required
def admin_categories_toggle(cid):
    db = get_db()
    if using_postgres():
        cur = db_execute(db, "SELECT is_active FROM categories WHERE id=%s;", (cid,))
    else:
        cur = db_execute(db, "SELECT is_active FROM categories WHERE id=?;", (cid,))
    row = db_fetchone(cur)

    if not row:
        flash("Categoria não encontrada.", "error")
        return redirect(url_for("admin_categories"))

    current = int(row["is_active"])
    new_val = 0 if current == 1 else 1

    if using_postgres():
        db_execute(db, "UPDATE categories SET is_active=%s WHERE id=%s;", (new_val, cid))
    else:
        db_execute(db, "UPDATE categories SET is_active=? WHERE id=?;", (new_val, cid))
    db_commit(db)

    flash("Status da categoria atualizado!", "success")
    return redirect(url_for("admin_categories"))


@app.post("/admin/categories/delete/<int:cid>")
@admin_required
def admin_categories_delete(cid):
    db = get_db()
    if using_postgres():
        db_execute(db, "UPDATE products SET category_id=NULL WHERE category_id=%s;", (cid,))
        db_execute(db, "DELETE FROM categories WHERE id=%s;", (cid,))
    else:
        db_execute(db, "UPDATE products SET category_id=NULL WHERE category_id=?;", (cid,))
        db_execute(db, "DELETE FROM categories WHERE id=?;", (cid,))
    db_commit(db)

    flash("Categoria removida.", "success")
    return redirect(url_for("admin_categories"))


# =========================
# ADMIN - PRODUTOS
# =========================
@app.post("/admin/add")
@admin_required
def admin_add():
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    category_id_raw = (request.form.get("category_id") or "").strip()

    price_raw = (request.form.get("price") or "0").strip()
    is_active = 1 if request.form.get("is_active") == "on" else 0

    is_promo = 1 if request.form.get("is_promo") == "on" else 0
    promo_price_raw = (request.form.get("promo_price") or "").strip()

    if not name:
        flash("Nome do produto é obrigatório.", "error")
        return redirect(url_for("admin"))

    try:
        price_cents = parse_price_to_cents(price_raw)
    except Exception:
        flash("Preço inválido.", "error")
        return redirect(url_for("admin"))

    promo_price_cents = None
    if is_promo:
        if promo_price_raw:
            try:
                promo_price_cents = parse_price_to_cents(promo_price_raw)
            except Exception:
                flash("Preço promocional inválido.", "error")
                return redirect(url_for("admin"))
        else:
            # marcou promo mas não colocou preço -> desmarca promo
            is_promo = 0
            promo_price_cents = None

    # categoria id
    try:
        category_id = int(category_id_raw)
    except Exception:
        category_id = None

    # imagem upload
    image_url = ""
    file = request.files.get("image_file")
    if file and file.filename:
        if not allowed_file(file.filename):
            flash("Formato de imagem não permitido. Use PNG/JPG/JPEG/WEBP.", "error")
            return redirect(url_for("admin"))
        try:
            image_url = process_and_save_image(file)
        except Exception as e:
            flash(f"Erro ao processar imagem: {e}", "error")
            return redirect(url_for("admin"))

    db = get_db()
    if using_postgres():
        db_execute(
            db,
            """
            INSERT INTO products
              (name, description, price_cents, image_url, category_id, is_active, is_promo, promo_price_cents)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (name, description, price_cents, image_url, category_id, is_active, is_promo, promo_price_cents),
        )
    else:
        db_execute(
            db,
            """
            INSERT INTO products
              (name, description, price_cents, image_url, category_id, is_active, is_promo, promo_price_cents)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (name, description, price_cents, image_url, category_id, is_active, is_promo, promo_price_cents),
        )
    db_commit(db)

    flash("Produto adicionado!", "success")
    return redirect(url_for("admin"))


@app.get("/admin/edit/<int:pid>")
@admin_required
def admin_edit(pid):
    db = get_db()
    if using_postgres():
        cur = db_execute(
            db,
            """
            SELECT p.*, c.name AS category_name
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id
            WHERE p.id=%s;
            """,
            (pid,),
        )
    else:
        cur = db_execute(
            db,
            """
            SELECT p.*, c.name AS category_name
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id
            WHERE p.id=?;
            """,
            (pid,),
        )
    row = db_fetchone(cur)
    if not row:
        flash("Produto não encontrado.", "error")
        return redirect(url_for("admin"))

    categories = fetch_categories(active_only=True)
    product = dict(row)

    # normaliza preços pra tela
    product["price"] = money_br(int(product.get("price_cents") or 0))
    if product.get("promo_price_cents") is not None:
        product["promo_price"] = money_br(int(product.get("promo_price_cents") or 0))
    else:
        product["promo_price"] = ""

    return render_template(
        "edit.html",
        app_name=APP_NAME,
        product=product,
        categories=categories,
        is_admin=is_admin_logged_in(),
    )


@app.post("/admin/edit/<int:pid>")
@admin_required
def admin_edit_post(pid):
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    category_id_raw = (request.form.get("category_id") or "").strip()

    price_raw = (request.form.get("price") or "0").strip()
    is_active = 1 if request.form.get("is_active") == "on" else 0

    is_promo = 1 if request.form.get("is_promo") == "on" else 0
    promo_price_raw = (request.form.get("promo_price") or "").strip()

    if not name:
        flash("Nome do produto é obrigatório.", "error")
        return redirect(url_for("admin_edit", pid=pid))

    try:
        price_cents = parse_price_to_cents(price_raw)
    except Exception:
        flash("Preço inválido.", "error")
        return redirect(url_for("admin_edit", pid=pid))

    promo_price_cents = None
    if is_promo:
        if promo_price_raw:
            try:
                promo_price_cents = parse_price_to_cents(promo_price_raw)
            except Exception:
                flash("Preço promocional inválido.", "error")
                return redirect(url_for("admin_edit", pid=pid))
        else:
            is_promo = 0
            promo_price_cents = None

    try:
        category_id = int(category_id_raw)
    except Exception:
        category_id = None

    db = get_db()

    # imagem upload (se mandar, substitui)
    image_url = None
    file = request.files.get("image_file")
    if file and file.filename:
        if not allowed_file(file.filename):
            flash("Formato de imagem não permitido. Use PNG/JPG/JPEG/WEBP.", "error")
            return redirect(url_for("admin_edit", pid=pid))
        try:
            image_url = process_and_save_image(file)
        except Exception as e:
            flash(f"Erro ao processar imagem: {e}", "error")
            return redirect(url_for("admin_edit", pid=pid))

    if image_url:
        # atualiza com imagem
        if using_postgres():
            db_execute(
                db,
                """
                UPDATE products
                SET name=%s, description=%s, price_cents=%s, image_url=%s,
                    category_id=%s, is_active=%s, is_promo=%s, promo_price_cents=%s
                WHERE id=%s;
                """,
                (name, description, price_cents, image_url, category_id, is_active, is_promo, promo_price_cents, pid),
            )
        else:
            db_execute(
                db,
                """
                UPDATE products
                SET name=?, description=?, price_cents=?, image_url=?,
                    category_id=?, is_active=?, is_promo=?, promo_price_cents=?
                WHERE id=?;
                """,
                (name, description, price_cents, image_url, category_id, is_active, is_promo, promo_price_cents, pid),
            )
    else:
        # atualiza sem mexer na imagem
        if using_postgres():
            db_execute(
                db,
                """
                UPDATE products
                SET name=%s, description=%s, price_cents=%s,
                    category_id=%s, is_active=%s, is_promo=%s, promo_price_cents=%s
                WHERE id=%s;
                """,
                (name, description, price_cents, category_id, is_active, is_promo, promo_price_cents, pid),
            )
        else:
            db_execute(
                db,
                """
                UPDATE products
                SET name=?, description=?, price_cents=?,
                    category_id=?, is_active=?, is_promo=?, promo_price_cents=?
                WHERE id=?;
                """,
                (name, description, price_cents, category_id, is_active, is_promo, promo_price_cents, pid),
            )

    db_commit(db)
    flash("Produto atualizado!", "success")
    return redirect(url_for("admin"))


@app.post("/admin/delete/<int:pid>")
@admin_required
def admin_delete(pid):
    db = get_db()
    if using_postgres():
        db_execute(db, "DELETE FROM products WHERE id=%s;", (pid,))
    else:
        db_execute(db, "DELETE FROM products WHERE id=?;", (pid,))
    db_commit(db)

    flash("Produto removido.", "success")
    return redirect(url_for("admin"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)