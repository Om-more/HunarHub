import base64
import json
import os
import secrets
import sqlite3
from datetime import datetime
from functools import wraps

import google.generativeai as genai
from dotenv import load_dotenv
from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for

load_dotenv()

app = Flask(__name__)
UPLOAD_FOLDER = "static/uploads"
DB_PATH = "hunarhub.db"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)

API_KEY = os.getenv("API_KEY")
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create the local SQLite tables used by the hackathon app."""
    with get_db_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS artisans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                craft_type TEXT,
                location TEXT,
                language_pref TEXT DEFAULT 'en',
                session_token TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artisan_id INTEGER NOT NULL REFERENCES artisans(id),
                image TEXT,
                name TEXT NOT NULL,
                category TEXT,
                location TEXT,
                description TEXT,
                price TEXT,
                date_added TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artisan_id INTEGER NOT NULL REFERENCES artisans(id),
                role TEXT NOT NULL,
                message TEXT,
                image_path TEXT,
                structured_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artisan_id INTEGER NOT NULL REFERENCES artisans(id),
                target_type TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                rating INTEGER NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL
            );
            """
        )


def get_current_artisan():
    artisan_id = session.get("artisan_id")
    if not artisan_id:
        return None

    with get_db_connection() as conn:
        artisan = conn.execute(
            "SELECT * FROM artisans WHERE id = ?",
            (artisan_id,),
        ).fetchone()
    return artisan


def require_artisan(view_func):
    """Require the lightweight onboarding session before scoped app actions."""

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        artisan = get_current_artisan()
        if artisan is None:
            session.pop("artisan_id", None)
            if request.path.startswith("/api/"):
                return jsonify({"success": False, "error": "Not onboarded"}), 401
            return redirect(url_for("onboard", next=request.path))

        g.artisan = artisan
        return view_func(*args, **kwargs)

    return wrapped


def save_product_to_db(artisan_id, product_data):
    with get_db_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO products
                (artisan_id, image, name, category, location, description, price, date_added)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artisan_id,
                product_data.get("image", "No image"),
                product_data.get("name"),
                product_data.get("category", "Uncategorized"),
                product_data.get("location", "Not specified"),
                product_data.get("description"),
                str(product_data.get("price", "")),
                now_text(),
            ),
        )
        return cursor.lastrowid


