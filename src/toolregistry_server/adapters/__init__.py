"""Protocol adapters for ToolRegistry.

This package contains adapters that expose a :class:`~toolregistry_server.RouteTable`
over different protocols:

- ``openapi``: RESTful HTTP endpoints via FastAPI
- ``mcp``: Model Context Protocol for LLM integration

All adapters inherit from :class:`Adapter` and implement :meth:`run`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..route_table import RouteTable


class Adapter(ABC):
    """Base class for protocol adapters.

    An adapter takes a :class:`~toolregistry_server.RouteTable` and
    exposes it over a specific protocol (OpenAPI, MCP, gRPC, CLI, etc.).

    Subclasses must implement :meth:`run`.  Each adapter defines its
    own keyword arguments — network adapters typically accept ``host``
    and ``port``, while others (e.g. MCP stdio, CLI) may not.

    Example::

        adapter = OpenAPIAdapter(route_table)
        adapter.run(host="0.0.0.0", port=8000)

        # or equivalently:
        adapter(host="0.0.0.0", port=8000)
    """

    def __init__(self, route_table: RouteTable) -> None:
        self._route_table = route_table

    @property
    def route_table(self) -> RouteTable:
        """The underlying route table."""
        return self._route_table

    @abstractmethod
    def run(self, **kwargs) -> None:
        """Run the adapter.

        Each subclass defines its own keyword arguments.  Typical
        examples:

        - ``OpenAPIAdapter.run(host=, port=, reload=)``
        - ``MCPAdapter.run(transport=, host=, port=)``
        """

    async def run_async(self, **kwargs) -> None:
        """Async version of :meth:`run`.

        Override in subclasses that have native async implementations.
        The default delegates to :meth:`run` synchronously.
        """
        self.run(**kwargs)

    def __call__(self, **kwargs) -> None:
        """Alias for :meth:`run` — makes the adapter directly callable."""
        self.run(**kwargs)
