"""Loads DEMO / SYNTHETIC price history into product_prices and replaces placeholder product descriptions.

Run once after migration_price_history.sql:   python seed_prices.py      (needs DB_URL, same as the backend)
Safe to re-run: it only replaces rows tagged data_source='DEMO_SYNTHETIC'.

NOT REAL MARKET DATA. Each series is a seeded random walk (geometric Brownian motion) built from the
product's own expected_return_pct and volatility_pct, then scaled so the last close equals the product's
current_price. Business days only, no holidays modelled. Same seed => same data every run.
"""
import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

SOURCE = "DEMO_SYNTHETIC"
N_DAYS = 1305          # ~5 years of business days
TRADING_DAYS = 252

DESCRIPTIONS = {
    "GSEC10": "A 10-year Indian government bond. Government securities are backed by the sovereign; prices move "
              "inversely to interest rates, so value can fall before maturity if rates rise.",
    "LIQFND": "A liquid mutual fund that invests in very short-term money-market instruments. Aims for stability "
              "and easy redemption; returns are modest and not guaranteed.",
    "CORPAAA": "A bond fund investing in highly rated (AAA) corporate debt. Offers a higher yield than government "
               "bonds in exchange for credit risk and interest-rate risk.",
    "GOLDBEES": "An exchange-traded fund that tracks the price of gold. Gold can diversify a portfolio but pays no "
                "interest or dividends, and its price can be volatile.",
    "BALADV": "A balanced advantage (dynamic asset allocation) fund that shifts between equity and debt depending "
              "on market conditions. Aims to reduce, not remove, equity volatility.",
    "NIFTYBEES": "An index ETF tracking the Nifty 50, India's benchmark index of 50 large listed companies. A "
                 "low-cost way to get diversified large-cap equity exposure; it moves with the market.",
    "BLUECHIP": "An actively managed large-cap equity mutual fund investing mainly in India's largest listed "
                "companies. Charges a higher expense ratio than an index ETF.",
    "BANKETF": "A sector ETF tracking Indian banking stocks. Concentrated in one sector, so it can be more "
               "volatile than a broad index.",
    "INFY": "Shares of Infosys Ltd, a large Indian IT services company. A single-company stock: results depend on "
            "company performance and market sentiment, and there is no guaranteed return.",
    "MIDCAP": "A mutual fund investing mainly in mid-sized listed companies. Mid-sized companies can grow faster "
              "than large ones but typically see larger price swings.",
    "REIT1": "A real-estate investment trust holding income-generating commercial property. Income comes from "
             "rent distributions; unit prices can vary with property markets and interest rates.",
    "SMALLCAP": "A mutual fund investing mainly in small listed companies. These can be less liquid and swing "
                "sharply in price.",
    "TECHSTK": "Shares of a high-growth technology company. Growth stocks can see large price swings and are "
               "sensitive to changing expectations.",
    "CRYPTOETF": "An ETF providing exposure to digital assets. Prices can swing very sharply and the asset class "
                 "is highly speculative.",
}


def generate_series(product_id, current_price, exp_return_pct, vol_pct, dates, has_volume):
    rng = np.random.default_rng(1000 + product_id)
    mu, sigma, dt = exp_return_pct / 100, vol_pct / 100, 1 / TRADING_DAYS
    n = len(dates)
    log_ret = (mu - 0.5 * sigma ** 2) * dt + sigma * np.sqrt(dt) * rng.standard_normal(n)
    path = np.exp(np.cumsum(log_ret))
    close = path / path[-1] * current_price                    # anchor: last close == current_price
    prev = np.concatenate(([close[0]], close[:-1]))
    daily = sigma * np.sqrt(dt)
    open_ = prev * (1 + 0.3 * daily * rng.standard_normal(n))
    high = np.maximum(open_, close) * (1 + np.abs(0.5 * daily * rng.standard_normal(n)))
    low = np.minimum(open_, close) * (1 - np.abs(0.5 * daily * rng.standard_normal(n)))
    volume = rng.lognormal(np.log(100_000), 0.4, n).astype(np.int64) if has_volume else [None] * n
    return pd.DataFrame({"price_date": [d.date() for d in dates], "open": open_.round(4),
                         "high": high.round(4), "low": low.round(4), "close": close.round(4),
                         "volume": volume})


def main():
    engine = create_engine(os.environ["DB_URL"])
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=N_DAYS)
    with engine.begin() as c:
        prods = c.execute(text(
            "SELECT p.product_id, p.symbol, p.current_price, p.expected_return_pct, p.volatility_pct, "
            "ac.name FROM products p JOIN asset_classes ac ON ac.asset_class_id = p.asset_class_id")).all()
        c.execute(text("DELETE FROM product_prices WHERE data_source = :s"), {"s": SOURCE})
        for pid, sym, price, er, vol, ac in prods:
            df = generate_series(pid, float(price), float(er), float(vol), dates, ac != "Mutual Fund")
            c.execute(text(
                "INSERT INTO product_prices (product_id, price_date, open_price, high_price, low_price, "
                "close_price, volume, data_source) VALUES (:p,:d,:o,:h,:l,:c,:v,:s)"),
                [dict(p=pid, d=r.price_date, o=float(r.open), h=float(r.high), l=float(r.low),
                      c=float(r.close), v=None if r.volume is None else int(r.volume), s=SOURCE)
                 for r in df.itertuples()])
            if sym in DESCRIPTIONS:  # only replace the placeholder (description == name) from seed.py
                c.execute(text("UPDATE products SET description=:d "
                               "WHERE product_id=:p AND (description IS NULL OR description = name)"),
                          {"d": DESCRIPTIONS[sym], "p": pid})
            print(f"{sym}: {len(df)} rows ({df.price_date.iloc[0]} to {df.price_date.iloc[-1]})")
    print("Done. All prices are DEMO_SYNTHETIC sample data, not real market data.")


if __name__ == "__main__":
    main()
