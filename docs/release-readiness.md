# Preview validation report

Kovaak Agent **0.1.0 Preview**, reviewed September 9, 2026, on Windows. Licensed under MIT; see [LICENSE](../LICENSE) and [third-party notices](../THIRD_PARTY_NOTICES.md).

The local workflows and request-security fix have passing evidence. **Live provider coaching has not been verified:** no API credentials were available. This release is a preview, not a fully validated stable 1.0.

## Request-security fix

The local server validates requests before reading data, exporting backups, saving settings/memory, or calling a model:

- Host must be exactly `127.0.0.1` or `localhost` with the actual server port. Duplicate Host headers and unrelated names are rejected.
- Supplied Origin and Referer must match the local request origin. Fetch Metadata must identify same-origin access or direct navigation. Duplicate security headers and cross-origin browser requests are rejected.
- POST requires `application/json`. Plain-text and form submissions are rejected even without browser metadata. Ambiguous body framing, negative lengths and oversized bodies are rejected before reading them.
- Reads and writes, including backup downloads and static pages, receive these protections. Response headers block framing, MIME sniffing and cross-origin resource use. No CORS permissions are granted.
- Startup supports local loopback access only. Public hosting, tunnels and reverse proxies are unsupported.

Local command-line clients may omit browser metadata but still need the exact local Host and JSON writes. These checks do not authenticate other users or programs on the same computer. Browser-specific exploit delivery and DNS rebinding were not exhaustively tested. The design follows the request-origin, Fetch Metadata and content-type approach described in [OWASP's CSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).

## Verified behavior

| Check | Result |
| --- | --- |
| Automated suite | **37 tests pass**, including eight request-security test methods. |
| Isolated source package | The complete suite passes in a separate folder populated only from [release-files.txt](release-files.txt), using `python -S -B -m unittest discover -s tests -v`. Disabling site-package loading verifies the standard-library-only runtime. An installed bundled Python executable was used; this was not a fresh stock-Python installation. |
| JavaScript and whitespace | `node --check web/app.js` and Git diff checks pass. |
| Browser workflow | All five tabs load. Connections imports the three-run sample. The daily view for August 30, 2026 shows three runs and four minutes. A saved synthetic memory remains after reload. The backup link starts a download. No captured JavaScript warnings/errors. |
| Backup recovery | A same-origin backup passes SQLite integrity checking and restores the retained run and saved memory in a new app folder. |
| Retention | Tests cover legacy migration, source rotation/correction/restart, atomic rollback and retry, calendar/leap-year bounds, paired accuracy, bounded context and shared memory across mocked providers/roles. |
| Annual reviews | Tests cover catch-up, pause/restart, concurrent scheduling and refresh after late historical imports. |
| Security regressions | Foreign/missing/duplicate hosts, foreign/opaque/malformed origins, different scheme/port, Referer and Fetch Metadata, unsupported content types, body framing, preflight and nonlocal binding are tested. Rejected requests cannot save memories or invoke model calls. |

Earlier visual checks covered themes, annual report dialogs, keyboard tab navigation and narrow-screen layouts. The three screenshots in `screenshots/` show the actual UI with synthetic records and example memories; no personal training records or provider credentials appear in them.

## Public source scope

The public source starts from a clean release snapshot. Obsolete planning PDFs, personal machine paths, player databases, credentials, backups and temporary files are excluded from the source file list and release history. Original development history was retained in a private local backup before sanitation; it is not part of this release. No unrelated collaborator branch or tag was included in the cleanup.

The complete public package is explicitly listed in [release-files.txt](release-files.txt), including `app/memory.py`, its tests, request-security regressions, MIT license, third-party notice, launcher, sample, README and screenshots. Scanning checks the complete release tree and its reachable history for obsolete PDFs, private path patterns and common credential/private-key formats. Such scans are targeted checks, not proof that every possible secret format is detectable.

## Remaining limitations

- Live OpenAI, OpenRouter and Gemini calls were not tested. Provider tests use mocked responses. Model access and availability depend on the chosen provider/account.
- Coaching quality and usefulness over months or years have not been independently evaluated. Persisting context does not guarantee identical or accurate recommendations across models.
- The annual worker cannot run while the application is closed; it catches up after launch. Daily/weekly/monthly model reports are not automatically scheduled.
- A portable document library, automatic cross-device synchronization and an in-app complete profile deletion/restore workflow are not implemented.
- A full accessibility audit, long-running soak test and other operating systems remain unverified.

See the [release notes](release-notes-0.1.0.md) for installation and the [README](../README.md) for provider setup, source identity limits, data handling, and manual backup recovery.
