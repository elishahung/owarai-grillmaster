"""Two-step subtitle translation.

The pre-pass (`prepass`) analyzes the whole film once into a `Briefing`;
chunk translators (`chunk`) then translate deterministic, char-balanced SRT
slices (`chunker`) concurrently against it, returning `{blocks: [{index,
text}]}` that Python rebuilds onto the source timecodes. `assets` samples
the frames and audio both steps see; `prompt` assembles their messages from
`prompts/*.md`. Pure domain code: inputs arrive as `inputs` values, agent
calls leave as `AgentTask`s for the caller to run.
"""
