import os
import re
import sqlite3
import json
from urllib.parse import quote
from functools import wraps
from datetime import datetime
from io import BytesIO
from typing import Tuple

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
    Response,
    abort,
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
except Exception:
    psycopg = None


APP_NAME = "Distribuidora de Bebidas Nova Cidade"
BASE_DIR = os.path.dirname(__file__)

# Se existir DATABASE_URL -> Postgres
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

# Normaliza caso venha postgres://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://") :]

# SQLite fallback (se não tiver Postgres)
DB_PATH = os.path.join(BASE_DIR, "database.sqlite3")

# Uploads locais (apenas para dev; em produção no Railway sem Volume isso SOME)
DEFAULT_UPLOAD = os.path.join(BASE_DIR, "static", "uploads")
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", DEFAULT_UPLOAD)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

STORE_WHATSAPP_NUMBER = os.getenv("STORE_WHATSAPP_NUMBER", "5531999999999")

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "NovaCidade@2026")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-me")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024  # 12MB


# =========================
# DB helpers
# =========================
def using_postgres() -> bool:
    return bool(DATABASE_URL)


def get_db():
    if "db" not in g:
        if using_postgres():
            if psycopg is None:
                raise RuntimeError("psycopg não instalado. Adicione psycopg[binary]==3.2.6 no requirements.txt")
            g.db = psycopg.connect(DATABASE_URL)
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


def db_commit(db):
    try:
        db.commit()
    except Exception:
        pass


def db_execute(db, sql: str, params=()):
    if using_postgres():
        cur = db.cursor()
        cur.execute(sql, params)
        return cur
    return db.execute(sql, params)


def db_fetchone(cur):
    return cur.fetchone()


def db_fetchall(cur):
    return cur.fetchall()


def sqlite_column_exists(db, table: str, col: str) -> bool:
    try:
        rows = db_execute(db, f"PRAGMA table_info({table});").fetchall()
        for r in rows:
            # r: (cid, name, type, notnull, dflt_value, pk)
            if str(r[1]).lower() == col.lower():
                return True
        return False
    except Exception:
        return False


def ensure_image_columns():
    """
    Garante colunas para armazenar imagem no banco:
    - products.image_blob (bytes do webp)
    - products.image_mime (ex: image/webp)
    - products.image_name (nome original)
    """
    db = get_db()
    if using_postgres():
        db_execute(db, "ALTER TABLE products ADD COLUMN IF NOT EXISTS image_blob BYTEA;")
        db_execute(db, "ALTER TABLE products ADD COLUMN IF NOT EXISTS image_mime TEXT;")
        db_execute(db, "ALTER TABLE products ADD COLUMN IF NOT EXISTS image_name TEXT;")
        db_commit(db)
        return

    # SQLite: precisa checar e alterar
    if not sqlite_column_exists(db, "products", "image_blob"):
        db_execute(db, "ALTER TABLE products ADD COLUMN image_blob BLOB;")
    if not sqlite_column_exists(db, "products", "image_mime"):
        db_execute(db, "ALTER TABLE products ADD COLUMN image_mime TEXT;")
    if not sqlite_column_exists(db, "products", "image_name"):
        db_execute(db, "ALTER TABLE products ADD COLUMN image_name TEXT;")
    db_commit(db)


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

        ensure_image_columns()

        # seed whatsapp
        cur = db_execute(db, "SELECT value FROM settings WHERE key=%s;", ("whatsapp_number",))
        row = db_fetchone(cur)
        if row is None:
            db_execute(db, "INSERT INTO settings (key, value) VALUES (%s, %s);", ("whatsapp_number", STORE_WHATSAPP_NUMBER))
            db_commit(db)

        # ===== SEED FRETE (NOVO) =====
        _seed_setting_if_missing("freight_default_cents", "0")
        _seed_setting_if_missing("freight_free_over_cents", "0")
        _seed_setting_if_missing("freight_map_json", "{}")

        # seed categorias
        cur = db_execute(db, "SELECT COUNT(*) FROM categories;")
        c = db_fetchone(cur)[0]
        if int(c) == 0:
            base_cats = [("Cervejas", 1), ("Refrigerantes", 1), ("Águas", 1), ("Outros", 1)]
            for name, active in base_cats:
                db_execute(
                    db,
                    "INSERT INTO categories (name, is_active) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING;",
                    (name, active),
                )
            db_commit(db)
        return

    # SQLITE
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

    ensure_image_columns()

    row = db_execute(db, "SELECT value FROM settings WHERE key=?;", ("whatsapp_number",)).fetchone()
    if row is None:
        db_execute(db, "INSERT INTO settings (key, value) VALUES (?, ?);", ("whatsapp_number", STORE_WHATSAPP_NUMBER))
        db_commit(db)

    # ===== SEED FRETE (NOVO) =====
    _seed_setting_if_missing("freight_default_cents", "0")
    _seed_setting_if_missing("freight_free_over_cents", "0")
    _seed_setting_if_missing("freight_map_json", "{}")

    cur = db_execute(db, "SELECT COUNT(*) as c FROM categories;")
    c = db_fetchone(cur)["c"]
    if int(c) == 0:
        base_cats = [("Cervejas", 1), ("Refrigerantes", 1), ("Águas", 1), ("Outros", 1)]
        for name, active in base_cats:
            db_execute(db, "INSERT OR IGNORE INTO categories (name, is_active) VALUES (?, ?);", (name, active))
        db_commit(db)


