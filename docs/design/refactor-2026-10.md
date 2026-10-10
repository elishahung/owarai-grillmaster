# GrillMaster 重構設計（2026-10）

> **已實作（2026-10）。** 本文件保留為設計紀錄，不再隨程式更新；現行架構以 `.agents/skills/` 的 skills 為準，與本文不一致時以程式碼與 skills 為準。

狀態：草案 v2（已納入 Fable 5.1 審核意見，見 §20）
範圍：整個 repo（套件結構、agent orchestration、pipeline、專案目錄、設定、測試、工具鏈、文件）
相容性：**不保留任何舊格式相容**。舊歸檔專案靠一次性遷移腳本處理，主程式零相容碼。

---

## 0. 已定案的決策

| # | 主題 | 決策 |
|---|------|------|
| D1 | 專案目錄 | 成品與工作區分離：根目錄只放 `project.json` 與成品，每個 stage 一個 `work/NN_<stage>/` |
| D2 | 設定 | `grill.toml`（含節目規則）+ `.env` 只放金鑰 |
| D3 | 靜態檢查 | ruff 嚴格規則集 + basedpyright `standard` + import-linter 分層契約 |
| D4 | 舊歸檔 | 一次性 `scripts/migrate_archive.py`，主程式不讀舊結構 |
| D5 | 功能修剪 | 刪 Rich 進度條 reporter、刪 Bilibili wbi playurl monkey-patch（先驗證上游已修）；remix 的 judge placeholder **保留並泛化**成可選的 insert 機制 |
| D6 | 結構化輸出 | 用各 CLI 原生 schema 旗標；驗證失敗時 **resume 同一個 session** 只送錯誤訊息修復 |
| D7 | CLI | 單一 Typer app，保留 `grill <src>` 捷徑，加上明確子指令 |
| D8 | chunk 輸出 | 改成原生 schema JSON `{blocks: [{index, text}]}`，timecode 由 Python 補回；刪掉 structural-fix agent、`validate_chunk.py` 與 fixed 快取 |
| D9 | 現成框架 | 不採用 ACP 等現成框架，自建薄 orchestration 層，事件詞彙借用 ACP（見 §6.1） |

---

## 1. 現況問題盤點

依嚴重度排序，全部有程式碼證據（survey 結果）。

### 1.1 Agent 呼叫層

- `run_inference` 的樣板在 9 個檔案重複：讀 `settings.X` → `Backend(spec.backend)` → 傳 `model`/`reasoning_effort`。角色（哪個 stage 用哪個 spec）散落在呼叫端。
- 三個 backend 各自為政：agy 走 pty + 標記切答案 + 讀 agy 內部 transcript 驗音檔；codex 走 `--output-last-message`；claude 走 SDK。沒有統一的事件流，所以 TUI 只看得到「stage 開始/結束」，看不到 agent 在做什麼。
- Schema 用「把 JSON Schema 貼進 prompt」實作，修復回合重送整個 prompt，而且刻意丟掉音檔，agent 會失去聽過音檔的脈絡。
- Agent 工具是 6 支獨立 CLI 腳本（`get_frames*.py` ×5、`validate_chunk.py`），各自 `sys.path` hack、各自重建 `Settings(_env_file=...)`，靠 prompt 裡的 shell 指令字串教 agent 使用；使用與否只能從 `extra_frames/` 有沒有檔案間接推斷。
- 錯誤分類不一致：chunk worker 自己有重試迴圈（`chunk_max_retries`），schema 修復有另一個迴圈，structural fix 又是第三個 agent。

### 1.2 Pipeline 與狀態

- 每個 stage 要同步改三處：`ProgressStage` enum、`Project.is_*` 布林、`workflow/api.py` 的 13 段 `if runner.run(...): return` 樣板，再加上 TUI 的 `STAGE_WEIGHTS`、`artifacts.py` 的 key 對照、`side_tasks.py` 的 thread 名稱。全部靠字串 key 對齊，錯了不會報錯（未知 key 權重默默變 1）。
- TUI 把 log 歸屬到 side task 的方式是比對 thread 名稱前綴（`date-research*`/`cover*`）。
- `Project` 是 987 行的 god object：狀態、ID 解析、平台判斷、30+ 個路徑屬性、歸檔搬移、節目規則查詢、prompt 片段格式化、舊 hint 遷移。
- 跨 stage 改寫：glossary check 直接改寫 pre-pass 的 `pre_pass.json`（先備份成 `.raw.json`），下游讀到的是哪一版要看執行時序。
- `project.json` 寫入不是 atomic。

### 1.3 模組邊界

- 領域模組直接吃 `Project`（refine、glossary_check、cover、date_research、package）或直接 import 全域 `settings`（20 個模組），單元測試得 mock 整個 Project 或 patch settings。
- `services/media.py` 1351 行、一個只有 static method 的 class，混了 probe、抽幀、音訊、合併/裁切、打包渲染（~650 行）、remix；同時用 `ffmpeg-python`（2019 年後無維護）與裸 `subprocess` 兩種風格。
- timecode 解析有 3 份、格式化有 5 份；`TimeRange` 定義在 media 裡。
- 「讀 JSON 快取，壞檔當 miss」實作 4 次；`pre_pass.json` 有 5 個消費者、3 種讀法（typed、raw dict、整段貼進 prompt）。
- `noise.py` 與 `placeholder.py` 是同一個「編號素材池 + state.json 游標」模式寫兩次。
- `ytdlp/info.py` 438 行混了 yt-dlp DTO、TVer/Abema HTTP client 與 talent 解析；平台判斷卻在 `Project.source`。
- 隱性耦合：`finalize` 讀 fixed glossary 與 raw dict 形式的 pre-pass；`live_chat/render` 從 `finalize.finalize` 拿 ASS 常數；`package` import `project` 的檔名常數，自己拼路徑，與 `Project` 的路徑屬性形成第二套路徑系統。
- prompt 的音訊變體靠對 `.md` 做字串 find/replace，改 prompt 措辭就可能悄悄失效，只能靠測試守。
- 3 個 lazy `__getattr__` 套件沒有說明理由。

### 1.4 測試與工具

- 約 580 個測試全是 `unittest.TestCase`，約 300 處 `patch.object`；`_make_temp_dir` 在 9 個測試檔複製貼上並寫到 repo 內 `tmp_test_artifacts/`（未 gitignore）；`FakeProgressReporter` 兩份互相分歧；`_build_project_mock` 4 份。
- 測試檔名過時（`test_gemini_normalizer.py`、`test_chunk_fix.py`）；`test_progress_integration.py` 1042 行跨 6 個模組。
- 沒有任何 ruff / type checker 設定，但程式碼裡有 `noqa: S603/PLC0415/BLE001`，代表曾用過擴充規則卻沒寫下來。`from __future__ import annotations` 約四成檔案有。

### 1.5 死碼與殘留

`MediaProcessor.evenly_spaced_timestamps`/`absolute_interval_timestamps`（只剩測試在用）、`.pre_pass/manifest.json`、`assets.json`、`manifests/chunk_*.json`（只寫不讀）、`.packagerc` 警告、`legacy_app`、`Project._split_legacy_hint`、`project.py` 裡 4 處 "Gemini" 字樣、`MediaProcessor` 過時的 docstring。

---

## 2. 目標與非目標

**目標**

1. 每個模組有單一擁有者與明確輸入輸出，分層由工具強制（import-linter），不靠紀律。
2. 一個 agent orchestration 層統一三個 CLI：同一份任務描述、同一套事件流、同一組 MCP 工具、同一種修復與錯誤處理。
3. TUI 看得到每個 agent session 大概在做什麼（目前步驟、最近一次工具呼叫、思考摘要），可展開完整活動紀錄。
4. 新增 stage、平台、agent 工具、設定時，只需改一個地方。
5. 專案目錄一眼看出成品在哪；重跑某個 stage 有官方指令，不用手動刪 dot-dir 再改 JSON。
6. 測試用 pytest 原生風格，靠 fake 而不是 patch 模組路徑；lint/type 零警告。

**非目標**

- 不改翻譯品質相關的 prompt 內容。例外只有三類，都在 §18 階段 4 後做品質比對：D8 的 chunk 輸出段落、Briefing 改為 strict 相容形狀（§6.8）連帶的欄位描述、目錄結構變更（§10）連帶的檔案路徑描述（例如 `glossary_check.md` 提到的路徑）。
- 不碰 NVENC 打包配方、ffmpeg filter 參數、SRT builder 的調校常數。
- 不加快取自動失效（維持「固定檔名存在即命中、手動清除」）。
- 不做 server / queue / 資料庫。

---

## 3. 保留與新增的不變式

**保留**（寫進新 skill）：

- **可續跑**：每個 stage 完成後記錄於 ledger；重跑同 ID 從斷點繼續。
- **chunk 邊界決定性**：pre-pass 與 chunk stage 以同一個純函式切 chunk，briefing 的 segment summary 以 index 範圍對應。
- **快取不自我失效**：stage 內部快取以固定檔名命中；要重跑就用 `grill reset`（§9.5）。
- **agent 寫檔型 stage 重入即丟棄舊輸出**（refine、glossary）：ledger 是唯一完成標記。
- **Side task 在 finally join**：cover / date research 即使 pipeline 失敗也要 join（成本已付）。
- **只有 ElevenLabs 計費**；agent 全是訂閱制。
- **Windows MAX_PATH 260**：ffmpeg / yt-dlp 不支援長路徑，所有目錄命名都要經過路徑長度預算。
- **打包一個 ffmpeg process 一個 filtergraph**、map 用 stream index 等 packaging 不變式原封不動搬移。
- **agent 寫出的 SRT 可能帶 UTF-8 BOM**（codex 會寫），所有讀 agent 產出 SRT 的地方用 `utf-8-sig`；集中在 `core/srt.read_srt_file()`，`subtitles.structure`、`finalize`、`check_srt` 都經過它。
- **agy 子行程移除付費 API key 環境變數**（`GEMINI_API_KEY` 等），確保走訂閱登入。
- **date research 不給專案目錄**：agent 在拋棄式暫存目錄工作。

**新增**：

- **Stage 只寫自己的 work 目錄與自己宣告的成品**（`StageDef.outputs`，§9.1）。沒有任何 stage 改寫別的 stage 的輸出（§10.3 解決 glossary 改 briefing 的問題）。唯一明列的例外：download stage 會在 `grill.toml` 追加新節目的空區段（§11.2）。
- **領域模組不 import `project` 與 `config`**；它們吃明確的輸入物件。只有 `stages/` 把專案佈局綁到領域輸入。
- **所有 agent 呼叫經過 `AgentRunner`**；沒有任何模組直接 spawn agent CLI。
- **所有外部程式（ffmpeg、agent CLI）經過兩個 process 模組**之一，統一 tree-kill 與逾時。

---

## 4. 架構總覽

