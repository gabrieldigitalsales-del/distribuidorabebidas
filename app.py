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

# ✅ IMPORTANTE: permite usar SQLite em volume (Railway/Render) sem perder dados
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "database.sqlite3"))

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


# ===== UTIL (PREÇO) =====
# ✅ PRECISA FICAR ANTES DO init_db() (isso é o que estava te dando erro 500)
def parse_price_to_cents(raw: str) -> int:
    """
    Aceita:
      - "15" => 15,00
      - "15,5" => 15,50
      - "15.5" => 15,50
      - "R$ 15,00" => 15,00
      - "1.234,56" => 1234,56
    """
    s = (raw or "0").strip()
    s = s.replace("R$", "").replace("r$", "").strip()

    # só inteiro (ex: 15)
    if s.isdigit():
        return int(s) * 100

    # se tiver vírgula, ela é decimal (pt-BR)
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    # senão, pode ter ponto decimal (15.5) ou lixo -> float resolve

    value = float(s)
    return int(round(value * 100))


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
    try:
        rows = db.execute(f"PRAGMA table_info({table});").fetchall()
    except sqlite3.OperationalError:
        return False
    return any(r["name"] == col for r in rows)


def init_db():
    db = get_db()

    # ===== settings =====
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )

    # ===== categorias =====
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        """
    )

    # ===== produtos (base) =====
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            price_cents INTEGER NOT NULL DEFAULT 0,
            image_url TEXT,
            category_id INTEGER,
            category TEXT
        );
        """
    )
    db.commit()

    # ===== migrações (para não quebrar import antigo / banco antigo) =====
    if not table_has_column(db, "products", "is_active"):
        db.execute("ALTER TABLE products ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;")
    if not table_has_column(db, "products", "is_promo"):
        db.execute("ALTER TABLE products ADD COLUMN is_promo INTEGER NOT NULL DEFAULT 0;")
    if not table_has_column(db, "products", "promo_price_cents"):
        db.execute("ALTER TABLE products ADD COLUMN promo_price_cents INTEGER;")
    if not table_has_column(db, "products", "stock_qty"):
        db.execute("ALTER TABLE products ADD COLUMN stock_qty INTEGER NOT NULL DEFAULT 0;")
    db.commit()

    # seed settings whatsapp (se não existir)
    cur = db.execute("SELECT value FROM settings WHERE key='whatsapp_number';").fetchone()
    if cur is None:
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?);", ("whatsapp_number", STORE_WHATSAPP_NUMBER))
        db.commit()

    # seed categorias
    ccur = db.execute("SELECT COUNT(*) as c FROM categories;").fetchone()
    if ccur and int(ccur["c"]) == 0:
        base_cats = [("Cervejas", 1), ("Refrigerantes", 1), ("Águas", 1), ("Energéticos", 1), ("Destilados", 1), ("Outros", 1)]
        db.executemany("INSERT OR IGNORE INTO categories (name, is_active) VALUES (?, ?);", base_cats)
        db.commit()

    # seed produtos (somente se não houver nenhum)
    pcur = db.execute("SELECT COUNT(*) as c FROM products;").fetchone()
    if pcur and int(pcur["c"]) == 0:
        cat_map = {r["name"]: r["id"] for r in db.execute("SELECT id, name FROM categories;").fetchall()}
        seed = [
            (
                "Cerveja Lata 350ml",
                "Gelada e trincando",
                399,
                "https://images.unsplash.com/photo-1518091043644-c1d4457512c6?auto=format&fit=crop&w=800&q=60",
                cat_map.get("Cervejas"),
                None,
                1,
                0,
                None,
                50,
            ),
            (
                "Refrigerante 2L",
                "Coca/Guaraná/Laranja",
                899,
                "https://images.unsplash.com/photo-1603833797131-3c0f5b0a1f2a?auto=format&fit=crop&w=800&q=60",
                cat_map.get("Refrigerantes"),
                None,
                1,
                0,
                None,
                30,
            ),
            (
                "Água 500ml",
                "Sem gás",
                199,
                "https://images.unsplash.com/photo-1523362628745-0c100150b504?auto=format&fit=crop&w=800&q=60",
                cat_map.get("Águas"),
                None,
                1,
                0,
                None,
                80,
            ),
        ]
        db.executemany(
            """
            INSERT INTO products
              (name, description, price_cents, image_url, category_id, category, is_active, is_promo, promo_price_cents, stock_qty)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            seed,
        )
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
    # se vier 11 dígitos (DDD+numero), coloca 55 na frente
    if len(digits) == 11:
        digits = "55" + digits
    return digits


# ===== UTILS =====
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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
                stock_qty=int(r["stock_qty"] or 0),
            )
        )
    return products


def unique_webp_name(original_filename: str) -> str:
    base = secure_filename(os.path.splitext(original_filename or "img")[0]) or "img"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{base}_{stamp}.webp"


def process_and_save_image(file_storage) -> str:
    """
    Padroniza upload:
      - centro-crop para quadrado (estilo delivery)
      - resize 800x800
      - salva em WEBP (leve)
    Retorna URL /static/uploads/xxx.webp
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

    fname = unique_webp_name(file_storage.filename)
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], fname)

    img.save(save_path, "WEBP", quality=82, method=6)
    return f"/static/uploads/{fname}"


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


# ===== LOGIN / LOGOUT =====
@app.get("/login")
def login():
    if is_admin_logged_in():
        return redirect(url_for("admin"))
    next_url = request.args.get("next") or url_for("admin")
    return render_template(
        "login.html",
        app_name=APP_NAME,
        next_url=next_url,
        is_admin=is_admin_logged_in(),
    )


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


# ===== ADMIN =====
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


