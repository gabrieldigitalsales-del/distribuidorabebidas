import os
import sqlite3
import re
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

APP_NAME = "Distribuidora de Bebidas Nova Cidade"
BASE_DIR = os.path.dirname(__file__)

# ✅ FIX: DB em caminho sempre gravável
# - Se você tiver Volume depois: defina DB_PATH=/data/database.sqlite3
# - Sem volume: usa /tmp (não dá erro 500, mas perde dados em redeploy)
DB_PATH = os.getenv("DB_PATH", "/tmp/database.sqlite3")

# ✅ garante que a pasta do DB existe
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Uploads
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
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

# Limite de upload (opcional)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024  # 12MB


# ===== DB =====
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def table_has_column(db, table: str, col: str) -> bool:
    rows = db.execute(f"PRAGMA table_info({table});").fetchall()
    return any(r["name"] == col for r in rows)


def init_db():
    db = get_db()

    # ===== settings (novo) =====
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )

    # categorias
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        """
    )

    # produtos (estrutura atual)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            price_cents INTEGER NOT NULL DEFAULT 0,
            image_url TEXT,
            category_id INTEGER,
            category TEXT,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    db.commit()

    # migração promo
    if not table_has_column(db, "products", "is_promo"):
        db.execute("ALTER TABLE products ADD COLUMN is_promo INTEGER NOT NULL DEFAULT 0;")
    if not table_has_column(db, "products", "promo_price_cents"):
        db.execute("ALTER TABLE products ADD COLUMN promo_price_cents INTEGER;")
    db.commit()

    # seed settings whatsapp (se não existir)
    cur = db.execute("SELECT value FROM settings WHERE key='whatsapp_number';").fetchone()
    if cur is None:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?);",
            ("whatsapp_number", STORE_WHATSAPP_NUMBER),
        )
        db.commit()

    # seed categorias
    ccur = db.execute("SELECT COUNT(*) as c FROM categories;")
    if ccur.fetchone()["c"] == 0:
        base_cats = [("Cervejas", 1), ("Refrigerantes", 1), ("Águas", 1), ("Outros", 1)]
        db.executemany("INSERT OR IGNORE INTO categories (name, is_active) VALUES (?, ?);", base_cats)
        db.commit()


@app.before_request
def _ensure_db():
    init_db()


# ===== AUTH =====
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


# ===== SETTINGS =====
def get_setting(key: str, default: str = "") -> str:
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key=?;", (key,)).fetchone()
    if not row or row["value"] is None:
        return default
    return str(row["value"])


def set_setting(key: str, value: str) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value;
        """,
        (key, value),
    )
    db.commit()


def normalize_whatsapp(raw: str) -> str:
    digits = re.sub(r"\D+", "", raw or "")
    if not digits:
        return ""
    if len(digits) == 11:
        digits = "55" + digits
    return digits


# ===== UTILS =====
def money_br(price_cents: int) -> str:
    v = (price_cents or 0) / 100.0
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fetch_categories(active_only=True):
    db = get_db()
    if active_only:
        rows = db.execute("SELECT * FROM categories WHERE is_active=1 ORDER BY name;").fetchall()
    else:
        rows = db.execute("SELECT * FROM categories ORDER BY is_active DESC, name;").fetchall()
    return [dict(id=r["id"], name=r["name"], is_active=bool(r["is_active"])) for r in rows]


def fetch_products(active_only=True):
    db = get_db()
    if active_only:
        rows = db.execute(
            """
            SELECT p.*, c.name AS category_name
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id
            WHERE p.is_active = 1
            ORDER BY COALESCE(c.name, p.category, 'Outros'), p.name;
            """
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT p.*, c.name AS category_name
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id
            ORDER BY p.is_active DESC, COALESCE(c.name, p.category, 'Outros'), p.name;
            """
        ).fetchall()

    products = []
    for r in rows:
        cat = r["category_name"] or r["category"] or "Outros"

        base_cents = int(r["price_cents"] or 0)
        promo_cents = int(r["promo_price_cents"] or 0) if r["promo_price_cents"] is not None else 0

        is_promo = bool(r["is_promo"]) and promo_cents > 0
        effective_cents = promo_cents if is_promo else base_cents

        products.append(
            dict(
                id=r["id"],
                name=r["name"],
                description=r["description"] or "",
                price_cents=base_cents,
                price=money_br(base_cents),
                promo_price_cents=(promo_cents if promo_cents > 0 else None),
                promo_price=(money_br(promo_cents) if promo_cents > 0 else ""),
                is_promo=is_promo,
                effective_price_cents=effective_cents,
                effective_price=money_br(effective_cents),
                image_url=r["image_url"] or "",
                category=cat,
                category_id=r["category_id"],
                is_active=bool(r["is_active"]),
            )
        )
    return products


# ===== ROTAS (CATÁLOGO / CHECKOUT) =====
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