```
                 ┌──────────────┐
                 │   cli/       │  Typer，組裝 RunContext（唯一讀 config 的入口之一）
                 └──────┬───────┘
                        ▼
                 ┌──────────────┐        ┌──────────────┐
                 │  pipeline/   │──emit─▶│  events/     │──▶ tui/ · console · run.jsonl
                 │ registry,    │        └──────────────┘
                 │ runner, side │               ▲
                 └──────┬───────┘               │ emit (agent activity)
                        ▼                       │
                 ┌──────────────┐        ┌──────┴───────┐      ┌──────────────┐
                 │  stages/     │──────▶ │  agents/     │─────▶│ agent CLIs   │
                 │ (glue: layout│        │ runner,      │      │ agy/codex/   │
                 │  → inputs)   │        │ adapters     │      │ claude       │
                 └──────┬───────┘        └──────────────┘      └──────┬───────┘
                        ▼                                             │ MCP stdio
   ┌─────────────────────────────────────────────────┐        ┌──────▼───────┐
   │ 領域：translate · postprocess · extras ·          │        │ agent_tools/ │
   │ subtitles · live_chat · package · sources · asr  │        │ get_frames,  │
   └──────────────────────┬──────────────────────────┘        │ check_srt    │
                          ▼                                    └──────┬───────┘
   ┌─────────────────────────────────────────────────┐               │
   │ 基礎：core (srt, timecode, json_artifact) · media │◀──────────────┘
   │ · project (state, layout) · glossary · config    │
   └─────────────────────────────────────────────────┘
```

---

## 5. 套件結構

改用 **src layout**，套件名 `grillmaster`，`[project.scripts] grill = "grillmaster.cli:main"`。`uv sync` 以 editable 安裝後，agent 工具用 `python -m grillmaster.agent_tools` 啟動，所有 `sys.path` hack 消失。

```
src/grillmaster/
├── __main__.py
├── cli/                 # Typer 指令：run(預設) / serial / package / reset / status / doctor
├── config/
│   ├── model.py         # AppConfig（pydantic），對應 grill.toml 每個區段
│   ├── secrets.py       # pydantic-settings，只讀 .env 金鑰（SecretStr）
│   ├── load.py          # 尋找 grill.toml（cwd 往上找 → GRILL_HOME）
│   ├── programs.py      # ProgramRules：series/channel 規則合併與 prompt 片段
│   └── schema.py        # 產生 grill.schema.json
├── core/
│   ├── srt.py           # SrtBlock、parse、serialize、read_srt_file（utf-8-sig）（唯一一份）
│   ├── timecode.py      # TimeRange；SRT/ASS 解析與格式化（唯一一份）
│   ├── fs.py            # atomic_write_text
│   ├── json_artifact.py # load_model（壞檔=miss）、read_model（嚴格）、write_model
│   ├── model_spec.py    # Backend、Effort、ModelSpec（config 與 agents 共用）
│   ├── prompts.py       # importlib.resources 讀 .md、片段組裝
│   ├── briefing.py      # Briefing schema（原 PrePassResult）+ render_for_prompt
│   ├── source_id.py     # Platform enum、ID/URL 解析、source_url（原 Project.parse_source_str/.source）
│   ├── paths.py         # MAX_PATH 預算與 fit_dir_name（原 services/paths.py）
│   ├── stage_key.py     # StageKey StrEnum（順序即執行順序；layout 與 registry 共用）
│   ├── tool_session.py  # MCP session manifest model（agents 寫、agent_tools 讀）
│   ├── models.py        # StrictModel / FrozenModel（pydantic 共用基底）
│   └── talent.py        # Talent（sources/state/translate/extras 共用唯一一份）
├── project/
│   ├── state.py         # ProjectState、StageRecord ledger
│   ├── layout.py        # ProjectLayout：所有路徑（唯一一份）
│   ├── store.py         # load/save（atomic）、archive 搬移
│   └── naming.py        # deliverable 名稱、archive/package 目的地（用 core.paths 做預算）
├── events/
│   ├── types.py         # 所有事件 dataclass
│   ├── bus.py           # EventBus：同步 fan-out 給 sink
│   ├── context.py       # contextvars：目前 stage / task 範圍
│   └── sinks.py         # ConsoleSink（loguru）、JsonlSink（logs/events-*.jsonl）
├── agents/              # §6
│   ├── task.py  runner.py  events.py  errors.py  prompt.py  schema.py  process.py
│   └── adapters/  base.py  agy.py  codex.py  claude.py
├── agent_tools/         # §7：MCP stdio server
│   ├── __main__.py  server.py  frames.py  srt_check.py
├── pipeline/
│   ├── state_store.py   # StateStore：StageContext.state/update 背後的鎖與 atomic 存檔
│   ├── registry.py      # 唯一的 stage 清單（順序、編號、權重、啟用條件）
│   ├── runner.py        # 跑 stage、記 ledger、break-after
│   ├── side_tasks.py    # cover / date research 的啟動與 join
│   ├── steps.py         # 單一 step 執行器（Started/Completed/Failed、token 用量）
│   ├── delivery.py      # package 等 delivery step，與最後的 archive 搬移
│   └── serial.py
├── stages/              # 每個 stage 一個模組：從 layout 組輸入 → 呼叫領域 → 寫輸出
│   ├── base.py          # StageDef、SideTaskDef、DeliveryStepDef、StageContext、RunOptions、Externals（pipeline 只往下 import）
│   ├── _common.py       # 共用 glue：program_rules、source_request、frames_tool、role_params/tool_params 等
│   ├── metadata.py download.py combine.py chat_fetch.py audio.py asr.py
│   ├── transcript.py prepass.py chunks.py refine.py glossary.py finalize.py
│   └── chat_translate.py cover.py date_research.py
├── sources/             # 原 services/ytdlp
│   ├── registry.py      # Platform → SourcePlatform 實作
│   ├── base.py          # SourcePlatform protocol
│   ├── youtube.py bilibili.py tver.py abema.py   # 平台差異（cookie 策略、talent、播出日）
│   ├── ytdlp.py         # yt-dlp 共用：info、download、progress hook
│   ├── broadcast_date.py official_subs.py live_chat.py
├── asr/                 # 原 services/elevenlabs（client + srt_builder）
├── translate/
│   ├── chunker.py       # 純函式
│   ├── assets.py        # pre-pass/chunk 共用的抽幀 + 切音訊（合併兩份重複碼）
│   ├── prepass.py  chunk.py
│   └── prompts/         # .md 片段（音訊相關段落拆成獨立片段，§12.1）
├── postprocess/         # refine.py glossary_check.py + prompts/
├── extras/              # cover.py date_research.py titles.py + prompts/
├── subtitles/
│   ├── structure.py     # 骨架驗證（refine/glossary/check_srt 共用）
│   ├── finalize.py      # Netflix-TC 標點、混合文字空格
│   └── ass.py           # ASS 畫布/字型/邊界常數與 style header（live_chat 也用）
├── glossary/            # fixed_glossary 資料與載入
├── live_chat/           # parse / translate / render
├── media/               # ffmpeg 基礎工具（不含打包配方）
│   ├── ffmpeg.py        # argv 建構 + 執行 + progress 解析（取代 ffmpeg-python）
│   ├── probe.py frames.py audio.py video.py
├── package/
│   ├── assemble.py      # 原 core.py：組成品資料夾
│   ├── render.py        # 原 media.py 的打包配方、BurnPlan、parts、mux
│   ├── remix.py
│   ├── pools.py         # 素材池 + 游標（noise 與 inserts 共用，§12.6）
│   └── inserts.py       # 泛化後的 judge placeholder
└── tui/                 # app.py（拆 widget）、state.py（事件 reducer）、artifacts.py
```

### 5.1 分層契約（import-linter）

`layers` 契約，由上到下（同一行以 `|` 分隔者互相獨立、彼此禁止 import）：

```
grillmaster.cli
grillmaster.tui | grillmaster.pipeline
grillmaster.stages
grillmaster.translate | grillmaster.postprocess | grillmaster.extras | grillmaster.package
grillmaster.live_chat | grillmaster.agent_tools
grillmaster.sources | grillmaster.asr | grillmaster.subtitles
grillmaster.agents
grillmaster.project | grillmaster.config
grillmaster.media | grillmaster.glossary
grillmaster.core | grillmaster.events
```

依賴關係對照（確認這個順序成立）：`package → live_chat`（chat burn plan）、`package → subtitles.ass`、`live_chat → subtitles.ass`、`postprocess → subtitles.structure`、`agent_tools → subtitles/media/glossary`、`agents → core.tool_session`（manifest model 在 core，agents 以模組名字串啟動 server，不 import `agent_tools`）。`tui` 只靠事件與 `project.layout`（預覽成品），不 import `pipeline`。

另加 `forbidden` 契約：`translate`、`postprocess`、`extras`、`package`、`live_chat`、`sources`、`asr`、`subtitles`、`agents`、`agent_tools`、`media` 不得 import `grillmaster.project` 與 `grillmaster.config`（純分層會允許它們往下 import 這兩者，所以要另外禁止）。deliverable 目的地由 `pipeline.delivery` 以 `project.naming` 算好再傳給 `package`。

`events` 在最底層，任何層都能發事件。

---

## 6. Agent orchestration 層（`agents/`）

### 6.1 為何不用現成框架

評估過 **Agent Client Protocol（ACP，Zed 主導的 JSON-RPC over stdio 協定）**，它的概念最接近需求：`session/new` 可以帶 MCP server、`session/update` 串流 thought/tool_call/message、`session/load` 可續接。不採用的原因：

- **agy 沒有原生 ACP**。只有社群 bridge，多半包 `--output-format stream-json`，版本綁定、還有 ToS 疑慮；codex 與 claude 也要額外跑 Node adapter（每次冷啟動數秒）。
- **ACP 沒有結構化輸出**，D6/D8 依賴的原生 schema 用不到。
- 三個 CLI 本身已提供我們需要的一切：串流事件（agy `stream-json`、codex `--json`、Claude SDK message stream）、原生 schema、MCP、session resume。再包一層協定只是多一個會壞的元件。

結論：自建一個薄層，**事件詞彙沿用 ACP**（`thought` / `tool_call` / `tool_result` / `message`；token 用量不是事件，隨每回合的 `FinalOutput.usage` 回報）。

### 6.2 核心概念

```python
class Role(StrEnum):  # 在 core/model_spec.py；由 grill.toml [agents.roles] 對應到 ModelSpec
    PREPASS = "prepass"
    CHUNK = "chunk"
    POSTPROCESS = "postprocess"
    UTILITY = "utility"
    CHAT = "chat"
    IMAGE = "image"


@dataclass(frozen=True)
class AgentTask[T]:
    name: str  # "chunks/0001-0119"、"refine"：事件與 session 目錄用
    role: Role
    instructions: str  # stage prompt + 節目規則
    prompt: str  # 使用者內容
    session_dir: Path  # session 紀錄位置（§6.6），由呼叫的 stage 決定
    workdir: (
        Path | None
    )  # agent 的 cwd 與可寫根目錄；None = 拋棄式暫存目錄（date research、titles）；同時執行的 task 不可共用（runner 拒絕）
    images: tuple[Path, ...] = ()
    audio: tuple[Path, ...] = ()  # 需要 capability AUDIO_INPUT
    tools: ToolSession | None = None  # grill MCP 工具與各自的時間窗/參考 SRT（§7）；runner 寫成 session/tools.json
    add_dirs: tuple[Path, ...] = ()  # 額外可讀根目錄（例如專案根目錄）
    output: OutputSpec[T]  # TextOutput | SchemaOutput(model) | FilesOutput(required, optional)；runner 每次全新 attempt 前刪掉所有宣告的檔案
    validate: Callable[[T], None] | None = None  # 丟 ValidationFailure(msg)
    requires: frozenset[Capability] = frozenset()  # 輸入推不出的需求：IMAGE_GENERATION、WEB_SEARCH
    max_repairs: int = 3  # resume 修復回合
    attempts: int = 1  # 全新 session 的重試次數（暫時性錯誤）


class AgentRunner:
    def run(self, task: AgentTask[T]) -> AgentResult[T]: ...
    def run_jobs(self, jobs: Sequence[AgentJob[T]]) -> list[JobFailure]: ...


# prepare 在 pool thread 取 slot 前建 task（含媒體準備）；accept 在成功後立刻於同一 worker
# 落地結果（寫快取）。每個 job 的例外都被收集成 JobFailure；KeyboardInterrupt 等 in-flight
# accept 跑完後才往外丟。呼叫端（stage）在整批結束後再 fail loudly。
AgentJob(name, prepare: Callable[[], AgentTask[T]], accept: Callable[[AgentResult[T]], None])
```

