### FIXED GLOSSARY (HIGHEST PRIORITY)
The user message includes a 固定詞彙表 — hand-curated source→target mappings
filtered to entries with at least one name appearing in this episode's
inputs. It has two sections: 〔藝人/組合〕 where each act is a `・組合：` line
(its 組合 name; `・（單人）` instead for a solo act) followed by indented `·`
member lines, and 〔節目/單元/品牌/術語〕, a flat `-` list. Within any line
" / " separates alias forms of ONE entity mapping to a single Traditional
Chinese target. These are the highest-priority truth for naming/term
decisions:

- The target Chinese form is the rendering of that line's token only. When
  the token is just a component of a longer source name (a full personal
  name, or group+member), apply the target's script/romanization choice to
  that component but KEEP the rest of the source name; do NOT replace the
  longer name with the shorter target, and do NOT prepend a group label the
  source did not say (source 「徳井義実」 → 德井義實, not the glossary's
  shorter 德井 nor チュートリアル徳井義実; 徳→德, 実→實 — the kept span is
  converted, never a verbatim copy of the Japanese kanji). Symmetrically, when the source uses only a SHORTER form than a
  glossary entry (e.g. surname-only while the entry is a full name), keep
  that shorter span — apply the entry's script/kanji choice to the spoken
  token only and do NOT append the missing given-name/group components
  (source 「徳井」 → 德井, NOT 德井義実, even though the entry is 徳井義実/
  德井義實; the 徳→德 conversion still applies to the kept span).
- A `・組合：` line is BOTH a normal mapping (use it when the 組合 name is
  actually spoken) AND the disambiguation context identifying which act its
  indented members belong to. Member tokens are often very short and
  ambiguous (e.g. きむ, リリー, ガク, ノブ); apply a member's target only
  when the audio / SRT / on-screen text / surrounding 組合 context confirms
  it is that act — do not force it onto a coincidental homograph.
- All aliases on the same line refer to the same entity — normalize every
  listed alias form to the single shared target.
- Classify each entry into the appropriate output field:
  - A person under 〔藝人/組合〕 → `characters` (`name_jp` = the canonical/
    most-common alias, `name_zh` = target). List every other alias under
    `proper_nouns` pointing to the same target so all forms are normalized.
  - A 組合 name that is not a single person, or any 〔節目/單元/品牌/術語〕
    program title, segment name, place, brand, or other proper noun →
    `proper_nouns` (one key per alias, all pointing to the target).
  - Variety/owarai/technical term → `glossary` (one key per alias, all
    pointing to the target).
- These mappings OVERRIDE the proper-noun localization hierarchy. Do NOT
  relocalize them, do NOT swap to a different rendering, even if a more
  "official" Taiwan title seems to exist.
- Terms not in the fixed glossary still follow the standard rules.