# ---- CATEGORIAS ----
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
        db.execute("INSERT INTO categories (name, is_active) VALUES (?, ?);", (name, is_active))
        db.commit()
        flash("Categoria adicionada!", "success")
    except sqlite3.IntegrityError:
        flash("Essa categoria já existe.", "error")
    return redirect(url_for("admin_categories"))


@app.post("/admin/categories/toggle/<int:cid>")
@admin_required
def admin_categories_toggle(cid):
    db = get_db()
    row = db.execute("SELECT is_active FROM categories WHERE id=?;", (cid,)).fetchone()
    if not row:
        flash("Categoria não encontrada.", "error")
        return redirect(url_for("admin_categories"))

    new_val = 0 if int(row["is_active"]) == 1 else 1
    db.execute("UPDATE categories SET is_active=? WHERE id=?;", (new_val, cid))
    db.commit()
    flash("Status da categoria atualizado!", "success")
    return redirect(url_for("admin_categories"))


@app.post("/admin/categories/delete/<int:cid>")
@admin_required
def admin_categories_delete(cid):
    db = get_db()
    db.execute("DELETE FROM categories WHERE id=?;", (cid,))
    db.execute("UPDATE products SET category_id=NULL WHERE category_id=?;", (cid,))
    db.commit()
    flash("Categoria removida.", "success")
    return redirect(url_for("admin_categories"))


# ---- PRODUTOS ----
@app.post("/admin/add")
@admin_required
def admin_add():
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    category_id_raw = (request.form.get("category_id") or "").strip()
    price_raw = request.form.get("price") or "0"
    stock_raw = (request.form.get("stock_qty") or "").strip()
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

    # estoque (opcional)
    stock_qty = 0
    if stock_raw:
        try:
            stock_qty = int(re.sub(r"\D+", "", stock_raw))
        except Exception:
            stock_qty = 0

    promo_price_cents = None
    if is_promo:
        try:
            promo_price_cents = parse_price_to_cents(promo_price_raw)
            if promo_price_cents <= 0:
                promo_price_cents = None
        except Exception:
            flash("Preço promocional inválido.", "error")
            return redirect(url_for("admin"))

    category_id = None
    if category_id_raw.isdigit():
        category_id = int(category_id_raw)

    image_url = ""
    image_file = request.files.get("image_file")
    if image_file and image_file.filename:
        if not allowed_file(image_file.filename):
            flash("Formato de imagem inválido. Use png, jpg, jpeg ou webp.", "error")
            return redirect(url_for("admin"))
        try:
            image_url = process_and_save_image(image_file)
        except Exception as e:
            flash(f"Erro ao processar imagem: {e}", "error")
            return redirect(url_for("admin"))

    db = get_db()
    db.execute(
        """
        INSERT INTO products
          (name, description, price_cents, image_url, category_id, category, is_active, is_promo, promo_price_cents, stock_qty)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (name, description, price_cents, image_url, category_id, None, is_active, is_promo, promo_price_cents, stock_qty),
    )
    db.commit()
    flash("Produto adicionado!", "success")
    return redirect(url_for("admin"))


@app.get("/admin/edit/<int:pid>")
@admin_required
def admin_edit(pid):
    db = get_db()
    p = db.execute("SELECT * FROM products WHERE id=?;", (pid,)).fetchone()
    if not p:
        return "Produto não encontrado", 404

    product = dict(p)
    product["price"] = (int(p["price_cents"] or 0) / 100.0)
    product["promo_price"] = (int(p["promo_price_cents"] or 0) / 100.0) if p["promo_price_cents"] else ""
    product["stock_qty"] = int(p["stock_qty"] or 0)
    categories = fetch_categories(active_only=True)

    return render_template(
        "admin_edit.html",
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
    price_raw = request.form.get("price") or "0"
    stock_raw = (request.form.get("stock_qty") or "").strip()
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

    stock_qty = 0
    if stock_raw:
        try:
            stock_qty = int(re.sub(r"\D+", "", stock_raw))
        except Exception:
            stock_qty = 0

    promo_price_cents = None
    if is_promo:
        try:
            promo_price_cents = parse_price_to_cents(promo_price_raw)
            if promo_price_cents <= 0:
                promo_price_cents = None
        except Exception:
            flash("Preço promocional inválido.", "error")
            return redirect(url_for("admin_edit", pid=pid))

    category_id = None
    if category_id_raw.isdigit():
        category_id = int(category_id_raw)

    db = get_db()
    current = db.execute("SELECT image_url FROM products WHERE id=?;", (pid,)).fetchone()
    if not current:
        return "Produto não encontrado", 404

    image_url = current["image_url"] or ""
    image_file = request.files.get("image_file")
    if image_file and image_file.filename:
        if not allowed_file(image_file.filename):
            flash("Formato de imagem inválido. Use png, jpg, jpeg ou webp.", "error")
            return redirect(url_for("admin_edit", pid=pid))
        try:
            image_url = process_and_save_image(image_file)
        except Exception as e:
            flash(f"Erro ao processar imagem: {e}", "error")
            return redirect(url_for("admin_edit", pid=pid))

    db.execute(
        """
        UPDATE products
        SET name=?, description=?, price_cents=?, image_url=?, category_id=?,
            is_active=?, is_promo=?, promo_price_cents=?, stock_qty=?
        WHERE id=?;
        """,
        (name, description, price_cents, image_url, category_id, is_active, is_promo, promo_price_cents, stock_qty, pid),
    )
    db.commit()
    flash("Produto atualizado!", "success")
    return redirect(url_for("admin"))


@app.post("/admin/delete/<int:pid>")
@admin_required
def admin_delete(pid):
    db = get_db()
    db.execute("DELETE FROM products WHERE id=?;", (pid,))
    db.commit()
    flash("Produto removido!", "success")
    return redirect(url_for("admin"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)