"""Streamlit frontend. Run: streamlit run app.py"""
import html
import os
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

API = os.getenv("API_URL", "http://localhost:8000")
st.set_page_config(page_title="InvestIQ - AI Investment Advisor", page_icon="📈", layout="wide")

NAV_RECO, NAV_PORT, NAV_TRADE = "🤖 Recommendations", "📊 Portfolio", "💱 Trade"
NAV = [NAV_RECO, NAV_PORT, NAV_TRADE]
PERIODS = {"1M": "1m", "6M": "6m", "1Y": "1y", "5Y": "5y"}

st.markdown(
    """
<style>
.block-container{padding-top:2rem;max-width:1200px}
.hero{background:linear-gradient(120deg,#1e3a8a 0%,#6d28d9 55%,#be185d 100%);padding:1.5rem 2rem;
      border-radius:18px;margin-bottom:1.2rem;color:#fff}
.hero h1{margin:0;font-size:2rem;color:#fff}
.hero p{margin:.3rem 0 0;opacity:.85}
.badge{display:inline-block;padding:2px 11px;border-radius:999px;font-size:.78rem;font-weight:600;margin:0 6px 4px 0}
.b-low{background:rgba(34,197,94,.18);color:#4ade80}
.b-mid{background:rgba(245,158,11,.18);color:#fbbf24}
.b-high{background:rgba(239,68,68,.18);color:#f87171}
.b-info{background:rgba(99,102,241,.22);color:#a5b4fc}
.kpi{background:rgba(148,163,184,.08);border:1px solid rgba(148,163,184,.22);border-radius:14px;padding:.85rem 1.1rem}
.kpi .l{font-size:.78rem;opacity:.65}
.kpi .v{font-size:1.45rem;font-weight:700;line-height:1.35}
.kpi .s{font-size:.75rem;opacity:.6}
.demo{background:rgba(245,158,11,.12);border:1px solid rgba(245,158,11,.45);color:#fbbf24;
      border-radius:10px;padding:.5rem .9rem;font-size:.85rem;margin-bottom:.6rem}
.sym{font-size:1.15rem;font-weight:700}
div[data-testid="stVerticalBlockBorderWrapper"]{border-radius:14px}
</style>
""",
    unsafe_allow_html=True,
)