`AgentRunner` 由 pipeline 建立一次並放進 `StageContext`，持有：role→spec 對照（由 pipeline 從 config 建好傳入，`agents` 不讀 config）、全域併發上限、EventBus。

`instructions` 與 `prompt` 分開只是為了 session 紀錄與測試好讀；**三個 adapter 都把兩者串成同一則使用者訊息**送出（只有 Claude 有 system prompt 通道，為了三家行為一致不使用它，Claude Code 的預設系統提示保留以維持工具能力）。

**Capability 由 adapter 宣告**，runner 在送出前檢查，不符就直接丟錯（不靜默降級）：

| Capability | agy | codex | claude |
|---|---|---|---|
| `AUDIO_INPUT` | ✅（view_file） | ❌ | ❌ |
| `IMAGE_INPUT` | ✅ | ✅ | ✅ |
| `IMAGE_GENERATION` | （不使用） | ✅ | ❌ |
| `NATIVE_SCHEMA` | ✅ `--json-schema` | ✅ `--output-schema` | ✅ `output_format` |
| `RESUME` | ✅ `--conversation` | ✅ `exec resume` | ✅ `resume=` |
| `MCP` | ✅ `<workdir>/.agents/mcp_config.json` | ✅ `-c mcp_servers.*` | ✅ `mcp_servers=` |
| `MCP_IMAGE_RESULT` | ✅ | ✅ | ✅ |
| `WEB_SEARCH` | ✅ | ✅（task 要求時才加 `tools.web_search=true`） | ✅ |

effort 在各家的表達不同，正規化仍在 adapter 內：agy 的 model id 把 effort 內嵌在名稱裡（`agy models` 列出 `gemini-3.1-pro-high`、`gemini-3.1-pro-low`），adapter 把 `ModelSpec(model="gemini-3.1-pro", effort="high")` 組成 `gemini-3.1-pro-high`，並對照 `agy models`（每行 `<id>	<顯示名稱>`；沒有 JSON 輸出）的實際清單驗證（每次執行快取一次），不存在就直接報錯並列出可用組合。這取代手寫的 `_AGY_MODEL_BASES` / `_AGY_VALID_EFFORTS` 表。codex、claude 維持現有的 effort 對照（`extra`→`xhigh` 等）。

runner 要求的 capability = task 的 `requires` 加上由輸入推導的部分（images → `IMAGE_INPUT`、audio → `AUDIO_INPUT`、schema → `NATIVE_SCHEMA`、frames 工具 → `MCP_IMAGE_RESULT`）。cover 的 `Role.IMAGE` 由 config 指定 spec，但 cover task 宣告 `requires={IMAGE_GENERATION}`，所以實質上只能是 codex，取代目前寫死 `Backend.CODEX` 的做法；date research、titles 宣告 `WEB_SEARCH`。

### 6.3 執行流程

```
run(task)
 ├─ resolve spec = roles[task.role]；adapter = adapters[spec.backend]；檢查 capability
 ├─ 建立 session 目錄（§6.6），寫 prompt.md、tools.json（MCP session manifest）
 ├─ attempt loop（task.attempts，只針對 AgentTransientError）
 │   ├─ acquire 全域 slot（§6.7）
 │   ├─ handle = adapter.start(task, spec, mcp)
 │   ├─ for event in handle.events(): 正規化事件 → bus.emit（帶 task 名稱）+ raw.jsonl
 │   ├─ final = handle.result()           # text 或 structured JSON + session_id
 │   ├─ repair loop（task.max_repairs，slot 不釋放）
 │   │    ├─ output = parse(final, task.output)    # pydantic / 讀檔
 │   │    ├─ task.validate(output)
 │   │    ├─ 通過 → 寫 result.json，回傳 AgentResult(output, session_id, usage, repairs)
 │   │    └─ ValidationFailure(msg) → handle = adapter.resume(handle, task, spec, mcp, repair_prompt(msg))
 │   └─ release slot；transient 錯誤時等待後進下一個 attempt（等待期間不占 slot）
 └─ 丟出分類後的錯誤（§6.5）
```

修復回合只送「哪裡錯了」，agent 保有原本的完整脈絡（包括已聽過的音檔、已抽的幀），符合「one-shot + 只修格式」的既有原則，而且成本遠低於重送整個 prompt。

`FilesOutput` 也走同一個迴圈：refine/glossary 的 SRT 骨架錯誤，以前是整個 stage 失敗，現在是 resume 讓 agent 修。

### 6.4 Adapter 介面與三家實作

```python
class AgentAdapter(Protocol):
    backend: Backend
    capabilities: frozenset[Capability]
    media_delivery: MediaDelivery  # ATTACHED（旗標/內容區塊）| VIEW_FILE（prompt 列路徑，runner 寫 view_file 指示）

    # TurnRequest：runner 組好的使用者訊息、spec、workdir、session 目錄、schema、媒體、MCP、逾時、raw sink。
    def start(self, request: TurnRequest) -> SessionHandle: ...
    # 修復回合是「同 session、同工具、同 schema、新的使用者訊息」：同一個 TurnRequest 換掉 message，
    # 每家 resume 都要重新帶 cwd / MCP / schema / model 參數。
    def resume(self, session_id: str, request: TurnRequest) -> SessionHandle: ...


class SessionHandle(Protocol):
    def events(self) -> Iterator[AgentEvent]: ...  # 串流、阻塞到結束
    def result(self) -> FinalOutput: ...  # text、structured、session_id、usage、defects
```

`defects` 是 adapter 自己發現的瑕疵（例如 agy 沒用 `view_file` 開啟的音訊）：修復訊息由 adapter 撰寫並附上要重送的媒體，runner 用同一個修復迴圈處理，不認得任何 backend 專屬的細節。

| | agy | codex | claude |
|---|---|---|---|
| 啟動 | `agy -p … --output-format stream-json --model <id>-<effort> --json-schema F --dangerously-skip-permissions --add-dir … --log-file …`，環境變數移除付費 API key | `codex exec --json -m M -c model_reasoning_effort=E --cd W --ignore-user-config --dangerously-bypass-approvals-and-sandbox -c tools.web_search=true --output-schema F -c mcp_servers.grill.*` | `query(options=ClaudeAgentOptions(cwd, model, effort, permission_mode="bypassPermissions", mcp_servers, strict_mcp_config=True, setting_sources=[], output_format, add_dirs))` |
| prompt 傳遞 | 首選 `--input-format stream-json` 經 stdin（S1）；否則維持 INPUT.md + `@` bootstrap。圖片的 `@<path>` 與音訊的 `view_file` 指示怎麼放進 stream-json 訊息也由 S1 決定 | stdin | SDK message（含 base64 圖片） |
| 事件來源 | `init` / `step_update`（`step_type`）/ `result` | `thread.started` / `item.*`（reasoning、command_execution、mcp_tool_call、web_search、agent_message）/ `turn.completed`（事件名以錄製 fixture 為準） | `AssistantMessage`（Thinking/Text/ToolUse block）、`UserMessage`（ToolResult）、`ResultMessage`（`structured_output`、`session_id`） |
| 音訊 | bootstrap 要求 `view_file`；聽音驗證改從事件流判斷（S6），不行才保留讀 agy 內部 transcript | — | — |
| resume | `--conversation <id>` + 原本全部旗標 | `codex exec resume <thread_id>`：**沒有 `--cd`/`--add-dir`**，cwd 用 `Popen(cwd=)`，`-c mcp_servers.*`、`--output-schema`、`-m` 重帶；不再用 `--ephemeral` | `ClaudeAgentOptions(resume=<id>, …原本全部選項)` |
| 錯誤 | 單輪 `-p`：exit code 3 + `AGY_ERROR` 行。**stream-json 輸入模式下錯誤只警告並繼續**，adapter 必須看到 `result` 或錯誤事件就關閉 stdin，否則會卡到逾時 | 非 0 exit + `error` event | `ResultMessage.is_error`、`api_error_status`、RateLimitEvent（沿用現有 401/429 訊息保留邏輯） |

**與使用者自己的 CLI 設定隔離**：使用者的 `~/.codex/config.toml` 有自己的 MCP server 與預設 effort，Claude 預設會載入使用者設定與 MCP。grill 的 session 必須只看到 grill 給的工具與參數：codex 用 `--ignore-user-config`（auth 仍讀 `CODEX_HOME`），Claude 用 `setting_sources=[]` + `strict_mcp_config=True`。agy 無對應旗標，S2 一併確認使用者層級的 MCP/rules 會不會混入。

共用的 `agents/process.py`：以 `Popen` 逐行讀 stdout（JSONL）、stderr 另開 thread 收尾段、逾時 tree-kill（沿用現在 `run_cli` 的 taskkill/killpg 邏輯）。若 S1 證實 agy 在非 TTY 下輸出正常，**移除 pty 與 `pywinpty` 依賴**、移除 ANSI 清理與 `<<<AGY_BEGIN>>>` 標記切割。

agy 的 effort 改用 `--effort`（S1 驗證 `--model` 能否吃 id 形式）；可行就刪除 `_AGY_MODEL_BASES` 顯示名稱對照表。

### 6.5 錯誤分類與重試

```
AgentError
├─ AgentConfigError        設定/capability 不符、未安裝 → 不重試，整個 stage 失敗
├─ AgentQuotaError         429/配額 → 不重試，fail fast（chunk 也一樣）
├─ AgentAuthError          401/需重新登入 → 不重試，訊息含登入指令
├─ AgentTransientError     逾時、行程崩潰、空輸出 → 依 task.attempts 開新 session 重試
└─ AgentOutputError        修復回合用完仍驗證失敗 → 不再開新 session
```

只有一個重試迴圈（runner 內）。`chunk_max_retries` 改成 chunk task 的 `attempts`，`_RETRY_DELAY_SECONDS` 移入 runner。

### 6.6 Session 紀錄

每個 task 一個目錄，位置由呼叫的 stage 決定（在該 stage 的 work 目錄下）：

```
work/09_chunks/0001-0119/session/
  prompt.md          # 實際送出的完整訊息（除錯用）
  tools.json         # MCP session manifest（server 啟動時讀）
  raw.jsonl          # adapter 原始事件（也是錄製契約測試 fixture 的來源）
  result.json        # session_id、backend/model/effort、usage、repairs、outcome、耗時
```

正規化後的事件（含工具呼叫）只寫到專案層級的 `logs/events-<ts>.jsonl`（帶 task 名稱），不在每個 session 重複一份。重試的 attempt 以 `session/` → `session.2/` 區分。目錄名稱刻意短，配合 MAX_PATH 預算（§10.4）。

不用 `--ephemeral` 代表 codex 每個 session 都會在 `~/.codex/sessions` 留檔；`grill doctor` 回報其大小，清理交給使用者。

