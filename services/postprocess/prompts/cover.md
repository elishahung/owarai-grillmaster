Use the $imagegen on `poster.jpg` in the current working directory. Inspect the actual poster before writing the prompt. The cover task is a reinterpretation of the original poster as if the same people and poster concept appeared inside a Rick-and-Morty-like adult animated cartoon, not a photo-shaped filter pass.

Write scope (strict): you may only create or modify `poster.cover.png`. Do **not** touch any other file, in particular:

- `project.json` — do not edit, do not flip progress flags, do not touch its contents in any way. The outer Python workflow owns this file and will mark progress after confirming your output.
- `poster.jpg` — read-only source.
- Subtitle, video, audio, and cache files — unrelated to cover generation.

Do not run scripts that mutate `project.json` (e.g. don't run the project's own Python entrypoints).

Cover rules:

- Preserve the original poster concept, people count, relative positions, props, background, callout shapes, visible text meaning, and visual hierarchy.
- Reimagine each person as a native character in a Rick-and-Morty-inspired American adult animated cartoon. Do not preserve photographic head shapes, skin texture, lighting, lens effects, or cutout-photo edges when they make the result look like a filter.
- Keep recognizable identity cues such as hairstyle, glasses, facial hair, expression, pose, relative body size, and distinctive facial proportions, but simplify them into cartoon construction.
- Preserve the original meaning and layout of visible text, but convert Japanese text into concise English.
- Do not add new objects, characters, logos, story themes, food, badges, or titles that are not present in the original poster unless the user explicitly requests them.
- Do not reuse stale prompt details from previous projects. In particular, do not mention malatang, a bowl, six heads, a bottom branding strip, sci-fi additions, or other elements unless they are visible in the current `poster.jpg`.
- Apply only a Rick-and-Morty-inspired American adult animated cartoon rendering style: thick uneven black outlines, flat saturated colors, simplified shading, large uneven eyes, exaggerated mouths and teeth, rubbery facial geometry, and slightly grotesque comedy caricature.
- Do not intentionally make the people look like different people.
- Avoid the word "sci-fi" in the prompt unless the source poster already has sci-fi elements.

Use this prompt shape, replacing bracketed text with what is actually visible in the poster:

```text
Reimagine the original poster as if this exact variety-show poster existed inside a Rick-and-Morty-inspired American adult animated cartoon. Keep the same poster concept, same number of people, same relative positions, same props/background/callout shapes, same visible text hierarchy, and same overall layout, but redraw the people as native cartoon characters rather than preserving photo-real head shapes or applying a filter. Keep each person's recognizable identity cues: [briefly list visible cues such as hairstyle, glasses, pose, expression, clothing, relative size]. Use thick uneven black outlines, flat saturated colors, simplified shading, large uneven eyes, exaggerated mouths and teeth, rubbery facial geometry, and slightly grotesque comedy caricature. Convert all visible Japanese text into concise English with the same meaning and similar placement: [list each visible text item and its English replacement]. Do not add new characters, objects, logos, food, badges, sci-fi elements, or extra text. Do not change the poster concept; redraw only the existing poster into this cartoon universe.
```

Save or copy the generated image to `poster.cover.png` in the current working directory. If the image tool saves under `~/.codex/generated_images/...` (e.g. `C:\Users\john-doe\.codex\generated_images\...`), copy the newest generated PNG to the project folder and leave the original in place. After copying, verify `poster.cover.png` exists.

Treat cover generation as one-shot. Do not regenerate automatically just because the result is imperfect.

Final state: `poster.cover.png` exists in the current working directory.

Reply with just the single word `done`. Do not include explanations, summaries, file paths, descriptions of what you did, or any other commentary — the calling workflow ignores your final message and any extra tokens are wasted.
