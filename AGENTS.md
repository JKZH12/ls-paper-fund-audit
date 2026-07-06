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
