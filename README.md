# Bug Radar

Bug Radar is a bug bounty dashboard that aggregates public programs from multiple platforms into one filter-first UI.

## Stack

- Frontend: `React + TypeScript + Vite`
- Data pipeline: `Python 3` (`collectors -> normalize -> diff`)
- Build output: static site in `dist/` with data in `dist/data/`

## Live data model

- Collectors now fetch live public data for:
  - HackerOne
  - Bugcrowd
  - Intigriti
  - YesWeHack
  - Independent programs (self-hosted VDP/BBP via FireBounty listing)
- OpenBugBounty collector is implemented with live fetch + automatic seed fallback when blocked by anti-bot protections.
- Every pipeline run writes:
  - `data/programs.json`
  - `data/changes.json`
  - `data/latest_updates.json`
  - `data/hacktivity.json`
  - `data/stats.json`

## Commands

1. Install dependencies:

```bash
npm install
```

2. Run one live refresh:

```bash
npm run pipeline
```

3. Keep data refreshing continuously (default every 30 minutes):

```bash
npm run live-sync
```

You can change interval:

```bash
BUG_RADAR_REFRESH_SECONDS=900 npm run live-sync
```

4. Start UI:

```bash
npm run dev
```

5. Build production static assets:

```bash
npm run build
```

## GitHub Pages deployment

- Workflow file: `.github/workflows/pages.yml`
- Triggers:
  - Push to `main`
  - Manual run (`workflow_dispatch`)
  - Scheduled refresh every 24 hours (00:00 UTC)
- Deploy target: `dist/`

After first push, set repository Pages source to **GitHub Actions** in:

- `Settings -> Pages -> Build and deployment -> Source`

## Project structure

```text
Bugradar/
|-- data/
|   |-- programs.json
|   |-- changes.json
|   |-- latest_updates.json
|   |-- hacktivity.json
|   |-- stats.json
|   `-- history/programs.prev.json
|-- scripts/
|   |-- collectors/
|   |-- seeds/
|   |-- normalize.py
|   |-- diff.py
|   |-- latest_updates.py
|   |-- hacktivity.py
|   |-- run_pipeline.py
|   |-- live_sync.py
|   `-- copy-data.mjs
|-- src/
|   |-- App.tsx
|   |-- filters.ts
|   |-- utils.ts
|   |-- types.ts
|   `-- styles.css
|-- index.html
|-- package.json
`-- vite.config.ts
```

## Notes

- The frontend does not call external bounty platform APIs directly; it reads generated local JSON.
- Live collector failures automatically fall back to local seed data per platform.