### 6.7 併發

整個 pipeline 只用 **thread**，移除領域碼中的 asyncio（chunk facade 的 `asyncio.run` + semaphore、structural fix 的 `to_thread`）。`AgentRunner` 持有全域 semaphore（`agents.max_concurrent`，原 `agent_concurrency`），所以 chunk fan-out、live-chat batch、背景 cover/date research 共用同一個上限，不會互相疊加爆量。`run_jobs` 用 ThreadPoolExecutor（以限時 `wait` 輪詢，讓 Windows 上的 Ctrl-C 可中斷），並以 `contextvars.copy_context()` 把 stage/task 範圍帶進 worker（§8.2）。Claude adapter 在自己的 worker thread 內 `asyncio.run`，對外仍是同步介面。

規則：

- 一個 session（含它的所有修復回合）從開始到結束持有一個 slot；attempt 之間的等待與新 attempt 前會釋放 slot。
- 不允許巢狀：持有 slot 的程式碼不得再呼叫 `run`/`run_jobs`（runner 以 contextvar 偵測並直接報錯），所以不會死鎖。
- `agents.timeout_minutes` 是**每一回合**的上限，不是整個 session 的總和（一次 40 分鐘的 pre-pass 加三次修復要能完成）。
- 背景 side task 可能在 chunk 階段排隊很久：`StepStarted(kind=side_task)` 在派發時發出，`AgentSessionStarted` 在拿到 slot 時發出，TUI 顯示「排隊中」。side task 的 join 逾時從拿到 slot 起算，不從派發起算。

### 6.8 原生 schema 的限制

codex `--output-schema` 走 strict 模式（所有欄位必填、每個 object 都要 `additionalProperties: false`，**沒有自由 key 的 map 型別**），agy 要求根節點是 object。`agents/schema.py` 提供 `strict_json_schema(model)` 轉換，並要求所有 agent 輸出 model 設計成 strict 相容（可選欄位寫成 `T | None` 且必填）。Python 端仍以 pydantic + `validate` 做權威驗證，原生 schema 只是讓第一回合更容易對。S4 spike 要確認三家都接受 `Briefing`、`ChunkTranslation`、`DateResearchResult`、`TitleSuggestions`、chat batch schema。

**這會改到 Briefing 的形狀**：現在 `PrePassResult.proper_nouns` 與 `glossary` 是 `dict[str, str]`，strict 模式無法表達，必須改成 `list[TermMapping(source: str, target: str)]`（`role_note` 等既有欄位不變）。連帶要改的地方：`pre_pass.md` 的輸出說明、`chunk.md` 對 briefing 欄位的描述、finalize 的拉丁名稱抽取、glossary check 對 briefing 的修正說明、live chat prompt、`Briefing.render_for_prompt()`。這與 D8 一起列為本次允許的 prompt 語意變更（§2 非目標已註明例外），完成後一併做品質比對。

---

## 7. Agent 工具：MCP server（`agent_tools/`）

### 7.1 形式

一個用官方 `mcp` Python SDK（FastMCP）寫的 **stdio server**，入口 `python -m grillmaster.agent_tools`。每次 agent 呼叫由 runner 寫一份 session manifest（`tools.json`），內容：

```json
{
  "project_root": "...",
  "frames": { "video": ".../video.mp4", "frames_dir": ".../work/10_refine/frames",
              "window": [0.0, null], "max_side": 768 },
  "check_srt": { "reference_srt": ".../work/09_chunks/merged.srt" }
}
```

server 由 `--session` 參數（或環境變數 `GRILL_TOOL_SESSION`）取得 manifest；每個工具有自己的子設定，子設定為 `null` 的工具不暴露。

注入方式：

- codex：`-c mcp_servers.grill.command="<sys.executable>"`、`-c mcp_servers.grill.args=["-m","grillmaster.agent_tools","--session","…"]`
- claude：`mcp_servers={"grill": {"type": "stdio", "command": …, "args": […]}}`
- agy：沒有逐次呼叫的 MCP 旗標。S2 依序驗證：(a) 在 agy 工作目錄放 `.agents/` plugin（內含 `mcp_config.json`），只影響 grill 的 session，首選；(b) `grill doctor --setup` 一次性 `agy mcp add grill <python> -m grillmaster.agent_tools`，session 由 agy 行程繼承的 `GRILL_TOOL_SESSION` 環境變數決定。(b) 的代價是使用者平常互動的 agy 也會看到 `grill` server（沒有 session 時它不暴露任何工具），所以只在 (a) 不成立時採用。

### 7.2 工具目錄

| 工具 | 用途 | 使用 stage |
|---|---|---|
| `get_frames(times: list[float])` | 抽指定時間點的幀（≤20、限時間窗），存到 stage 的 `frames/` 供稽核，並**直接以 MCP image content 回傳**，agent 不必再自己開檔 | prepass、chunks、refine、glossary |
| `check_srt(path: str)` | 對 manifest 的 reference 骨架驗證候選 SRT（index/timecode/區塊數/非空），回 `VALID` 或錯誤清單 | refine、glossary |
| `lookup_glossary(query: str)`（第二階段） | 查 fixed glossary，取代把 3590 行 JSON 複製進工作目錄 | glossary（之後可能 prepass） |

工具回傳 image content 是否被三家都轉給模型，列為 S3；不行的 backend 退回「回傳檔案路徑、agent 用自己的 view 工具開」，由 adapter capability `MCP_IMAGE_RESULT` 決定 prompt 裡怎麼描述。

### 7.3 prompt 側

各 stage 不再拼 shell 指令字串。`agents` 依 `task.tools` 自動附上一段「可用工具」說明（工具名、時間窗、上限），stage 專屬的使用時機說明（例如 pre-pass 要主動查字卡）留在各 stage 的 prompt `.md`。工具使用證據來自事件流（`logs/events-*.jsonl` 的 `tool_call`），不再靠看 `extra_frames/` 有沒有檔案。

---

## 8. 事件、log 與 TUI

### 8.1 事件匯流排取代 `NoopProgressReporter`

`NoopProgressReporter` 的 20 個方法換成一個 `EventSink.emit(event)`。事件是 frozen dataclass：

```
RunStarted(project, plan)          RunFinished(outcome, error?)
BatchItemStarted(index, total, source)
StepStarted(key, kind)  StepCompleted(key, kind, elapsed, result?)  StepSkipped(key, kind, reason)  StepFailed(key, kind, error)
                                   # kind: stage | side_task | delivery（PlanKind）
ProgressStarted(scope, label, total)  ProgressAdvanced(scope, n, note?)  ProgressFinished(scope)
AgentSessionStarted(task, stage, backend, model, effort)
AgentActivity(task, kind, summary)       # kind: thought | tool_call | tool_result | message | repair
AgentSessionFinished(task, outcome, elapsed, usage, repairs)
LogLine(stage?, task?, level, text)
```

Sink：`TuiSink`（更新 `PipelineState`）、`ConsoleSink`（非 TTY 的純 log）、`JsonlSink`（`projects/<id>/logs/events-<ts>.jsonl`，事後回放）。Rich reporter 刪除（D5），`grill package` 與 `grill run` 相同：TTY 上用 TUI，否則 ConsoleSink。進度條一律經 `events.progress.track`：只有正常結束才發 `ProgressFinished`，失敗時進度條停在原處，由該步驟的 `StepFailed` 收尾。

chunk 專用事件（`chunk_started/finished/failed`）不再需要：chunk board 由 `AgentSessionStarted/Finished` + task 名稱前綴 `chunks/` 推導。

### 8.2 用 contextvars 歸屬，取代 thread 名稱

`events/context.py` 提供 `stage_scope(key)`、`task_scope(name)` context manager。runner 進入 stage 時設定；`run_jobs` 與 side task executor 以 `copy_context()` 傳遞。loguru 用 `logger.patch` 把目前範圍寫進 record，TUI sink 依此分欄。`owner_key_for_thread_name` 刪除。

### 8.3 TUI 呈現

- Plan、權重、label 全部來自 `pipeline/registry.py` 的 `StageDef`，TUI 不再有 `STAGE_WEIGHTS` 之類的硬編碼 key。
- 每個 agent stage 的詳細面板新增 **Sessions 表**：task、backend/model、經過時間、工具呼叫次數、修復次數、**最後一則活動摘要**（例：`🔧 get_frames 62.5, 70, 77`、`💭 確認「みなみかわ」的寫法…`、`✏️ 寫入 refined.srt`）。
- chunk board 的格子選取後顯示該 chunk 的 session 活動；`a` 鍵切換完整活動紀錄（來自 `AgentActivity` 串流，不含原始 token）。
- 摘要規則集中在 `agents/events.py`：thought 取第一行截 80 字、tool_call 顯示工具名 + 參數縮寫、message 只在最終輸出時顯示長度。
- `tui/app.py` 570 行的 App 拆成 widget：Header、StageList、StageDetail、SessionTable、ChunkBoard、ActivityLog、Summary。

---

## 9. Pipeline

### 9.1 宣告式 stage registry

現行 skill 寫著「stage 數或分支變多時再引入 registry」。這次的重構符合條件：work 目錄編號、TUI 權重、ledger、啟用條件、參數快照都要從同一個地方來。

```python
@dataclass(frozen=True)
class StageDef:
    key: StageKey  # StrEnum：metadata, download, combine, chat_fetch, audio, asr,
    # transcript, prepass, chunks, refine, glossary, finalize, chat_translate
    label: str
    weight: int
    run: Callable[[StageContext], str | None]  # 回傳值成為 StepCompleted.result（例：ASR 費用）
    outputs: Callable[
        [ProjectLayout], Sequence[Path]
    ]  # 宣告的成品（subs/…）；reset 與「只寫自己的東西」測試用
    enabled: Callable[[RunOptions], bool] = always
    on_skip: Callable[[StageContext], None] | None = (
        None  # 例：section 參數在續跑時被忽略的警告
    )
    params: Callable[[AppConfig], dict[str, str]] = no_params  # TUI 顯示 + ledger 快照
    clear_state: Callable[[ProjectState], None] = no_clear  # reset 時清掉本 stage 寫的 state 欄位
    preflight: Callable[[AppConfig, Secrets], None] = no_preflight  # 開跑前檢查（例：ASR key）


STAGES: tuple[
    StageDef, ...
] = ...  # 必須與 core.stage_key.StageKey 的順序一致（registry 載入時斷言）
SIDE_TASKS: tuple[SideTaskDef[Any], ...] = (cover, date_research)
DELIVERY: tuple[DeliveryStepDef, ...] = (package,)  # 非 stage，但 label/weight 也在這裡
```

`StageKey` 放在 `core/stage_key.py`，順序即執行順序；`ProjectLayout`（work 目錄編號）與遷移腳本在 registry 存在之前就能用它。

`StageContext` 提供：`layout`、`state`（含 `update()` atomic 存檔）、`config`、`options`（本次執行參數）、`agents`（AgentRunner）、`events`、`workdir`（本 stage 的 `work/NN_key/`，延遲建立）、`session_dir(label)`，以及外部程序 seam `ffmpeg`、`ytdlp`、`http`、`speech_to_text`（`run_project` 建一次 `Externals` 傳入；測試換 fake）。stage 是純模組函式，沒有 build factory。

### 9.2 Runner