def get_products_for_artisan(artisan_id):
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                image AS Image,
                name AS Name,
                category AS Category,
                location AS Location,
                description AS Description,
                price AS Price,
                date_added AS Date_Added
            FROM products
            WHERE artisan_id = ?
            ORDER BY id DESC
            """,
            (artisan_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def build_artisan_context(artisan_id):
    with get_db_connection() as conn:
        artisan = conn.execute(
            "SELECT name, craft_type, location FROM artisans WHERE id = ?",
            (artisan_id,),
        ).fetchone()
        products = conn.execute(
            """
            SELECT name, category, price
            FROM products
            WHERE artisan_id = ?
            ORDER BY id DESC
            LIMIT 5
            """,
            (artisan_id,),
        ).fetchall()

    if artisan is None:
        return ""

    recent_products = ", ".join(
        f"{row['name']} ({row['category'] or 'uncategorized'}, Rs {row['price'] or '0'})"
        for row in products
    )
    if not recent_products:
        recent_products = "none yet"

    return (
        f"Artisan profile: {artisan['name']}, craft: {artisan['craft_type'] or 'not specified'}, "
        f"location: {artisan['location'] or 'not specified'}.\n"
        f"Recent products they've listed: {recent_products}.\n"
        "Use this context to personalize suggestions, avoid repeating names already used, "
        "and keep pricing consistent with the existing range unless the new item clearly differs. "
        "Do not mention this context block explicitly to the user.\n\n"
    )


def parse_structured_product_response(raw_text):
    """Gemini can occasionally ignore strict JSON; fall back without breaking chat."""
    try:
        parsed = json.loads(raw_text)
        return {
            "reply": parsed.get("reply") or raw_text,
            "product_suggestion": parsed.get("product_suggestion"),
            "structured_json": raw_text,
        }
    except (TypeError, json.JSONDecodeError):
        return {
            "reply": raw_text,
            "product_suggestion": None,
            "structured_json": raw_text,
        }


def query_with_image(user_question, artisan_id=None, image_path=None, image_bytes=None):
    context = build_artisan_context(artisan_id) if artisan_id else ""
    has_image = image_path is not None or image_bytes is not None

    base_prompt = f"""
    {context}
    User will enter an image and will ask question related to that art image he/she shared,
    Suggest him according to the question asked related to image which is related more to
    naming it, describing it, market it, price of it according to the trend, platform guidance for Meesho, Amazon Karigar, Etsy (here suggest him some youtube tutorial for creation of seller account in those platforms & guide accordingly).
    If no image is been shared then answer the questions in general which is related to Art, handicrafts, handmade products, handlooms, pottery, etc.
    Also generate a downloadable .txt link for any response if user asks you to do so.
    And don't answer anything else from that.
    Question: {user_question}
    Answer in a friendly and natural way:
    """

    if has_image:
        prompt = f"""
        {base_prompt}

        Respond with ONLY valid JSON, no markdown fences, no preamble.
        Use this exact shape:
        {{
          "reply": "the normal friendly free-text answer, unchanged in tone",
          "product_suggestion": {{
            "name": "suggested product name or null",
            "category": "suggested category or null",
            "description": "suggested description or null",
            "price": "suggested price as a plain number string, or null"
          }}
        }}
        """
    else:
        prompt = base_prompt

    try:
        if image_path is not None:
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            response = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": image_bytes}])
            return parse_structured_product_response(response.text)

        if image_bytes is not None:
            response = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": image_bytes}])
            return parse_structured_product_response(response.text)

        response = model.generate_content(prompt)
        return {
            "reply": response.text,
            "product_suggestion": None,
            "structured_json": None,
        }
    except Exception as e:
        return {
            "reply": f"Error processing request: {str(e)}",
            "product_suggestion": None,
            "structured_json": None,
        }


@app.route("/")
@require_artisan
def dashboard():
    return render_template("chat.html")


@app.route("/onboard")
def onboard():
    if get_current_artisan() is not None:
        return redirect(request.args.get("next") or url_for("ai_chat"))
    return render_template("onboard.html", next_url=request.args.get("next", ""))


@app.route("/api/create-artisan", methods=["POST"])
def create_artisan():
    data = request.get_json(silent=True) or request.form
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Name is required"}), 400

    with get_db_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO artisans (name, craft_type, location, language_pref, session_token, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                (data.get("craft_type") or "").strip(),
                (data.get("location") or "").strip(),
                (data.get("language_pref") or "en").strip() or "en",
                secrets.token_hex(16),
                now_text(),
            ),
        )
        session["artisan_id"] = cursor.lastrowid

    next_url = data.get("next") or url_for("ai_chat")
    if request.is_json:
        return jsonify({"success": True, "redirect": next_url})
    return redirect(next_url)


@app.route("/events.html")
def event():
    return render_template("Events.html")


@app.route("/addprod.html")
@require_artisan
def addprod():
    return render_template("addprod.html")


@app.route("/chat.html")
@require_artisan
def chat():
    return render_template("chat.html")


@app.route("/aboutapp.html")
def aboutapp():
    return render_template("aboutapp.html")


@app.route("/AI.html")
@require_artisan
def ai_interface():
    return render_template("AI.html")


@app.route("/history.html")
@require_artisan
def history():
    return render_template("history.html")


@app.route("/api/save-product", methods=["POST"])
@require_artisan
def save_product():
    try:
        data = request.get_json()

        required_fields = ["name", "description", "price"]
        for field in required_fields:
            if not data.get(field):
                return jsonify({"success": False, "error": f"{field.title()} is required"}), 400

        product_data = {
            "image": data.get("image", "No image"),
            "name": data.get("name"),
            "category": data.get("category", "Uncategorized"),
            "location": data.get("location", "Not specified"),
            "description": data.get("description"),
            "price": data.get("price"),
        }

        product_id = save_product_to_db(g.artisan["id"], product_data)
        return jsonify(
            {
                "success": True,
                "message": "Product saved successfully",
                "product_id": product_id,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/get-products", methods=["GET"])
@require_artisan
def get_products():
    try:
        products = get_products_for_artisan(g.artisan["id"])
        return jsonify({"success": True, "products": products})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
@require_artisan
def api_chat():
    try:
        data = request.get_json()
        message = data.get("message", "")
        image_data = data.get("image")
        image_bytes = None
        image_path = "inline_upload" if image_data else None

        if image_data:
            image_bytes = base64.b64decode(image_data.split(",", 1)[1])

        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO chat_messages (artisan_id, role, message, image_path, structured_json, created_at)
                VALUES (?, 'user', ?, ?, NULL, ?)
                """,
                (g.artisan["id"], message, image_path, now_text()),
            )

        answer = query_with_image(message, artisan_id=g.artisan["id"], image_bytes=image_bytes)

        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO chat_messages
                    (artisan_id, role, message, image_path, structured_json, created_at)
                VALUES (?, 'assistant', ?, NULL, ?, ?)
                """,
                (g.artisan["id"], answer["reply"], answer.get("structured_json"), now_text()),
            )
            message_id = cursor.lastrowid

        return jsonify(
            {
                "success": True,
                "response": answer["reply"],
                "product_suggestion": answer.get("product_suggestion"),
                "message_id": message_id,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/ai-chat", methods=["GET", "POST"])
@require_artisan
def ai_chat():
    answer = None
    img_url = None
    user_message = None
    if request.method == "POST":
        user_message = request.form.get("question", "")
        image = request.files.get("image")
        if image and image.filename != "":
            img_path = os.path.join(app.config["UPLOAD_FOLDER"], image.filename)
            image.save(img_path)
            img_url = url_for("static", filename=f"uploads/{image.filename}")
            result = query_with_image(user_message, artisan_id=g.artisan["id"], image_path=img_path)
        else:
            result = query_with_image(user_message, artisan_id=g.artisan["id"])
        answer = result["reply"]
    return render_template("AI.html", answer=answer, img_url=img_url, user_message=user_message)


@app.route("/api/profile", methods=["GET", "POST"])
@require_artisan
def profile():
    if request.method == "GET":
        return jsonify(
            {
                "success": True,
                "profile": {
                    "name": g.artisan["name"],
                    "craft_type": g.artisan["craft_type"] or "",
                    "location": g.artisan["location"] or "",
                    "language_pref": g.artisan["language_pref"] or "en",
                },
            }
        )

    data = request.get_json(silent=True) or {}
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE artisans
            SET craft_type = ?, location = ?, language_pref = ?
            WHERE id = ?
            """,
            (
                (data.get("craft_type") or "").strip(),
                (data.get("location") or "").strip(),
                (data.get("language_pref") or "en").strip() or "en",
                g.artisan["id"],
            ),
        )
    return jsonify({"success": True})


