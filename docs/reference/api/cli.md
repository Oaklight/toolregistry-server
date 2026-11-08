# CLI

`CLI` is the command-line entry point: it defines the `openapi` and `mcp` subcommands, parses the shared and adapter flags, and dispatches to `App`. See the [CLI reference](../cli/index.md) for every flag, and [Extending Applications](../../guides/extending.md) for adding your own subcommands.

::: toolregistry_server.cli.CLI
