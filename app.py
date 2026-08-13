from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import parse_qs, quote, urlparse

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Boolean, DateTime, String, Text, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from werkzeug.middleware.proxy_fix import ProxyFix


load_dotenv()


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""


db = SQLAlchemy(model_class=Base)
ViewFunction = TypeVar("ViewFunction", bound=Callable[..., Any])


class Candle(db.Model):
    """A virtual candle and tribute/condolence message."""

    id: Mapped[int] = mapped_column(primary_key=True)
    participant_name: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    is_visible: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
    )


class TributeAcknowledgement(db.Model):
    """A family acknowledgement attached to one tribute."""

    id: Mapped[int] = mapped_column(primary_key=True)
    candle_id: Mapped[int] = mapped_column(
        nullable=False,
        unique=True,
        index=True,
    )
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


def _database_uri() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()

    if database_url:
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)

    database_path = Path(
        os.getenv(
            "DATABASE_PATH",
            str(Path("instance") / "memorial.db"),
        )
    ).expanduser()

    database_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{database_path.resolve()}"


def create_app() -> Flask:
    """Create and configure the memorial application."""
    application = Flask(__name__)
    application.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY") or secrets.token_hex(32),
        SQLALCHEMY_DATABASE_URI=_database_uri(),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        MAX_CONTENT_LENGTH=64 * 1024,
    )

    application.wsgi_app = ProxyFix(
        application.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
    )

    db.init_app(application)
    register_template_helpers(application)
    register_routes(application)

    with application.app_context():
        db.create_all()

    return application


def safe_https_url(value: str | None) -> str:
    candidate = (value or "").strip()
    return candidate if candidate.lower().startswith("https://") else ""


def whatsapp_phone_url(phone: str, memorial_name: str) -> str:
    digits = "".join(character for character in phone if character.isdigit())

    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = f"254{digits[1:]}"

    if len(digits) < 10:
        return ""

    message = (
        "Hello. I would like to confirm or ask about family support for "
        f"{memorial_name}."
    )
    return f"https://wa.me/{digits}?text={quote(message)}"


def application_static_folder() -> str:
    """Return the application's static folder path."""
    return str(Path(__file__).resolve().parent / "static")


def video_embed_url(value: str | None) -> str:
    candidate = safe_https_url(value)
    if not candidate:
        return ""

    parsed = urlparse(candidate)
    host = parsed.netloc.lower().removeprefix("www.")
    video_id = ""

    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith("/live/"):
            video_id = parsed.path.split("/live/", 1)[1].split("/")[0]
        elif parsed.path.startswith("/embed/"):
            video_id = parsed.path.split("/embed/", 1)[1].split("/")[0]

    if video_id:
        safe_id = "".join(ch for ch in video_id if ch.isalnum() or ch in "-_")
        if safe_id:
            return f"https://www.youtube.com/embed/{safe_id}"

    return ""