```
for stage in STAGES:
    if not stage.enabled(options): emit Skipped(disabled); continue
    if ledger.done(stage.key): emit Skipped(already-complete); maybe break-after; continue
    with stage_scope(stage.key): stage.run(ctx)
    ledger.mark(stage.key, elapsed, params)   # atomic save
    if options.break_after == stage.key: stop
```

`--break-after` 接受 stage key（`--break-after asr`），不再是 `is_asr_completed`。

開跑前 `Pipeline.check(options, config, secrets, state=)` 對本次會執行的 stage（啟用、未完成、在 break-after 之內）呼叫 `preflight`，失敗時什麼都不建立（缺 ElevenLabs key 會在下載前失敗）。stage 讀上游成品一律經 `require(path, produced_by)`，缺檔時丟 `MissingArtifactError` 並指出該跑的 `grill reset <id> --from <stage>`。

### 9.3 Side tasks

`SideTaskDef[T](key, start_after: StageKey, enabled, run, record=, describe=)`：`run` 只回傳 payload，manager 在 state lock 下寫紀錄（預設 `TaskRecord`，elapsed/usage 取自 `StepOutcome`；date research 自訂 `record` 寫 `DateResearchRecord`）。`SideTaskManager` 依 `start_after` 自動在對應 stage 後啟動，仍在 finally join、仍在 `--break-after` 時整個跳過。date research 的「已付費結果在停用時仍套用」規則保留，寫在 `stages/date_research.py`。

### 9.4 Delivery

順序改為 stages → delivery step（package）→ 關閉 run log 與 JSONL → archive → `RunFinished`（#32 package→archive）。package 因此只讀本地專案、不讀 NAS 上的歸檔；archive 不是 `DeliveryStepDef`，由 runner 在 log 關閉後以注入的 `archive(layout) -> layout` 執行（只有注入時才出現在 plan），其事件只到 console/TUI。`serial` 改用 `pipeline` 的公開 API（`pipeline.runner.run_project`），parent 的 briefing 透過 `ProjectLayout(parent_dir).effective_briefing()` 讀取。

### 9.5 新指令 `grill reset`

```
grill reset <id> --from refine     # 清掉 refine 之後所有 stage 的 ledger、work 目錄與宣告的成品
grill reset <id> --only chunks     # 只清 chunks（下游不動，使用者自負）
```

刪除範圍完全由 `StageDef.outputs`、`StageDef.clear_state`（該 stage 寫入的 state 欄位）與 work 目錄決定，沒有另外的清單要維護。

這是使用者主動、顯式的失效，不違反「快取不自我失效」。取代現在 skill 裡「刪 `.pre_pass/` 再把 `is_prepass_completed` 改 false」的手動流程。

---

## 10. 專案目錄與狀態

### 10.1 目錄

```
projects/<id>/
├── project.json               # ProjectState（§10.2）
├── video.mp4                  # 處理用影片（section 執行時為裁切後）
├── poster.jpg  cover.png
├── subs/
│   ├── ja.srt                 # ASR 產生的日文字幕（transcript stage 輸出）
│   ├── ja.official.srt        # 平台 CC（有才有）
│   ├── cht.srt                # finalized 繁中 SRT
│   ├── cht.ass                # 樣式化 ASS
│   └── chat.cht.json          # 翻好的聊天室（--chat 才有）
├── work/
│   ├── 01_metadata/           info.json（yt-dlp）
│   ├── 02_download/           full.mp4（分段接合後；無 section 時 combine 搬成 video.mp4）、parts/ 平台 CC 原檔
│   ├── 03_combine/
│   ├── 04_chat_fetch/         live_chat.jsonl、messages.json
│   ├── 05_audio/              audio.ogg
│   ├── 06_asr/                asr.json
│   ├── 07_transcript/         （輸出在 subs/ja.srt）
│   ├── 08_prepass/            briefing.json、frames/、session/
│   ├── 09_chunks/             <from>-<to>/{frames/, audio.ogg, translation.json, session/}、merged.srt
│   ├── 10_refine/             refined.srt、report.md、frames/、session/
│   ├── 11_glossary/           checked.srt、briefing.json（僅修正時）、report.md、frames/、session/（agent 寫 briefing.candidate.json，驗證通過且有差異才 promote）
│   ├── 12_finalize/
│   ├── 13_chat_translate/     batches/、polish.json、session*/
│   ├── side/cover/            session/
│   ├── side/date_research/    result.json、session/
│   └── package/               titles.json、session/
└── logs/
    ├── run-<ts>.log
    └── events-<ts>.jsonl
```

規則：

- 編號 = `StageKey` 順序。改變 stage 順序就是目錄結構變更（no-compat 原則下可接受）。`work/side/` 與 `work/package/` 不屬於 stage，刻意不編號。
- `07_transcript`、`12_finalize` 這種沒有中間產物的 stage 不建立空目錄（`StageContext.workdir` 延遲建立）。
- `subs/ja.official.srt` 由 combine stage 產生（section 執行時要依裁切範圍平移/過濾 timestamp，所以必須在 combine 之後）。平台沒有 CC 或多段影片時不產生，這是正常情況；**有下載到 CC 但正規化失敗則讓 stage 失敗**（現行是警告後繼續，依「fail loudly」原則改掉）。`features.official_subtitles = false` 時完全不下載。
- `ProjectLayout` 是**唯一**知道這些路徑的地方；package、TUI artifacts、agent_tools manifest 一律從 layout 取，刪掉 `project` 模組匯出的檔名常數。

### 10.2 `project.json`

```json
{
  "id": "epeokvphg6",
  "platform": "tver",
  "created_at": "...",
  "name": "全力脱力タイムズ_…",
  "translation_hint": null,
  "parent": null,
  "broadcast_date": "2026-10-09",   // 平台日期；研究找到的日期在 side_tasks.date_research.broadcast_date，讀者用 effective_broadcast_date
  "source": { "title": "...", "description": "...", "series": "...", "channel": "...",
              "broadcast_label": "...", "talents": [ ... ] },
  "section": { "start": null, "end": null },
  "asr_cost_usd": 0.31,
  "stages": {
    "metadata": { "completed_at": "...", "elapsed_s": 2.1, "params": { "official_cc": "on" } },
    "prepass":  { "completed_at": "...", "elapsed_s": 412.0,
                  "params": { "model": "agy/gemini-3.1-pro/high" }, "agent_usage": { ... } }
  },
  "side_tasks": { "cover": { ... }, "date_research": { "verdict": "unknown", ... } }
}
```

- 13 個 `is_*` 布林、`ProgressStage` enum、`check_enum_field_sync` 全部刪除；ledger 的 key 是 `StageKey`，pydantic 驗證未知 key 直接失敗。
- 寫入一律 atomic（寫 tmp → `os.replace`）。
- `params` 快照讓 `grill status` 能顯示「這個 chunk 是用哪個模型翻的」，**不**用於快取判斷。

### 10.3 有效 briefing

pre-pass 寫 `work/08_prepass/briefing.json`。glossary check 若修正 briefing，寫成 `briefing.candidate.json`，結果被接受且與 pre-pass 版不同時才 atomic promote 到**自己的** `work/11_glossary/briefing.json`，不碰 pre-pass 的檔案（取代 `pre_pass.raw.json` 備份機制）。下游一律呼叫：

```python
layout.effective_briefing() -> Path   # glossary 版存在就用它，否則 pre-pass 版
```

所有消費者（finalize、chat translate、titles、package info、serial 的 parent）改用 typed `Briefing` 讀取，給 prompt 時統一走 `Briefing.render_for_prompt()`。

### 10.4 MAX_PATH 預算

最深路徑變成 `work/09_chunks/0001-0119/session.2/result.json`、`work/09_chunks/0001-0119/frames/frame_0001234.567_768.jpg`（約 55–60 單位），`PROJECT_INNER_PATH_RESERVE` 由 `project/naming.py` 依 layout 常數計算而非手寫 80，並有測試斷言「layout 中最長的相對路徑 ≤ reserve」。agent 自己在 workdir 寫的檔案（refine、glossary 直接在 stage 目錄內）另留餘裕。

### 10.5 一次性遷移腳本

`scripts/migrate_archive.py <root>`：遞迴找舊結構（有 `is_*` 欄位的 `project.json`），`--dry-run` 預設開啟，完成全部遷移後刪除腳本。腳本只依賴新的 `grillmaster.project` / `core` 模型來寫出新格式，不 import 任何舊碼。對照：

| 舊 | 新 |
|---|---|
| `project.json` 的 `is_*`、`is_cover_generated`、`is_broadcast_date_researched` | `stages` ledger、`side_tasks`（研究過且 found 時，`broadcast_date` 搬進 `side_tasks.date_research.broadcast_date`） |
| `video.ja.srt` / `video.official.ja.srt` / `video.cht.finalized.srt` / `video.cht.ass` / `chat.cht.json` | `subs/ja.srt` / `subs/ja.official.srt` / `subs/cht.srt` / `subs/cht.ass` / `subs/chat.cht.json` |
| `poster.cover.png` | `cover.png` |
| `metadata.info.json`、下載的分段 mp4 與 CC 原檔、`video.full.mp4` | `work/01_metadata/info.json`、`work/02_download/`、`work/02_download/full.mp4` |
| `.asr/audio.ogg`、`.asr/asr.json` | `work/05_audio/`、`work/06_asr/` |
| `.pre_pass/pre_pass.raw.json`（若有）與 `pre_pass.json` | 有 raw：raw → `work/08_prepass/briefing.json`、現版 → `work/11_glossary/briefing.json`；沒有：現版 → prepass。dict 欄位轉成 `TermMapping` list |
| `.chunks/responses/*.raw.srt`（或 `.fixed.srt`） | 解析成 `work/09_chunks/<range>/translation.json`；`video.cht.srt` → `work/09_chunks/merged.srt` |
| `video.cht.refined.srt`、`.refine/report.md` | `work/10_refine/refined.srt`、`report.md` |
| `video.cht.glossary_checked.srt`、`.glossary_check/report.md` | `work/11_glossary/checked.srt`、`report.md` |
| `.live_chat/*` | `work/04_chat_fetch/`、`work/13_chat_translate/` |
| `.artifacts/date_research.json`、`.titles/titles.json` | `work/side/date_research/result.json`、`work/package/titles.json` |
| `extra_frames/`、`.chunks/media/`、`manifests/`、`assets.json`、`manifest.json` | 丟棄（只是快取或死碼） |

---

## 11. 設定（`grill.toml` + `.env`）

### 11.1 檔案

- `grill.toml`：gitignored，位置由 cwd 往上找，找不到用 `GRILL_HOME`。`projects/` 目錄相對於 `grill.toml` 所在位置，所以 `grill` 可以在任何目錄執行，`scripts/grill.bat` 的 `cd` 不再必要（改成 `uv tool install --editable .` 或保留一行 `uv run --project`）。
- `grill.example.toml`：tracked，每個鍵都有註解。
- `grill.schema.json`：由 `AppConfig` 產生，`grill.toml` 第一行 `#:schema ./grill.schema.json` 讓 Taplo / Even Better TOML 補全與驗證；測試斷言 schema 是最新的。
- `.env`：只剩 `ELEVENLABS_API_KEY` 這類金鑰。

### 11.2 結構

