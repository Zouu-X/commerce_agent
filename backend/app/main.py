from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.commerce import router as commerce_router
from app.commerce.errors import ResourceNotFoundError
from app.db.session import engine


def create_app() -> FastAPI:
    app = FastAPI(
        title="Commerce Support Agent API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["GET"],
        allow_headers=["X-Tenant-Id", "X-Store-Id", "X-Customer-Id"],
    )
    app.include_router(commerce_router)

    @app.exception_handler(ResourceNotFoundError)
    async def resource_not_found(_request: Request, error: ResourceNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.get("/api/v1/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/ready", tags=["system"])
    async def ready() -> dict[str, str]:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return {"status": "ready"}

    return app


app = create_app()
