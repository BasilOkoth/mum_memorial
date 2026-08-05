from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from sqlalchemy import Boolean, DateTime, String, Text, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from werkzeug.middleware.proxy_fix import ProxyFix


load_dotenv()


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""


db = SQLAlchemy(model_class=Base)
ViewFunction = TypeVar("ViewFunction", bound=Callable[..., Any])


class Candle(db.Model):
    """A virtual candle and optional tribute message."""

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


def memorial_settings() -> dict[str, str]:
    """Return memorial content configured through environment variables."""
    return {
        "memorial_name": os.getenv(
            "MEMORIAL_NAME",
            "Our Beloved Mum",
        ).strip(),
        "memorial_message": os.getenv(
            "MEMORIAL_MESSAGE",
            "Forever loved, forever remembered, forever in our hearts.",
        ).strip(),
        "burial_date": os.getenv(
            "BURIAL_DATE",
            "Burial details will be shared with family and friends.",
        ).strip(),
        "burial_venue": os.getenv("BURIAL_VENUE", "").strip(),
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
        "organizer_phone": os.getenv("ORGANIZER_PHONE", "").strip(),
    }


def register_template_helpers(application: Flask) -> None:
    """Register helpers used by templates."""

    @application.context_processor
    def inject_common_values() -> dict[str, Any]:
        return {
            **memorial_settings(),
            "csrf_token": csrf_token,
            "current_year": datetime.now(timezone.utc).year,
        }


def csrf_token() -> str:
    """Return a stable CSRF token for the current browser session."""
    token = session.get("_csrf_token")

    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token

    return str(token)


def validate_csrf() -> None:
    """Reject missing or invalid CSRF tokens."""
    expected = session.get("_csrf_token", "")
    supplied = request.form.get("_csrf_token", "")

    if not expected or not hmac.compare_digest(str(expected), supplied):
        abort(400, description="The form expired. Refresh and try again.")


def client_ip_hash() -> str:
    """Create a non-reversible identifier for anti-spam throttling."""
    client_ip = request.remote_addr or "unknown"
    secret = str(app.config["SECRET_KEY"]).encode("utf-8")

    return hmac.new(
        secret,
        client_ip.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def normalize_text(value: str | None, maximum_length: int) -> str:
    """Normalize user text and enforce a maximum length."""
    return " ".join((value or "").split())[:maximum_length]


def candle_rate_limited(ip_hash: str) -> bool:
    """Allow one candle per visitor during the configured cooldown."""
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
    """Require an authenticated memorial moderator."""

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not session.get("memorial_admin"):
            return redirect(url_for("admin_login", next=request.path))

        return view(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


def register_routes(application: Flask) -> None:
    """Register public and moderation routes."""

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

        return render_template(
            "index.html",
            candles=candles,
            candle_count=candle_count,
            photo_url=photo_url,
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
        message = normalize_text(request.form.get("message"), 280)

        if not participant_name:
            participant_name = "Anonymous"

        ip_hash = client_ip_hash()

        if candle_rate_limited(ip_hash):
            flash(
                "Your previous candle was received. "
                "Please wait before lighting another.",
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

        flash(
            "Your candle is now shining in loving memory. "
            "Thank you for participating.",
            "success",
        )
        return redirect(url_for("memorial_page") + "#candle-wall")

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

        return render_template("admin.html", candles=candles)

    @application.post("/admin/candles/<int:candle_id>/toggle")
    @admin_required
    def toggle_candle(candle_id: int) -> Any:
        validate_csrf()
        candle = db.get_or_404(Candle, candle_id)
        candle.is_visible = not candle.is_visible
        db.session.commit()

        flash("Candle visibility updated.", "success")
        return redirect(url_for("admin_dashboard"))

    @application.post("/admin/candles/<int:candle_id>/delete")
    @admin_required
    def delete_candle(candle_id: int) -> Any:
        validate_csrf()
        candle = db.get_or_404(Candle, candle_id)
        db.session.delete(candle)
        db.session.commit()

        flash("Candle deleted.", "success")
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

    @application.errorhandler(404)
    def not_found(error: Exception) -> tuple[str, int]:
        return render_template(
            "error.html",
            title="Page not found",
            message="The requested page does not exist.",
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
