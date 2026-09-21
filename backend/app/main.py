from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app import seed
from app.db import connect
from app.routers import api

app = FastAPI(title="Taximeter", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

app.include_router(api)

@app.get("/api/health")
def health():
    conn = connect()  # 默认库
    try:
        conn.execute("SELECT 1")
    finally:
        conn.close()
    return {"ok": True, "project": "taximeter"}
