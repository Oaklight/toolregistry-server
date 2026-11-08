"""Run the gateway demo with uvicorn.

Environment:

* ``GATEWAY_HOST`` / ``GATEWAY_PORT`` — bind address (default
  ``0.0.0.0:8000``);
* ``GATEWAY_ADMIN_TOKEN`` — operator token for ``/ext/gateway``;
* ``GATEWAY_KEYS_FILE`` — JSON key file (default ``keys.json``);
* ``GATEWAY_TOOL_SOURCE`` — ``auto``, ``hub`` or ``local``;
* ``GATEWAY_LOG_LEVEL`` — uvicorn log level (default ``info``).
"""

import logging
import os

import uvicorn

from .app import create_app


def main() -> None:
    """Start the demo server."""
    logging.basicConfig(
        level=os.environ.get("GATEWAY_LOG_LEVEL", "info").upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(
        create_app(),
        host=os.environ.get("GATEWAY_HOST", "0.0.0.0"),
        port=int(os.environ.get("GATEWAY_PORT", "8000")),
        log_level=os.environ.get("GATEWAY_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
