## On-screen text (OCR)

The text cards the video shows (edited captions, name and info cards, written notes), read by OCR from frames sampled twice a second, with when each is on screen. Long-running fixtures (logos, corner titles) and cards without Japanese text are left out.

- Edited captions often spell a name or term correctly where the ASR misheard it, and often quote the line said at that moment. Use a card as local evidence for a name, a term or a misheard line around its time, and cite it (`OCR <seconds>`) like any other evidence.
- OCR misreads characters and also reads scene text (signs, labels, clothing). A card is a lead, not ground truth: when a card and the transcript disagree on something that matters, check the frame before you change anything.
- The cards get their own translated layer. A block whose `ja` misheard the line a card quotes may be corrected from the card, within what that block's time covers; never add a card's content as a new line or to a block it does not match: a line the ASR dropped has no timing, and the card layer carries it.

```tsv
{telops}
```
