"""FastAPI backend.  Run:  cd backend && uvicorn main:app --reload
Env vars: DB_URL, AUTH_SECRET (required) | ADMIN_KEY (optional, enables /admin/retrain)"""
import hashlib
import hmac
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from recommender import HybridRecommender

DB_URL = os.environ["DB_URL"]            # mysql+pymysql://user:password@localhost/ib_reco
AUTH_SECRET = os.environ["AUTH_SECRET"]  # any long random string
ADMIN_KEY = os.getenv("ADMIN_KEY")

engine = create_engine(DB_URL, pool_pre_ping=True)
model = HybridRecommender(engine)
bearer = HTTPBearer()


@asynccontextmanager
async def lifespan(_):
    model.fit()
    yield


app = FastAPI(title="Investment Recommendation API", lifespan=lifespan)


# ---------- helpers ----------
def rows(sql, **params):
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql), params)]


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def login_response(row) -> dict:
    token = jwt.encode({"sub": str(row["client_id"]), "exp": int(time.time()) + 8 * 3600},
                       AUTH_SECRET, algorithm="HS256")
    return {**{k: row[k] for k in ("client_id", "full_name", "email", "risk_score", "risk_category")},
            "access_token": token}


def me(cred: HTTPAuthorizationCredentials = Depends(bearer)) -> int:
    """Returns the logged-in client's id from the token."""
    try:
        return int(jwt.decode(cred.credentials, AUTH_SECRET, algorithms=["HS256"])["sub"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired login session")


def risk_from_answers(a):
    score = round(1 + (sum(a) - 5) / 20 * 9)  # 5..25 -> 1..10
    return score, ("conservative" if score <= 3 else "moderate" if score <= 6 else "aggressive")


# ---------- request models (validation happens automatically) ----------
class RegisterIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8)
    date_of_birth: date
    annual_income: float = Field(gt=0)
    investment_horizon_yrs: int = Field(ge=1, le=50)
    answers: list[Annotated[int, Field(ge=1, le=5)]] = Field(min_length=5, max_length=5)
    advisor_id: int | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TxnIn(BaseModel):
    product_id: int
    txn_type: Literal["BUY", "SELL"]
    quantity: float = Field(gt=0)


class FeedbackIn(BaseModel):
    status: Literal["CLICKED", "ACCEPTED", "DISMISSED"]


# ---------- auth ----------
@app.post("/auth/register")
def register(b: RegisterIn):
    score, cat = risk_from_answers(b.answers)
    try:
        with engine.begin() as c:
            cid = c.execute(text(
                "INSERT INTO clients (advisor_id, full_name, email, password_hash, date_of_birth, "
                "annual_income, investment_horizon_yrs, risk_score, risk_category) "
                "VALUES (:adv,:n,:e,:ph,:dob,:inc,:h,:s,:cat)"),
                dict(adv=b.advisor_id, n=b.full_name.strip(), e=b.email.lower(),
                     ph=hash_password(b.password), dob=b.date_of_birth, inc=b.annual_income,
                     h=b.investment_horizon_yrs, s=score, cat=cat)).lastrowid
    except IntegrityError as e:
        if "uq_client_email" in str(e.orig):
            raise HTTPException(409, "An account with this email already exists")
        raise HTTPException(400, "Invalid advisor_id")
    return login_response(dict(client_id=cid, full_name=b.full_name.strip(), email=b.email.lower(),
                               risk_score=score, risk_category=cat))


@app.post("/auth/login")
def login(b: LoginIn):
    r = rows("SELECT client_id, full_name, email, password_hash, risk_score, risk_category "
             "FROM clients WHERE email=:e", e=b.email.lower())
    if not r or not r[0]["password_hash"] or not verify_password(b.password, r[0]["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    return login_response(r[0])


# ---------- logged-in client ("me") ----------
@app.get("/me")
def profile(cid: int = Depends(me)):
    r = rows("SELECT client_id, full_name, email, annual_income, investment_horizon_yrs, "
             "risk_score, risk_category FROM clients WHERE client_id=:i", i=cid)
    if not r:
        raise HTTPException(404, "client not found")
    return r[0]


@app.get("/me/recommendations")
def recommendations(k: int = Query(5, ge=1, le=20), cid: int = Depends(me)):
    r = rows("SELECT client_id, risk_score, annual_income FROM clients WHERE client_id=:i", i=cid)
    if not r:
        raise HTTPException(404, "client not found")
    items = model.recommend(r[0], k)
    with engine.begin() as c:  # one transaction for the whole batch
        for it in items:
            it["reco_id"] = c.execute(text(
                "INSERT INTO recommendations (client_id, product_id, model_version, score, reason) "
                "VALUES (:c,:p,:v,:s,:r)"),
                dict(c=cid, p=it["product_id"], v=model.VERSION, s=it["score"], r=it["reason"])).lastrowid
    return items


@app.post("/recommendations/{reco_id}/feedback")
def feedback(reco_id: int, b: FeedbackIn, cid: int = Depends(me)):
    with engine.begin() as c:  # ownership check is part of the UPDATE itself
        n = c.execute(text("UPDATE recommendations SET status=:s, responded_at=NOW() "
                           "WHERE reco_id=:i AND client_id=:c"),
                      dict(s=b.status, i=reco_id, c=cid)).rowcount
    if not n:
        raise HTTPException(404, "recommendation not found")
    return {"ok": True}


@app.get("/me/portfolio")
def portfolio(cid: int = Depends(me)):
    return rows("SELECT symbol, name, asset_class, quantity, avg_cost, invested, market_value, pnl "
                "FROM v_client_portfolio WHERE client_id=:i", i=cid)


@app.post("/me/transactions")
def trade(t: TxnIn, cid: int = Depends(me)):
    with engine.begin() as c:
        price = c.execute(text("SELECT current_price FROM products WHERE product_id=:p AND is_active=1"),
                          {"p": t.product_id}).scalar()
        if price is None:
            raise HTTPException(404, "product not found")
        if t.txn_type == "SELL":
            held = c.execute(text("SELECT quantity FROM holdings WHERE client_id=:c AND product_id=:p "
                                  "FOR UPDATE"), {"c": cid, "p": t.product_id}).scalar() or 0
            if float(held) < t.quantity:
                raise HTTPException(400, "cannot sell more than held")
        c.execute(text("INSERT INTO transactions (client_id, product_id, txn_type, quantity, price) "
                       "VALUES (:c,:p,:t,:q,:pr)"),
                  dict(c=cid, p=t.product_id, t=t.txn_type, q=t.quantity, pr=price))
    return {"ok": True, "price": float(price)}


# ---------- public / admin ----------
@app.get("/products")
def products(q: str | None = None):
    sql = ("SELECT product_id, symbol, name, risk_level, expected_return_pct, current_price "
           "FROM products WHERE is_active=1")
    if q:
        sql += " AND MATCH(name, description) AGAINST (:q IN NATURAL LANGUAGE MODE)"
    return rows(sql, **({"q": q} if q else {}))


# ---------- product details + price history (login required, like the other /me routes) ----------
PERIOD_DAYS = {"1m": 30, "6m": 182, "1y": 365, "5y": 1826}


def _num(v):
    return float(v) if isinstance(v, Decimal) else v


def product_row(pid: int) -> dict:
    r = rows("SELECT p.product_id, p.symbol, p.name, p.description, ac.name AS asset_class, p.risk_level, "
             "p.expected_return_pct, p.volatility_pct, p.expense_ratio_pct, p.min_investment, p.current_price "
             "FROM products p JOIN asset_classes ac ON ac.asset_class_id = p.asset_class_id "
             "WHERE p.product_id=:i AND p.is_active=1", i=pid)
    if not r:
        raise HTTPException(404, "product not found")
    return {k: _num(v) for k, v in r[0].items()}


@app.get("/products/{product_id}")
def product_detail(product_id: int, cid: int = Depends(me)):
    p = product_row(product_id)
    c = rows("SELECT risk_score, risk_category, investment_horizon_yrs, annual_income "
             "FROM clients WHERE client_id=:i", i=cid)
    if not c:
        raise HTTPException(404, "client not found")
    c = c[0]
    rs, cat, horizon = int(c["risk_score"]), c["risk_category"], int(c["investment_horizon_yrs"])
    income, rl = float(c["annual_income"]), int(p["risk_level"])

    # same rules the recommender uses as hard filters (recommender.py), so nothing here is invented
    risk_ok = rl <= rs + 1
    afford_ok = p["min_investment"] <= income * 0.10
    checks = [
        {"label": "Risk level", "ok": risk_ok,
         "detail": f"Product risk {rl}/10 vs your risk score {rs}/10. The model only suggests products "
                   f"up to your score + 1."},
        {"label": "Minimum investment", "ok": afford_ok,
         "detail": f"Minimum ₹{p['min_investment']:,.0f}. The model only suggests products whose minimum "
                   f"is at most 10% of your annual income (₹{income * 0.10:,.0f})."},
        {"label": "Time horizon", "ok": not (rl >= 7 and horizon < 3),
         "detail": f"Your investment horizon is {horizon} year(s). "
                   + ("Higher-risk products can fall sharply over short periods."
                      if rl >= 7 and horizon < 3 else "No short-horizon conflict with this risk level.")},
    ]
    p["suitability"] = {
        "summary": f"Your risk profile is {cat.title()} ({rs}/10), your investment horizon is {horizon} "
                   f"year(s), and this product's risk level is {rl}/10, which is "
                   f"{'within' if risk_ok else 'above'} the model's ceiling for your profile (your score + 1).",
        "checks": checks,
    }

    notes = [f"Risk level {rl}/10 is rated {'low' if rl <= 3 else 'moderate' if rl <= 6 else 'high'} "
             f"on this platform's scale.",
             f"Volatility is {p['volatility_pct']:.1f}% per year: a higher number means larger price swings.",
             f"Expected return ({p['expected_return_pct']:.1f}%) is a model assumption, not a guarantee. "
             f"Actual returns can be lower, including losses."]
    if p["expense_ratio_pct"] > 0:
        notes.append(f"Expense ratio of {p['expense_ratio_pct']:.2f}% a year is deducted from returns.")
    if p["asset_class"] == "Equity":
        notes.append("Single-company exposure: less diversified than a fund or index ETF.")
    if p["asset_class"] == "Alternative":
        notes.append("Alternative assets can be harder to value and may be less liquid.")
    p["risk_notes"] = notes
    return p


@app.get("/products/{product_id}/history")
def product_history(product_id: int, period: Literal["1m", "6m", "1y", "5y"] = "1y",
                    cid: int = Depends(me)):
    product_row(product_id)  # 404 if unknown / inactive
    latest = rows("SELECT MAX(price_date) AS d FROM product_prices WHERE product_id=:p", p=product_id)[0]["d"]
    empty = {"product_id": product_id, "period": period, "currency": "INR", "data_source": None,
             "is_demo": False, "points": [], "summary": None}
    if latest is None:
        return empty
    # window is measured back from the newest stored price, so it stays valid even if data is not refreshed daily
    data = rows("SELECT price_date, open_price, high_price, low_price, close_price, volume, data_source "
                "FROM product_prices WHERE product_id=:p AND price_date >= :cut ORDER BY price_date",
                p=product_id, cut=latest - timedelta(days=PERIOD_DAYS[period]))
    if not data:
        return empty
    points = [{"date": d["price_date"].isoformat(), "open": float(d["open_price"]),
               "high": float(d["high_price"]), "low": float(d["low_price"]),
               "close": float(d["close_price"]),
               "volume": int(d["volume"]) if d["volume"] is not None else None} for d in data]
    closes = [x["close"] for x in points]
    peak, max_dd = closes[0], 0.0
    for v in closes:
        peak = max(peak, v)
        max_dd = min(max_dd, (v - peak) / peak * 100)
    src = data[-1]["data_source"]
    return {**empty, "data_source": src, "is_demo": src.startswith("DEMO"), "points": points,
            "summary": {"start_date": points[0]["date"], "end_date": points[-1]["date"],
                        "start_close": closes[0], "end_close": closes[-1],
                        "change_pct": round((closes[-1] / closes[0] - 1) * 100, 2),
                        "period_high": max(x["high"] for x in points),
                        "period_low": min(x["low"] for x in points),
                        "max_drawdown_pct": round(max_dd, 2)}}


@app.post("/admin/retrain")
def retrain(x_admin_key: str = Header("")):
    if not ADMIN_KEY or not hmac.compare_digest(x_admin_key, ADMIN_KEY):
        raise HTTPException(403, "Forbidden")
    model.fit()
    with engine.begin() as c:
        c.execute(text("INSERT INTO model_runs (model_version, n_clients, n_products) VALUES (:v,:a,:b)"),
                  dict(v=model.VERSION, a=len(model.M), b=len(model.products)))
    return {"clients": len(model.M), "products": len(model.products)}
