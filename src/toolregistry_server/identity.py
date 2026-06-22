"""Server identity configuration.

A :class:`ServerIdentity` instance carries branding information that
flows through all layers — CLI banner, OpenAPI metadata, MCP server
name, and admin panel title.

Default values match the standalone ``toolregistry-server`` package.
Downstream projects (e.g. toolregistry-hub) create their own instance::

    identity = ServerIdentity(
        name="ToolRegistry Hub",
        version="1.2.0",
        banner_art=HUB_BANNER_ART,
    )
    app = HubApp(identity=identity)
    app.serve_mcp(transport="stdio")
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServerIdentity:
    """Branding and metadata for a server instance.

    Attributes:
        name: Human-readable product name.  Used as the OpenAPI title,
            MCP server name, and admin panel heading.
        version: Version string displayed in banner and OpenAPI schema.
            Defaults to the ``toolregistry-server`` package version.
        description: Short description for the OpenAPI schema.
        banner_art: ASCII art for the CLI banner.  ``None`` means use
            the default ``toolregistry-server`` banner.
    """

    name: str = "ToolRegistry Server"
    version: str = ""
    description: str = "Tool server powered by toolregistry"
    banner_art: str | None = None
    prog: str = ""
    """CLI program name (e.g. ``toolregistry-server``).

    Defaults to ``name`` lowercased with spaces replaced by hyphens.
    Set explicitly for names with punctuation or non-ASCII characters.
    """

    def __post_init__(self) -> None:
        if not self.version:
            from . import __version__

            object.__setattr__(self, "version", __version__)
        if not self.prog:
            object.__setattr__(self, "prog", self.name.lower().replace(" ", "-"))
