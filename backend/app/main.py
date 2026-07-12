from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(
        title="Commerce Support Agent API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )

    @app.get("/api/v1/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
