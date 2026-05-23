from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import asyncpg
import os
import httpx
from contextlib import asynccontextmanager
from datetime import datetime

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    await app.state.db.execute("""
        CREATE TABLE IF NOT EXISTS price_observations (
            id BIGSERIAL PRIMARY KEY,
            platform VARCHAR(64),
            product_url TEXT,
            city VARCHAR(64),
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
        (platform, product_url, city, price, currency)
        VALUES ($1, $2, $3, $4, $5)
    """,
        data.get("platform"),
        data.get("url"),
        data.get("city", "Inconnue"),
        float(data.get("price", 0)),
        data.get("currency", "EUR")
    )
    return {"status": "Prix enregistré ✅"}

@app.get("/prices")
async def get_prices(url: str, request: Request):
    db = request.app.state.db
    rows = await db.fetch("""
        SELECT city, price, currency, observed_at
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
        label = "STABLE"
        color = "green"
    elif cv < 0.12:
        label = "LÉGÈRE VARIABILITÉ"
        color = "orange"
    else:
        label = "FORTE VARIABILITÉ"
        color = "red"
    
    return {
        "min_price": min_p,
        "max_price": max_p,
        "ecart": round(max_p - min_p, 2),
        "variability_label": label,
        "variability_color": color,
        "sample_size": len(prices),
        "observations": [
            {
                "city": r["city"],
                "price": float(r["price"]),
                "currency": r["currency"]
            }
            for r in rows
        ]
    }

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
        ORDER BY (MAX(price) - MIN(price)) / AVG(price) DESC
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
