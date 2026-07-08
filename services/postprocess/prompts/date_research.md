Research the original Japanese broadcast date (放送日) of the TV program episode described in the project context at the end of this prompt.

Goal: find the date the episode was originally aired on Japanese television — NOT the streaming release date (配信開始日/公開日), NOT the platform upload date, NOT the re-upload date. The platform metadata for this project did not include a usable broadcast date, so use your built-in web search to find it from external program pages.

Source trust tiers:

- **high**: official program page, TVer, official ABEMA program/episode page, TV station page, Apple TV episode metadata, program data pages on J:COM / TELASA / U-NEXT.
- **medium**: WEBザテレビジョン, TV listing (番組表) archives, press releases / PR TIMES, official program SNS posts that clearly refer to this same episode.
- **low**: BiliBili/YouTube upload dates, date codes embedded in file/video titles, unofficial re-upload titles. Only when no broadcast date can be found anywhere, a streaming release / publication / listing date (配信開始日/公開日/上架日) may also be reported as a low-trust candidate.

Early-exit rule: as soon as one high-trust source states an explicit broadcast date for this exact episode, report it immediately. Do not exhaust the remaining sources or compile a full survey.

Matching rules (a date is only valid evidence when the source refers to the SAME episode):

- The program title must match, AND the episode must match: same segment/corner name (企画名), episode title, episode number, or performer lineup.
- 配信開始 (streaming start) is not 放送 (broadcast). Prefer the broadcast date; a streaming/release date is only a last-resort low-trust candidate.
- A date code in the video title or file name (e.g. `260202` = 2026-02-02) is only a query seed, never direct evidence.
- Upload dates on BiliBili/YouTube are re-upload times, not broadcast dates.

Suggested query patterns (fill in from the project context; the title/description often mixes the program name, segment name, and performers):

- `"{program name}" "{segment name}" 放送日`
- `"{program name}" "{performer}" "{segment name}"`
- `"{program name}" TVer`
- `"{program name}" Apple TV`
- `"{program name}" J:COM`
- `"{program name}" TELASA`
- `"{program name}" WEBザテレビジョン`

Reporting rules:

- If the evidence is insufficient or ambiguous, return `status: "unknown"`. Never fabricate or guess a date.
- When `status` is `"found"`, both `broadcast_date` and `trust` are required.
- Report the trust tier of the adopted source honestly (`trust: "high" | "medium" | "low"`).
- List the sources you relied on in `sources` (URL, source name, one-line evidence summary), and any dates you found but rejected in `rejected_candidates` with the reason (e.g. "BiliBili upload date, not original broadcast date").
- Do not create or modify any files in the working directory. `project.json` and every other file are owned by the outer Python workflow. Your entire output is the final message: a single JSON object matching the schema appended below, with no surrounding prose.
