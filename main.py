from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import asyncpg
import os
import httpx
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    await app.state.db.execute("""
        CREATE TABLE IF NOT EXISTS price_observations (
            id BIGSERIAL PRIMARY KEY,
            platform VARCHAR(64),
            product_url TEXT,
            product_name TEXT,
            city VARCHAR(64),
            department VARCHAR(64),
            price DECIMAL(10,2),
            currency VARCHAR(8) DEFAULT 'EUR',
            observed_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    yield
    await app.state.db.close()

app = FastAPI(title="PrixWatch API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"status": "PrixWatch en ligne ✅"}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/report")
async def report_price(request: Request):
    data = await request.json()
    db = request.app.state.db
    await db.execute("""
        INSERT INTO price_observations 
        (platform, product_url, product_name, city, department, price, currency)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
    """,
        data.get("platform", "inconnu"),
        data.get("url", ""),
        data.get("product_name", "Produit"),
        data.get("city", "Inconnue"),
        data.get("department", "Inconnu"),
        float(data.get("price", 0)),
        data.get("currency", "EUR")
    )
    return {"status": "Prix enregistré ✅"}

@app.get("/prices")
async def get_prices(url: str, request: Request):
    db = request.app.state.db
    rows = await db.fetch("""
        SELECT city, department, price, currency, product_name, observed_at
        FROM price_observations
        WHERE product_url = $1
        ORDER BY observed_at DESC
        LIMIT 50
    """, url)

    if not rows:
        raise HTTPException(status_code=404, detail="Aucun prix trouvé")

    prices = [float(r["price"]) for r in rows]
    min_p = min(prices)
    max_p = max(prices)
    mean_p = sum(prices) / len(prices)
    cv = (max_p - min_p) / mean_p if mean_p > 0 else 0

    if cv < 0.03:
        label, color = "STABLE", "green"
    elif cv < 0.12:
        label, color = "LÉGÈRE VARIABILITÉ", "orange"
    else:
        label, color = "FORTE VARIABILITÉ", "red"

    return {
        "product_name": rows[0]["product_name"] if rows else "Produit",
        "min_price": round(min_p, 2),
        "max_price": round(max_p, 2),
        "avg_price": round(mean_p, 2),
        "ecart": round(max_p - min_p, 2),
        "variability_label": label,
        "variability_color": color,
        "sample_size": len(prices),
        "observations": [
            {
                "city": r["city"],
                "department": r["department"],
                "price": float(r["price"]),
                "currency": r["currency"],
                "observed_at": r["observed_at"].isoformat(),
            }
            for r in rows
        ]
    }

@app.get("/history")
async def get_history(url: str, request: Request):
    db = request.app.state.db
    now = datetime.utcnow()

    periods = {
        "1j": now - timedelta(days=1),
        "2j": now - timedelta(days=2),
        "1sem": now - timedelta(weeks=1),
        "1mois": now - timedelta(days=30),
        "1an": now - timedelta(days=365),
    }

    result = {}
    for label, since in periods.items():
        rows = await db.fetch("""
            SELECT price FROM price_observations
            WHERE product_url = $1
            AND observed_at > $2
        """, url, since)

        if rows:
            prices = [float(r["price"]) for r in rows]
            result[label] = {
                "min": round(min(prices), 2),
                "max": round(max(prices), 2),
                "avg": round(sum(prices) / len(prices), 2),
                "count": len(prices)
            }
        else:
            result[label] = None

    return result

@app.get("/wall")
async def get_wall(request: Request):
    db = request.app.state.db
    rows = await db.fetch("""
        SELECT 
            platform,
            AVG(price) as avg_price,
            MIN(price) as min_price,
            MAX(price) as max_price,
            COUNT(*) as total
        FROM price_observations
        WHERE observed_at > NOW() - INTERVAL '30 days'
        GROUP BY platform
        ORDER BY (MAX(price) - MIN(price)) / NULLIF(AVG(price), 0) DESC
    """)
    return [
        {
            "platform": r["platform"],
            "avg_price": round(float(r["avg_price"]), 2),
            "min_price": round(float(r["min_price"]), 2),
            "max_price": round(float(r["max_price"]), 2),
            "observations": r["total"]
        }
        for r in rows
    ]

@app.get("/trending")
async def get_trending(request: Request):
    """
    Retourne les 5 produits les plus signalés
    pour la collecte automatique en arrière-plan
    """
    db = request.app.state.db
    rows = await db.fetch("""
        SELECT 
            product_url,
            product_name,
            platform,
            COUNT(*) as total,
            AVG(price) as avg_price
        FROM price_observations
        WHERE observed_at > NOW() - INTERVAL '7 days'
        GROUP BY product_url, product_name, platform
        ORDER BY total DESC
        LIMIT 5
    """)
    return [
        {
            "url": r["product_url"],
            "name": r["product_name"],
            "platform": r["platform"],
            "avg_price": round(float(r["avg_price"]), 2),
        }
        for r in rows
    ]
