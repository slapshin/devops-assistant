"""`python -m app`: run the server with APP_HOST/APP_PORT from configuration."""

import sys

import uvicorn

from app.settings import ConfigError, load_settings

CONFIG_ERROR_EXIT_CODE = 2


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return CONFIG_ERROR_EXIT_CODE

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
