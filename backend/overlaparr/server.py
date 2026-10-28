"""uvicorn entry point. log_config=None keeps uvicorn's loggers on the root handlers,
so access/error lines go through the redacting filter like every other record."""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "overlaparr.main:app_factory",
        factory=True,
        host="0.0.0.0",
        port=int(os.environ.get("OVERLAPARR_PORT", "8686")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
