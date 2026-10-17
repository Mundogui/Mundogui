"""SIAPD Matrix SaaS: questionários configuráveis, multiempresa e cobrança."""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone
from functools import wraps

import stripe
from flask import Flask, abort, flash, redirect, render_template, request, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy

PLAN_LIMITS = {
    "free": {"questionnaires": 1, "responses": 100},
    "pro": {"questionnaires": 10, "responses": 2000},
    "business": {"questionnaires": 100, "responses": 50000},
}
ALLOWED_QUESTION_COUNTS = {12, 14, 18, 20, 22, 24}

app = Flask(__name__)
app.config.update(
    # A deployment must set SECRET_KEY; a random fallback is safer than a shared default.
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32),
    SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///siapd.db"),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


class Organization(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    plan = db.Column(db.String(20), nullable=False, default="free")
    stripe_customer_id = db.Column(db.String(255), unique=True)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organization.id"), nullable=False, index=True)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    organization = db.relationship("Organization")

    def set_password(self, password):
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()
        self.password_hash = f"{salt.hex()}:{digest}"

    def check_password(self, password):
        salt, digest = self.password_hash.split(":")
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()
        return hmac.compare_digest(candidate, digest)


class Questionnaire(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organization.id"), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    public_token = db.Column(db.String(48), nullable=False, unique=True, index=True, default=lambda: secrets.token_urlsafe(24))
    is_published = db.Column(db.Boolean, default=False, nullable=False)
    organization = db.relationship("Organization")
    questions = db.relationship("Question", back_populates="questionnaire", order_by="Question.position", cascade="all, delete-orphan")


class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    questionnaire_id = db.Column(db.Integer, db.ForeignKey("questionnaire.id"), nullable=False, index=True)
    label = db.Column(db.String(500), nullable=False)
    axis = db.Column(db.String(1), nullable=False)  # X or Y
    position = db.Column(db.Integer, nullable=False)
    questionnaire = db.relationship("Questionnaire", back_populates="questions")


class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    questionnaire_id = db.Column(db.Integer, db.ForeignKey("questionnaire.id"), nullable=False, index=True)
    sector = db.Column(db.String(120), nullable=False, default="Geral")
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    answers = db.relationship("Answer", back_populates="submission", cascade="all, delete-orphan")


class Answer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(db.Integer, db.ForeignKey("submission.id"), nullable=False, index=True)
    question_id = db.Column(db.Integer, db.ForeignKey("question.id"), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    submission = db.relationship("Submission", back_populates="answers")


class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organization.id"), nullable=False, unique=True)
    stripe_subscription_id = db.Column(db.String(255), unique=True)
    status = db.Column(db.String(40), nullable=False, default="inactive")
    price_id = db.Column(db.String(255))


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def level_for(x_score, y_score, x_max, y_max):
    """Calcula quadrante por percentual, independentemente do total de fatores."""
    x_high, y_high = x_score > x_max / 2, y_score > y_max / 2
    return {(False, False): "Inicial", (True, False): "Gerenciado", (False, True): "Definido", (True, True): "Integrado"}[x_high, y_high]


def owner_required(view):
    @wraps(view)
    @login_required
    def wrapped(questionnaire_id, *args, **kwargs):
        questionnaire = db.session.get(Questionnaire, questionnaire_id) or abort(404)
        if questionnaire.organization_id != current_user.organization_id:
            abort(403)
        return view(questionnaire_id, *args, **kwargs)
    return wrapped


@app.get("/")
def home():
    return render_template("home.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password, organization_name = request.form["password"], request.form["organization"].strip()
        if len(password) < 12 or not organization_name or User.query.filter_by(email=email).first():
            flash("Use uma organização, e-mail único e senha com ao menos 12 caracteres.", "error")
        else:
            organization = Organization(name=organization_name)
            user = User(email=email, organization=organization)
            user.set_password(password)
            db.session.add_all([organization, user])
            db.session.commit()
            login_user(user)
            return redirect(url_for("dashboard"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form["email"].strip().lower()).first()
        if user and user.check_password(request.form["password"]):
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("Credenciais inválidas.", "error")
    return render_template("login.html")


@app.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("home"))


@app.get("/dashboard")
@login_required
def dashboard():
    questionnaires = Questionnaire.query.filter_by(organization_id=current_user.organization_id).all()
    return render_template("dashboard.html", questionnaires=questionnaires, limits=PLAN_LIMITS[current_user.organization.plan])


@app.route("/questionnaires/new", methods=["GET", "POST"])
@login_required
def new_questionnaire():
    if request.method == "POST":
        count = int(request.form.get("question_count", 0))
        existing = Questionnaire.query.filter_by(organization_id=current_user.organization_id).count()
        if count not in ALLOWED_QUESTION_COUNTS:
            flash("Escolha 12, 14, 18, 20, 22 ou 24 fatores.", "error")
        elif existing >= PLAN_LIMITS[current_user.organization.plan]["questionnaires"]:
            flash("O limite do seu plano foi atingido. Faça upgrade para criar mais questionários.", "error")
        else:
            questionnaire = Questionnaire(organization_id=current_user.organization_id, title=request.form["title"].strip())
            for position in range(count):
                db.session.add(Question(questionnaire=questionnaire, label=f"Fator {position + 1}", axis="Y" if position < count // 2 else "X", position=position))
            db.session.add(questionnaire)
            db.session.commit()
            return redirect(url_for("edit_questionnaire", questionnaire_id=questionnaire.id))
    return render_template("new_questionnaire.html", counts=sorted(ALLOWED_QUESTION_COUNTS))


@app.route("/questionnaires/<int:questionnaire_id>/edit", methods=["GET", "POST"])
@owner_required
def edit_questionnaire(questionnaire_id):
    questionnaire = db.session.get(Questionnaire, questionnaire_id)
    if request.method == "POST":
        questionnaire.title = request.form["title"].strip()
        questionnaire.is_published = request.form.get("is_published") == "on"
        for question in questionnaire.questions:
            question.label = request.form[f"label-{question.id}"].strip()
            question.axis = request.form[f"axis-{question.id}"]
            if question.axis not in {"X", "Y"}:
                abort(400, "Eixo inválido.")
        db.session.commit()
        flash("Questionário salvo.", "success")
    return render_template("edit_questionnaire.html", questionnaire=questionnaire)


@app.route("/q/<token>", methods=["GET", "POST"])
def respond(token):
    questionnaire = Questionnaire.query.filter_by(public_token=token, is_published=True).first_or_404()
    if request.method == "POST":
        month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        used = Submission.query.join(Questionnaire).filter(
            Questionnaire.organization_id == questionnaire.organization_id,
            Submission.created_at >= month_start,
        ).count()
        if used >= PLAN_LIMITS[questionnaire.organization.plan]["responses"]:
            abort(429, "O limite mensal de respostas deste questionário foi atingido.")
        submission = Submission(questionnaire_id=questionnaire.id, sector=request.form.get("sector", "Geral")[:120])
        db.session.add(submission)
        for question in questionnaire.questions:
            try:
                score = int(request.form[f"question-{question.id}"])
            except (KeyError, ValueError):
                abort(400, "Todas as respostas devem ser informadas entre 0 e 10.")
            if not 0 <= score <= 10:
                abort(400, "As notas devem estar entre 0 e 10.")
            db.session.add(Answer(submission=submission, question_id=question.id, score=score))
        db.session.commit()
        return redirect(url_for("result", submission_id=submission.id))
    return render_template("respond.html", questionnaire=questionnaire)


@app.get("/results/<int:submission_id>")
def result(submission_id):
    submission = db.session.get(Submission, submission_id) or abort(404)
    questionnaire = db.session.get(Questionnaire, submission.questionnaire_id)
    scores = {answer.question_id: answer.score for answer in submission.answers}
    x = sum(scores[q.id] for q in questionnaire.questions if q.axis == "X")
    y = sum(scores[q.id] for q in questionnaire.questions if q.axis == "Y")
    x_max = 10 * sum(q.axis == "X" for q in questionnaire.questions)
    y_max = 10 * sum(q.axis == "Y" for q in questionnaire.questions)
    return render_template("result.html", questionnaire=questionnaire, x=x, y=y, x_max=x_max, y_max=y_max, level=level_for(x, y, x_max, y_max))


@app.post("/billing/checkout/<plan>")
@login_required
def checkout(plan):
    price = os.environ.get(f"STRIPE_PRICE_{plan.upper()}")
    if plan not in {"pro", "business"} or not os.environ.get("STRIPE_SECRET_KEY") or not price:
        abort(503, "Stripe ainda não foi configurado para este plano.")
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    organization = current_user.organization
    session = stripe.checkout.Session.create(mode="subscription", line_items=[{"price": price, "quantity": 1}], customer=organization.stripe_customer_id or None, customer_email=None if organization.stripe_customer_id else current_user.email, success_url=url_for("dashboard", _external=True), cancel_url=url_for("dashboard", _external=True), metadata={"organization_id": str(organization.id), "plan": plan})
    return redirect(session.url, code=303)


@app.post("/billing/webhook")
def stripe_webhook():
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        abort(503)
    try:
        event = stripe.Webhook.construct_event(request.data, request.headers.get("Stripe-Signature", ""), secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        abort(400)
    data = event["data"]["object"]
    if event["type"] == "checkout.session.completed":
        org = db.session.get(Organization, int(data["metadata"]["organization_id"]))
        if org:
            org.plan, org.stripe_customer_id = data["metadata"]["plan"], data.get("customer")
            subscription = Subscription.query.filter_by(organization_id=org.id).first()
            if subscription is None:
                subscription = Subscription(organization_id=org.id)
                db.session.add(subscription)
            subscription.stripe_subscription_id = data.get("subscription")
            subscription.status = "active"
    elif event["type"] == "customer.subscription.deleted":
        subscription = Subscription.query.filter_by(stripe_subscription_id=data["id"]).first()
        if subscription:
            subscription.status = "canceled"
            db.session.get(Organization, subscription.organization_id).plan = "free"
    db.session.commit()
    return {"received": True}


with app.app_context():
    db.create_all()
