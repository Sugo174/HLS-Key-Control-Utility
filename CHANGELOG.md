# Changelog

All notable changes to HLS Key Control Utility are documented in this file.

## [v1.0.0] - 2026-11-02

### Added

- Personalization workflow for HLS generation, meter write, and verification.
- Service workflow for service HLS handling and factory-HLS reset.
- DLMS/COSEM optical-head communication with GMAC support.
- SQL Server registry for meters, HLS lifecycle states, operation journal, and export batches.
- Recovery checks for uncertain HLS write and reset outcomes.
- HLS history lookup by meter serial number and by HLS value.
- Branded Excel export for confirmed personalization batches.
- Daily text operation logs.
- Windows integrated authentication option for SQL Server.
- Portable Windows build guidance and public project documentation.

### Changed

- Updated application and Excel styling to use EMIS Electra visual assets.
- Centralized asset resolution for both the desktop interface and Excel export.

### Security

- Encrypted stored HLS values and HMAC-SHA256 fingerprints.
- Local secrets, exports, logs, and build artifacts excluded from version control.

[v1.0.0]: https://github.com/Sugo174/HLS-Key-Control-Utility/releases/tag/v1.0.0
