"""Hybrid recommender: suitability filter + content (risk/return fit) + collaborative filtering + popularity."""
import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity

RISK_FREE = 6.5  # % p.a., used for the return-per-risk (Sharpe-like) term


class HybridRecommender:
    VERSION = "hybrid-v1"

    def __init__(self, engine, w_content=0.5, w_cf=0.35, w_pop=0.15):
        self.engine = engine
        self.w = (w_content, w_cf, w_pop)

    def fit(self):
        self.products = pd.read_sql(
            "SELECT product_id, symbol, name, risk_level, expected_return_pct, volatility_pct, "
            "min_investment FROM products WHERE is_active = 1", self.engine).set_index("product_id")

        buys = pd.read_sql(
            "SELECT client_id, product_id, COUNT(*) n FROM transactions "
            "WHERE txn_type='BUY' GROUP BY client_id, product_id", self.engine)
        buys["w"] = np.log1p(buys["n"])

        fb = pd.read_sql(
            "SELECT client_id, product_id, SUM(CASE status WHEN 'ACCEPTED' THEN 2 "
            "WHEN 'CLICKED' THEN 0.5 WHEN 'DISMISSED' THEN -1 ELSE 0 END) w "
            "FROM recommendations GROUP BY client_id, product_id", self.engine)

        inter = (pd.concat([buys[["client_id", "product_id", "w"]], fb])
                 .groupby(["client_id", "product_id"])["w"].sum().clip(lower=0).reset_index())
        self.M = (inter.pivot(index="client_id", columns="product_id", values="w")
                  .reindex(columns=self.products.index).fillna(0))

        # item-item cosine similarity on the client x product implicit-feedback matrix
        if len(self.M) > 1:
            sim = cosine_similarity(self.M.T.values)
        else:
            sim = np.zeros((len(self.products), len(self.products)))
        self.item_sim = pd.DataFrame(sim, index=self.M.columns, columns=self.M.columns)

        self.popularity = (self.M > 0).sum() / max(len(self.M), 1)
        p = self.products
        sharpe = (p.expected_return_pct - RISK_FREE) / p.volatility_pct
        self.sharpe_norm = (sharpe - sharpe.min()) / (sharpe.max() - sharpe.min() + 1e-9)
        return self

    def recommend(self, client: dict, k=5):
        cid, risk = client["client_id"], client["risk_score"]
        p = self.products
        owned = set()
        if cid in self.M.index:
            owned = set(self.M.columns[self.M.loc[cid] > 0])

        # 1) hard suitability rules (compliance): risk ceiling, not already held, affordable
        p = p[(p.risk_level <= risk + 1) & (~p.index.isin(owned))
              & (p.min_investment <= float(client["annual_income"]) * 0.10)]
        if p.empty:
            return []

        # 2) content score: how close is product risk to client risk + return per unit risk
        risk_fit = 1 - (p.risk_level - risk).abs() / 9
        content = 0.7 * risk_fit + 0.3 * self.sharpe_norm.loc[p.index]

        # 3) collaborative score: products similar to what the client already holds
        w_c, w_cf, w_p = self.w
        if owned:
            vec = self.M.loc[cid].values
            cf = pd.Series(self.item_sim.values @ vec, index=self.item_sim.index).loc[p.index]
            cf = cf / (cf.max() + 1e-9)
        else:  # cold start: no history -> drop CF, re-weight
            cf = pd.Series(0.0, index=p.index)
            w_c, w_p = w_c / (w_c + w_p), w_p / (w_c + w_p)
            w_cf = 0.0

        pop = self.popularity.loc[p.index]
        score = w_c * content + w_cf * cf + w_p * pop

        out = []
        for pid in score.sort_values(ascending=False).index[:k]:
            why = []
            if risk_fit[pid] > 0.85: why.append("matches your risk profile")
            if cf[pid] > 0.6: why.append("held by clients with similar portfolios")
            if self.sharpe_norm[pid] > 0.6: why.append("strong return per unit of risk")
            if pop[pid] > 0.3: why.append("popular among clients")
            row = self.products.loc[pid]
            out.append({"product_id": int(pid), "symbol": row.symbol, "name": row["name"],
                        "risk_level": int(row.risk_level),
                        "expected_return_pct": float(row.expected_return_pct),
                        "score": round(float(score[pid]), 4),
                        "reason": "; ".join(why) or "diversifies your portfolio"})
        return out
