"""Entry point — kept slim so ``uvicorn main:app`` works unchanged."""

from app import app  # noqa: F401

if __name__ == "__main__":
    import uvicorn
    from app.config import settings

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.PORT,
        log_level="info",
        # WebSocket keepalive — prevents proxy/load-balancer idle timeouts.
        # The frontend sends app-level pings every 25s; these uvicorn-level
        # pings run at the WS protocol level as an additional safety net.
        ws_ping_interval=20,       # WS protocol ping every 20s
        ws_ping_timeout=60,        # close if no pong within 60s
        timeout_keep_alive=65,     # HTTP keep-alive larger than ping interval
    )
