  - 発話 is a second hearing by a different speech recognizer than the one
    that wrote `ja`, timed like it. It is about as accurate as `ja`,
    possibly less: another angle on the same audio, not a referee. It keeps
    the speech but not the spelling: names and homophones may carry the
    wrong kanji (緒方 for 尾形), so weigh how a reading sounds, and take the
    writing from テロップ, the briefing or a frame. `話者N` numbers only mark
    a change of speaker within the span; they do not say who speaks.
    - When it sounds like what `ja` says, `ja` stands.
    - When it sounds like what `zh` already renders over `ja` (an earlier
      pass heard the audio and corrected the ASR), `zh` stands.
    - When it sounds different from both, fix the line if the new reading
      also sounds like what `ja` wrote (a real mishearing does) and fits the
      context better, or if something independent backs it: a caption or
      name card, a frame, CC, a source, or a second `watch_clip` of the span
      shifted by a few seconds that hears the same.
    - When it is unclear on the line, or two hearings disagree, leave the
      line and name it in `summary`.
