"""Protocol adapters for ToolRegistry.

This package contains adapters that expose a :class:`~toolregistry_server.RouteTable`
over different protocols:

- ``openapi``: RESTful HTTP endpoints via FastAPI
- ``mcp``: Model Context Protocol for LLM integration

All adapters inherit from :class:`Adapter` and implement :meth:`run`.
Each adapter also provides :meth:`create_and_run` as a class method
that handles adapter-specific setup (tokens, names, etc.) from kwargs.
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

    Subclasses must implement:

    - :meth:`run` — start serving with typed kwargs
    - :meth:`create_and_run` — class method that constructs the adapter
      from a route table + kwargs, then calls :meth:`run`

    Example::

        # Low-level: construct + run
        adapter = OpenAPIAdapter(route_table, tokens=["secret"])
        adapter.run(host="0.0.0.0", port=8000)

        # High-level: one-shot from kwargs (used by App)
        OpenAPIAdapter.create_and_run(route_table, host="0.0.0.0", port=8000)
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

        Each subclass defines its own keyword arguments.
        """

    @classmethod
    @abstractmethod
    def create_and_run(cls, route_table: RouteTable, **kwargs) -> None:
        """Construct the adapter and run it in one step.

        Subclasses extract their constructor args (e.g. tokens, name)
        from kwargs, build the adapter, and call :meth:`run` with
        the remaining transport kwargs.

        Args:
            route_table: The RouteTable to serve.
            **kwargs: Adapter-specific construction + run arguments.
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
