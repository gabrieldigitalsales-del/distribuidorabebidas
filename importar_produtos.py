# importar_produtos.py
# -*- coding: utf-8 -*-

import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "database.sqlite3"


def money_to_cents(v: float) -> int:
    return int(round(v * 100))


def get_or_create_category(conn, category_name: str):
    cur = conn.cursor()

    row = cur.execute(
        "SELECT id FROM categories WHERE name=?;",
        (category_name.strip(),)
    ).fetchone()

    if row:
        return row[0]

    cur.execute(
        "INSERT INTO categories (name, is_active) VALUES (?, 1);",
        (category_name.strip(),)
    )
    conn.commit()

    return cur.lastrowid


def upsert_product(conn, product):
    cur = conn.cursor()

    category_id = get_or_create_category(conn, product["category"])

    cur.execute("""
        INSERT INTO products (
            id,
            name,
            description,
            price_cents,
            image_url,
            category_id,
            category,
            is_active,
            is_promo,
            promo_price_cents
        )
        VALUES (?, ?, '', ?, '', ?, NULL, 1, 0, NULL)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            price_cents=excluded.price_cents,
            category_id=excluded.category_id,
            is_active=1
    """, (
        int(product["id"]),
        product["name"].strip(),
        money_to_cents(float(product["price"])),
        category_id
    ))

    conn.commit()


