# Kovaak Agent 0.1.0 Preview

A local Kovaak's training dashboard with retained history and shared coaching memory. This is a **preview**, not a validated stable 1.0 release.

## Included

- Orange light/dark interface with Progress, Latest session, Coach, Memory, and Reviews tabs.
- CSV capture and SQLite history that persists across restarts and source rotation.
- Daily, weekly, monthly, and yearly dashboards with scenario-specific comparisons.
- Editable goals, constraints, outcomes, and coaching roles shared across supported provider adapters.
- Saved coaching reports, automatic annual statistical reviews, and database backup download.
- Local-only HTTP request protections, including protected backup access.
- MIT license, bundled daisyUI attribution, setup instructions, and actual screenshots using synthetic data.

## Start

Install Python 3.11 or newer, download and extract the source archive, open a terminal in the project folder, and run:

```text
python -m app.server
```

Open `http://127.0.0.1:8765`. No third-party Python packages or frontend build are required. See the repository README for source setup, sample dates, provider settings, and backups.

## Validation and limitations

**37 automated tests pass**, including request security, retained memory, and backup recovery. Browser checks verified sample import, all five tabs, memory persistence, period controls, and backup download.

**Live coaching was not tested because no API credentials were available.** Provider tests use mocked responses. API access, model availability, provider charges, and the quality of model recommendations must be evaluated separately. Dashboards, memory editing, backups, and annual statistics work without an API key.

The annual worker runs only while the app is open and catches up after restart. Cross-device sync, a portable document library, and scheduled daily/weekly/monthly model reports are not included. Windows was tested; other operating systems and long-running coaching usefulness remain unverified.
