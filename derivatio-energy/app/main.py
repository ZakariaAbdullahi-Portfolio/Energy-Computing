from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import simulation, tariffs, entsoe, zaptec, scheduler

app = FastAPI(
    title="Derivatio Energy API",
    description="Simulering och optimering av effekttariffer för EV-laddning",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(simulation.router, prefix="/api/v1")
app.include_router(tariffs.router, prefix="/api/v1")
app.include_router(entsoe.router, prefix="/api/v1")
app.include_router(zaptec.router)
app.include_router(scheduler.router)

@app.get("/health")
def health():
    return {"status": "ok", "service": "derivatio-energy-api"}