def call(method, path, **kw):
    headers = dict(kw.pop("headers", {}) or {})
    token = st.session_state.get("access_token")
    if token and not path.startswith("/auth/"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.request(method, API + path, timeout=30, headers=headers, **kw)
    except requests.RequestException as exc:
        st.error(f"Could not connect to the backend: {exc}")
        return None
    if not r.ok:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        st.error(detail)
        return None
    return r.json()


def logout():
    st.session_state.clear()
    st.rerun()


def inr(x):
    return f"₹{float(x):,.2f}"


def risk_badge(level):
    cls = "b-low" if level <= 3 else "b-mid" if level <= 6 else "b-high"
    return f'<span class="badge {cls}">Risk {level}/10</span>'


def kpi(label, value, sub=""):
    st.markdown(
        f'<div class="kpi"><div class="l">{label}</div><div class="v">{value}</div>'
        f'<div class="s">{sub}</div></div>',
        unsafe_allow_html=True,
    )


def hero(title, subtitle):
    st.markdown(
        f'<div class="hero"><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p></div>',
        unsafe_allow_html=True,
    )


# -----------------------------
# AUTHENTICATION PAGE
# -----------------------------
if not st.session_state.get("authenticated"):
    hero("📈 InvestIQ", "AI-powered investment product recommendations built around your risk profile")

    login_tab, register_tab = st.tabs(["🔐 Login", "📝 Create Account"])

    with login_tab:
        st.subheader("Welcome back")
        st.write("Login to access your personal investment dashboard.")

        with st.form("login_form"):
            email = st.text_input("Email", placeholder="you@example.com")
            password = st.text_input("Password", type="password", placeholder="Enter your password")
            submitted = st.form_submit_button("Login", use_container_width=True)

        if submitted:
            if not email.strip() or not password:
                st.error("Please enter both email and password.")
            else:
                result = call("POST", "/auth/login", json={"email": email.strip(), "password": password})
                if result:
                    st.session_state["authenticated"] = True
                    st.session_state["client"] = result
                    st.session_state["access_token"] = result["access_token"]
                    st.session_state.pop("recos", None)
                    st.success(f"Welcome back, {result['full_name']}!")
                    st.rerun()

    with register_tab:
        st.subheader("Create your InvestIQ account")
        st.write("Tell us about yourself so we can calculate your investment risk profile.")

        with st.form("register_form"):
            name = st.text_input("Full name")
            email = st.text_input("Email")
            password = st.text_input("Password", type="password", help="Minimum 8 characters")
            confirm_password = st.text_input("Confirm password", type="password")
            dob = st.date_input("Date of birth", value=pd.Timestamp("2000-01-01"))
            income = st.number_input("Annual income (₹)", min_value=1.0, value=800000.0, step=50000.0)
            horizon = st.slider("Investment horizon (years)", 1, 30, 5)

            st.markdown("### Risk assessment")
            questions = [
                "I stay calm when markets fall 20%",
                "I prefer high returns over safety",
                "I can lock money for 5+ years",
                "I have a stable income and savings buffer",
                "I understand stocks and volatility",
            ]
            answers = [st.slider(q, 1, 5, 3) for q in questions]

            create = st.form_submit_button("Create Account", use_container_width=True)

        if create:
            if not name.strip() or not email.strip() or not password:
                st.error("Please complete all required fields.")
            elif password != confirm_password:
                st.error("Passwords do not match.")
            elif len(password) < 8:
                st.error("Password must be at least 8 characters.")
            else:
                result = call(
                    "POST",
                    "/auth/register",
                    json={
                        "full_name": name.strip(),
                        "email": email.strip(),
                        "password": password,
                        "date_of_birth": str(dob),
                        "annual_income": income,
                        "investment_horizon_yrs": horizon,
                        "answers": answers,
                    },
                )
                if result:
                    st.session_state["authenticated"] = True
                    st.session_state["client"] = result
                    st.session_state["access_token"] = result["access_token"]
                    st.session_state.pop("recos", None)
                    st.success("Account created successfully!")
                    st.rerun()

    st.stop()


# -----------------------------
# AUTHENTICATED CLIENT DASHBOARD
# -----------------------------
client = st.session_state["client"]
st.session_state.setdefault("nav", NAV_RECO)


def open_details(reco):
    st.session_state["detail_pid"] = reco["product_id"]
    st.session_state["detail_reco"] = reco


def close_details():
    st.session_state.pop("detail_pid", None)
    st.session_state.pop("detail_reco", None)


def go_trade(prod):
    """Reuse the existing Trade page: preselect the product + BUY, then switch to it."""
    st.session_state["trade_sel"] = f'{prod["symbol"]} - {prod["name"]}'
    st.session_state["trade_side"] = "BUY"
    st.session_state["nav"] = NAV_TRADE


def price_chart(hist, kind):
    df = pd.DataFrame(hist["points"])
    df["date"] = pd.to_datetime(df["date"])
    up = df["close"].iloc[-1] >= df["close"].iloc[0]
    line, fill = ("#22c55e", "rgba(34,197,94,.15)") if up else ("#ef4444", "rgba(239,68,68,.15)")
    lo, hi = df["low"].min(), df["high"].max()
    fig = go.Figure()
    if kind == "Candlestick":
        fig.add_trace(go.Candlestick(x=df["date"], open=df["open"], high=df["high"], low=df["low"],
                                     close=df["close"], name="OHLC"))
        fig.update_layout(xaxis_rangeslider_visible=False)
    else:
        fig.add_trace(go.Scatter(x=df["date"], y=[df["close"].min()] * len(df), mode="lines",
                                 line=dict(width=0), hoverinfo="skip", showlegend=False))
        fig.add_trace(go.Scatter(x=df["date"], y=df["close"], mode="lines", name="Close",
                                 line=dict(color=line, width=2), fill="tonexty", fillcolor=fill,
                                 hovertemplate="%{x|%d %b %Y}<br>₹%{y:,.2f}<extra></extra>"))
    pad = (hi - lo) * 0.05 or 1
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      height=420, margin=dict(l=0, r=0, t=10, b=0), hovermode="x unified",
                      showlegend=False, yaxis=dict(title="Price (₹)", range=[lo - pad, hi + pad],
                                                   gridcolor="rgba(148,163,184,.15)"),
                      xaxis=dict(showgrid=False))
    return fig


