"""Deliverable packaging: the folder, the burned-in render, remix, media pools.

Pure assembly over explicit inputs: callers hand in paths, the burn plan's
layers and pools, an `FfmpegRunner` and an `EventSink` for progress bars.
`render` holds the package look (the NVENC recipe, moved verbatim from the
legacy media module) and the plain burn-in; `remix` splits a show into
noise-headed parts; `pools` is the numbered-file pool with its reserve-then-use
cursor that remix noise and inserts share; `assemble` builds the folder and
decides the burn plan (chat panel or dialogue alone).
"""
