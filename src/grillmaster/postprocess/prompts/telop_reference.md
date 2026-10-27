## On-screen text (OCR)

The text cards the video shows (edited captions, name and info cards, written notes), read by OCR from frames sampled twice a second, with when each is on screen. Long-running fixtures (logos, corner titles) are left out.

- Edited captions often spell a name or term correctly where the ASR misheard it, and often quote the line said at that moment. Use a card as local evidence for a name, a term or a misheard line around its time, and cite it (`OCR <seconds>`) like any other evidence.
- OCR misreads characters and also reads scene text (signs, labels, clothing). A card is a lead, not ground truth: when a card and the transcript disagree on something that matters, check the frame before you change anything.
- The cards get their own translated layer; never add their content to the dialogue subtitles.

```tsv
{telops}
```
