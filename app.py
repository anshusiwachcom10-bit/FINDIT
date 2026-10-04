import json
import os
import re
import secrets
import sqlite3
from datetime import date
from functools import wraps
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from config import config
from database.database import close_db, execute_db, get_db, init_db, query_db
from database.matching import (
    MATCH_STATUSES,
    MIN_MATCH_SCORE,
    find_matches,
)


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _row_dict(row):
    return dict(row) if row is not None else None


def _item_dict(row, user_id=None):
    item = _row_dict(row)
    if not item:
        return None

    path = item.pop("image_path", None)
    item["image_url"] = path if path and path.startswith("/static/") else None
    item["is_owner"] = user_id is not None and item.pop("user_id", None) == user_id
    item.pop("user_id", None)
    return item


def _safe_next_url(target):
    if not target:
        return None
    host_url = urlparse(request.host_url)
    redirect_url = urlparse(urljoin(request.host_url, target))
    if redirect_url.scheme in ("http", "https") and redirect_url.netloc == host_url.netloc:
        return redirect_url.path + (("?" + redirect_url.query) if redirect_url.query else "")
    return None


def _csrf_token():
    token = session.get("_csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _require_csrf():
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    expected = session.get("_csrf_token")
    if not supplied or not expected or not secrets.compare_digest(supplied, expected):
        if request.path.startswith("/api/"):
            return jsonify(error="Your session expired. Refresh the page and try again."), 400
        abort(400, description="Your session expired. Refresh the page and try again.")
    return None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.current_user is None:
            if request.path.startswith("/api/"):
                return jsonify(error="Please sign in to continue."), 401
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped


def _get_item(item_id):
    return query_db(
        """
        SELECT id, user_id, type, title, category, description, location,
               event_date, image_path, status, created_at
        FROM items WHERE id = ?
        """,
        (item_id,),
        one=True,
    )


def _all_items():
    return query_db(
        """
        SELECT id, user_id, type, title, category, description, location,
               event_date, image_path, status, created_at
        FROM items ORDER BY created_at DESC, id DESC
        """
    )


def _matches_for(item, viewer_id=None):
    if not item or not item.get("id"):
        return []
    return _get_matches_for_report(item["id"], viewer_id)


def _item_from_match_row(row, prefix):
    return {
        "id": row[f"{prefix}_id"],
        "user_id": row[f"{prefix}_user_id"],
        "type": row[f"{prefix}_type"],
        "title": row[f"{prefix}_title"],
        "category": row[f"{prefix}_category"],
        "description": row[f"{prefix}_description"],
        "location": row[f"{prefix}_location"],
        "event_date": row[f"{prefix}_event_date"],
        "image_path": row[f"{prefix}_image_path"],
        "status": row[f"{prefix}_item_status"],
        "created_at": row[f"{prefix}_created_at"],
    }


def _get_match_rows(where, values=()):
    return query_db(
        f"""
        SELECT
            m.id AS match_id, m.score, m.match_reasons,
            m.status AS match_status, m.created_at AS match_created_at,
            l.id AS lost_id, l.user_id AS lost_user_id, l.type AS lost_type,
            l.title AS lost_title, l.category AS lost_category,
            l.description AS lost_description, l.location AS lost_location,
            l.event_date AS lost_event_date, l.image_path AS lost_image_path,
            l.status AS lost_item_status, l.created_at AS lost_created_at,
            f.id AS found_id, f.user_id AS found_user_id, f.type AS found_type,
            f.title AS found_title, f.category AS found_category,
            f.description AS found_description, f.location AS found_location,
            f.event_date AS found_event_date, f.image_path AS found_image_path,
            f.status AS found_item_status, f.created_at AS found_created_at
        FROM matches m
        JOIN items l ON l.id = m.lost_report_id
        JOIN items f ON f.id = m.found_report_id
        WHERE {where}
        ORDER BY m.score DESC, m.created_at DESC, m.id DESC
        """,
        values,
    )


def _serialize_match(row, viewer_id=None):
    stored = json.loads(row["match_reasons"])
    lost_item = _item_from_match_row(row, "lost")
    found_item = _item_from_match_row(row, "found")
    return {
        "id": row["match_id"],
        "lost_item": _item_dict(lost_item, viewer_id),
        "found_item": _item_dict(found_item, viewer_id),
        "report": None,
        "match": None,
        "score": row["score"],
        "confidence": row["score"],
        "level": stored["level"],
        "reasons": stored["reasons"],
        "components": stored["components"],
        "weights": stored["weights"],
        "date_difference_days": stored["date_difference_days"],
        "status": row["match_status"],
        "status_label": _match_status_label(row["match_status"]),
        "created_at": row["match_created_at"],
    }


def _get_matches_for_report(item_id, viewer_id=None):
    rows = _get_match_rows(
        "m.status != 'rejected' AND (m.lost_report_id = ? OR m.found_report_id = ?)",
        (item_id, item_id),
    )
    results = []
    for row in rows:
        match = _serialize_match(row, viewer_id)
        if row["lost_id"] == item_id:
            match["report"] = match["lost_item"]
            match["match"] = match["found_item"]
        else:
            match["report"] = match["found_item"]
            match["match"] = match["lost_item"]
        match["item"] = match["match"]
        if viewer_id is None or viewer_id not in (
            row["lost_user_id"],
            row["found_user_id"],
        ):
            match.pop("status", None)
            match.pop("status_label", None)
        results.append(match)
    return results


def _get_match_for_user(match_id, user_id):
    rows = _get_match_rows("m.id = ?", (match_id,))
    if not rows:
        return None
    row = rows[0]
    if user_id not in (row["lost_user_id"], row["found_user_id"]):
        return None
    match = _serialize_match(row, user_id)
    if row["lost_user_id"] == user_id:
        match["report"] = match["lost_item"]
        match["match"] = match["found_item"]
    else:
        match["report"] = match["found_item"]
        match["match"] = match["lost_item"]
    return match


def _store_matches_for_item(item, notify=False):
    candidates = [_row_dict(row) for row in _all_items()]
    qualified = find_matches(item, candidates, MIN_MATCH_SCORE)
    inserted = []
    db = get_db()
    for match in qualified:
        candidate = match["item"]
        lost_id, found_id = (
            (item["id"], candidate["id"])
            if item["type"] == "lost"
            else (candidate["id"], item["id"])
        )
        reasons = json.dumps(
            {
                "reasons": match["reasons"],
                "components": match["components"],
                "weights": match["weights"],
                "date_difference_days": match["date_difference_days"],
                "level": match["level"],
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
        cursor = db.execute(
            """
            INSERT INTO matches (lost_report_id, found_report_id, score, match_reasons)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(lost_report_id, found_report_id) DO NOTHING
            """,
            (lost_id, found_id, match["score"], reasons),
        )
        if cursor.rowcount == 1:
            inserted.append((candidate, match))
        cursor.close()
    if notify:
        for candidate, match in inserted:
            owner_id = candidate.get("user_id")
            if owner_id and owner_id != item.get("user_id"):
                db.execute(
                    """
                    INSERT INTO notifications (user_id, title, message)
                    VALUES (?, ?, ?)
                    """,
                    (
                        owner_id,
                        "A potential item match was found",
                        f"{match['level']} ({match['score']}%) for your "
                        f"{candidate['type']} report: {candidate['title']}.",
                    ),
                )
    db.commit()
    return inserted


def _backfill_matches():
    for row in _all_items():
        item = _row_dict(row)
        if (item.get("status") or "").lower() == "open":
            _store_matches_for_item(item)


def _match_status_label(status):
    return (status or "potential").replace("_", " ").title()


def _validate_item_form(data):
    fields = {
        "title": "Item name",
        "category": "Category",
        "description": "Description",
        "location": "Location",
        "event_date": "Date",
    }
    cleaned = {}
    for key, label in fields.items():
        value = (data.get(key) or "").strip()
        if not value:
            return None, f"{label} is required."
        if len(value) > (1200 if key == "description" else 160):
            return None, f"{label} is too long."
        cleaned[key] = value

    try:
        date.fromisoformat(cleaned["event_date"])
    except ValueError:
        return None, "Enter a valid date."

    return cleaned, None


def _save_uploaded_image(file):
    if file is None or not file.filename:
        return None, None
    original_name = secure_filename(file.filename)
    if not original_name or "." not in original_name:
        return None, "Choose a valid image file."

    extension = original_name.rsplit(".", 1)[1].lower()
    if extension not in current_app.config["ALLOWED_EXTENSIONS"]:
        allowed = ", ".join(sorted(current_app.config["ALLOWED_EXTENSIONS"]))
        return None, f"Image must be one of: {allowed}."

    signature = file.stream.read(12)
    file.stream.seek(0)
    valid_signatures = {
        "png": signature.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpg": signature.startswith(b"\xff\xd8\xff"),
        "jpeg": signature.startswith(b"\xff\xd8\xff"),
        "gif": signature.startswith((b"GIF87a", b"GIF89a")),
        "webp": signature.startswith(b"RIFF") and signature[8:12] == b"WEBP",
    }
    if not valid_signatures.get(extension, False):
        return None, "The selected file is not a valid image of that type."

    stored_name = f"{uuid4().hex}.{extension}"
    destination = os.path.join(current_app.config["UPLOAD_FOLDER"], stored_name)
    file.save(destination)
    return f"/static/images/uploads/{stored_name}", None


def _create_item(data, image_file):
    cleaned, error = _validate_item_form(data)
    if error:
        return None, error

    image_path, error = _save_uploaded_image(image_file)
    if error:
        return None, error

    try:
        cursor = get_db().execute(
            """
            INSERT INTO items
                (user_id, type, title, category, description, location,
                 event_date, image_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                g.current_user["id"],
                data["type"],
                cleaned["title"],
                cleaned["category"],
                cleaned["description"],
                cleaned["location"],
                cleaned["event_date"],
                image_path,
            ),
        )
        item_id = cursor.lastrowid
        cursor.close()
        row = _get_item(item_id)
        item = _row_dict(row)
        _store_matches_for_item(item, notify=True)
        matches = _matches_for(item, g.current_user["id"])
        return {
            "item": _item_dict(row, g.current_user["id"]),
            "matches": [
                {
                    "id": match["id"],
                    "item": match["match"],
                    "score": match["score"],
                    "confidence": match["confidence"],
                    "level": match["level"],
                    "reasons": match["reasons"],
                    "components": match["components"],
                    "status": match["status"],
                }
                for match in matches
            ],
        }, None
    except sqlite3.Error:
        get_db().rollback()
        if image_path:
            image_file_path = os.path.join(
                current_app.config["UPLOAD_FOLDER"],
                os.path.basename(image_path),
            )
            if os.path.exists(image_file_path):
                os.remove(image_file_path)
        raise


def _query_items(args):
    conditions = []
    values = []

    item_type = (args.get("type") or args.get("status") or "").strip().lower()
    if item_type in ("lost", "found"):
        conditions.append("type = ?")
        values.append(item_type)

    category = (args.get("category") or "").strip()
    if category:
        conditions.append("LOWER(category) = LOWER(?)")
        values.append(category)

    location = (args.get("location") or "").strip()
    if location:
        conditions.append("LOWER(COALESCE(location, '')) LIKE LOWER(?) ESCAPE '\\'")
        values.append("%" + _escape_like(location) + "%")

    event_date = (args.get("date") or "").strip()
    if event_date:
        try:
            date.fromisoformat(event_date)
        except ValueError:
            return None, "Enter a valid date filter."
        conditions.append("event_date = ?")
        values.append(event_date)

    status = (args.get("item_status") or "").strip().lower()
    if status and status in ("open", "recovered", "returned", "closed"):
        conditions.append("LOWER(status) = ?")
        values.append(status)

    search = (args.get("q") or "").strip()
    if search:
        pattern = "%" + _escape_like(search) + "%"
        conditions.append(
            "(title LIKE ? ESCAPE '\\' OR category LIKE ? ESCAPE '\\' "
            "OR location LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')"
        )
        values.extend([pattern] * 4)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = query_db(
        f"""
        SELECT id, user_id, type, title, category, description, location,
               event_date, image_path, status, created_at
        FROM items {where}
        ORDER BY created_at DESC, id DESC
        LIMIT 100
        """,
        tuple(values),
    )
    return rows, None


def _escape_like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _decorate_item(row):
    result = _item_dict(row, g.current_user["id"] if g.current_user else None)
    result["matches"] = [
        {
            "id": match["id"],
            "item": match["match"],
            "score": match["score"],
            "confidence": match["confidence"],
            "level": match["level"],
            "reasons": match["reasons"],
            "status": match["status"],
        }
        for match in _matches_for(
            _row_dict(row),
            g.current_user["id"] if g.current_user else None,
        )
    ]
    return result


def _get_matches_for_user(user_id):
    rows = _get_match_rows(
        "m.status != 'rejected' AND (l.user_id = ? OR f.user_id = ?)",
        (user_id, user_id),
    )
    matches = []
    for row in rows:
        match = _serialize_match(row, user_id)
        if row["lost_user_id"] == user_id:
            match["report"] = match["lost_item"]
            match["match"] = match["found_item"]
        else:
            match["report"] = match["found_item"]
            match["match"] = match["lost_item"]
        matches.append(match)
    return matches


def _render_page(**context):
    return render_template("page.html", **context)


def create_app(config_name="development", test_config=None):
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError(
            "SECRET_KEY is required. Set it in the environment before starting FINDIT."
        )
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=not (
            app.config.get("DEBUG", False) or app.config.get("TESTING", False)
        ),
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    )

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()
        _backfill_matches()

    @app.before_request
    def load_current_user():
        user_id = session.get("user_id")
        g.current_user = (
            query_db(
                "SELECT id, name, email, created_at FROM users WHERE id = ?",
                (user_id,),
                one=True,
            )
            if user_id
            else None
        )

    @app.context_processor
    def inject_template_context():
        return {"current_user": g.get("current_user"), "csrf_token": _csrf_token()}

    @app.errorhandler(RequestEntityTooLarge)
    def handle_upload_too_large(error):
        message = "That file is too large. Please choose an image smaller than 16 MB."
        if request.path.startswith("/api/"):
            return jsonify(error=message), 413
        flash(message, "error")
        return redirect(request.referrer or url_for("index"))

    @app.route("/")
    def index():
        rows = query_db(
            """
            SELECT id, user_id, type, title, category, description, location,
                   event_date, image_path, status, created_at
            FROM items WHERE LOWER(status) = 'open'
            ORDER BY created_at DESC, id DESC LIMIT 3
            """
        )
        totals = query_db(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN type = 'found' THEN 1 ELSE 0 END) AS found,
                SUM(CASE WHEN LOWER(status) IN ('recovered', 'returned', 'closed') THEN 1 ELSE 0 END) AS recovered
            FROM items
            """,
            one=True,
        )
        recent_items = [_decorate_item(row) for row in rows]
        hero_match = next(
            (
                {
                    "report": item,
                    "match": item_match["item"],
                    "confidence": item_match["confidence"],
                    "level": item_match["level"],
                    "reasons": item_match["reasons"],
                }
                for item in recent_items
                for item_match in item["matches"]
            ),
            None,
        )
        return render_template(
            "index.html",
            recent_items=recent_items,
            hero_match=hero_match,
            homepage_stats=_row_dict(totals),
            csrf_token=_csrf_token(),
        )

    @app.route("/find-items")
    @app.route("/lost")
    def lost_items():
        rows, error = _query_items(request.args)
        if error:
            rows = []
        categories = query_db(
            "SELECT DISTINCT category FROM items WHERE category IS NOT NULL AND TRIM(category) != '' ORDER BY category"
        )
        locations = query_db(
            "SELECT DISTINCT location FROM items WHERE location IS NOT NULL AND TRIM(location) != '' ORDER BY location"
        )
        return render_template(
            "page.html",
            page_title="Find a lost item",
            page_kicker="Lost & found discovery",
            page_intro="Browse recently reported items, filter by location and category, and spot the strongest match before a safe handoff.",
            page_category="items",
            page_slug="find-items",
            items=[_decorate_item(row) for row in rows],
            categories=[row["category"] for row in categories],
            locations=[row["location"] for row in locations],
            filter_error=error,
        )

    @app.route("/found")
    @app.route("/found-items")
    def found_items():
        return redirect(url_for("lost_items", type="found"))

    @app.route("/how-it-works")
    def how_it_works():
        return _render_page(
            page_title="How it works",
            page_kicker="Simple, thoughtful process",
            page_intro="From the first report to the final return, FINDIT makes lost-item recovery clear, respectful, and safe.",
            page_category="how",
            page_slug="how-it-works",
        )

    @app.route("/community")
    @app.route("/about")
    def community():
        return _render_page(
            page_title="Our community",
            page_kicker="Local stories",
            page_intro="Every found item brings people together. Our community helps reunite essentials, keepsakes, and personal belongings with care.",
            page_category="community",
            page_slug="community",
        )

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if request.method == "POST":
            csrf_error = _require_csrf()
            if csrf_error:
                return csrf_error
            name = (request.form.get("name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            if not name or len(name) > 100:
                flash("Enter your name (up to 100 characters).", "error")
            elif not EMAIL_PATTERN.fullmatch(email) or len(email) > 254:
                flash("Enter a valid email address.", "error")
            elif len(password) < 8:
                flash("Use a password with at least 8 characters.", "error")
            else:
                try:
                    user_id = execute_db(
                        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                        (name, email, generate_password_hash(password)),
                    )
                except sqlite3.IntegrityError:
                    flash("An account with that email already exists.", "error")
                else:
                    session.clear()
                    session["user_id"] = user_id
                    _csrf_token()
                    target = _safe_next_url(request.form.get("next"))
                    return redirect(target or url_for("dashboard"))
            return redirect(url_for("signup", next=request.form.get("next", "")))
        if g.current_user:
            return redirect(url_for("dashboard"))
        return _render_page(
            page_title="Create your account",
            page_kicker="Join FINDIT",
            page_intro="Create an account to report lost items, discover potential matches, and manage your activity securely.",
            page_category="auth",
            page_slug="signup",
            next_url=_safe_next_url(request.args.get("next")) or "",
        )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            csrf_error = _require_csrf()
            if csrf_error:
                return csrf_error
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            user = query_db(
                "SELECT id, password_hash FROM users WHERE email = ?",
                (email,),
                one=True,
            )
            if not user or not user["password_hash"] or not check_password_hash(user["password_hash"], password):
                flash("Email or password was not recognized.", "error")
                return redirect(url_for("login", next=request.form.get("next", "")))
            session.clear()
            session["user_id"] = user["id"]
            _csrf_token()
            target = _safe_next_url(request.form.get("next"))
            return redirect(target or url_for("dashboard"))
        if g.current_user:
            return redirect(url_for("dashboard"))
        return _render_page(
            page_title="Welcome back",
            page_kicker="Sign in",
            page_intro="Access your reports, matches, and account activity in one trusted place.",
            page_category="auth",
            page_slug="login",
            next_url=_safe_next_url(request.args.get("next")) or "",
        )

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        csrf_error = _require_csrf()
        if csrf_error:
            return csrf_error
        session.clear()
        flash("You have been signed out.", "success")
        return redirect(url_for("index"))

    @app.route("/report/lost")
    @login_required
    def report_lost():
        return _render_page(
            page_title="Tell us what you lost",
            page_kicker="Report a lost item",
            page_intro="Share the details you remember and upload a quick photo so the right person can recognize it.",
            page_category="report",
            page_slug="report-lost",
        )

    @app.route("/report/found")
    @login_required
    def report_found():
        return _render_page(
            page_title="You found something",
            page_kicker="Report a found item",
            page_intro="Add the item details, location, and timeframe to help connect it with the right owner.",
            page_category="report",
            page_slug="report-found",
        )

    @app.route("/dashboard")
    @login_required
    def dashboard():
        user_id = g.current_user["id"]
        own_items = query_db(
            """
            SELECT id, user_id, type, title, category, description, location,
                   event_date, image_path, status, created_at
            FROM items WHERE user_id = ? ORDER BY created_at DESC, id DESC
            """,
            (user_id,),
        )
        matches = _get_matches_for_user(user_id)
        recent_activity = [
            {
                "title": row["title"],
                "type": row["type"],
                "location": row["location"],
                "created_at": row["created_at"],
                "id": row["id"],
            }
            for row in own_items[:8]
        ]
        stats = {
            "lost": sum(1 for row in own_items if row["type"] == "lost"),
            "found": sum(1 for row in own_items if row["type"] == "found"),
            "matches": len(matches),
            "recovered": sum(
                1 for row in own_items
                if row["status"].lower() in ("recovered", "returned", "closed")
            ),
        }
        return _render_page(
            page_title="Your FINDIT workspace",
            page_kicker="Dashboard",
            page_intro="Track active reports, matches, saved items, and recent activity in one cleaner view.",
            page_category="dashboard",
            page_slug="dashboard",
            items=[_item_dict(row, user_id) for row in own_items],
            matches=matches,
            recent_activity=recent_activity,
            stats=stats,
        )

    @app.route("/matches")
    @login_required
    def matches():
        return _render_page(
            page_title="Potential matches",
            page_kicker="Recent matches",
            page_intro="Review probable matches and compare item details before taking the next step.",
            page_category="matches",
            page_slug="matches",
            matches=_get_matches_for_user(g.current_user["id"]),
        )

    @app.route("/profile")
    @login_required
    def profile():
        user_id = g.current_user["id"]
        rows = query_db(
            """
            SELECT id, user_id, type, title, category, description, location,
                   event_date, image_path, status, created_at
            FROM items WHERE user_id = ? ORDER BY created_at DESC, id DESC
            """,
            (user_id,),
        )
        return _render_page(
            page_title="Profile",
            page_kicker="Account",
            page_intro="Manage your reports, potential matches, and account details in one place.",
            page_category="account",
            page_slug="profile",
            items=[_item_dict(row, user_id) for row in rows],
            matches=_get_matches_for_user(user_id),
        )

    @app.route("/notifications")
    @login_required
    def notifications():
        rows = query_db(
            """
            SELECT id, title, message, is_read, created_at
            FROM notifications WHERE user_id = ? ORDER BY created_at DESC, id DESC
            """,
            (g.current_user["id"],),
        )
        return _render_page(
            page_title="Notifications",
            page_kicker="Inbox",
            page_intro="See new matches, status updates, and follow-ups from the FINDIT community.",
            page_category="account",
            page_slug="notifications",
            notifications=[_row_dict(row) for row in rows],
        )

    @app.route("/items/<int:item_id>")
    def item_detail(item_id):
        row = _get_item(item_id)
        if row is None:
            abort(404)
        owner = None
        if g.current_user and row["user_id"] == g.current_user["id"]:
            owner = g.current_user
        return _render_page(
            page_title=row["title"],
            page_kicker=f"{row['type'].title()} item",
            page_intro=row["description"] or "",
            page_category="detail",
            page_slug="item-detail",
            item=_item_dict(row, g.current_user["id"] if g.current_user else None),
            item_owner=owner,
            matches=_matches_for(
                _row_dict(row),
                g.current_user["id"] if g.current_user else None,
            ),
        )

    @app.route("/admin")
    def admin():
        return _render_page(
            page_title="Admin overview",
            page_kicker="Operations",
            page_intro="Review high-priority reports and platform activity.",
            page_category="admin",
            page_slug="admin",
        )

    @app.route("/api/items")
    def api_items():
        rows, error = _query_items(request.args)
        if error:
            return jsonify(error=error), 400
        return jsonify(items=[_decorate_item(row) for row in rows])

    @app.route("/api/items/<int:item_id>")
    def api_item_detail(item_id):
        row = _get_item(item_id)
        if row is None:
            return jsonify(error="That item could not be found."), 404
        return jsonify(
            item=_item_dict(row, g.current_user["id"] if g.current_user else None),
            matches=_matches_for(
                _row_dict(row),
                g.current_user["id"] if g.current_user else None,
            ),
        )

    @app.route("/api/items", methods=["POST"])
    @login_required
    def api_create_item():
        csrf_error = _require_csrf()
        if csrf_error:
            return csrf_error
        data = request.form if request.form else (request.get_json(silent=True) or {})
        item_type = (data.get("type") or "").strip().lower()
        if item_type not in ("lost", "found"):
            return jsonify(error="Choose whether the item was lost or found."), 400
        form_data = dict(data)
        form_data["type"] = item_type
        result, error = _create_item(form_data, request.files.get("image"))
        if error:
            return jsonify(error=error), 400
        return jsonify(**result), 201

    @app.route("/api/items/<int:item_id>/matches")
    def api_item_matches(item_id):
        row = _get_item(item_id)
        if row is None:
            return jsonify(error="That item could not be found."), 404
        return jsonify(matches=_matches_for(
            _row_dict(row),
            g.current_user["id"] if g.current_user else None,
        ))

    @app.route("/api/matches/<int:match_id>", methods=["GET", "PATCH"])
    @login_required
    def api_match_detail(match_id):
        match = _get_match_for_user(match_id, g.current_user["id"])
        if match is None:
            return jsonify(error="That match could not be found."), 404
        if request.method == "GET":
            return jsonify(match=match)

        csrf_error = _require_csrf()
        if csrf_error:
            return csrf_error
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="Send a JSON object containing the new match status."), 400
        status = (data.get("status") or "").strip().lower()
        if status not in MATCH_STATUSES:
            return jsonify(
                error="Choose a match status: Potential, Reviewed, Contacted, Resolved, or Rejected."
            ), 400
        execute_db("UPDATE matches SET status = ? WHERE id = ?", (status, match_id))
        updated = _get_match_for_user(match_id, g.current_user["id"])
        return jsonify(match=updated)

    @app.route("/matches/<int:match_id>")
    @login_required
    def match_detail(match_id):
        match = _get_match_for_user(match_id, g.current_user["id"])
        if match is None:
            abort(404)
        return _render_page(
            page_title=f"{match['level']} · {match['report']['title']}",
            page_kicker=match["level"],
            page_intro="Review the shared report details and the reasons this pair was matched.",
            page_category="match_detail",
            page_slug="match-detail",
            match=match,
            match_statuses=MATCH_STATUSES,
        )

    @app.route("/api/matches")
    @login_required
    def api_matches():
        return jsonify(matches=_get_matches_for_user(g.current_user["id"]))

    @app.route("/api/dashboard")
    @login_required
    def api_dashboard():
        user_id = g.current_user["id"]
        rows = query_db(
            """
            SELECT id, user_id, type, title, category, description, location,
                   event_date, image_path, status, created_at
            FROM items WHERE user_id = ? ORDER BY created_at DESC, id DESC
            """,
            (user_id,),
        )
        matches = _get_matches_for_user(user_id)
        return jsonify(
            stats={
                "lost_reports": sum(1 for row in rows if row["type"] == "lost"),
                "found_reports": sum(1 for row in rows if row["type"] == "found"),
                "potential_matches": len(matches),
                "recovered_items": sum(
                    1 for row in rows
                    if row["status"].lower() in ("recovered", "returned", "closed")
                ),
            },
            items=[_item_dict(row, user_id) for row in rows],
            matches=matches,
        )

    @app.route("/api/profile")
    @login_required
    def api_profile():
        user = {
            "id": g.current_user["id"],
            "name": g.current_user["name"],
            "email": g.current_user["email"],
            "created_at": g.current_user["created_at"],
        }
        items = query_db(
            """
            SELECT id, user_id, type, title, category, description, location,
                   event_date, image_path, status, created_at
            FROM items WHERE user_id = ? ORDER BY created_at DESC, id DESC
            """,
            (user["id"],),
        )
        return jsonify(
            user=user,
            lost_items=[_item_dict(row, user["id"]) for row in items if row["type"] == "lost"],
            found_items=[_item_dict(row, user["id"]) for row in items if row["type"] == "found"],
            matches=_get_matches_for_user(user["id"]),
        )

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    return app


if __name__ == "__main__":
    app = create_app("development")
    app.run(debug=True, host="0.0.0.0", port=5000)
else:
    app = create_app("production")
