## On-demand video frames
The pre-sampled reference images may not cover the exact moment you need. When a line is doubtful — garbled or suspicious ASR, an unclear proper noun or name, an on-screen text card (字卡, which Japanese variety shows often flash exactly at these moments), or you simply want to confirm what is on screen — extract the exact frames you need instead of guessing.

Call the `get_frames` tool with the specific timestamps (in seconds) you want to see; it returns the frames as images.

- Valid timestamps cover the entire video. Stay strictly within this window.
- Use this when visual evidence would clarify names, captions, objects, reactions, scene changes, or any decision that would be weaker if based on text alone.
