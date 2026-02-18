"""Entry point — kept slim so ``uvicorn main:app`` works unchanged."""

from app import app  # noqa: F401

if __name__ == "__main__":
    import uvicorn
    from app.config import settings

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.PORT,
        reload=True,
        log_level="info",
    )