def memorial_settings() -> dict[str, Any]:
    memorial_name = os.getenv(
        "MEMORIAL_NAME",
        "Our Beloved Mum",
    ).strip()

    organizer_phone = os.getenv("ORGANIZER_PHONE", "").strip()

    livestream_url = safe_https_url(os.getenv("LIVESTREAM_URL", ""))
    video_memories = [
        item.strip()
        for item in os.getenv("VIDEO_MEMORIES", "").split(",")
        if safe_https_url(item.strip())
    ]

    gallery_folder = Path(application_static_folder()) / "gallery"
    allowed_extensions = {".jpg", ".jpeg", ".png", ".webp"}

    gallery_photos = []
    if gallery_folder.exists():
        gallery_photos = [
            f"/static/gallery/{photo.name}"
            for photo in sorted(
                gallery_folder.iterdir(),
                key=lambda photo: photo.name.lower(),
            )
            if photo.is_file()
            and photo.suffix.lower() in allowed_extensions
        ]

    if not gallery_photos:
        gallery_photos = ["/static/mum.jpg"]

    return {
        "memorial_name": memorial_name,
        "memorial_message": os.getenv(
            "MEMORIAL_MESSAGE",
            "Forever loved, forever remembered, forever in our hearts.",
        ).strip(),
        "burial_date": os.getenv(
            "BURIAL_DATE",
            "Burial details will be shared with family and friends.",
        ).strip(),
        "burial_venue": os.getenv("BURIAL_VENUE", "").strip(),
        "burial_datetime": os.getenv("BURIAL_DATETIME", "").strip(),
        "burial_map_url": safe_https_url(os.getenv("BURIAL_MAP_URL", "")),
        "photo_url": os.getenv("PHOTO_URL", "").strip(),
        "mpesa_number": os.getenv("MPESA_NUMBER", "07XXXXXXXX").strip(),
        "mpesa_name": os.getenv(
            "MPESA_NAME",
            "Family Representative",
        ).strip(),
        "contribution_purpose": os.getenv(
            "CONTRIBUTION_PURPOSE",
            "Burial and funeral support",
        ).strip(),
        "organizer_phone": organizer_phone,
        "organizer_whatsapp_url": whatsapp_phone_url(
            organizer_phone,
            memorial_name,
        ),
        "whatsapp_group_name": os.getenv(
            "WHATSAPP_GROUP_NAME",
            "Family Burial Contribution Group",
        ).strip(),
        "whatsapp_group_url": safe_https_url(
            os.getenv("WHATSAPP_GROUP_URL", "")
        ),
        "family_update": os.getenv(
            "FAMILY_UPDATE",
            "Join the official WhatsApp group for contribution updates, "
            "transport coordination and family announcements.",
        ).strip(),
        "family_acknowledgement": os.getenv(
            "FAMILY_ACKNOWLEDGEMENT",
            "Our family is deeply grateful to everyone who stood with us, "
            "shared memories, sent messages, prayed with us, contributed, "
            "travelled and honoured this life with us. Every tribute on this "
            "memorial is part of a story we will continue to carry with love.",
        ).strip(),
        "livestream_url": livestream_url,
        "livestream_embed_url": video_embed_url(livestream_url),
        "video_memories": [
            {"url": video_url, "embed_url": video_embed_url(video_url)}
            for video_url in video_memories
        ],
        "eulogy_filename": os.getenv(
            "EULOGY_FILENAME",
            "eulogy.pdf",
        ).strip(),
        "gallery_photos": gallery_photos,
    }


def register_template_helpers(application: Flask) -> None:
    @application.context_processor
    def inject_common_values() -> dict[str, Any]:
        return {
            **memorial_settings(),
            "csrf_token": csrf_token,
            "current_year": datetime.now(timezone.utc).year,
        }


def csrf_token() -> str:
    token = session.get("_csrf_token")

    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token

    return str(token)


def validate_csrf() -> None:
    expected = session.get("_csrf_token", "")
    supplied = request.form.get("_csrf_token", "")

    if not expected or not hmac.compare_digest(str(expected), supplied):
        abort(400, description="The form expired. Refresh and try again.")


def client_ip_hash() -> str:
    client_ip = request.remote_addr or "unknown"
    secret = str(app.config["SECRET_KEY"]).encode("utf-8")

    return hmac.new(
        secret,
        client_ip.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def normalize_text(value: str | None, maximum_length: int) -> str:
    return " ".join((value or "").split())[:maximum_length]


def candle_rate_limited(ip_hash: str) -> bool:
    cooldown_seconds = max(
        5,
        int(os.getenv("CANDLE_COOLDOWN_SECONDS", "30")),
    )
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=cooldown_seconds)

    recent_candle = db.session.scalar(
        select(Candle.id)
        .where(
            Candle.ip_hash == ip_hash,
            Candle.created_at >= cutoff,
        )
        .limit(1)
    )

    return recent_candle is not None


