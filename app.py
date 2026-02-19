import os
import sqlite3
import re
from urllib.parse import quote
from functools import wraps
from datetime import datetime
from io import BytesIO

from flask import (
    Flask, g, render_template, request,
    redirect, url_for, flash, jsonify, session
)

from werkzeug.utils import secure_filename

# Pillow
try:
    from PIL import Image, ImageOps
except:
    Image = None
    ImageOps = None


APP_NAME = "Distribuidora de Bebidas Nova Cidade"

# ===== BANCO (NUNCA MAIS DÁ ERRO 500) =====
DB_PATH=/tmp/database.sqlite3
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Upload
BASE_DIR = os.path.dirname(__file__)
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "123456")

STORE_WHATSAPP_NUMBER = os.getenv("STORE_WHATSAPP_NUMBER", "5531999999999")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


# ===================== DB =====================
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    db = get_db()

    db.execute("""
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        is_active INTEGER DEFAULT 1
    )
    """)

    db.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        description TEXT,
        price_cents INTEGER,
        promo_price_cents INTEGER,
        is_promo INTEGER DEFAULT 0,
        image_url TEXT,
        category TEXT,
        is_active INTEGER DEFAULT 1
    )
    """)

    db.commit()

@app.before_request
def ensure_db():
    init_db()


# ===================== UTILS =====================
def money_br(cents):
    return f"R$ {(cents or 0)/100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def parse_price(raw):
    raw = raw.replace("R$", "").strip().replace(".", "").replace(",", ".")
    return int(float(raw) * 100)

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def process_image(file):
    if not Image:
        return ""

    img = Image.open(BytesIO(file.read()))
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")

    img = img.resize((800, 800))

    filename = secure_filename(file.filename)
    filename = filename + "_" + datetime.now().strftime("%Y%m%d%H%M%S") + ".webp"

    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    img.save(path, "WEBP", quality=85)

    return f"/static/uploads/{filename}"


# ===================== AUTH =====================
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


# ===================== ROTAS =====================
@app.route("/")
def index():
    db = get_db()
    products = db.execute("SELECT * FROM products WHERE is_active=1").fetchall()

    grouped = {}
    for p in products:
        cat = p["category"] or "Outros"
        grouped.setdefault(cat, []).append({
            "id": p["id"],
            "name": p["name"],
            "description": p["description"],
            "price": money_br(p["price_cents"]),
            "promo_price": money_br(p["promo_price_cents"]) if p["promo_price_cents"] else "",
            "is_promo": bool(p["is_promo"]),
            "effective_price_cents": p["promo_price_cents"] if p["is_promo"] else p["price_cents"],
            "image_url": p["image_url"]
        })

    return render_template("index.html", grouped=grouped, app_name=APP_NAME)


@app.route("/admin")
@admin_required
def admin():
    db = get_db()
    products = db.execute("SELECT * FROM products").fetchall()
    return render_template("admin.html", products=products, app_name=APP_NAME)


@app.route("/admin/add", methods=["POST"])
@admin_required
def add_product():
    db = get_db()

    name = request.form.get("name")
    desc = request.form.get("description")
    category = request.form.get("category")
    price = parse_price(request.form.get("price"))
    is_promo = 1 if request.form.get("is_promo") == "on" else 0
    promo_price = parse_price(request.form.get("promo_price")) if request.form.get("promo_price") else None

    image = request.files.get("image")
    image_url = process_image(image) if image and allowed_file(image.filename) else ""

    db.execute("""
    INSERT INTO products (name, description, price_cents, promo_price_cents, is_promo, image_url, category)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (name, desc, price, promo_price, is_promo, image_url, category))

    db.commit()
    return redirect(url_for("admin"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == ADMIN_USER and request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin"))
        flash("Login inválido")

    return render_template("login.html", app_name=APP_NAME)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/api/whatsapp", methods=["POST"])
def whatsapp():
    data = request.json
    items = data.get("items", [])
    total = 0
    lines = []

    for item in items:
        subtotal = item["qty"] * item["price_cents"]
        total += subtotal
        lines.append(f"{item['qty']}x {item['name']} - {money_br(subtotal)}")

    message = f"Pedido:\n\n" + "\n".join(lines) + f"\n\nTotal: {money_br(total)}"
    link = f"https://wa.me/{STORE_WHATSAPP_NUMBER}?text={quote(message)}"

    return jsonify({"link": link})


if __name__ == "__main__":
    app.run(debug=True)