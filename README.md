# Owarai GrillMaster

下載日本綜藝節目，生成繁體中文 SRT / ASS 字幕方便個人使用識讀

![](/doc/image2.jpg)
![](/doc/image3.png)
![](/doc/image1.png)

## 說明

- 目標是 one shot 即可直接觀看，不想校準 (避免被暴雷)
- 1 小時左右的影片成本大概 $20 台幣 (ASR $6 + 翻譯 $14)，處理時間約 15 分鐘，如果使用訂閱方是那就只有 ASR 成本
- 設定偏好都是個人主觀，如需修改請自行 fork
- 更詳細請[查看心得](/article.md)

## 工具

經過各種嘗試，API、自架等組合後，覺得以下方式最合適

### ASR

`ElevenLabs Scribe v2` 日文辨識效果穩定，尤其在一堆人大聲喧嘩，或者裝傻吐槽之間無間隔狀況都能分析出來。

### 翻譯

測試多種模型還是 `Gemini 3 Flash` 的潤飾最能抓住日本綜藝的韻味 (Pro 更好，但成本...)，加上圖片音檔的理解真的很好，但 `Gemini 3 Flash` 的輸出常常會漏 Index 或弄錯時間軸，所以如果驗證錯誤，會交給 agent (Codex/Claude) 驗證修正結構

進行**兩階段翻譯**：

1. **Pre-pass**：完整 SRT + 節目資訊 + 完整音檔 + 少量全片代表圖片，輸出：人物對照、專有名詞/ASR 修正 dict、梗的固定譯法、整體語氣、每段局部摘要
2. **併發翻譯**：SRT 按字元數平均切塊，每塊配上 pre-pass 簡報 + 局部摘要 + 該段音檔切片 + 該段的代表圖片，平行送出翻譯
3. **組裝**：每塊輸出驗證 index/timecode 連續性，block 數相同時本地快速重對齊，否則交給 agent (Codex/Claude) 自我驗證修正，再拼接寫檔

不只聽音訊，也會參考影片抽出的圖片，幫助辨識人物、場景、道具與畫面上的提示文字
![](doc/image4.jpg)

另外，翻譯過程的 chunk / pre-pass 資源與回應會保留在專案資料夾中，方便失敗後直接 resume，不用每次都重切音訊、重抽圖、重跑整個翻譯

## 流程

```
Video ID
    ↓
下載影片 (yt-dlp)
    ↓
合併影片 (FFmpeg)
    ↓
提取音檔 (FFmpeg, mono 16kHz opus 編碼，輸出 .ogg)
    ↓
語音辨識 (ElevenLabs Scribe v2)
    ↓
產生 SRT 字幕
    ↓
Pre-pass 分析 (Gemini: 全片簡報，定調人物/專名/語氣/分段摘要)
    ↓
併發 chunk 翻譯 (Gemini: 分塊平行翻譯 → 組裝驗證修正)
    ↓
潤飾字幕 (agent, 可選)
    ↓
固定詞彙校對 (agent)
    ↓
Finalize：格式清理，輸出 ASS (套樣式) + SRT
    ↓
歸檔 (可選)
    ↓
封裝交付 (可選：字幕燒錄進影片)
```

## 安裝

### 前置需求

- Python 3.13+
- FFmpeg (自行安裝並加入 PATH)
- uv (推薦) 或 pip

### 安裝步驟

```bash
# 使用 uv
uv sync

# 或使用 pip
pip install -e .
```

## 使用方式

### 方式一：加入 PATH

將 `scripts/` 資料夾加到系統 PATH，然後執行：

```bash
grill <SOURCE> [TRANSLATION_HINT]
```

### 方式二：直接執行

```bash
python main.py <SOURCE> [TRANSLATION_HINT]
```

- `SOURCE`: 影片 ID 或完整 URL
- `TRANSLATION_HINT`: 可選，提供給翻譯用的提示，通常是 bilibili 只有隱晦標題的需要

### 範例

```bash
# 使用影片標題作為翻譯提示
grill BV18KBJBeEmV

# 自訂翻譯提示
grill BV1CakEBaEJp "華大千鳥 - 全力100萬 - 間諜 1/7"

# 使用完整 URL
grill "https://www.bilibili.com/video/BV18KBJBeEmV"
```

## 環境變數

建立 `.env` 檔案：