```toml
#:schema ./grill.schema.json

[paths]
archive = 'V:\show\grilled'
package = 'C:\Users\eli\Videos\bilibili'
cookies = "cookies.txt"

[agents]
timeout_minutes = 40
max_concurrent = 5

[agents.roles]                 # backend/model[/effort]
prepass     = "agy/gemini-3.1-pro/high"
chunk       = "agy/gemini-3.1-pro/high"
postprocess = "codex/gpt-6-astra/ultra"
utility     = "codex/gpt-6-astra/high"     # titles、date research
chat        = "codex/gpt-6-astra/high"     # 省略時用 utility
image       = "codex/gpt-6-astra/high"     # cover，需要 IMAGE_GENERATION

[asr]
model = "scribe_v2"
language = "jpn"

[translate]
chunk_char_limit = 6000
chunk_attempts = 3
prepass_frame_interval_s = 60
chunk_frame_interval_s = 30
frame_max_side = 768

[features]
official_subtitles = true
cover = false
date_research = false
title_suggestion = false

[package]
remix_pool = "noise"           # --remix 不帶值時用的素材池

[[package.inserts]]            # 泛化的 placeholder（§12.6）
pool = "judge"
output = "judge"
when = "remix"                 # "remix" | "always"

[programs.series."水曜日のダウンタウン"]
remix = true
inserts = ["judge"]
[programs.series."水曜日のダウンタウン".instruction]
common = "..."
prepass = "..."
```

- 程式碼裡只有 `cli/` 與 `pipeline/` 讀 `AppConfig`；stage 把需要的子區段（例如 `TranslateOptions`）傳給領域模組。全域 `settings` 單例刪除。
- `ModelSpec` 解析邏輯沿用，但 backend 欄位改成 `Backend` enum，config 載入時就驗證。
- 下載時自動登記新 series/channel：用 `tomlkit` 在 `grill.toml` 尾端追加空區段，保留使用者的註解與排版。這是唯一會寫到專案目錄之外的 stage 行為（§3 的明列例外）。
- instruction 的 step key 與 stage key 對齊（`pre_pass` → `prepass`、`translate` → `chunks`、`glossary_check` → `glossary`）。

---

## 12. 領域模組整理

### 12.1 Prompt

- 全部 prompt 經 `core/prompts.py`（`importlib.resources`），刪除 11 處模組載入時 `read_text`。
- **音訊變體改成片段組合**：把 `chunk.md`、`pre_pass.md` 中與音訊有關的段落拆成 `*_audio.md` 片段，`has_audio` 時才組進去。刪掉 `_NO_AUDIO_SUBS` 的 find/replace 與守護它的測試。這只是重新排列既有文字，不改措辭；以「拆分前後 `has_audio=True/False` 兩種輸出逐字相同」的一次性測試驗證後移除該測試。
- 使用者訊息的段落標題（`【節目標題】` 等）集中成 `PromptSection` 常數，pre-pass 與 chunk 共用。
- 有 `{slot}` 的模板一律經 `core.prompts.render_template(package, name, **values)`：模板的 slot 與傳入值必須完全一致，缺漏或多餘都直接報錯。
- `get_frames` 的通用說明只有一份（`core/prompts/frames_tool.md`，經 `frames_guidance()` 組入），時間窗由 agents 層的「可用工具」段落列出；各 stage 只保留自己的使用時機片段（pre-pass 片段另說明它的時間窗是「影片開頭到最後一個字幕區塊結束」）。

### 12.2 翻譯（D8）

- chunk 輸出 schema：`ChunkTranslation(blocks: list[ChunkLine(index: int, text: str)])`。validator：每個來源 index 恰好一次、無多餘 index、text 非空；錯誤訊息逐一列出缺漏/重複的 index，作為 resume 的修復指示。這個覆蓋檢查是 `core.id_coverage.id_coverage`（回傳缺漏/未知/重複/空白 id），聊天室批次的 validator 共用，各呼叫端自行組修復訊息。快取在讀取時驗證一次，補回 timecode 時不再重驗。
- Python 用來源 `SrtBlock` 補回 timecode 組成 SRT，`merged.srt` 由 stage 合併後重新編號。
- 刪除：`structural_fix.py`、`structural_fix.md`、`validate_chunk.py`、`*.fixed.srt` 快取、`normalizer.py`（空 speaker-dash 行改由 validator 拒絕或在補回時清理，二擇一於實作時依資料決定）。
- 快取：`work/09_chunks/<range>/translation.json` 存在即命中。
- `chunk.md` 的輸出格式段落改寫為 JSON 說明；這是本次唯一允許的 prompt 語意變更，完成後跑一集完整節目與現行版本比對品質。
- `translate/assets.py` 合併 pre-pass 與 chunk 的抽幀/切音訊碼；只寫不讀的 `assets.json`、`manifest.json`、`manifests/` 刪除。
- `TranslationRequest` 拆成 `PrepassInputs` 與 `ChunkInputs`，兩者共用 `SourceContext`（標題、說明、hint、talent、節目規則、CC、parent briefing）。

### 12.3 Postprocess / extras

- `refine`、`glossary_check` 改吃 `RefineInputs` / `GlossaryInputs`（路徑 + 文字），輸出 `FilesOutput`，validator 用 `subtitles.structure.check_aligned_file()`；agent 可用 `check_srt` 工具自檢。
- 它們的 `workdir` 是自己的 stage 目錄（由 stage 明確傳入，agent 只在這裡寫檔，輸出路徑都在其下）；要讀的其他檔案（`subs/ja.srt`、`merged.srt`、有效 briefing）以絕對路徑寫進 prompt，並以 `add_dirs` 開放專案根目錄的讀取。
- glossary 的「疑似區塊」偵測、報告必寫規則、briefing 修正驗證保留，寫入位置改為 §10.3。
- `cover`、`date_research`、`titles` 移到 `extras/`；`titles` 的快取改到 `work/package/titles.json`。

### 12.4 Subtitles

- `subtitles/structure.py`：`check_aligned(reference, candidate)`（index、timecode、數量、非空）與讀檔版 `check_aligned_file(reference, path)`（讀不到或無法解析也列為問題），refine/glossary/check_srt 共用。
- `subtitles/ass.py`：ASS 畫布、字型、邊界常數與 style header，`finalize` 與 `live_chat/render` 都從這裡拿。
- `finalize` 改吃 typed `Briefing` + glossary 名稱單位，不再自己 parse raw dict。

### 12.5 Media 拆分

- `media/ffmpeg.py`：唯一建構與執行 ffmpeg/ffprobe 的地方（argv list + 逐行 progress 解析 + tree-kill），取代 `ffmpeg-python` 依賴與散落的 `subprocess.run`。
- `media/probe.py`（duration、audio gaps）、`frames.py`、`audio.py`、`video.py`（combine、cut）。
- 打包配方（`PACKAGE_*` 常數、`BurnPlan`、`Box`、`SubtitleLayer`、parts、mux、noise bed）整塊搬到 `package/render.py`；remix 編碼搬到 `package/remix.py`。內容逐字搬移，不重寫 filter。
- 刪除只剩測試在用的 `evenly_spaced_timestamps`、`absolute_interval_timestamps`。

### 12.6 素材池與泛化的 placeholder（judge）

`package/pools.py` 統一「`<package>/pools/<name>/` 內 `001.*`、`002.*`… 連號素材 + `.cursor.json` 游標 + 先保留再使用」（游標讀改寫持有 `.cursor.lock`，O_EXCL 互斥）：

```python
class MediaPool:
    def next_file(self) -> Path                       # inserts 用：整檔輪替
    def reserve_seconds(self, total: float) -> list[Cut]   # remix noise 用：依秒數遊走
```

- noise 從 `noise/<name>/` 搬到 `pools/<name>/`，`--remix [pool]`。
- placeholder 泛化為 **insert**：`[[package.inserts]]` 宣告 `pool`、輸出檔名 `output`、觸發條件 `when`（`remix` 或 `always`）；節目層級可用 `programs.*.inserts = [...]` 只對特定節目啟用。沒設定任何 insert 就什麼都不複製，個人化的 `judge` 名稱從程式碼移到使用者的 `grill.toml`。
- 池不存在時：insert 視為設定錯誤直接失敗（因為是使用者明確宣告的），不再靜默略過。

### 12.7 Sources

- `sources/registry.py` 擁有 ID 解析與平台判斷（原 `Project.parse_source_str`、`.source`、`.source_url`）。
- 每個平台一個模組實作 `SourcePlatform`：`url(id)`、`cookie_policy`、`fetch_extras()`（talent、播出標籤）、`resolve_broadcast_date()`、下載前置（Abema token reset）。新增平台只動 `sources/`。
- 刪除 Bilibili wbi playurl monkey-patch（D5）；實作前先用目前 yt-dlp 對當初出錯的影片試下載一次確認。

### 12.8 其他

- `asr/`：`_extract_word_items` 的重複合併；`ElevenLabsASR` 改吃 `AsrOptions` + 金鑰，不讀全域設定。
- `live_chat/`：依 `layout` 取路徑；渲染常數從 `subtitles.ass` 取；translate 改用 `AgentRunner.run_jobs`（每批完成即寫快取）。
- 移除所有 lazy `__getattr__` 套件；重依賴（`claude_agent_sdk`）只在 adapter 模組 import，adapter 由 registry 延遲載入。

---

## 13. CLI

單一 Typer app，`grill <src> [HINT]` 以 callback 實作為 `grill run` 的捷徑（D7），刪除 `legacy_app`、`RESERVED_COMMANDS` 分流。

| 指令 | 說明 |
|---|---|
| `grill run <src> [HINT]`（= `grill <src>`） | 處理單一來源；選項：`--break-after`、`--parent`、`--cover`、`--date-research`、`--chat`、`--chat-layout`、`--remix [pool]`、`--start`、`--to` |
| `grill serial <src>...` | 串接多集 |
| `grill package <dir>` | 重新打包 |
| `grill archive <id>` | 只做歸檔搬移（打包成功、歸檔失敗時免重 render） |
| `grill reset <id> --from/--only <stage>` | 顯式重跑（§9.5） |
| `grill status [<id>]` | 印 ledger、每個 stage 的模型、成本、session 結果 |
| `grill doctor [--setup]` | 檢查 ffmpeg、三個 CLI 版本/登入、MCP 註冊、`grill.toml` 驗證；`--setup` 做一次性註冊（S2 路線 b） |

`--remix` 不帶值的 argv 展開保留（Typer 仍無 optional-value option），移到 `cli/args.py`。

---

## 14. 測試策略

### 14.1 結構與風格

- `tests/` 鏡像 `src/grillmaster/` 的套件結構（`tests/agents/test_runner.py`…）。
- 全部改寫為 **pytest function + fixture**，不再用 `unittest.TestCase`。依重構階段逐模組改寫，不做一次大搬家。
- 禁止以字串路徑 patch（`patch("services.media.subprocess.run")`）：依賴改由參數注入（`AgentRunner`、`FfmpegRunner`、`Clock`），測試傳 fake。`monkeypatch` 只用於環境變數。
- 暫存一律 `tmp_path`；刪除 `tmp_test_artifacts/` 與 13 份 `_make_temp_dir`。
- pytest 移入 `[dependency-groups] dev`（`uv sync` 預設安裝），`uv run pytest` 即可，不再需要 `--with pytest` 與 `python -m pytest` 的 sys.path 技巧。
- `[tool.pytest.ini_options]`：`testpaths = ["tests"]`、`markers = ["live: calls real agent CLIs"]`、`addopts = "-ra --strict-markers -m 'not live'"`。

### 14.2 共用 fixture（`tests/conftest.py` 與子目錄 conftest）