def _seed_setting_if_missing(key: str, value: str):
    db = get_db()
    if using_postgres():
        cur = db_execute(db, "SELECT value FROM settings WHERE key=%s;", (key,))
        row = db_fetchone(cur)
        if row is None:
            db_execute(db, "INSERT INTO settings (key, value) VALUES (%s, %s);", (key, value))
            db_commit(db)
    else:
        row = db_execute(db, "SELECT value FROM settings WHERE key=?;", (key,)).fetchone()
        if row is None:
            db_execute(db, "INSERT INTO settings (key, value) VALUES (?, ?);", (key, value))
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
        row = db_fetchone(cur)
        if not row or row[0] is None:
            return default
        return str(row[0])
    row = db_execute(db, "SELECT value FROM settings WHERE key=?;", (key,)).fetchone()
    if not row or row["value"] is None:
        return default
    return str(row["value"])


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


def normalize_bairro(raw: str) -> str:
    s = (raw or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s\-]", "", s)  # tira pontuação
    return s


def load_freight_map() -> dict:
    """
    settings.freight_map_json: {"centro": 600, "nova cidade": 800}
    """
    try:
        data = json.loads(get_setting("freight_map_json", "{}") or "{}")
        if isinstance(data, dict):
            out = {}
            for k, v in data.items():
                nk = normalize_bairro(k)
                try:
                    out[nk] = int(v)
                except Exception:
                    pass
            return out
    except Exception:
        pass
    return {}


def compute_freight_cents(bairro: str, cart_total_cents: int) -> int:
    """
    Regras:
    1) Se cart_total >= free_over -> frete 0
    2) Se bairro existir no mapa -> usa valor do bairro
    3) Senão -> usa frete padrão
    """
    try:
        free_over = int(get_setting("freight_free_over_cents", "0") or "0")
    except Exception:
        free_over = 0

    if free_over > 0 and int(cart_total_cents or 0) >= free_over:
        return 0

    fmap = load_freight_map()
    nb = normalize_bairro(bairro)
    if nb and nb in fmap:
        return int(fmap[nb])

    try:
        default = int(get_setting("freight_default_cents", "0") or "0")
    except Exception:
        default = 0
    return max(0, int(default))


def fetch_categories(active_only=True):
    db = get_db()
    if using_postgres():
        if active_only:
            cur = db_execute(db, "SELECT id, name, is_active FROM categories WHERE is_active=1 ORDER BY name;")
        else:
            cur = db_execute(db, "SELECT id, name, is_active FROM categories ORDER BY is_active DESC, name;")
        rows = db_fetchall(cur)
        return [dict(id=r[0], name=r[1], is_active=bool(r[2])) for r in rows]

    if active_only:
        rows = db_execute(db, "SELECT * FROM categories WHERE is_active=1 ORDER BY name;").fetchall()
    else:
        rows = db_execute(db, "SELECT * FROM categories ORDER BY is_active DESC, name;").fetchall()
    return [dict(id=r["id"], name=r["name"], is_active=bool(r["is_active"])) for r in rows]