def render_details():
    pid = st.session_state["detail_pid"]
    reco = st.session_state.get("detail_reco")
    st.button("← Back to recommendations", on_click=close_details)

    p = call("GET", f"/products/{pid}")
    if not p:
        return
    st.markdown(
        f'<span class="sym">{html.escape(p["symbol"])}</span> &nbsp;'
        f'<span style="font-size:1.6rem;font-weight:700">{html.escape(p["name"])}</span>',
        unsafe_allow_html=True,
    )
    st.markdown(f'<span class="badge b-info">{html.escape(p["asset_class"])}</span>'
                f'{risk_badge(int(p["risk_level"]))}', unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        kpi("Current price", inr(p["current_price"]), "Latest price on file")
    with c2:
        kpi("Asset class", html.escape(p["asset_class"]))
    with c3:
        kpi("Risk level", f'{int(p["risk_level"])}/10', f'Volatility {p["volatility_pct"]:.1f}% / yr')
    with c4:
        kpi("Expected return", f'{p["expected_return_pct"]:.1f}%', "Model assumption, not guaranteed")

    st.markdown("#### Price history")
    ctl1, ctl2 = st.columns([2, 1])
    with ctl1:
        period = st.radio("Period", list(PERIODS), index=2, horizontal=True, key="period_sel")
    with ctl2:
        kind = st.radio("Chart", ["Line", "Candlestick"], horizontal=True, key="chart_kind")

    hist = call("GET", f"/products/{pid}/history", params={"period": PERIODS[period]})
    if hist and hist["points"]:
        if hist["is_demo"]:
            st.markdown('<div class="demo"><b>DEMO / HISTORICAL SAMPLE DATA</b> - synthetic prices generated '
                        'for this project, not real market data.</div>', unsafe_allow_html=True)
        st.plotly_chart(price_chart(hist, kind), use_container_width=True)
        s = hist["summary"]
        h1, h2, h3, h4 = st.columns(4)
        with h1:
            kpi(f"Change over {period}", f'{s["change_pct"]:+.2f}%', f'{s["start_date"]} to {s["end_date"]}')
        with h2:
            kpi("Period high", inr(s["period_high"]))
        with h3:
            kpi("Period low", inr(s["period_low"]))
        with h4:
            kpi("Max drawdown", f'{s["max_drawdown_pct"]:.2f}%', "Largest peak-to-trough fall")
        st.caption("Historical performance describes what the price series did in the past. It is separate "
                   "from the expected return above and does not predict future results.")
    elif hist is not None:
        st.info("No price history is loaded for this product yet. Run `python seed_prices.py` "
                "(after `migration_price_history.sql`).")

    left, right = st.columns([3, 2])
    with left:
        st.markdown("#### About this product")
        st.write(p["description"] or "No description available.")
        k1, k2 = st.columns(2)
        with k1:
            kpi("Minimum investment", inr(p["min_investment"]) if p["min_investment"] else "None")
        with k2:
            kpi("Expense ratio", f'{p["expense_ratio_pct"]:.2f}% / yr' if p["expense_ratio_pct"] else "None")
    with right:
        st.markdown("#### Risk considerations")
        for note in p["risk_notes"]:
            st.markdown(f"- {note}")

    st.markdown("#### Why this was recommended to you")
    with st.container(border=True):
        if reco:
            st.markdown(f"**Model reasoning:** {reco['reason']}.")
            st.caption(f"Match score {reco['score']}: a relative ranking from the hybrid model (risk fit, "
                       "return per unit of risk, similar clients, popularity), not a probability of gain.")
        st.write(p["suitability"]["summary"])
        for chk in p["suitability"]["checks"]:
            st.markdown(f'{"✅" if chk["ok"] else "⚠️"} **{chk["label"]}:** {chk["detail"]}')

    st.button("🛒 Buy / Trade", type="primary", on_click=go_trade, args=(p,))


hero(f"Welcome, {client['full_name']} 👋", "Your personalized investment research dashboard")

with st.sidebar:
    st.title("📈 InvestIQ")
    st.success(f"Logged in as {client['full_name']}")
    st.caption(client["email"])
    st.metric("Risk profile", f"{client['risk_score']}/10")
    st.caption(f"Category: {client['risk_category'].title()}")
    st.divider()
    if st.button("Log out", use_container_width=True):
        logout()

page = st.radio("Navigation", NAV, horizontal=True, key="nav", label_visibility="collapsed")

# ---------- Recommendations ----------
if page == NAV_RECO:
    if st.session_state.get("detail_pid"):
        render_details()
    else:
        st.subheader("Investment Recommendations")
        st.write("Recommendations are generated from your risk profile, portfolio behaviour and product data.")
        k = st.slider("How many recommendations?", 3, 10, 5)

        if st.button("Get recommendations", type="primary"):
            st.session_state["recos"] = call("GET", "/me/recommendations", params={"k": k})

        for r in st.session_state.get("recos") or []:
            with st.container(border=True):
                st.markdown(
                    f'<span class="sym">{html.escape(r["symbol"])}</span> - {html.escape(r["name"])}<br>'
                    f'{risk_badge(int(r["risk_level"]))}'
                    f'<span class="badge b-info">Expected return {r["expected_return_pct"]}%</span>'
                    f'<span class="badge b-info">Match score {r["score"]}</span>',
                    unsafe_allow_html=True,
                )
                st.write("Why: " + r["reason"])
                a, b, c_ = st.columns(3)
                a.button("View Details", key=f"v{r['reco_id']}", on_click=open_details, args=(r,),
                         use_container_width=True)
                if b.button("Accept", key=f"a{r['reco_id']}", use_container_width=True):
                    if call("POST", f"/recommendations/{r['reco_id']}/feedback", json={"status": "ACCEPTED"}):
                        st.toast("Feedback saved")
                if c_.button("Dismiss", key=f"d{r['reco_id']}", use_container_width=True):
                    if call("POST", f"/recommendations/{r['reco_id']}/feedback", json={"status": "DISMISSED"}):
                        st.toast("Feedback saved")

# ---------- Portfolio ----------
elif page == NAV_PORT:
    st.subheader("Your Portfolio")
    pf = pd.DataFrame(call("GET", "/me/portfolio") or [])
    if pf.empty:
        st.info("No holdings yet. You can place a BUY order from the Trade tab.")
    else:
        for col in ["quantity", "avg_cost", "invested", "market_value", "pnl"]:
            pf[col] = pf[col].astype(float)
        m1, m2, m3 = st.columns(3)
        with m1:
            kpi("Invested", inr(pf["invested"].sum()))
        with m2:
            kpi("Market value", inr(pf["market_value"].sum()))
        with m3:
            kpi("Profit / loss", inr(pf["pnl"].sum()))
        st.dataframe(pf, use_container_width=True)
        st.plotly_chart(
            px.pie(pf, names="asset_class", values="market_value", title="Allocation by asset class",
                   hole=0.45, template="plotly_dark"),
            use_container_width=True,
        )

# ---------- Trade ----------
else:
    st.subheader("Trade")
    prods = call("GET", "/products") or []
    opt = {f'{p["symbol"]} - {p["name"]}': p for p in prods}
    if st.session_state.get("trade_sel") not in opt:
        st.session_state.pop("trade_sel", None)
    if opt:
        sel = st.selectbox("Product", list(opt), key="trade_sel")
        side = st.radio("Side", ["BUY", "SELL"], horizontal=True, key="trade_side")
        qty = st.number_input("Quantity", min_value=1.0, value=10.0)
        price = float(opt[sel]["current_price"])
        t1, t2 = st.columns(2)
        with t1:
            kpi("Current price", inr(price))
        with t2:
            kpi("Estimated order value", inr(price * qty), "Executed at the price on file when placed")
        if st.button("Place order", type="primary"):
            result = call(
                "POST",
                "/me/transactions",
                json={"product_id": opt[sel]["product_id"], "txn_type": side, "quantity": qty},
            )
            if result:
                st.success(f"{side} executed at ₹{result['price']:.2f}")
                st.info("Your holdings have been updated automatically by the database trigger.")
