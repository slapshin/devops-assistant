"""`python -m app`: run the server with APP_HOST/APP_PORT from configuration."""

import sys

import uvicorn

from app.settings import ConfigError, load_settings


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=settings.app_host,
        port=settings.app_port,
        log_level=settings.log_level.lower(),
        proxy_headers=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