def main():
    conn = sqlite3.connect(DB_PATH)

    # 👇 COLE AQUI SUA LISTA COMPLETA ORIGINAL 👇
    produtos = [
        # REFRIGERANTES (SUKITA)
        # ------------------------
        {"id": 73, "name": "SUKITA LARANJA 2 LITROS", "category": "Refrigerantes", "price": 7.99, "stock": 4},
        {"id": 62, "name": "SUKITA LATA 350 ML", "category": "Refrigerantes", "price": 3.50, "stock": 18},
        {"id": 74, "name": "SUKITA LIMÃO 2 LITROS", "category": "Refrigerantes", "price": 7.99, "stock": 0},
        {"id": 54, "name": "SUKITA PET 200 ML", "category": "Refrigerantes", "price": 2.00, "stock": 11},
        {"id": 72, "name": "SUKITA UVA 2 LITROS", "category": "Refrigerantes", "price": 7.99, "stock": 0},
        {"id": 56, "name": "SUKITA UVA 200 ML", "category": "Refrigerantes", "price": 2.00, "stock": 0},
        {"id": 63, "name": "SUKITA UVA LATA 350 ML", "category": "Refrigerantes", "price": 3.50, "stock": 2},

        # ------------------------
        # SORVETES / PICOLES
        # ------------------------
        {"id": 222, "name": "SUNDAE LEITINHO C/ COB. CHOCOLATE 130 ML", "category": "Sorvetes e Picolés", "price": 7.90, "stock": 8},
        {"id": 220, "name": "SORVETE FLOCOS 400 ML", "category": "Sorvetes e Picolés", "price": 2.00, "stock": 2},
        {"id": 221, "name": "SORVETE NAPOLITANO 400 ML", "category": "Sorvetes e Picolés", "price": 11.50, "stock": 3},
        {"id": 210, "name": "PICOLÉ CARIBENHO", "category": "Sorvetes e Picolés", "price": 10.00, "stock": 0},
        {"id": 209, "name": "PICOLÉ DE BRIGADEIRO RECHEADO", "category": "Sorvetes e Picolés", "price": 8.25, "stock": 24},
        {"id": 211, "name": "PICOLÉ DE COCO", "category": "Sorvetes e Picolés", "price": 8.25, "stock": 23},
        {"id": 214, "name": "PICOLÉ DE LIMÃO", "category": "Sorvetes e Picolés", "price": 5.25, "stock": 30},
        {"id": 217, "name": "PICOLÉ DE UVA", "category": "Sorvetes e Picolés", "price": 5.00, "stock": 29},
        {"id": 212, "name": "PICOLÉ DUELLITO", "category": "Sorvetes e Picolés", "price": 5.00, "stock": 29},
        {"id": 213, "name": "PICOLÉ EXTRA CROC", "category": "Sorvetes e Picolés", "price": 8.90, "stock": 26},
        {"id": 215, "name": "PICOLÉ MORANGO", "category": "Sorvetes e Picolés", "price": 12.90, "stock": 21},

        # ------------------------
        # SUCOS / CHÁS
        # ------------------------
        {"id": 165, "name": "TIAL NECTAR 250 ML", "category": "Sucos", "price": 4.00, "stock": 25},
        {"id": 104, "name": "SUCO TIAL 330 ML", "category": "Sucos", "price": 3.99, "stock": 1},
        {"id": 106, "name": "SUCO TIAL 1 L", "category": "Sucos", "price": 5.00, "stock": 10},

        # ------------------------
        # ENERGÉTICOS
        # ------------------------
        {"id": 6, "name": "RED BULL TROPICAL", "category": "Energéticos", "price": 10.50, "stock": 4},
        {"id": 75, "name": "RED BULL LATA 250 ML", "category": "Energéticos", "price": 10.50, "stock": 7},
        {"id": 78, "name": "RED BULL LATÃO 473 ML", "category": "Energéticos", "price": 16.99, "stock": 18},
        {"id": 77, "name": "RED BULL MELANCIA LATA 250 ML", "category": "Energéticos", "price": 10.50, "stock": 3},
        {"id": 76, "name": "RED BULL ZERO LATA 250 ML", "category": "Energéticos", "price": 10.50, "stock": 4},
        {"id": 176, "name": "RED HOUSE ENERGÉTICO 2 L", "category": "Energéticos", "price": 12.90, "stock": 5},
        {"id": 203, "name": "ENGOV 250 ML", "category": "Energéticos", "price": 15.99, "stock": 4},
        {"id": 81, "name": "FUSION ENERGÉTICO 2 LITROS", "category": "Energéticos", "price": 10.00, "stock": 7},
        {"id": 79, "name": "FUSION ENERGÉTICO LATÃO 473 ML", "category": "Energéticos", "price": 6.00, "stock": 7},
        {"id": 82, "name": "FUSION TROPICAL 2 LITROS", "category": "Energéticos", "price": 9.99, "stock": 0},

        # ------------------------
        # ÁGUA / TÔNICA
        # ------------------------
        {"id": 168, "name": "TÔNICA ANTARCTICA ZERO 350 ML", "category": "Água", "price": 4.20, "stock": 14},
        {"id": 156, "name": "TÔNICA FYS", "category": "Água", "price": 4.50, "stock": 1},
        {"id": 65, "name": "TÔNICA LATA 350 ML", "category": "Água", "price": 4.20, "stock": 4},
        {"id": 178, "name": "ÁGUA MINERAL 240 ML", "category": "Água", "price": 2.50, "stock": 1},
        {"id": 97, "name": "ÁGUA MINERAL PUREZ VITAL C/ GÁS", "category": "Água", "price": 1.50, "stock": 22},
        {"id": 171, "name": "CRYSTAL COM GÁS SABORIZADA 510 ML", "category": "Água", "price": 4.00, "stock": 11998},

        # ------------------------
        # CERVEJAS (parte)
        # ------------------------
        {"id": 44, "name": "SPATEN GARRAFA 600 ML", "category": "Cervejas", "price": 9.99, "stock": 16},
        {"id": 16, "name": "SPATEN LATÃO 473 ML", "category": "Cervejas", "price": 6.40, "stock": 59},
        {"id": 12, "name": "SPATEN LONG NECK 330 ML", "category": "Cervejas", "price": 7.50, "stock": 24},

        {"id": 42, "name": "STELLA ARTOIS GARRAFA 600 ML", "category": "Cervejas", "price": 10.60, "stock": 43},
        {"id": 11, "name": "STELLA ARTOIS LONG NECK 330 ML", "category": "Cervejas", "price": 7.50, "stock": 0},
        {"id": 138, "name": "STELLA LATÃO", "category": "Cervejas", "price": 6.30, "stock": 64},
        {"id": 162, "name": "STELLA PURE GOLD LATA 350 ML", "category": "Cervejas", "price": 5.99, "stock": 9},
        {"id": 10, "name": "STELLA PURE GOLD LATÃO 473 ML", "category": "Cervejas", "price": 7.00, "stock": 0},
        {"id": 8, "name": "STELLA PURE GOLD LONG NECK 330 ML", "category": "Cervejas", "price": 7.50, "stock": 21},
        {"id": 19, "name": "SUB-ZERO LATÃO 473 ML", "category": "Cervejas", "price": 5.00, "stock": 38},

        {"id": 37, "name": "BUDWEISER LITRINHO 300 ML", "category": "Cervejas", "price": 3.50, "stock": 112},
        {"id": 1, "name": "BUDWEISER LONG NECK 330 ML", "category": "Cervejas", "price": 6.50, "stock": 32},
        {"id": 2, "name": "BUDWEISER ZERO LATA 350 ML", "category": "Cervejas", "price": 4.80, "stock": 0},
        {"id": 9, "name": "BUDWEISER ZERO LONG NECK 330 ML", "category": "Cervejas", "price": 6.70, "stock": 18},
        {"id": 174, "name": "BUDWEISER 1 L", "category": "Cervejas", "price": 9.99, "stock": 0},

        {"id": 21, "name": "BOA LATÃO 473 ML", "category": "Cervejas", "price": 4.80, "stock": 179},
        {"id": 47, "name": "BOA LITRÃO", "category": "Cervejas", "price": 9.99, "stock": 1},
        {"id": 35, "name": "BOA LITRINHO 300 ML", "category": "Cervejas", "price": 3.50, "stock": 134},
        {"id": 13, "name": "BOHEMIA LATÃO 473 ML", "category": "Cervejas", "price": 5.40, "stock": 50},
        {"id": 24, "name": "BRAHMA DUPLO MALTE LATÃO 473 ML", "category": "Cervejas", "price": 3.50, "stock": 118},
        {"id": 39, "name": "BRAHMA DUPLO MALTE LITRINHO", "category": "Cervejas", "price": 6.10, "stock": 7},
        {"id": 45, "name": "BRAHMA GARRAFA 600 ML", "category": "Cervejas", "price": 4.00, "stock": 115},
        {"id": 5, "name": "BRAHMA LATÃO 473 ML", "category": "Cervejas", "price": 8.50, "stock": 32},
        {"id": 46, "name": "BRAHMA LITRÃO", "category": "Cervejas", "price": 5.50, "stock": 193},
        {"id": 34, "name": "BRAHMA LITRINHO 300 ML", "category": "Cervejas", "price": 10.50, "stock": 10},
        {"id": 23, "name": "BRAHMA ZERO LATA 350 ML", "category": "Cervejas", "price": 3.50, "stock": 153},
        {"id": 88, "name": "BRUTAL FRUIT", "category": "Cervejas", "price": 4.80, "stock": 0},
        {"id": 40, "name": "BUDWEISER GARRAFA 600 ML", "category": "Cervejas", "price": 10.99, "stock": 0},
        {"id": 25, "name": "BUDWEISER LATA 350 ML", "category": "Cervejas", "price": 8.50, "stock": 0},
        {"id": 15, "name": "BUDWEISER LATÃO 473 ML", "category": "Cervejas", "price": 6.00, "stock": 15},

        # ------------------------
        # SKOL BEATS / ICE
        # ------------------------
        {"id": 145, "name": "SKOL BEATS RED MIX LONG NECK", "category": "Gelo e Drinks Prontos", "price": 7.50, "stock": 25},
        {"id": 90, "name": "SKOL BEATS SENSES LATA 269 ML", "category": "Gelo e Drinks Prontos", "price": 7.50, "stock": 2},
        {"id": 85, "name": "SKOL BEATS SENSES LONG NECK", "category": "Gelo e Drinks Prontos", "price": 7.50, "stock": 17},
        {"id": 93, "name": "SKOL BEATS TROPICAL LATA 269 ML", "category": "Gelo e Drinks Prontos", "price": 8.50, "stock": 7},
        {"id": 83, "name": "SKOL BEATS TROPICAL LONG NECK", "category": "Gelo e Drinks Prontos", "price": 8.50, "stock": 22},
        {"id": 146, "name": "SKOL BEATS RED MIX LATA", "category": "Gelo e Drinks Prontos", "price": 6.00, "stock": 0},
        {"id": 87, "name": "51 ICE BALADA", "category": "Gelo e Drinks Prontos", "price": 7.99, "stock": 0},

        # ------------------------
        # REFRIGERANTES (PEPSI)
        # ------------------------
        {"id": 70, "name": "PEPSI BLACK 2 LITROS", "category": "Refrigerantes", "price": 10.00, "stock": 0},
        {"id": 53, "name": "PEPSI BLACK 200 ML", "category": "Refrigerantes", "price": 2.00, "stock": 0},
        {"id": 60, "name": "PEPSI BLACK LATA 350 ML", "category": "Refrigerantes", "price": 3.60, "stock": 3},
        {"id": 166, "name": "PEPSI BLACK SEM AÇÚCAR 350 ML", "category": "Refrigerantes", "price": 3.99, "stock": 9},
        {"id": 61, "name": "PEPSI LATA 350 ML", "category": "Refrigerantes", "price": 10.00, "stock": 5},
        {"id": 69, "name": "PEPSI TWIST 2 LITROS", "category": "Refrigerantes", "price": 3.60, "stock": 10},
        {"id": 68, "name": "PEPSI COLA 2 LITROS", "category": "Refrigerantes", "price": 3.99, "stock": 9},
        {"id": 164, "name": "PEPSI 200 ML", "category": "Refrigerantes", "price": 2.00, "stock": 1},

        # ------------------------
        # REFRIGERANTES (COCA-COLA)
        # ------------------------
        {"id": 113, "name": "COCA-COLA 200 ML", "category": "Refrigerantes", "price": 2.50, "stock": 25},
        {"id": 115, "name": "COCA-COLA 600 ML", "category": "Refrigerantes", "price": 6.00, "stock": 15},
        {"id": 149, "name": "COCA-COLA CAFÉ 220 ML", "category": "Refrigerantes", "price": 3.60, "stock": 25},
        {"id": 114, "name": "COCA-COLA LATA 350 ML", "category": "Refrigerantes", "price": 4.75, "stock": 38},
        {"id": 117, "name": "COCA-COLA MINI 220 ML", "category": "Refrigerantes", "price": 3.60, "stock": 23},
        {"id": 121, "name": "COCA-COLA RETORNÁVEL 2 L", "category": "Refrigerantes", "price": 8.00, "stock": 9},
        {"id": 120, "name": "COCA-COLA ZERO 2 LITROS", "category": "Refrigerantes", "price": 14.00, "stock": 0},
        {"id": 148, "name": "COCA-COLA ZERO 200 ML", "category": "Refrigerantes", "price": 2.50, "stock": 36},
        {"id": 173, "name": "COCA-COLA 250 ML", "category": "Refrigerantes", "price": 4.70, "stock": 29},
        {"id": 192, "name": "COCA-COLA 310 ML", "category": "Refrigerantes", "price": 4.20, "stock": 12},
        {"id": 182, "name": "COCA-COLA ZERO 220 ML", "category": "Refrigerantes", "price": 3.60, "stock": 24},
        {"id": 191, "name": "COCA-COLA ZERO 250 ML", "category": "Refrigerantes", "price": 4.70, "stock": 34},
        {"id": 158, "name": "COCA-COLA 1 L", "category": "Refrigerantes", "price": 7.75, "stock": 14},
        {"id": 119, "name": "COCA-COLA 2 LITROS", "category": "Refrigerantes", "price": 14.00, "stock": 17},

        # ------------------------
        # DESTILADOS / BEBIDAS
        # ------------------------
        {"id": 179, "name": "VODCA ORLOFF 1 L", "category": "Destilados", "price": 65.00, "stock": 2},
        {"id": 107, "name": "VERMELHÃO", "category": "Destilados", "price": 65.00, "stock": 2},
        {"id": 112, "name": "CACHAÇA 51", "category": "Destilados", "price": 20.00, "stock": 4},
        {"id": 163, "name": "CAMPARI", "category": "Destilados", "price": 70.00, "stock": 6},
        {"id": 200, "name": "CAMPO LARGO 750 ML", "category": "Destilados", "price": 15.00, "stock": 5},
        {"id": 201, "name": "CANELINHA 900 ML", "category": "Destilados", "price": 15.00, "stock": 5},
        {"id": 199, "name": "CATUABA SELVAGEM 900 ML", "category": "Destilados", "price": 18.00, "stock": 4},
        {"id": 205, "name": "CHANCELER 1 L", "category": "Destilados", "price": 13.99, "stock": 24},
        {"id": 130, "name": "XEQUE MATE", "category": "Gelo e Drinks Prontos", "price": 6.00, "stock": 0},
        {"id": 202, "name": "XEQUE MATE 362 ML", "category": "Gelo e Drinks Prontos", "price": 8.90, "stock": 10},
        {"id": 110, "name": "WHISKY RED LABEL", "category": "Destilados", "price": 100.00, "stock": 1},

        # ------------------------
        # HEINEKEN / H2O / IGARAPÉ / ETC (da foto)
        # ------------------------
        {"id": 95, "name": "H2O LIMONETO", "category": "Refrigerantes", "price": 5.00, "stock": 0},
        {"id": 102, "name": "H2O LIMONETO 1,5 L", "category": "Refrigerantes", "price": 9.00, "stock": 0},
        {"id": 132, "name": "HALLS", "category": "Snacks e Doces", "price": 9.00, "stock": 0},
        {"id": 193, "name": "HEINEKEN 350 ML", "category": "Cervejas", "price": 2.50, "stock": 23},
        {"id": 123, "name": "HEINEKEN GARRAFA 600 ML", "category": "Cervejas", "price": 6.45, "stock": 30},
        {"id": 20, "name": "HEINEKEN LATÃO 473 ML", "category": "Cervejas", "price": 13.00, "stock": 9},
        {"id": 125, "name": "HEINEKEN LONG NECK 330 ML", "category": "Cervejas", "price": 7.00, "stock": 6},
        {"id": 139, "name": "HEINEKEN LONG NECK ZERO", "category": "Cervejas", "price": 7.70, "stock": 68},
        {"id": 169, "name": "HEINEKEN PURO MALTE 269 ML", "category": "Cervejas", "price": 8.00, "stock": 82},

        {"id": 195, "name": "IGARAPÉ 1,5 ML COM GÁS", "category": "Água", "price": 4.99, "stock": 8},
        {"id": 187, "name": "IGARAPÉ 1,5 ML COM GÁS", "category": "Água", "price": 9.00, "stock": 0},
        {"id": 196, "name": "IGARAPÉ 1,5 ML SEM GÁS", "category": "Água", "price": 4.75, "stock": 12},
        {"id": 194, "name": "IGARAPÉ 500 ML SEM GÁS", "category": "Água", "price": 4.75, "stock": 9},

        {"id": 135, "name": "ISQUEIRO", "category": "Outros", "price": 2.50, "stock": 20},
        {"id": 204, "name": "JACK POWER 20", "category": "Outros", "price": 12.00, "stock": 11},

        # ------------------------
        # GELO / GATORADE / GUARANÁ
        # ------------------------
        {"id": 122, "name": "GATORADE", "category": "Outros", "price": 6.00, "stock": 33},
        {"id": 27, "name": "GELO CUBO 4 KG", "category": "Gelo e Drinks Prontos", "price": 12.00, "stock": 6},
        {"id": 26, "name": "GELO TRITURADO 8 KG", "category": "Gelo e Drinks Prontos", "price": 12.00, "stock": 5},
        {"id": 33, "name": "GELO TROPICAL ÁGUA DE COCO", "category": "Gelo e Drinks Prontos", "price": 2.99, "stock": 22},
        {"id": 32, "name": "GELO TROPICAL LARANJA", "category": "Gelo e Drinks Prontos", "price": 2.99, "stock": 27},
        {"id": 29, "name": "GELO TROPICAL MAÇÃ VERDE", "category": "Gelo e Drinks Prontos", "price": 2.99, "stock": 33},
        {"id": 31, "name": "GELO TROPICAL MARACUJÁ", "category": "Gelo e Drinks Prontos", "price": 2.99, "stock": 22},
        {"id": 30, "name": "GELO TROPICAL MELANCIA", "category": "Gelo e Drinks Prontos", "price": 2.99, "stock": 5},
        {"id": 28, "name": "GELO TROPICAL MORANGO", "category": "Gelo e Drinks Prontos", "price": 2.99, "stock": 23},

        {"id": 67, "name": "GUARANÁ ANTARCTICA 2 LITROS", "category": "Refrigerantes", "price": 10.00, "stock": 5},
        {"id": 52, "name": "GUARANÁ ANTARCTICA 200 ML", "category": "Refrigerantes", "price": 2.00, "stock": 4},
        {"id": 58, "name": "GUARANÁ ANTARCTICA LATA 350 ML", "category": "Refrigerantes", "price": 4.10, "stock": 27},
        {"id": 66, "name": "GUARANÁ ANTARCTICA ZERO 2 LITROS", "category": "Refrigerantes", "price": 10.50, "stock": 7},
        {"id": 57, "name": "GUARANÁ ANTARCTICA ZERO 200 ML", "category": "Refrigerantes", "price": 2.00, "stock": 5},
        {"id": 59, "name": "GUARANÁ ANTARCTICA ZERO LATA 350 ML", "category": "Refrigerantes", "price": 4.10, "stock": 22},
    ]

    inserted = 0

    for p in produtos:
        upsert_product(conn, p)
        inserted += 1

    conn.close()

    print(f"OK! Produtos importados/atualizados: {inserted}")
    print(f"Banco usado: {DB_PATH}")


if __name__ == "__main__":
    main()