- `layout`：在 `tmp_path` 建一個真實的 `ProjectLayout` + `ProjectState`。
- `config`：最小 `AppConfig`，可覆寫。
- `events`：`RecordingSink`，斷言事件序列。
- `fake_agents`：`FakeAgentRunner`，以 task 名稱腳本化回應（輸出、錯誤、修復序列）；也提供 `FakeAdapter` 給 runner 自身測試。
- `fake_ffmpeg`：記錄 argv、可指定輸出檔。
- `media_fixture`：一段 2 秒的測試影片（產生一次，session 範圍快取），只給 `media/` 的整合測試用。

### 14.3 測試層

| 層 | 內容 | 依賴 |
|---|---|---|
| 單元 | core（srt、timecode、json_artifact）、chunker、validator、finalize 標點、config 解析、layout/naming、ProgramRules、pools 游標 | 無 |
| Adapter 契約 | 用錄製的原始事件 fixture（`tests/fixtures/agents/{agy,codex,claude}/*.jsonl`）驗證：事件正規化、final output 抽取、錯誤分類、resume 參數組裝。三家用同一組參數化斷言 | 無 |
| Runner | FakeAdapter：capability 檢查、修復走 resume、attempts 只對 transient、錯誤分類、session 目錄內容、事件順序、併發上限 | 無 |
| MCP 工具 | 直接呼叫工具函式；另有一個 stdio 往返測試（用 `mcp` client 啟動 server、呼叫 `check_srt`） | 子行程 |
| Stage | `StageContext` + FakeAgentRunner + tmp layout：輸入組裝正確、輸出寫到正確位置、重入規則（refine 丟棄舊檔） | 無 |
| Pipeline | 以假 StageDef 測 registry 順序、resume 跳過、break-after、side task join（含 pipeline 失敗時）、delivery | 無 |
| TUI | `PipelineState` 是純 reducer（事件 → 狀態）可單元測；App 用 Textual `run_test` pilot 做 1–2 個冒煙測試 | textual |
| Live（`-m live`，預設不跑） | 每個 adapter 一個最小呼叫（回傳固定 JSON、呼叫一次 `get_frames`），也是重新錄製 fixture 的腳本 | 真 CLI、少量配額 |

錄製 fixture 用 `scripts/record_agent_fixture.py`，手動執行；fixture 進版控，CLI 升級後重錄即可發現格式變更。

### 14.4 刪除或改名

`test_gemini_normalizer.py`、`test_chunk_fix.py`、`test_translate_prompts.py`（find/replace 守護）、`test_progress_integration.py`（拆進各層）、`test_workflow_breakpoints.py` 的 36 個 patch 版本（改為 pipeline 層假 stage 測試）。

---

## 15. 工具鏈

### 15.1 pyproject

```toml
[build-system]                     # 現在沒有 → uv 把專案當 virtual，不會安裝 scripts 也不能 -m 啟動
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "grillmaster"
requires-python = ">=3.13"
dependencies = [..., "mcp", "tomlkit"]          # 移除 ffmpeg-python、rich（若 textual 不需直接依賴）、pywinpty（S1 成立時）
[project.scripts]
grill = "grillmaster.cli:main"

[tool.uv]
package = true

[dependency-groups]
dev = ["pytest", "pytest-cov", "ruff", "basedpyright", "import-linter", "poethepoet"]

[tool.poe.tasks]
fmt       = "ruff format"
fmt-check = "ruff format --check"
lint      = "ruff check"
types     = "basedpyright"
layers    = "lint-imports"
test      = "pytest"
check     = ["fmt-check", "lint", "types", "layers", "test"]   # poe sequence
```

沒有 CI，`uv run poe check` 是「完成」的定義；AGENTS.md 會寫明。過渡期（階段 1–5）lint 與 types 只回報不擋，`check` 暫時只含 `fmt-check`、`layers`（僅對已搬入新結構的套件）、`test`；階段 6 才把 `lint`、`types` 加回 `check`。

### 15.2 ruff

```toml
[tool.ruff]
target-version = "py313"
line-length = 88
src = ["src", "tests"]

[tool.ruff.lint]
select = [
  "E", "W", "F", "I", "N", "UP", "B", "A", "C4", "DTZ", "T10", "T20", "ISC", "ICN",
  "PIE", "PT", "RET", "SIM", "TID", "TC", "ARG", "PTH", "ERA", "PL", "TRY", "FLY",
  "PERF", "FURB", "LOG", "RUF", "S", "BLE", "FBT001", "FBT002", "SLF", "INP", "Q",
]
ignore = [
  "E501",      # formatter 處理行長；長字串（prompt 片段）允許
  "ISC001",    # 與 formatter 衝突
  "TRY003",    # 例外訊息需要帶上下文
  "PLR0913",   # 由 Inputs dataclass 收斂參數，不用規則硬卡
  "S603", "S607",              # 刻意執行外部 CLI；統一經過 agents/process 與 media/ffmpeg
]
# 中文字串裡的全形標點是正常內容，不是 confusable
allowed-confusables = ["，", "。", "（", "）", "：", "；", "、", "「", "」", "！", "？", "～", "－"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "PLR2004", "ARG", "FBT", "SLF001", "INP001"]
"scripts/**" = ["T20", "INP001"]
"src/grillmaster/cli/**" = ["TC"]                     # Typer 執行期解析註解
"src/grillmaster/agents/adapters/__init__.py" = ["PLC0415"]   # adapter 延遲載入（重依賴）
"src/grillmaster/media/ffmpeg.py" = ["TID251"]
"src/grillmaster/agents/process.py" = ["TID251"]
"src/grillmaster/agents/adapters/claude.py" = ["TID251"]

[tool.ruff.lint.isort]
known-first-party = ["grillmaster"]
required-imports = ["from __future__ import annotations"]

[tool.ruff.lint.flake8-tidy-imports]
ban-relative-imports = "parents"
[tool.ruff.lint.flake8-tidy-imports.banned-api]
# 只禁止啟動行程的函式；例外型別（CalledProcessError 等）由兩個 process 模組重新匯出
"subprocess.run".msg = "Use grillmaster.media.ffmpeg or grillmaster.agents.process"
"subprocess.Popen".msg = "Use grillmaster.media.ffmpeg or grillmaster.agents.process"
"subprocess.call".msg = "Use grillmaster.media.ffmpeg or grillmaster.agents.process"
"subprocess.check_output".msg = "Use grillmaster.media.ffmpeg or grillmaster.agents.process"
"asyncio.run".msg = "Pipeline code is thread-based; only agents.adapters.claude may run a loop"

[tool.ruff.lint.flake8-type-checking]
runtime-evaluated-base-classes = ["pydantic.BaseModel", "pydantic_settings.BaseSettings"]

[tool.ruff.lint.pylint]
max-branches = 15
max-returns = 8
```

- 統一 `from __future__ import annotations`（required-imports 強制）。pydantic model 由 `runtime-evaluated-base-classes` 保護；Typer 指令以 per-file-ignore 關掉 TC；CLI 參數一律 `Annotated[...]` 風格，避免 B008。
- 預期第一次開規則會冒出的修正（列入工作量）：`S324`（`live_chat/render.py` 的 md5，改 `usedforsecurity=False`）、`DTZ006`、`PLW1510`（`subprocess.run` 沒有 `check=`，搬進 process 模組時一併處理）。
- 一次性 `ruff format` 全 repo **先於** src layout 搬移，避免格式雜訊混入搬移 diff。

### 15.3 basedpyright

```toml
[tool.basedpyright]
typeCheckingMode = "standard"
pythonVersion = "3.13"
include = ["src", "tests"]
reportMissingTypeStubs = false
reportUnknownMemberType = false        # yt_dlp、textual_image 型別不完整
```

`Any` 的 `project` 參數（TUI）全部改成具體型別。

### 15.4 import-linter

`.importlinter` 寫入 §5.1 的契約；`lint-imports` 在 `poe check` 中執行。

### 15.5 其他慣例

- docstring：Markdown 單反引號（統一，刪掉 RST 雙反引號）；不強制 Args/Returns 格式。
- dataclass vs pydantic：跨行程/持久化/agent 輸出 → pydantic；純記憶體值物件 → `@dataclass(frozen=True, slots=True)`。
- 例外：每個套件一個 `errors.py`，基底類別命名 `<Package>Error`。

---

## 16. 文件與 skills

- `AGENTS.md`（`.claude/CLAUDE.md` 為其 symlink）：更新 skill 對照表、指令（`uv run poe check`）、分層規則一句話版本。
- Skills 改寫（皆在 `.agents/skills/`）：
  - `project-architecture`：pipeline registry、StageContext、layout、ledger、config、events、分層契約、`grill reset`。
  - `agent-orchestration`（**取代** `inference-layer`）：AgentTask/Runner/Adapter、capability、resume 修復、錯誤分類、session 紀錄、MCP 工具、錄製 fixture。
  - `translate-pipeline`、`postprocess-and-packaging`、`live-chat`：更新路徑與 API。
- `article.md`、`doc/` 搬到 `docs/`（`docs/article.md`、`docs/images/`）；本文件放 `docs/design/`。
- README 更新安裝（`uv sync`、`uv tool install --editable .`）、`grill.toml` 範例、輸出目錄說明。

---

## 17. 刪除清單

| 項目 | 原因 |
|---|---|
| `services/progress.py`（Rich reporter + Noop 協定） | 事件匯流排取代（D5） |
| `ProgressStage`、`Project.is_*`、`check_enum_field_sync` | ledger 取代 |
| `services/inference/tools/get_frames*.py`（5 支）、`validate_chunk.py` | MCP 工具取代 |
| `structural_fix.py`、`structural_fix.md`、fixed 快取 | D8 |
| agy 的 pty / ANSI 清理 / 答案標記 / `pywinpty`、`_AGY_MODEL_BASES` | S1 成立時 |
| `schema_enforce.py` 的 prompt-schema 後綴 | 原生 schema（D6）；validate-repair 邏輯移入 runner |
| `ffmpeg-python` 依賴 | `media/ffmpeg.py` 取代 |
| Bilibili wbi playurl monkey-patch | D5（驗證後） |
| `legacy_app`、`RESERVED_COMMANDS`、`.packagerc` 警告、`_split_legacy_hint` | 無相容需求 |
| `evenly_spaced_timestamps`、`absolute_interval_timestamps`、`assets.json`、`manifest.json`、`manifests/` | 死碼 |
| `owner_key_for_thread_name`、`STAGE_WEIGHTS` | contextvars 與 registry 取代 |
| `tmp_test_artifacts/` | `tmp_path` 取代 |
| `config.json`、`config.example.json`、`config.schema.json` | `grill.toml` 取代 |

---

## 18. 執行計畫

每個階段結束時：`uv run poe check` 全綠、更新受影響的 skill、跑一次真實短片段（`--start 0 --to 5:00`）冒煙。每個階段一個或數個 commit，可獨立回退。

### 階段 0：Spike（只做驗證，不改主程式）

每個 spike 是最小的 pass/fail 呼叫（遵守「配額測試最小化」），結果寫回本文件第 19 節。

