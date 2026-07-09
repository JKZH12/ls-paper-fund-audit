# Workspace Rules

## Trading Boundary

- All order-related requests in this workspace are simulated orders only.
- Orders may only affect the local paper portfolio ledger in this repository.
- Do not place, route, stage, or prepare real broker orders from this workspace.
- Do not connect this workflow to real brokerage execution, real cash movement, or live order management.
- Treat words such as "下单", "买入", "卖出", "short", "cover", and "trade" as paper-trading instructions unless the user explicitly moves the discussion outside this workspace and starts a separate workflow.

## Default Portfolio

- Portfolio name: `LS Paper Fund`
- Strategy type: `long_short_hedge_fund`
- Base currency: `USD`
- Initial cash: `50,000,000`

## Daily Workflow

- Record simulated trades through short commands such as `python3 -m paper_portfolio buy NVDA 100 1000`.
- The verbose `python3 -m paper_portfolio trade` command remains supported.
- Update manual marks through `python3 -m paper_portfolio mark NVDA 1015`.
- Generate daily reports through `python3 -m paper_portfolio report`.
- Verify audit trail through `python3 -m paper_portfolio audit verify`.
- Anchor audit artifacts to git/GitHub through `python3 -m paper_portfolio audit anchor`.
- Keep all outputs local to this workspace unless the user explicitly asks for publishing or export.

## Cross-Device / Cloud Handoff

- Treat this GitHub repository as the execution source of truth for Mac, Windows, and Codex cloud work.
- `data/portfolio.sqlite` is a local runtime database and is intentionally ignored by git.
- If the SQLite database is missing after clone, rebuild it from the append-only audit log:

```bash
python3 -m paper_portfolio audit rebuild --events audit/events.jsonl
python3 -m paper_portfolio audit verify
python3 -m paper_portfolio summary
```

- If rebuilding over an existing local database is intentional, use `--force` only after checking `git status` and confirming the current database is disposable.
- Before any order-related work on a second machine, run `git pull --ff-only`, rebuild the database if needed, then run `audit verify` and `summary`.
- After any trade, mark, report, dashboard, or audit change, run tests when code changed, run `audit verify`, and anchor/push the updated audit/report/dashboard artifacts before handing off to another device.
- Do not let two machines mutate the ledger at the same time. Pull first, write once, verify, then push.

## Natural-Language Paper Order Handling

- Accept plain-language instructions such as `买入1.5%的Kioxia`, `short 1%的AVGO`, `卖出一半WDC`, or `cover 0.5%的AMD` as simulated paper orders.
- When the user specifies `% NAV`, use the latest verified `Total equity` from `python3 -m paper_portfolio summary` as the sizing base.
- If the user omits price, fetch a current quote with `/Users/jack/.local/bin/fmp-api quote symbol=... --compact` on Mac or the equivalent configured `fmp-api` wrapper on Windows.
- For non-USD listings, store the ledger price in USD-equivalent terms and preserve the trace in notes/source:
  - Japan: local JPY price times `JPYUSD`
  - Hong Kong: local HKD price times `HKDUSD`
  - Europe: local EUR price times `EURUSD`
- Use the canonical ticker in the ledger when venue is clear, e.g. `285A.T`, `6981.T`, `2513.HK`, `1888.HK`, `SOI.PA`.
- If ticker, venue, FX, ADR liquidity, or corporate-action treatment is uncertain, stop and ask the user before writing the trade.

## Model Book Dashboard

- `reports/dashboard/index.html` is a read-only analytics view over the ledger, audit trail, and reports.
- Do not add mutation, order-entry, broker, or real-execution behavior to the dashboard.
- When the user asks for a dashboard, default to refreshing live FMP marks first, regenerating the daily report, and then updating the dashboard unless the user explicitly asks for a read-only/no-write view.
- Keep pair/basket labels, cross-market FX traces, quote freshness, latest audit head, and report links visible when updating the dashboard.
- When dashboard data is refreshed, verify the JavaScript syntax and keep the daily report/audit manifest in sync.
- If the dashboard is already open in a browser, reload it with a cache-busting query string after refreshing so the visible tab cannot keep showing stale PnL or audit metrics.
