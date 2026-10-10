"""Orchestration: the stage registry, the runner, side tasks, delivery, reset.

Sits under `cli` and beside `tui` (neither imports the other). It may import
`stages` and every layer below, and is, with `cli`, the only reader of
`AppConfig`.
"""
