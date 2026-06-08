from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import routes, sessions

app = FastAPI(
    title="London Runner API",
    description="Signal-aware running route optimizer for London",
    version="1.0.0",
)

# Flutter 앱에서 API 호출 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.router)
app.include_router(sessions.router)

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "London Runner API"}