def fetch_products(active_only=True):
    db = get_db()

    if using_postgres():
        where = "WHERE p.is_active = 1" if active_only else ""
        order = (
            "ORDER BY p.is_active DESC, COALESCE(c.name, p.category, 'Outros'), p.name;"
            if not active_only
            else "ORDER BY COALESCE(c.name, p.category, 'Outros'), p.name;"
        )
        cur = db_execute(
            db,
            f"""
            SELECT p.id, p.name, p.description, p.price_cents, p.promo_price_cents, p.is_promo,
                   p.image_url, p.category, p.category_id, p.is_active,
                   c.name AS category_name,
                   p.image_blob
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id
            {where}
            {order}
            """,
        )

        rows = db_fetchall(cur)
        out = []
        for r in rows:
            pid, name, desc, price_cents, promo_price_cents, is_promo, image_url, category, category_id, is_active, category_name, image_blob = r
            cat = category_name or category or "Outros"
            base_cents = int(price_cents or 0)
            promo_cents = int(promo_price_cents or 0) if promo_price_cents is not None else 0
            is_promo_ok = bool(is_promo) and promo_cents > 0
            effective_cents = promo_cents if is_promo_ok else base_cents

            final_image_url = image_url or ""
            if image_blob is not None:
                final_image_url = f"/img/{pid}.webp"

            out.append(
                dict(
                    id=pid,
                    name=name,
                    description=desc or "",
                    price_cents=base_cents,
                    price=money_br(base_cents),
                    promo_price_cents=(promo_cents if promo_cents > 0 else None),
                    promo_price=(money_br(promo_cents) if promo_cents > 0 else ""),
                    is_promo=is_promo_ok,
                    effective_price_cents=effective_cents,
                    effective_price=money_br(effective_cents),
                    image_url=final_image_url,
                    category=cat,
                    category_id=category_id,
                    is_active=bool(is_active),
                )
            )
        return out

    where = "WHERE p.is_active = 1" if active_only else ""
    order = (
        "ORDER BY p.is_active DESC, COALESCE(c.name, p.category, 'Outros'), p.name;"
        if not active_only
        else "ORDER BY COALESCE(c.name, p.category, 'Outros'), p.name;"
    )
    rows = db_execute(
        db,
        f"""
        SELECT p.*, c.name AS category_name
        FROM products p
        LEFT JOIN categories c ON c.id = p.category_id
        {where}
        {order}
        """
    ).fetchall()

    out = []
    for r in rows:
        cat = r["category_name"] or r["category"] or "Outros"
        base_cents = int(r["price_cents"] or 0)
        promo_cents = int(r["promo_price_cents"] or 0) if r["promo_price_cents"] is not None else 0
        is_promo_ok = bool(r["is_promo"]) and promo_cents > 0
        effective_cents = promo_cents if is_promo_ok else base_cents

        final_image_url = r["image_url"] or ""
        try:
            if r["image_blob"] is not None:
                final_image_url = f"/img/{r['id']}.webp"
        except Exception:
            pass

        out.append(
            dict(
                id=r["id"],
                name=r["name"],
                description=r["description"] or "",
                price_cents=base_cents,
                price=money_br(base_cents),
                promo_price_cents=(promo_cents if promo_cents > 0 else None),
                promo_price=(money_br(promo_cents) if promo_cents > 0 else ""),
                is_promo=is_promo_ok,
                effective_price_cents=effective_cents,
                effective_price=money_br(effective_cents),
                image_url=final_image_url,
                category=cat,
                category_id=r["category_id"],
                is_active=bool(r["is_active"]),
            )
        )
    return out


def process_image_to_webp_bytes(file_storage) -> Tuple[bytes, str, str]:
    """
    Retorna: (webp_bytes, mime, original_name)
    """
    if not Image:
        raise RuntimeError("Pillow não está instalado. Rode: pip install pillow")

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

    out = BytesIO()
    img.save(out, "WEBP", quality=82, method=6)
    return out.getvalue(), "image/webp", (file_storage.filename or "imagem")


