"""One module per pipeline stage or side task: the glue that reads the
config sections and `ProjectLayout` paths a domain module needs, calls it,
and records the result in the project state. Each exports `STAGE`
(`StageDef`) or `TASK` (`SideTaskDef`) for `pipeline.registry`.
"""
