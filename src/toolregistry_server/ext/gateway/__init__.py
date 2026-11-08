"""Gateway admin extension for the ToolRegistry admin panel.

Export:
    >>> from toolregistry_server.ext.gateway import GatewayAdminExtension
"""

from .extension import GATEWAY_EXTENSION_NAME, REQUIRES_API, GatewayAdminExtension

__all__ = ["GATEWAY_EXTENSION_NAME", "GatewayAdminExtension", "REQUIRES_API"]
