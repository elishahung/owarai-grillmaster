## Agent web search
Use local evidence first: program title/description, full audio when available, reference images, fixed glossary if supplied, parent pre-pass context if supplied, and the source SRT as a fallible timing/text scaffold. The SRT is not ground truth: expect ASR errors and resolve conflicts with audio, images, program metadata, and reliable external references when needed.

Use built-in web search only when local context is insufficient for an external fact that would materially affect `characters`, `proper_nouns`, `glossary`, `catchphrases`, or `segment_summaries`: official/common spellings of talent or group names, program or segment titles, public work titles, brand names, recurring public catchphrases, or other public references.

Prefer official pages, reliable listings, or stable public references over unsourced snippets. If web evidence is inconclusive, keep the mapping conservative instead of inventing a confident localization. Do not use web search for routine phrasing or tone choices already settled by local evidence. The result remains the schema JSON only: do not add citations, prose, or markdown.
