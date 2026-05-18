"""One-shot script to close all open zombie positions (entry_price < 10%).

Run once to clear the legacy sub-threshold position ledger:
    uv run python -m scripts.reset_zombies
"""

from __future__ import annotations

from agents.execution_agent import ExecutionAgent


def main() -> None:
    agent = ExecutionAgent()
    open_before = len(agent.get_open_positions())
    closed = agent.reset_zombie_positions(max_entry_price=0.10)
    open_after = len(agent.get_open_positions())
    print(f"Positions before: {open_before}")
    print(f"Zombie positions closed: {closed}")
    print(f"Positions remaining: {open_after}")


if __name__ == "__main__":
    main()