@app.route("/api/feedback", methods=["POST"])
@require_artisan
def feedback():
    data = request.get_json(silent=True) or {}
    target_type = data.get("target_type")
    rating = data.get("rating")
    target_id = data.get("target_id")

    if target_type not in {"chat_message", "product"}:
        return jsonify({"success": False, "error": "Invalid target_type"}), 400
    if rating not in {1, -1}:
        return jsonify({"success": False, "error": "Invalid rating"}), 400

    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid target_id"}), 400

    table_name = "chat_messages" if target_type == "chat_message" else "products"
    with get_db_connection() as conn:
        target = conn.execute(
            f"SELECT id FROM {table_name} WHERE id = ? AND artisan_id = ?",
            (target_id, g.artisan["id"]),
        ).fetchone()
        if target is None:
            return jsonify({"success": False, "error": "Target not found"}), 404

        conn.execute(
            """
            INSERT INTO feedback (artisan_id, target_type, target_id, rating, comment, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                g.artisan["id"],
                target_type,
                target_id,
                rating,
                (data.get("comment") or "").strip(),
                now_text(),
            ),
        )

    return jsonify({"success": True})


@app.route("/api/feedback-summary", methods=["GET"])
@require_artisan
def feedback_summary():
    summary = {
        "chat_message": {"up": 0, "down": 0},
        "product": {"up": 0, "down": 0},
    }

    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT target_type, rating, COUNT(*) AS count
            FROM feedback
            WHERE artisan_id = ?
            GROUP BY target_type, rating
            """,
            (g.artisan["id"],),
        ).fetchall()

    for row in rows:
        direction = "up" if row["rating"] == 1 else "down"
        summary[row["target_type"]][direction] = row["count"]

    return jsonify({"success": True, "summary": summary})


os.makedirs(UPLOAD_FOLDER, exist_ok=True)
init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
