### OFFICIAL SOURCE METADATA
The user message includes official source metadata such as cast/talent names
from the distribution platform.

When official source cast/talent metadata is present:
- `characters` MUST include every listed cast/talent entry.
- Preserve each official source name exactly as written in `name_jp`; do not
  normalize spacing, convert script, rewrite kanji/kana, or replace it with an
  ASR spelling.
- Use the official source names as authoritative anchors for identifying
  recurring people and correcting ASR name errors.
- If audio, images, or ASR appear to conflict with the official source spelling,
  keep the official source spelling in `characters.name_jp` and put aliases or
  ASR corrections in `proper_nouns`.
