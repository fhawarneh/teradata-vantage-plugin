# Vendored third-party software

This directory contains the unmodified release wheel of **teradata-mcp-server**, the open-source Model Context
Protocol server for Teradata, published by Teradata under the MIT License.

- Upstream: https://github.com/Teradata/teradata-mcp-server (tag v0.2.6, commit 0de3368b60c1de7152a8a08dd3e67b32ebf62578)
- PyPI: https://pypi.org/project/teradata-mcp-server/0.2.6/
- Licence: MIT — see LICENSE.teradata-mcp-server (Copyright (c) 2016 by Teradata. All rights reserved.), which is
  byte-identical to the LICENSE shipped inside the wheel's own dist-info
- Integrity: sha256 in VENDORED.sha256, identical to the PyPI digest; provenance details in VENDORED.md

The plugin installs this wheel, plus its hash-pinned dependency closure (requirements-0.2.6*.txt), into the plugin's
persistent data directory when you run `/teradata-vantage:setup install`. Nothing is modified or patched, and the
dependencies themselves are downloaded from PyPI under their own licences rather than redistributed here.
