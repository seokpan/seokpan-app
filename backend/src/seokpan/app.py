from fastapi import FastAPI

from seokpan.health import router as health_router


def create_app() -> FastAPI:
    application = FastAPI(
        title="Seokpan API",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    application.include_router(health_router)
    return application


app = create_app()
