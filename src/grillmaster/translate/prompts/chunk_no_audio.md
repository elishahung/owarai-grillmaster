<!-- assignment -->
and several **reference images sampled from the same chunk range** (no audio is available for this run). You translate ONLY the blocks in your assigned index range, and you must focus your visual inspection strictly on that range.

<!-- evidence -->
Treat the **chunk images** as the truth source for visible facts (who is on screen, reactions, props, captions, costumes, locations, scene changes) and the ASR SRT as the block/timecode scaffold and the (fallible) transcript of what was said. When they conflict, prefer images for visual context and use the ASR text for spoken content and to preserve segmentation. No audio track is available for this run.

<!-- asr_correction -->
**Correct ASR, then localize naturally:** Use the images, the pre-pass briefing, and surrounding context to correct weird ASR mistakes, resolve homophone mix-ups, identify speakers, and understand nonsensical raw text.

<!-- particles -->
only where they genuinely match the speaker's rhythm/emotion inferred from the dialogue and context.

<!-- subjects -->
unless the subject is unambiguously recoverable from the source line, `segment_summary`, or immediately preceding blocks.
