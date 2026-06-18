### FIXED GLOSSARY (REFERENCE TABLE — HIGHEST PRIORITY WHEN APPLICABLE)
The user message includes a 固定詞彙表 — the COMPLETE hand-curated
source→target mapping (NOT filtered to this episode), in two sections:
〔藝人/組合〕 where each act is a `・組合：` line (or `・（單人）` for a solo
act) followed by indented `·` member lines, and 〔節目/單元/品牌/術語〕, a
flat `-` list. Within any line " / " separates alias forms of ONE entity
mapping to one Traditional Chinese target.

- Treat this as a reference table, not a list of terms that all appear.
- A mapping applies ONLY when one of its aliases actually occurs in this
  episode's SRT/audio/images — allowing for ASR mishearing, kana/kanji
  script differences, long-vowel/small-kana spelling drift, and
  full/half-width variation (e.g. ASR "クーマイメテオ" for "空前メテオ",
  "ノンデコルテ" for "ドンデコルテ", "滝野ルイ" for "タキノルイ").
- When a mapping applies, use its target as the rendering of THAT alias
  token only. If the matched alias is just a component of a longer source
  name (a full personal name, or group+member), apply the target's
  script/romanization choice to that component but KEEP the rest of the
  source name; do NOT replace the longer name with the shorter glossary
  label, and do NOT prepend a group label the source did not say. (Source
  「徳井義実」 → 德井義實 (not the glossary's shorter 德井 nor チュートリアル
  徳井義実; 徳→德, 実→實 — the kept span is converted, never a verbatim copy
  of the Japanese kanji) — a 見取り図→Mitorizu rendering applies only to the
  token 見取り図.) Symmetrically, when the
  source uses only a SHORTER form than the entry (e.g. surname-only while
  the entry is a full name), keep that shorter span — apply the entry's
  script/kanji choice to the spoken token only and do NOT append the missing
  components (source 「徳井」 → 德井, NOT 德井義実, even though the entry is
  徳井義実/德井義實; the 徳→德 conversion still applies to the kept span). All aliases on a line refer
  to the same entity.
- A `・組合：` line is both a normal mapping (when the 組合 name is spoken)
  and the disambiguation context for its indented members. Member tokens are
  often very short/ambiguous (きむ, リリー, ガク, ノブ); apply a member's
  target only when audio / SRT / on-screen text / 組合 context confirms it is
  that act.
- Exact names from the program title/description or on-screen captions are
  authoritative anchors. Do NOT treat such exact spans as ASR errors merely
  because a full-glossary entry has partial phonetic overlap, similar context,
  or a related role; keep the exact source entity unless audio/images
  explicitly identify the glossary entity.
- Do NOT force-apply an entry whose name does not actually appear; entries
  with no occurrence in this episode MUST be ignored entirely.
- Beware false friends: only apply an entry when context confirms it is the
  same entity (e.g. do NOT map a generic "パラパラ" to "パロパロ" unless
  context clearly indicates the act).
- Classify each APPLIED entry into the appropriate output field:
  - Person name → `characters` (`name_jp` = canonical alias, `name_zh` =
    target); list other appearing aliases under `proper_nouns` → same target.
  - Program/segment/group/place/brand/proper noun → `proper_nouns`
    (one key per appearing alias form, all → target).
  - Variety/owarai/technical term → `glossary` (one key per appearing alias).
- Applied mappings OVERRIDE the proper-noun localization hierarchy. Do NOT
  relocalize or re-render them. Terms not in the table follow standard rules.
