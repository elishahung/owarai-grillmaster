"""Pipeline events: typed records fanned out to the TUI, console and JSONL log.

Sits on the bottom layer beside `core` (neither imports the other), so any
layer can emit. Stage keys travel as plain `str` for that reason.
"""