def save_image_to_db(product_id: int, webp_bytes: bytes, mime: str, original_name: str):
    db = get_db()
    if using_postgres():
        db_execute(
            db,
            """
            UPDATE products
            SET image_blob=%s, image_mime=%s, image_name=%s, image_url=%s
            WHERE id=%s;
            """,
            (webp_bytes, mime, original_name, f"/img/{product_id}.webp", product_id),
        )
    else:
        db_execute(
            db,
            """
            UPDATE products
            SET image_blob=?, image_mime=?, image_name=?, image_url=?
            WHERE id=?;
            """,
            (webp_bytes, mime, original_name, f"/img/{product_id}.webp", product_id),
        )
    db_commit(db)


# =========================
# SERVIR IMAGEM DO BANCO
# =========================
@app.get("/img/<int:pid>.webp")
def product_image(pid: int):
    db = get_db()
    if using_postgres():
        cur = db_execute(db, "SELECT image_blob, image_mime FROM products WHERE id=%s;", (pid,))
        row = db_fetchone(cur)
        if not row:
            abort(404)
        blob, mime = row[0], row[1]
    else:
        row = db_execute(db, "SELECT image_blob, image_mime FROM products WHERE id=?;", (pid,)).fetchone()
        if not row:
            abort(404)
        blob, mime = row["image_blob"], row["image_mime"]

    if blob is None:
        abort(404)

    return Response(blob, mimetype=(mime or "image/webp"), headers={"Cache-Control": "public, max-age=86400"})


# =========================
# ROTAS (CATÁLOGO / CHECKOUT)
# =========================
@app.get("/")
def index():
    products = fetch_products(active_only=True)
    grouped = {}
    for p in products:
        grouped.setdefault(p["category"], []).append(p)
    return render_template("index.html", app_name=APP_NAME, grouped=grouped, is_admin=is_admin_logged_in())


@app.get("/checkout")
def checkout():
    store_number = get_setting("whatsapp_number", STORE_WHATSAPP_NUMBER)

    # envia pro template as regras atuais de frete
    try:
        freight_default_cents = int(get_setting("freight_default_cents", "0") or "0")
    except Exception:
        freight_default_cents = 0
    try:
        freight_free_over_cents = int(get_setting("freight_free_over_cents", "0") or "0")
    except Exception:
        freight_free_over_cents = 0

    # lista de bairros (pra sugerir no input)
    fmap = load_freight_map()
    bairros = sorted({k.title() for k in fmap.keys() if k})

    return render_template(
        "checkout.html",
        app_name=APP_NAME,
        store_whatsapp=store_number,
        is_admin=is_admin_logged_in(),
        freight_default_cents=freight_default_cents,
        freight_free_over_cents=freight_free_over_cents,
        freight_bairros=bairros,
    )


@app.post("/api/freight_quote")
def api_freight_quote():
    data = request.get_json(force=True)
    bairro = (data.get("bairro") or "").strip()
    try:
        cart_total_cents = int(data.get("cart_total_cents") or 0)
    except Exception:
        cart_total_cents = 0

    freight_cents = compute_freight_cents(bairro, cart_total_cents)
    return jsonify(
        {
            "freight_cents": freight_cents,
            "freight_text": money_br(freight_cents),
        }
    )