```env
# ElevenLabs Speech to Text
ELEVENLABS_API_KEY=xxx
ELEVENLABS_STT_MODEL=scribe_v2
ELEVENLABS_STT_LANGUAGE_CODE=jpn

# Agent / 模型 backends（每個階段可獨立選 backend + model；gemini-cli/claude/codex 走訂閱制省
#   API 費用；claude/codex 無法吃音訊，只用影格+字幕）。AGENT_GEMINI_API_KEY 只在某階段用
#   gemini-api 時才需要。*_MODEL 寫成 "model" 或 "model/effort"（effort 為 low/medium/high，
#   省略則預設 high），會自動拆成 model + reasoning_effort。
AGENT_GEMINI_API_KEY=xxx

AGENT_PREPASS_BACKEND=gemini-api               # gemini-api / gemini-cli / claude / codex
AGENT_PREPASS_MODEL=gemini-3.1-pro-preview     # "model" 或 "model/effort"（如 claude-opus-4-8/high）
AGENT_CHUNK_BACKEND=gemini-api                 # gemini-api / gemini-cli / claude / codex
AGENT_CHUNK_MODEL=gemini-3-flash-preview       # "model" 或 "model/effort"
AGENT_POSTPROCESS_BACKEND=codex                # 後處理（refine/glossary/chunk 結構修正）：codex 或 claude；封面固定用 codex
AGENT_POSTPROCESS_MODEL=gpt-5.5/medium         # "model" 或 "model/effort"

# 可選：pre-pass 圖片抽樣與固定譯名表
PREPASS_FRAME_INTERVAL_SECONDS=120     # pre-pass 全片圖片抽樣頻率（每幾秒一張，另外固定包含影片首尾幀）
ENABLE_PREPASS_FULL_FIXED_GLOSSARY=false  # 固定譯名表整份帶入 pre-pass（false=只帶比對到的）
VIDEO_FRAME_MAX_SIDE=768               # 影片抽幀最長邊尺寸（pre-pass、chunk 與 agent 隨選抽幀工具共用）

# 可選：chunk 切塊與圖片抽樣
CHUNK_CHAR_LIMIT=6000                  # 每塊目標字元數 (約 5 分鐘字幕)
CHUNK_API_CONCURRENCY=10               # chunk 併發上限（gemini-api 網路請求，可開高）
CHUNK_AGENT_CONCURRENCY=3              # chunk 併發上限（agent：gemini-cli/claude/codex 本機子行程，故較低）
CHUNK_MAX_RETRIES=3                    # chunk 失敗重試次數
CHUNK_FRAME_INTERVAL_SECONDS=30        # chunk 圖片抽樣頻率（每幾秒一張，另外固定包含每段首尾幀）
CHUNK_MISSING_BLOCK_TOLERANCE=2        # 每塊允許未對齊/缺漏字幕區塊數上限

# 可選：後處理開關（codex 需安裝 Codex CLI；claude 用本機 Claude 訂閱）
ENABLE_POSTPROCESS_REFINE=true            # 翻譯後再用 agent 潤飾繁中字幕
ENABLE_POSTPROCESS_GLOSSARY_CHECK=true    # 潤飾後再用 agent 校對殘留的英文/假名專名
ENABLE_COVER_GENERATION=true              # 下載後並行 Codex 風格化封面圖

# 可選：下載/歸檔/封裝
COOKIES_TXT_PATH=cookies.txt       # 影片來源網站 cookies (供 yt-dlp 使用)
ARCHIVED_PATH=NAS:\video\ai\     # 歸檔路徑 - 處理完直接移至指定資料夾並將資料夾名稱改為影片名稱
PACKAGE_PATH=NAS:\video\package\ # 封裝路徑 - 將 ASS 字幕燒錄進影片並複製封面到 <package_path>/<id>_<name>/
```

## 專案結構

```
projects/{video_id}/
├── project.json              # 專案狀態
├── video.mp4                 # 合併後的影片
├── video.ja.srt              # 日文原文字幕
├── .asr/                     # ASR 音檔與 ElevenLabs 原始結果
│   ├── audio.ogg
│   └── asr.json
├── .pre_pass/                # Gemini pre-pass 簡報與圖片快取
│   └── pre_pass.json
├── .chunks/                  # chunk 音檔 / 圖片 / 翻譯回應快取（供 resume）
├── .refine/                  # Agent 潤飾報告（可選）
├── .glossary_check/          # Agent 固定詞彙校對報告（可選）
├── poster.jpg                # yt-dlp 取得的原始封面
├── poster.cover.png          # Agent 風格化封面（可選）
├── video.cht.srt             # 繁體中文翻譯字幕
├── video.cht.refined.srt     # Agent 潤飾後字幕（可選）
├── video.cht.glossary_checked.srt  # Agent 固定詞彙校對後字幕（可選）
├── video.cht.finalized.srt   # 最終 SRT（標點清理，給不支援 ASS 的裝置）
└── video.cht.ass             # 最終 ASS（套樣式 + 標點清理）
```
