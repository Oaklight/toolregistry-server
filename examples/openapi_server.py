"""Minimal OpenAPI server example.

Run:
    python examples/openapi_server.py

Then visit:
    http://localhost:8000/docs   — interactive Swagger UI
    http://localhost:8000/tools  — list registered tools
"""

from toolregistry import ToolRegistry
from toolregistry_server import RouteTable
from toolregistry_server.openapi import create_openapi_app

from tools import add, greet, multiply

# 1. Create registry and register tools
registry = ToolRegistry()
registry.register(add)
registry.register(greet)
registry.register(multiply)

# 2. Build route table
route_table = RouteTable(registry)

# 3. Create FastAPI app
app = create_openapi_app(route_table)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