@app.post("/api/whatsapp_link")
def api_whatsapp_link():
    data = request.get_json(force=True)

    customer_name = (data.get("customer_name") or "").strip() or "Não informado"
    address = (data.get("address") or "").strip()
    bairro = (data.get("bairro") or "").strip()
    phone = (data.get("phone") or "").strip() or "Não informado"
    payment_method = (data.get("payment_method") or "").strip() or "Não informado"
    delivery_mode = (data.get("delivery_mode") or "").strip() or "Não informado"
    change_for = (data.get("change_for") or "").strip()
    items = data.get("items") or []

    if not items:
        return jsonify({"error": "Carrinho vazio."}), 400

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

    retirada_local = delivery_mode.lower() == "retirada no local"

    if retirada_local:
        freight_cents = 0
        address_text = "Retirada no local"
        bairro_text = ""
    else:
        address_text = address or "Não informado"
        bairro_text = bairro
        freight_cents = compute_freight_cents(bairro_text, total_cents)

    grand_total = total_cents + freight_cents

    pay_line = payment_method
    if payment_method.lower() == "dinheiro" and change_for:
        pay_line += f" (troco para {change_for})"

    bairro_line = f"\n🏘️ *Bairro:* {bairro_text}" if bairro_text else ""

    msg = (
        f"🛒 *Pedido — {APP_NAME}*\n\n"
        f"👤 *Nome:* {customer_name}\n"
        f"🚚 *Entrega:* {delivery_mode}\n"
        f"📍 *Endereço:* {address_text}"
        f"{bairro_line}\n"
        f"📞 *WhatsApp/Telefone:* {phone}\n"
        f"💳 *Pagamento:* {pay_line}\n\n"
        f"📦 *Itens:*\n" + "\n".join(lines) + "\n\n"
        f"🧾 *Subtotal:* {money_br(total_cents)}\n"
        f"🛵 *Frete:* {money_br(freight_cents)}\n"
        f"💰 *Total:* {money_br(grand_total)}\n\n"
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

    # frete settings
    freight_default_cents = int(get_setting("freight_default_cents", "0") or "0")
    freight_free_over_cents = int(get_setting("freight_free_over_cents", "0") or "0")
    freight_map_json = get_setting("freight_map_json", "{}") or "{}"

    return render_template(
        "admin.html",
        app_name=APP_NAME,
        products=products,
        categories=categories,
        store_whatsapp=store_number,
        is_admin=is_admin_logged_in(),
        freight_default=money_br(freight_default_cents),
        freight_free_over=money_br(freight_free_over_cents),
        freight_map_json=freight_map_json,
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


@app.post("/admin/settings/freight")
@admin_required
def admin_update_freight():
    """
    Campos:
      - freight_default (ex: 8,00)
      - freight_free_over (ex: 60,00)
      - freight_lines (linhas "Bairro = 8,00")
    """
    freight_default = (request.form.get("freight_default") or "").strip()
    freight_free_over = (request.form.get("freight_free_over") or "").strip()
    freight_lines = (request.form.get("freight_lines") or "").strip()

    try:
        default_cents = parse_price_to_cents(freight_default or "0")
    except Exception:
        default_cents = 0

    try:
        free_over_cents = parse_price_to_cents(freight_free_over or "0")
    except Exception:
        free_over_cents = 0

    fmap = {}
    # aceita linhas tipo:
    # Centro = 6,00
    # Nova Cidade=8,00
    for line in freight_lines.splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            continue
        left, right = line.split("=", 1)
        bairro = normalize_bairro(left.strip())
        val = right.strip()
        if not bairro:
            continue
        try:
            cents = parse_price_to_cents(val)
        except Exception:
            continue
        fmap[bairro] = int(cents)

    set_setting("freight_default_cents", str(int(default_cents)))
    set_setting("freight_free_over_cents", str(int(free_over_cents)))
    set_setting("freight_map_json", json.dumps(fmap, ensure_ascii=False))

    flash("Regras de frete atualizadas!", "success")
    return redirect(url_for("admin"))


# ---- CATEGORIAS ----
@app.get("/admin/categories")
@admin_required
def admin_categories():
    categories = fetch_categories(active_only=False)
    return render_template("categories.html", app_name=APP_NAME, categories=categories, is_admin=is_admin_logged_in())


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
            db_execute(db, "INSERT INTO categories (name, is_active) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING;", (name, is_active))
        else:
            db_execute(db, "INSERT OR IGNORE INTO categories (name, is_active) VALUES (?, ?);", (name, is_active))
        db_commit(db)
        flash("Categoria adicionada!", "success")
    except Exception:
        flash("Não foi possível adicionar a categoria.", "error")

    return redirect(url_for("admin_categories"))


@app.post("/admin/categories/toggle/<int:cid>")
@admin_required
def admin_categories_toggle(cid):
    db = get_db()
    if using_postgres():
        cur = db_execute(db, "SELECT is_active FROM categories WHERE id=%s;", (cid,))
        row = db_fetchone(cur)
        if not row:
            flash("Categoria não encontrada.", "error")
            return redirect(url_for("admin_categories"))
        new_val = 0 if int(row[0]) == 1 else 1
        db_execute(db, "UPDATE categories SET is_active=%s WHERE id=%s;", (new_val, cid))
    else:
        row = db_execute(db, "SELECT is_active FROM categories WHERE id=?;", (cid,)).fetchone()
        if not row:
            flash("Categoria não encontrada.", "error")
            return redirect(url_for("admin_categories"))
        new_val = 0 if int(row["is_active"]) == 1 else 1
        db_execute(db, "UPDATE categories SET is_active=? WHERE id=?;", (new_val, cid))

    db_commit(db)
    flash("Status da categoria atualizado!", "success")
    return redirect(url_for("admin_categories"))


@app.post("/admin/categories/delete/<int:cid>")
@admin_required
def admin_categories_delete(cid):
    db = get_db()
    try:
        if using_postgres():
            db_execute(db, "UPDATE products SET category_id=NULL WHERE category_id=%s;", (cid,))
            db_execute(db, "DELETE FROM categories WHERE id=%s;", (cid,))
        else:
            db_execute(db, "UPDATE products SET category_id=NULL WHERE category_id=?;", (cid,))
            db_execute(db, "DELETE FROM categories WHERE id=?;", (cid,))
        db_commit(db)
        flash("Categoria removida.", "success")
    except Exception:
        flash("Não foi possível remover a categoria.", "error")
    return redirect(url_for("admin_categories"))


# ---- PRODUTOS ----
@app.post("/admin/add")
@admin_required
def admin_add():
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    category_id_raw = (request.form.get("category_id") or "").strip()
    price_raw = request.form.get("price") or "0"
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
    if is_promo and promo_price_raw:
        try:
            promo_price_cents = parse_price_to_cents(promo_price_raw)
        except Exception:
            flash("Preço promocional inválido.", "error")
            return redirect(url_for("admin"))

    category_id = None
    try:
        category_id = int(category_id_raw)
    except Exception:
        category_id = None

    # Primeiro cria o produto SEM imagem (pra ter o ID)
    db = get_db()
    if using_postgres():
        cur = db_execute(
            db,
            """
            INSERT INTO products (name, description, price_cents, image_url, category_id, is_active, is_promo, promo_price_cents)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id;
            """,
            (name, description, price_cents, "", category_id, is_active, is_promo, promo_price_cents),
        )
        pid = db_fetchone(cur)[0]
    else:
        db_execute(
            db,
            """
            INSERT INTO products (name, description, price_cents, image_url, category_id, is_active, is_promo, promo_price_cents)
            VALUES (?,?,?,?,?,?,?,?);
            """,
            (name, description, price_cents, "", category_id, is_active, is_promo, promo_price_cents),
        )
        pid = int(db_execute(db, "SELECT last_insert_rowid();").fetchone()[0])

    db_commit(db)

    # Agora processa e salva imagem NO BANCO (se enviada)
    file = request.files.get("image_file")
    if file and file.filename:
        try:
            webp_bytes, mime, original_name = process_image_to_webp_bytes(file)
            save_image_to_db(pid, webp_bytes, mime, original_name)
        except Exception as e:
            flash(f"Produto criado, mas falha ao processar imagem: {e}", "error")
            return redirect(url_for("admin"))

    flash("Produto adicionado!", "success")
    return redirect(url_for("admin"))


@app.get("/admin/edit/<int:pid>")
@admin_required
def admin_edit(pid):
    db = get_db()
    categories = fetch_categories(active_only=True)

    if using_postgres():
        cur = db_execute(
            db,
            """
            SELECT p.id, p.name, p.description, p.price_cents, p.image_url,
                   p.category_id, p.is_active, p.is_promo, p.promo_price_cents,
                   p.image_blob
            FROM products p WHERE p.id=%s;
            """,
            (pid,),
        )
        row = db_fetchone(cur)
        if not row:
            flash("Produto não encontrado.", "error")
            return redirect(url_for("admin"))
        image_url = row[4] or ""
        if row[9] is not None:
            image_url = f"/img/{pid}.webp"
        p = dict(
            id=row[0],
            name=row[1],
            description=row[2] or "",
            price_cents=int(row[3] or 0),
            image_url=image_url,
            category_id=row[5],
            is_active=bool(row[6]),
            is_promo=bool(row[7]) and (row[8] is not None and int(row[8]) > 0),
            promo_price_cents=(int(row[8]) if row[8] is not None else None),
        )
    else:
        row = db_execute(db, "SELECT * FROM products WHERE id=?;", (pid,)).fetchone()
        if not row:
            flash("Produto não encontrado.", "error")
            return redirect(url_for("admin"))
        promo = int(row["promo_price_cents"] or 0) if row["promo_price_cents"] is not None else 0
        image_url = row["image_url"] or ""
        try:
            if row["image_blob"] is not None:
                image_url = f"/img/{pid}.webp"
        except Exception:
            pass
        p = dict(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            price_cents=int(row["price_cents"] or 0),
            image_url=image_url,
            category_id=row["category_id"],
            is_active=bool(row["is_active"]),
            is_promo=bool(row["is_promo"]) and promo > 0,
            promo_price_cents=(promo if promo > 0 else None),
        )

    return render_template("edit.html", app_name=APP_NAME, p=p, categories=categories, is_admin=is_admin_logged_in())


@app.post("/admin/edit/<int:pid>")
@admin_required
def admin_edit_post(pid):
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    category_id_raw = (request.form.get("category_id") or "").strip()
    price_raw = request.form.get("price") or "0"
    is_active = 1 if request.form.get("is_active") == "on" else 0

    is_promo = 1 if request.form.get("is_promo") == "on" else 0
    promo_price_raw = (request.form.get("promo_price") or "").strip()

    if not name:
        flash("Nome é obrigatório.", "error")
        return redirect(url_for("admin_edit", pid=pid))

    try:
        price_cents = parse_price_to_cents(price_raw)
    except Exception:
        flash("Preço inválido.", "error")
        return redirect(url_for("admin_edit", pid=pid))

    promo_price_cents = None
    if is_promo and promo_price_raw:
        try:
            promo_price_cents = parse_price_to_cents(promo_price_raw)
        except Exception:
            flash("Preço promocional inválido.", "error")
            return redirect(url_for("admin_edit", pid=pid))

    category_id = None
    try:
        category_id = int(category_id_raw)
    except Exception:
        category_id = None

    db = get_db()
    if using_postgres():
        db_execute(
            db,
            """
            UPDATE products
            SET name=%s, description=%s, price_cents=%s, category_id=%s,
                is_active=%s, is_promo=%s, promo_price_cents=%s
            WHERE id=%s;
            """,
            (name, description, price_cents, category_id, is_active, is_promo, promo_price_cents, pid),
        )
    else:
        db_execute(
            db,
            """
            UPDATE products
            SET name=?, description=?, price_cents=?, category_id=?,
                is_active=?, is_promo=?, promo_price_cents=?
            WHERE id=?;
            """,
            (name, description, price_cents, category_id, is_active, is_promo, promo_price_cents, pid),
        )
    db_commit(db)

    file = request.files.get("image_file")
    if file and file.filename:
        try:
            webp_bytes, mime, original_name = process_image_to_webp_bytes(file)
            save_image_to_db(pid, webp_bytes, mime, original_name)
        except Exception as e:
            flash(f"Produto atualizado, mas falha ao processar imagem: {e}", "error")
            return redirect(url_for("admin_edit", pid=pid))

    flash("Produto atualizado!", "success")
    return redirect(url_for("admin"))


@app.post("/admin/delete/<int:pid>")
@admin_required
def admin_delete(pid):
    db = get_db()
    try:
        if using_postgres():
            db_execute(db, "DELETE FROM products WHERE id=%s;", (pid,))
        else:
            db_execute(db, "DELETE FROM products WHERE id=?;", (pid,))
        db_commit(db)
        flash("Produto removido.", "success")
    except Exception:
        flash("Não foi possível remover.", "error")
    return redirect(url_for("admin"))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))