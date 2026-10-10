<!-- assignment -->
the **chunk-specific audio slice**, and several **reference images sampled from the same chunk range**. You translate ONLY the blocks in your assigned index range, and you must focus your listening and visual inspection strictly on that range.

<!-- evidence -->
Treat the **chunk images** as the truth source for visible facts (who is on screen, reactions, props, captions, costumes, locations, scene changes), the **chunk audio slice** as the truth source for spoken content, tone, rhythm, and emotion, and the ASR SRT as the block/timecode scaffold plus a fallible transcript. When they conflict, prefer images for visual context, audio for what was said, and use ASR mainly to preserve segmentation and guide translation.

<!-- asr_correction -->
**Correct ASR, then localize naturally:** Use the images and audio to correct weird ASR mistakes, resolve homophone mix-ups, identify speakers, and understand nonsensical raw text.

<!-- particles -->
only where they genuinely match the speaker's rhythm/emotion as heard in the audio.

<!-- subjects -->
unless the subject is unambiguously recoverable from the audio, source line, `segment_summary`, or immediately preceding blocks.
