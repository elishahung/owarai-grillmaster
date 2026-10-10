"""Per-model SRT options: compensation for ElevenLabs model timing faults.

Scribe v2 sometimes times a sentence's last characters seconds to a minute
after the rest, over music, a VTR or a replay. The silence guards keep those
characters in their sentence, so the subtitle block spanned the whole
silence. For `scribe_v2`, a silence of more than 3 s inside an utterance
counts as 1 s toward its end. The block keeps its text and its start, and it
ends soon after the sentence. The 1 s stays because the late characters keep
Scribe's compressed durations; closing the whole silence made blocks vanish
too early.

Rejected:

- Splitting at the silence leaves the tail as a stray fragment at the wrong
  time.
- Moving the speech after the tail as well shifts correctly timed lines up
  to 40 s early.

Side effect: an utterance that used to run up against the next speaker's
line no longer merges with it into a `-` dialogue block.

Any other model gets the plain defaults, because the fault is Scribe v2's.
Re-validate before enabling this for another model. Measurements and the
review are in `.agents/skills/project-architecture/references/asr-provider-evaluation.md`.
"""

from __future__ import annotations

from grillmaster.asr.srt_builder import SrtFormatOptions

_SCRIBE_V2_MODEL = "scribe_v2"
_SCRIBE_V2_OPTIONS = SrtFormatOptions(
    inner_silence_limit_s=3.0, inner_silence_kept_s=1.0
)


def srt_options_for_model(model: str) -> SrtFormatOptions:
    """The SRT options for output of the ElevenLabs `model`."""
    return _SCRIBE_V2_OPTIONS if model == _SCRIBE_V2_MODEL else SrtFormatOptions()
