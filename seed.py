"""Demo data: python seed.py   (set DB_URL if your MySQL creds differ)"""
import os, random
import hashlib, secrets
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text

engine = create_engine(os.getenv("DB_URL", "mysql+pymysql://root:mysql13.@localhost/ib_reco"))
random.seed(42)

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${digest.hex()}"

DEMO_PASSWORD = "Demo@123"

CLASSES = ["Equity", "Bond", "ETF", "Mutual Fund", "Commodity", "Alternative"]
# symbol, name, class, risk, exp.return %, volatility %, expense %, min invest, price
PRODUCTS = [
    ("GSEC10", "10Y Government Bond", "Bond", 1, 7.0, 3.0, 0.10, 1000, 100),
    ("LIQFND", "Liquid Fund", "Mutual Fund", 1, 6.5, 0.5, 0.20, 500, 1000),
    ("CORPAAA", "AAA Corporate Bond Fund", "Bond", 2, 7.8, 4.0, 0.40, 5000, 50),
    ("GOLDBEES", "Gold ETF", "Commodity", 4, 9.0, 14.0, 0.50, 1000, 60),
    ("BALADV", "Balanced Advantage Fund", "Mutual Fund", 5, 11.0, 10.0, 0.90, 5000, 80),
    ("NIFTYBEES", "Nifty 50 Index ETF", "ETF", 6, 12.0, 16.0, 0.05, 500, 250),
    ("BLUECHIP", "Large Cap Blue-chip Fund", "Mutual Fund", 6, 12.5, 15.0, 0.80, 5000, 70),
    ("BANKETF", "Banking Sector ETF", "ETF", 7, 13.5, 20.0, 0.20, 1000, 500),
    ("INFY", "Infosys Ltd", "Equity", 7, 14.0, 22.0, 0.0, 1500, 1500),
    ("MIDCAP", "Mid Cap Growth Fund", "Mutual Fund", 8, 15.5, 22.0, 0.75, 5000, 120),
    ("REIT1", "Commercial Real Estate REIT", "Alternative", 5, 10.5, 9.0, 0.60, 10000, 300),
    ("SMALLCAP", "Small Cap Fund", "Mutual Fund", 9, 18.0, 28.0, 0.70, 5000, 90),
    ("TECHSTK", "High-growth Tech Stock", "Equity", 9, 20.0, 35.0, 0.0, 2000, 2200),
    ("CRYPTOETF", "Digital Asset ETF", "Alternative", 10, 25.0, 60.0, 1.00, 5000, 40),
]

with engine.begin() as c:
    for name in CLASSES:
        c.execute(text("INSERT IGNORE INTO asset_classes(name) VALUES (:n)"), {"n": name})
    cls = {r[1]: r[0] for r in c.execute(text("SELECT asset_class_id, name FROM asset_classes"))}
    for s, n, ac, rk, er, vo, ex, mi, pr in PRODUCTS:
        c.execute(text("INSERT IGNORE INTO products (asset_class_id, symbol, name, description, risk_level, "
                       "expected_return_pct, volatility_pct, expense_ratio_pct, min_investment, current_price) "
                       "VALUES (:a,:s,:n,:n,:r,:e,:v,:x,:m,:p)"),
                  dict(a=cls[ac], s=s, n=n, r=rk, e=er, v=vo, x=ex, m=mi, p=pr))
    for i in range(1, 5):
        c.execute(text("INSERT IGNORE INTO advisors(full_name,email,specialization) VALUES (:n,:e,'Wealth')"),
                  {"n": f"Advisor {i}", "e": f"advisor{i}@bank.com"})

    prods = c.execute(text("SELECT product_id, risk_level, current_price FROM products")).all()
    for i in range(1, 81):
        risk = random.randint(1, 10)
        cat = "conservative" if risk <= 3 else "moderate" if risk <= 6 else "aggressive"
        cid = c.execute(text(
            "INSERT INTO clients (advisor_id, full_name, email, password_hash, date_of_birth, annual_income, "
            "investment_horizon_yrs, risk_score, risk_category) VALUES (:a,:n,:e,:ph,:d,:i,:h,:r,:c)"),
            dict(a=random.randint(1, 4), n=f"Client {i}", e=f"client{i}@mail.com", ph=hash_password(DEMO_PASSWORD),
                 d=f"{random.randint(1965, 2003)}-06-15", i=random.randint(4, 40) * 100000,
                 h=random.randint(1, 25), r=risk, c=cat)).lastrowid
        # clients mostly buy products near their own risk level
        for pid, rl, price in random.sample(prods, random.randint(2, 6)):
            if abs(rl - risk) <= 2 or random.random() < 0.1:
                c.execute(text("INSERT INTO transactions (client_id, product_id, txn_type, quantity, price, txn_ts) "
                               "VALUES (:c,:p,'BUY',:q,:pr,:t)"),
                          dict(c=cid, p=pid, q=random.randint(5, 100), pr=price,
                               t=datetime.now() - timedelta(days=random.randint(1, 700))))
print("Seeded.")
