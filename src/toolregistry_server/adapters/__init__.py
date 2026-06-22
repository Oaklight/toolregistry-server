"""Protocol adapters for ToolRegistry.

This package contains adapters that expose a :class:`~toolregistry_server.RouteTable`
over different protocols:

- ``openapi``: RESTful HTTP endpoints via FastAPI
- ``mcp``: Model Context Protocol for LLM integration

Adding a new adapter
--------------------

1. Create ``adapters/myproto/__init__.py`` with a class that inherits
   :class:`Adapter`.

2. Implement two abstract methods:

   - ``run(**kwargs)`` — start serving with typed kwargs.
   - ``create_and_run(cls, route_table, **kwargs)`` — classmethod that
     extracts constructor args from kwargs, builds the adapter, and
     calls ``run()``.

3. (Optional) Add a convenience wrapper on :class:`~toolregistry_server.app.App`::

       def serve_myproto(self, **kwargs):
           self.serve(MyProtoAdapter, **kwargs)

4. (Optional) Add a module-level shortcut in ``__init__.py``::

       serve_myproto = _default_app.serve_myproto

``App.serve()`` is adapter-agnostic — it never needs to be modified.

Example skeleton::

    class GRPCAdapter(Adapter):
        def __init__(self, route_table, *, reflection=True):
            super().__init__(route_table)
            self._reflection = reflection

        def run(self, *, host="0.0.0.0", port=50051, **kwargs):
            ...  # start gRPC server

        @classmethod
        def create_and_run(cls, route_table, **kwargs):
            adapter = cls(route_table, reflection=kwargs.pop("reflection", True))
            adapter.run(**kwargs)
"""

from __future__ import annotations

import argparse
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

    @staticmethod
    def add_cli_arguments(parser: argparse.ArgumentParser) -> None:
        """Add adapter-specific CLI arguments to a parser.

        Override in subclasses to declare protocol-specific flags
        (e.g. ``--transport`` for MCP, ``--tokens`` for OpenAPI).

        The default implementation adds common arguments shared by
        all network-serving adapters (``--env``, ``--no-env``,
        ``--host``, ``--port``, ``--config``, ``--profile``).
        """
        parser.add_argument("--env", type=str, default=None, help="Path to .env file")
        parser.add_argument(
            "--no-env", action="store_true", help="Skip loading .env file"
        )
        parser.add_argument(
            "--config",
            type=str,
            default=None,
            help="Path to a JSONC or YAML configuration file for tools",
        )
        parser.add_argument(
            "--profile",
            type=str,
            default=None,
            metavar="PROFILE",
            help=(
                "Deployment profile for tag-based tool filtering. "
                "'remote' disables file_system/destructive/privileged. "
                "'local' disables network. (default: no filtering)"
            ),
        )

    async def run_async(self, **kwargs) -> None:
        """Async version of :meth:`run`.

        Override in subclasses that have native async implementations.
        The default delegates to :meth:`run` synchronously.
        """
        self.run(**kwargs)

    def __call__(self, **kwargs) -> None:
        """Alias for :meth:`run` — makes the adapter directly callable."""
        self.run(**kwargs)
