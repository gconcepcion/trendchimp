from __future__ import annotations

from decimal import Decimal

from rich.console import Console
from rich.table import Table

console = Console()


def render_account(account) -> None:
    table = Table(title="Account", show_header=False)
    table.add_row("Equity", f"${account.equity:,.2f}")
    table.add_row("Cash", f"${account.cash:,.2f}")
    table.add_row("Buying power", f"${account.buying_power:,.2f}")
    table.add_row("Portfolio value", f"${account.portfolio_value:,.2f}")
    console.print(table)


def render_positions(positions) -> None:
    if not positions:
        console.print("[dim]No open positions.[/dim]")
        return
    table = Table(title="Open positions")
    for col in ("Symbol", "Side", "Qty", "Avg entry", "Market value", "Unrealized P&L"):
        table.add_column(col)
    for p in positions:
        side = "[green]LONG[/green]" if p.qty > 0 else "[red]SHORT[/red]"
        pl_color = "green" if p.unrealized_pl >= 0 else "red"
        table.add_row(
            p.symbol, side, f"{p.qty}", f"${p.avg_entry_price:,.2f}",
            f"${p.market_value:,.2f}",
            f"[{pl_color}]${p.unrealized_pl:,.2f}[/{pl_color}]",
        )
    console.print(table)
