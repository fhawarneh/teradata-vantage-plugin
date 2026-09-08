# Notices

## Third-party software

**teradata-mcp-server** (Community edition) — Copyright (c) 2016 by Teradata. All rights reserved. http://teradata.com
Licensed under the MIT License. The plugin vendors the unmodified 0.2.6 release wheel under
`servers/` together with the upstream licence text (`servers/LICENSE.teradata-mcp-server`, byte-identical to the
`LICENSE` inside the wheel's own `dist-info`). Source: https://github.com/Teradata/teradata-mcp-server — provenance
and integrity details in `servers/VENDORED.md`.

Nothing else is redistributed here. The server's pinned dependency closure
(`servers/requirements-0.2.6*.txt`) is downloaded from PyPI, under each package's own licence, only when you run
`/teradata-vantage:setup install`; in bridge mode `mcp-remote` is fetched by `npx` at launch. Neither is bundled
with the plugin.

## Trademarks

Teradata, Vantage and ClearScape Analytics are trademarks of Teradata Corporation. This plugin is an independent,
community-maintained project and is not affiliated with, endorsed by, or sponsored by Teradata Corporation.