| ID | 驗證內容 | 失敗時的後備 |
|---|---|---|
| S1 | agy：(1) 非 TTY 下 `-p --output-format stream-json` 正常輸出；(2) `--input-format stream-json` 經 stdin 送大 prompt，含圖片（`@<path>` 或內容區塊）與音訊 `view_file` 指示的放法；(3) stream-json 輸入模式遇到錯誤時的事件形狀，確認 adapter 能據以關閉 stdin；(4) `--model gemini-3.1-pro-high` 形式可用，以及不支援的 effort 組合的錯誤訊息 | 保留 pty；保留 INPUT.md + `@` bootstrap；錯誤偵測改讀 stderr/exit code |
| S2 | agy 的 MCP 注入：工作目錄 `.agents/` plugin，或全域註冊 + 環境變數繼承 | 工具參數帶 session token |
| S3 | 三家是否把 MCP image content 轉給模型 | 回傳路徑，agent 自行開檔 |
| S4 | 三家原生 schema 接受 `strict_json_schema()` 轉出的五個 schema | 該 backend 退回 prompt-schema（記為 capability 缺失） |
| S5 | 三家 resume：codex 非 ephemeral `exec resume`（cwd 由行程設定、重帶 `-c`/`--output-schema`）、agy `--conversation`、claude `resume=`，且第二回合能看到前文、仍有 MCP 工具與 schema | 該 backend 修復時重送完整 prompt |
| S8 | 錄製三家各一份最小 session 的原始事件（codex `--json` 事件名、agy `step_type` 詞彙、claude message 序列），作為契約測試 fixture 的第一版 | — |
| S6 | agy 在 stream-json 事件中能否辨識 `view_file` 載入音訊 | 保留讀 agy 內部 transcript |
| S7 | 目前 yt-dlp 對 Bilibili 舊問題影片不需 patch | 保留 patch 並移到 `sources/bilibili.py` |

原則：**新結構與舊結構並存，逐 stage 搬過去；某個舊模組只有在最後一個 import 它的地方消失時才刪除**。這樣不需要任何過渡用的 shim（例如模組層級的 runner 單例）。

### 階段 1：工具鏈與骨架

`ruff format` 全 repo（獨立 commit）→ src layout 搬移（`git mv` 成 `src/grillmaster/` 下的舊結構，只改 import 路徑，不改邏輯）→ pyproject（build-system、scripts、dev group、ruff/basedpyright/import-linter 設定，lint/types report-only）→ `tests/` 改用 `uv run pytest`。結束時行為與現在完全相同。

### 階段 2：基礎層（全新模組，舊碼暫不改）

`core/`（srt、timecode、json_artifact、prompts、briefing、source_id、paths、stage_key、tool_session）、`config/`（grill.toml、secrets、programs、schema）、`project/`（state ledger、layout、store、naming）、`events/`（types、bus、context、sinks）。遷移腳本在此完成並對 NAS 做 dry-run（只讀不寫）。每個模組附單元測試。

### 階段 3：Agent 層（與舊 `services/inference` 並存）

`agents/`（task、runner、三個 adapter、process、schema、errors）+ `agent_tools/`（MCP server、get_frames、check_srt）+ S8 的 fixture 與契約測試 + FakeAdapter 的 runner 測試。此階段不改任何呼叫端，也不刪舊層。

### 階段 4：Pipeline、stages 與領域模組（逐 stage 移植）

先建 `pipeline/`（registry、runner、side tasks、delivery、serial）與 CLI 骨架，接著依 stage 順序逐一移植：寫 `stages/<key>.py`，同時把它用到的領域模組改成吃 `*Inputs` + `AgentRunner`、搬到新套件（translate 含 D8 與 Briefing 形狀變更、postprocess、extras、subtitles、media 拆分、package 的 render/pools/inserts、sources、asr、live_chat）。每移植完一個 stage，跑該 stage 的測試；全部移植完成後：

- TUI 改接事件（sessions 表、活動紀錄），刪除 `services/progress.py`。
- 刪除 `workflow/`、`project.py`、`settings.py`、`main.py`、`services/inference`、`services/` 殘餘。
- 跑一集完整節目，與現行版本的成品並排比對翻譯品質（D8 與 Briefing 形狀變更的驗收）。

### 階段 5：遷移

對 NAS 正式執行遷移腳本；抽樣用新版 `grill package` 重新打包幾個舊專案、用一個舊專案當 `--parent` 跑一集 serial。完成後刪除遷移腳本。

### 階段 6：收尾

lint/types 從 report-only 轉成強制並清到零、清掉理由不明的 `noqa`、文件與 skills 改寫、README、`docs/` 整理。

---

## 19. 風險與未決事項

| 風險 | 緩解 |
|---|---|
| agy 為閉源、更新頻繁，stream-json 欄位可能變 | adapter 契約測試 + 錄製 fixture；`grill doctor` 檢查版本；事件正規化失敗時降級為「無活動摘要」但不影響結果抽取 |
| 原生 schema 的 strict 限制讓 Briefing 欄位設計變彆扭 | S4 先驗；必要時 Briefing 拆成較小的子 schema |
| chunk JSON 輸出（D8）或 Briefing 形狀變更影響翻譯品質 | 階段 4 結尾用同一集節目並排比對；chunk 品質下降就退回「SRT 文字 + resume 修復」方案 |
| resume 讓修復回合累積上下文，超出 agent 的 context window | 修復上限維持 3；session 紀錄 usage 以便觀察 |
| 一次大重構期間主程式不可用 | 新舊並存、逐 stage 移植；階段 1–3 不改行為，階段 4 每移植一個 stage 都能跑 |
| agy 全域 MCP 註冊（S2 路線 b）讓使用者平常的 agy 也看到 grill 工具 | 只在路線 a 失敗時採用；無 session 時 server 不暴露工具 |
| MCP 子行程在 Windows 上殘留 | 子行程由 agent CLI 管理；`grill doctor` 列出殘留 `grillmaster.agent_tools` 行程 |

### Spike 結果（2026-10-10）

| ID | 結果 | 對設計的影響 |
|---|---|---|
| S1 | ✅ agy 1.3.2 非 TTY 下 `-p --output-format stream-json` 輸出正常（`init` / `step_update` / `result`，`result.structured_output`）。`--input-format stream-json` 需搭配 `-p=`（空 prompt），stdin 訊息形狀 `{"event":"user","message":{"content":"…"}}`，**只支援 text 區塊**。agy 不會自動把文字裡的 `@path` 圖片附上，模型會改用 `view_file` 開啟。`--model gemini-3.1-pro-high` 這種 id 形式可用 | 移除 pty、`pywinpty`、ANSI 清理與答案標記；prompt 經 stdin；圖片與音訊一律列絕對路徑並要求 `view_file` 開啟 |
| S2 | ✅ agy 讀工作目錄的 `.agents/mcp_config.json`（`{"mcpServers":{name:{command,args}}}`），工具呼叫在事件中是 `call_mcp_tool`；agy 的環境變數會傳給 MCP 子行程 | agy 採路線 (a)：在 agent 工作目錄寫 `.agents/mcp_config.json`，session manifest 以 `--session` 參數傳 |
| S3 | ✅ agy、codex、Claude 都把 MCP image content 交給模型（答對顏色） | `get_frames` 直接回傳 image content |
| S4 | ✅ agy `--json-schema`、codex `--output-schema` 皆接受含 nested object array 的 strict schema，輸出位於 `structured_output` / 最後一則 `agent_message` | 依設計 |
| S5 | ✅ agy `--conversation <id>`（同 id 續接、記得前文）、codex `exec resume <thread_id>`（重帶 `-c mcp_servers.*` 與 `--output-schema`，記得前文） | 依設計 |
| S6 | ✅ agy 事件流有 `view_file` 對音檔絕對路徑的 `DONE` step | 聽音驗證改用事件流，不再讀 agy 內部 transcript |
| S7 | ✅ 目前 yt-dlp 不需 patch 即可抓 Bilibili 1080p | 刪除 monkey-patch |
| S8 | ✅ agy 六份、codex 兩份原始事件已錄製為 fixture | 契約測試 |
| — | ✅ Claude（登入後補驗，SDK 0.2.135 內建 CLI）：`output_format` 經 `StructuredOutput` 工具回到 `ResultMessage.structured_output`、MCP 圖片、`resume=` 皆通過，fixture 為真實 SDK 訊息。codex 的 MCP server **不會**繼承父行程環境變數 | session manifest 一律用 `--session` 參數傳，不用環境變數 |
| — | agy 在沒有呼叫 `finish` 的回合，`result.structured_output` 仍是上一回合的舊值 | adapter 只在同回合出現 `finish` step 時採用 structured output；修復提示不得叫 agent「不要呼叫工具」 |

---

## 20. 審核紀錄

### v1 → v2：Fable 5.1 審核（2026-10-10）

審核結論為 needs rework，所有 blocker 與 major 已處理：

| 等級 | 問題 | 處理 |
|---|---|---|
| Blocker | 階段順序倒置：階段 3 刪 `services/inference` 時呼叫端還沒移植，階段 4 刪 `project.py`/`settings.py` 時領域模組仍在 import | §18 改為新舊並存、逐 stage 移植，最後一個 import 消失才刪；`StageKey` 移到 `core` 讓 layout 不依賴 registry |
| Blocker | 分層契約與目錄樹矛盾（`project` → `sources`、`package` 需要 naming、同層互相依賴、`agents` → `agent_tools`） | §5.1 改為具體分層順序並逐條核對依賴；ID 解析、MAX_PATH、tool manifest 移到 `core` |
| Blocker | `resume(session_id, prompt)` 不可行：codex `exec resume` 沒有 `--cd`/`--add-dir`，三家 resume 都要重帶參數 | §6.4 改為 `resume(previous, task, spec, mcp, prompt)`，補上 codex 以行程 cwd 處理 |
| Major | 現行 `PrePassResult` 的 `dict[str, str]` 欄位無法用 strict schema 表達 | §6.8 Briefing 改 `TermMapping` list，列出連帶 prompt 修改，§2 非目標加註例外 |
| Major | agy stream-json 輸入模式錯誤只警告不退出；model id 內嵌 effort；S1 未涵蓋圖片 | §6.4 錯誤處理與 effort 組法、S1 擴充、API key 清除寫入不變式 |
| Major | pyproject 沒有 build-system，scripts 與 `-m` 啟動都不會生效 | §15.1 加 hatchling 與 `tool.uv.package` |
| Major | ruff 設定誤報：中文全形標點、延遲 import、Typer 註解、整個 subprocess 被禁 | §15.2 加 allowed-confusables、per-file-ignores、只禁啟動行程的函式 |
| Major | `StageDef` 沒有宣告輸出，reset 與「只寫自己的東西」無從檢查；delivery 權重與 on_skip 無處放 | §9.1 加 `outputs`、`on_skip`、`DELIVERY` |
| Major | agent CLI 會載入使用者自己的設定與 MCP | §6.4 codex `--ignore-user-config`、Claude `setting_sources=[]` + `strict_mcp_config` |
| Major | semaphore 與 side task 的計時、修復回合的逾時語意未定 | §6.7 規則化 |
| Major | 靜默遺漏的行為：BOM、date research 暫存 cwd、官方 CC 擁有者、遷移範圍、instructions 串接方式 | §3、§6.2、§10.1、§10.5 補齊 |
| Minor | 統計數字、`translate/media.py` 撞名、format 與搬移順序、過渡期 check 定義、pytest markers、session 檔案過多、未編號目錄、ACP 臆測段落 | 均已修正 |

刻意未採納：

- 審核者建議刪除 `inserts.when`：保留，因為使用者明確要求把 judge 泛化為可選機制，`when` 是讓它不綁死 remix 的最小參數。
