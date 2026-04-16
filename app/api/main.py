from fastapi import FastAPI
from sqlalchemy import text
from app.api.connection import SessionFactory

app = FastAPI()

@app.get("/health-check")
def health_check_handler():
    with SessionFactory() as session:
        stmt = text("SELECT * FROM user LIMIT 1;")
        row = session.execute(stmt).fetchone()
    return {"user": row._asdict()}