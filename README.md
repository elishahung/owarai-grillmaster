# Owarai GrillMaster

下載日本綜藝節目，生成繁體中文 SRT / ASS 字幕方便個人使用識讀

![](/doc/tui.png)
![](/doc/image2.jpg)
![](/doc/image3.png)
![](/doc/image1.png)

## 說明

- 目標是 one shot 即可直接觀看，不想校準 (避免被暴雷)
- 1 小時左右的影片成本大概 $20 台幣 (ASR $6 + 翻譯 $14)，處理時間約 15 分鐘，如果使用訂閱方式那就只有 ASR 成本
- 設定偏好都是個人主觀，如需修改請自行 fork
- 更詳細請[查看心得](/article.md)

## 工具

經過各種嘗試，API、自架等組合後，覺得以下方式最合適

- **ASR**：`ElevenLabs Scribe v2`，一堆人大聲喧嘩、裝傻吐槽沒有間隔也能辨識
- **翻譯**：`Gemini 3` 系列最能抓住日本綜藝的韻味，也很會看圖聽音檔；輸出結構出錯時交給 Codex / Claude 修正

翻譯分兩階段：先看完整部片做一份簡報（人物、專有名詞、梗的譯法、語氣），再把字幕切塊平行翻譯。翻譯時會參考音檔和影片截圖，幫助辨識人物、場景與畫面上的文字

![](doc/image4.jpg)

中途失敗可以直接重跑同一個 ID，會從中斷的地方繼續

## 流程

```
下載影片 → 語音辨識 → 全片簡報 → 分塊翻譯 → 潤飾 → 名詞校對 → 輸出 ASS + SRT → 歸檔 / 燒錄字幕（可選）
```

## 安裝

需要 Python 3.13+、FFmpeg（加入 PATH）、uv

```bash
uv sync
```

## 使用方式

將 `scripts/` 加到 PATH 後：

```bash
grill <影片 ID 或 URL> [翻譯提示]
```

翻譯提示可省略；影片標題與說明會自動帶入，翻譯提示是額外補充（例如 bilibili 標題太隱晦時）。已存在的專案在 pre-pass 跑完前仍可補上提示

```bash
grill BV18KBJBeEmV
grill BV1CakEBaEJp "華大千鳥 - 全力100萬 - 間諜 1/7"

# 依序處理多集，前一集的譯名會帶到下一集
grill serial ep100001 ep100002 ep100003

# 重新燒錄已完成的專案
grill package <專案資料夾>
```

## 環境變數

建立 `.env`：

```env
ELEVENLABS_API_KEY=xxx

# 各階段模型，格式為 backend/model[/effort]
# backend：agy、claude、codex（皆為訂閱制；只有 agy 能聽音檔）
AGENT_PREPASS_MODEL=agy/gemini-3.1-pro/high
AGENT_CHUNK_MODEL=agy/gemini-3.1-pro/high
AGENT_POSTPROCESS_MODEL=codex/gpt-6.1-sol/high
AGENT_COMMON_MODEL=codex/gpt-6.1-sol/medium

# 可選功能
ENABLE_COVER_GENERATION=true               # 產生風格化封面
ENABLE_BROADCAST_DATE_AGENT_FALLBACK=true  # 查不到播出日時上網找
ENABLE_PACKAGE_TITLE_SUGGESTION=true       # 燒錄時產生候選標題

# 路徑
COOKIES_TXT_PATH=cookies.txt
ARCHIVED_PATH=NAS:\video\ai\               # 完成後歸檔位置
PACKAGE_PATH=NAS:\video\package\           # 燒錄字幕後的成品位置
```

其他可調參數（切塊大小、併發數、抽圖頻率等）請見 `settings.py`

## 節目設定

`config.json`（不進 git，格式見 `config.example.json`）依節目系列（`series`）或頻道（`channel`）設定規則。下載時會自動為新出現的名稱加上空白項目，之後手動編輯：

- `remix`：`true` 時打包一律用 remix（同 `--remix`）
- `instruction`：給各步驟模型的額外指示，鍵為 `pre_pass`、`translate`、`refine`、`glossary_check`；`common` 會加到上述每個步驟。頻道與系列都有設定時兩者都會帶入

檔案開頭的 `"$schema": "./config.schema.json"` 讓 IDE 檢查格式並補全鍵名（自動建立的 `config.json` 會帶上）。修改設定格式後，用 `uv run python -m services.program_config.schema` 重新產生 schema。

## 輸出

每個專案在 `projects/<影片 ID>/`，主要成品：

- `video.cht.ass`：套好樣式的繁中字幕
- `video.cht.finalized.srt`：給不支援 ASS 的播放器
- `video.ja.srt`：日文原文字幕
