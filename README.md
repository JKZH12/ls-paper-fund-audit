# Paper Portfolio

Local simulated portfolio ledger for a paper long/short hedge fund.

This project does not place real trades and does not connect to a broker. It
stores simulated orders, holdings, cash, prices, and daily reports in this
workspace.

## Simulated-Only Policy

All order-related requests in this workspace are treated as simulated orders
only. They may only affect the local paper portfolio ledger. This project must
not place, route, stage, or prepare real broker orders, and it must not connect
to real cash movement or live order management.

## Default Portfolio

- Name: `LS Paper Fund`
- Strategy type: `long_short_hedge_fund`
- Base currency: `USD`
- Initial cash: `50,000,000`

## Quick Start

Initialize the database and default portfolio:

```bash
python3 -m paper_portfolio init --name "LS Paper Fund" --initial-cash 50000000 --base-currency USD --strategy-type long_short_hedge_fund
```

Or rebuild the local SQLite ledger from the append-only audit log after cloning
the repository on another machine:

```bash
python3 -m paper_portfolio audit rebuild --events audit/events.jsonl
python3 -m paper_portfolio audit verify
python3 -m paper_portfolio summary
```

`data/portfolio.sqlite` is a local runtime database and is intentionally ignored
by git. The cross-device source of truth is the audit log plus reports.

Record a simulated order:

```bash
python3 -m paper_portfolio buy NVDA 100 1000 --fee 1 --notes "initial test order"
```

Supported sides:

- `buy`: open or add to a long position
- `sell`: reduce a long position
- `short`: open or add to a short position
- `cover`: reduce a short position

Update a mark price:

```bash
python3 -m paper_portfolio mark NVDA 1015
```

Generate a daily report:

```bash
python3 -m paper_portfolio report
```

The report is written to `reports/daily/YYYY-MM-DD.md`.

Every simulated order, price mark, and daily report also writes an audit event
to `audit/events.jsonl` and a daily manifest to `audit/manifests/YYYY-MM-DD.json`.
Verify the hash chain with:

```bash
python3 -m paper_portfolio audit verify
```

Anchor the audit artifacts to git:

```bash
python3 -m paper_portfolio audit anchor
```

The anchor command stages audit files, daily reports, and the dashboard. Use
`--include-code` when code, tests, README, or AGENTS.md changed too.

## Simulated Order Intake Format

When asking Codex to record an order, use this compact format:

```text
Sim trade: buy 100 NVDA @ 1000, fee 1, notes: initial test order
Sim trade: short 50 TSLA @ 180, fee 1, notes: hedge beta
Sim price: NVDA 1015
Generate portfolio report
```

All trades are paper trades only.