def admin_required(view: ViewFunction) -> ViewFunction:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not session.get("memorial_admin"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


def register_routes(application: Flask) -> None:
    @application.get("/")
    def memorial_page() -> str:
        candles = db.session.scalars(
            select(Candle)
            .where(Candle.is_visible.is_(True))
            .order_by(Candle.created_at.desc())
            .limit(200)
        ).all()

        candle_count = db.session.scalar(
            select(func.count(Candle.id)).where(
                Candle.is_visible.is_(True)
            )
        ) or 0

        settings = memorial_settings()
        photo_url = settings["photo_url"] or url_for(
            "static",
            filename="mum-placeholder.svg",
        )

        eulogy_path = Path(application.static_folder or "static") / settings["eulogy_filename"]
        eulogy_available = eulogy_path.exists()

        owned_candle_ids = set(session.get("owned_candles", []))
        acknowledged_candle_ids = set(
            db.session.scalars(
                select(TributeAcknowledgement.candle_id)
            ).all()
        )

        current_ip_hash = client_ip_hash()
        for candle in candles:
            if candle.ip_hash == current_ip_hash:
                owned_candle_ids.add(candle.id)

        return render_template(
            "index.html",
            candles=candles,
            candle_count=candle_count,
            photo_url=photo_url,
            eulogy_available=eulogy_available,
            owned_candle_ids=owned_candle_ids,
            acknowledged_candle_ids=acknowledged_candle_ids,
        )

    @application.post("/light-candle")
    def light_candle() -> Any:
        validate_csrf()

        if request.form.get("website"):
            return redirect(url_for("memorial_page"))

        participant_name = normalize_text(
            request.form.get("participant_name"),
            80,
        )
        message = normalize_text(
            request.form.get("message"),
            600,
        )

        if not participant_name:
            participant_name = "Anonymous"

        ip_hash = client_ip_hash()

        if candle_rate_limited(ip_hash):
            flash(
                "Your previous entry was received. Please wait before submitting another.",
                "warning",
            )
            return redirect(url_for("memorial_page") + "#light-candle")

        candle = Candle(
            participant_name=participant_name,
            message=message,
            ip_hash=ip_hash,
        )
        db.session.add(candle)
        db.session.commit()

        owned_candles = session.get("owned_candles", [])
        if candle.id not in owned_candles:
            owned_candles.append(candle.id)
        session["owned_candles"] = owned_candles[-50:]
        session.modified = True

        flash(
            "Your tribute has been added and a memorial candle is now shining beside it.",
            "success",
        )
        return redirect(url_for("memorial_page") + f"#tribute-{candle.id}")

    @application.get("/eulogy")
    def read_eulogy() -> Any:
        settings = memorial_settings()
        eulogy_path = Path(application.static_folder or "static") / settings["eulogy_filename"]

        if not eulogy_path.exists():
            abort(404, description="The eulogy has not yet been uploaded.")

        return send_from_directory(
            application.static_folder,
            settings["eulogy_filename"],
            as_attachment=False,
        )

    @application.get("/eulogy/download")
    def download_eulogy() -> Any:
        settings = memorial_settings()
        eulogy_path = Path(application.static_folder or "static") / settings["eulogy_filename"]

        if not eulogy_path.exists():
            abort(404, description="The eulogy has not yet been uploaded.")

        return send_from_directory(
            application.static_folder,
            settings["eulogy_filename"],
            as_attachment=True,
            download_name=f"{settings['memorial_name']} - Eulogy.pdf",
        )

    @application.post("/tributes/<int:candle_id>/edit")
    def edit_own_tribute(candle_id: int) -> Any:
        validate_csrf()

        owned_candle_ids = set(session.get("owned_candles", []))
        candle = db.get_or_404(Candle, candle_id)

        owns_by_session = candle_id in owned_candle_ids
        owns_by_ip = candle.ip_hash == client_ip_hash()

        if not (owns_by_session or owns_by_ip):
            abort(
                403,
                description="You can only edit tributes submitted from this browser or connection.",
            )

        participant_name = normalize_text(
            request.form.get("participant_name"),
            80,
        )
        message = normalize_text(
            request.form.get("message"),
            600,
        )

        if not participant_name:
            participant_name = "Anonymous"

        if not message:
            flash("Please enter a tribute before saving.", "warning")
            return redirect(url_for("memorial_page") + f"#tribute-{candle_id}")

        candle.participant_name = participant_name
        candle.message = message
        db.session.commit()

        flash("Your tribute has been updated.", "success")
        return redirect(url_for("memorial_page") + f"#tribute-{candle_id}")

    @application.route("/admin/login", methods=["GET", "POST"])
    def admin_login() -> Any:
        configured_password = os.getenv("ADMIN_PASSWORD", "")

        if not configured_password:
            abort(
                503,
                description="Set ADMIN_PASSWORD before using moderation.",
            )

        if request.method == "POST":
            validate_csrf()
            supplied_password = request.form.get("password", "")

            if hmac.compare_digest(
                configured_password,
                supplied_password,
            ):
                session.clear()
                session["memorial_admin"] = True
                csrf_token()
                return redirect(url_for("admin_dashboard"))

            flash("Incorrect moderation password.", "error")

        return render_template("admin_login.html")

    @application.get("/admin")
    @admin_required
    def admin_dashboard() -> str:
        candles = db.session.scalars(
            select(Candle).order_by(Candle.created_at.desc())
        ).all()

        acknowledged_candle_ids = set(
            db.session.scalars(
                select(TributeAcknowledgement.candle_id)
            ).all()
        )

        return render_template(
            "admin.html",
            candles=candles,
            acknowledged_candle_ids=acknowledged_candle_ids,
        )

    @application.post("/admin/candles/<int:candle_id>/acknowledge")
    @admin_required
    def acknowledge_candle(candle_id: int) -> Any:
        validate_csrf()
        db.get_or_404(Candle, candle_id)

        acknowledgement = db.session.scalar(
            select(TributeAcknowledgement).where(
                TributeAcknowledgement.candle_id == candle_id
            )
        )

        if acknowledgement:
            db.session.delete(acknowledgement)
            flash("Family acknowledgement removed.", "success")
        else:
            db.session.add(TributeAcknowledgement(candle_id=candle_id))
            flash("Tribute acknowledged with love.", "success")

        db.session.commit()
        return redirect(url_for("admin_dashboard") + f"#entry-{candle_id}")

    @application.post("/admin/candles/<int:candle_id>/toggle")
    @admin_required
    def toggle_candle(candle_id: int) -> Any:
        validate_csrf()
        candle = db.get_or_404(Candle, candle_id)
        candle.is_visible = not candle.is_visible
        db.session.commit()

        flash("Entry visibility updated.", "success")
        return redirect(url_for("admin_dashboard"))

    @application.post("/admin/candles/<int:candle_id>/edit")
    @admin_required
    def edit_candle(candle_id: int) -> Any:
        validate_csrf()
        candle = db.get_or_404(Candle, candle_id)

        participant_name = normalize_text(
            request.form.get("participant_name"),
            80,
        )
        message = normalize_text(
            request.form.get("message"),
            600,
        )

        if not participant_name:
            participant_name = "Anonymous"

        if not message:
            flash("A tribute message is required.", "warning")
            return redirect(url_for("admin_dashboard") + f"#entry-{candle_id}")

        candle.participant_name = participant_name
        candle.message = message
        db.session.commit()

        flash("Tribute updated successfully.", "success")
        return redirect(url_for("admin_dashboard") + f"#entry-{candle_id}")

    @application.post("/admin/candles/<int:candle_id>/delete")
    @admin_required
    def delete_candle(candle_id: int) -> Any:
        validate_csrf()
        candle = db.get_or_404(Candle, candle_id)

        acknowledgement = db.session.scalar(
            select(TributeAcknowledgement).where(
                TributeAcknowledgement.candle_id == candle_id
            )
        )
        if acknowledgement:
            db.session.delete(acknowledgement)

        db.session.delete(candle)
        db.session.commit()

        flash("Entry deleted.", "success")
        return redirect(url_for("admin_dashboard"))

    @application.post("/admin/logout")
    @admin_required
    def admin_logout() -> Any:
        validate_csrf()
        session.clear()
        return redirect(url_for("memorial_page"))

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.errorhandler(400)
    def bad_request(error: Exception) -> tuple[str, int]:
        return render_template(
            "error.html",
            title="Request could not be completed",
            message=str(error),
        ), 400

    @application.errorhandler(403)
    def forbidden(error: Exception) -> tuple[str, int]:
        return render_template(
            "error.html",
            title="Editing not available",
            message=str(error),
        ), 403

    @application.errorhandler(404)
    def not_found(error: Exception) -> tuple[str, int]:
        return render_template(
            "error.html",
            title="Page not found",
            message=str(error),
        ), 404

    @application.errorhandler(500)
    def server_error(error: Exception) -> tuple[str, int]:
        application.logger.exception(
            "Unexpected memorial application error: %s",
            error,
        )
        db.session.rollback()

        return render_template(
            "error.html",
            title="Temporary problem",
            message="Please refresh the page and try again.",
        ), 500


app = create_app()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG") == "1",
    )
