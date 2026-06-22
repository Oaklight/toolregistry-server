"""Protocol adapters for ToolRegistry.

This package contains adapters that expose a :class:`~toolregistry_server.RouteTable`
over different service protocols:

- ``openapi``: RESTful HTTP endpoints via FastAPI
- ``mcp``: Model Context Protocol for LLM integration

All adapters inherit from :class:`Adapter` and implement :meth:`serve`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..route_table import RouteTable


class Adapter(ABC):
    """Base class for protocol adapters.

    An adapter takes a :class:`~toolregistry_server.RouteTable` and
    exposes it over a specific protocol (OpenAPI, MCP, etc.).

    Subclasses must implement :meth:`serve` to start the server.

    Example::

        adapter = OpenAPIAdapter(route_table, tokens=["secret"])
        adapter.serve(host="0.0.0.0", port=8000)
    """

    def __init__(self, route_table: RouteTable) -> None:
        self._route_table = route_table

    @property
    def route_table(self) -> RouteTable:
        """The underlying route table."""
        return self._route_table

    @abstractmethod
    def serve(self, *, host: str, port: int, **kwargs) -> None:
        """Start the server.

        Args:
            host: Host address to bind to.
            port: Port number to bind to.
            **kwargs: Protocol-specific options.
        """

    def __call__(self, *, host: str = "127.0.0.1", port: int = 8000, **kwargs) -> None:
        """Alias for :meth:`serve` — makes the adapter directly callable.

        Example::

            adapter = MCPAdapter(route_table)
            adapter(host="0.0.0.0", port=8000, transport="sse")
        """
        self.serve(host=host, port=port, **kwargs)
