# Raphael｜拉斐爾

讓想法直接成為文字，少一點打字造成的中斷。

Raphael 是以 Windows 為起點的個人語音助理，靈感來自「智慧之王拉斐爾」。第一階段先專注於一件事：在原本正在使用的應用程式中，按下快捷鍵說話，將保留原意、整理過的文字送回輸入位置。

## 專案理念

- **先把聽寫做好。** 可靠的錄音、辨識、取消與貼上，是後續助理功能的基礎。
- **整理表達，保留你的意思。** 減少口頭贅詞、補上標點、採用說話中的自我更正；聽寫中的問題應保留為文字，不應被模型當成提問來回答。
- **留在你的工作流程裡。** 浮層只提供聆聽狀態、處理進度與簡短通知，盡量不搶焦點。
- **逐漸熟悉你的用詞。** 個人詞典與更正學習協助處理人名、專有名詞及慣用寫法。
- **讓資料與費用有跡可循。** 明確區分本機與雲端處理，提供歷史、使用量與預算設定。

這是非官方的粉絲個人專案，與原作權利人無關。儲存庫不提供動畫素材、私人圖案、原始錄音或個人語音模型。

## 目前可以做什麼

目前的里程碑是 Windows 上的第一階段聽寫流程：

- 預設使用右 Alt 開始／結束聽寫，也可改成按住說話；Esc 取消。
- 本機或雲端語音辨識、文字整理，以及送回原輸入位置的貼上流程。
- 透明浮層顯示《 0 % 》到《 100 % 》的處理進度，完成貼上後才結束動畫。
- 歷史紀錄、個人詞典、常見詞語建議，以及從貼上後更正中學習詞語。
- 自訂快捷鍵、語言、麥克風、模型與預算設定。

更正學習會觀察這次成功貼上的輸入欄位，最多 60 秒；送出、離開欄位或觀察結束時才整理可確認的詞語更正。密碼欄位與無法安全讀取的欄位會跳過；整句重寫不等於新增詞語。「常見詞語建議」則來自重複出現的用詞，與更正學習是不同機制。不同編輯器的 UI Automation 支援程度仍會影響學習效果。

對話、語音回覆、喚醒詞、操作其他應用程式與跨平台支援屬於後續方向，目前未包含在這個里程碑中。

## 架構

```text
TypeScript UI / overlay
        |
Tauri commands and events
        |
Rust desktop shell
        |
JSON-RPC over stdio
        |
Python core
    +-- Brain: text cleanup, dictionary, history, budget
    +-- Device: audio capture, VAD, speech recognition
```

| 層級 | 責任 |
| --- | --- |
| `app/src` | 主視窗、設定、詞典、歷史與浮層呈現。 |
| `app/src-tauri` | Windows 快捷鍵、視窗、焦點、剪貼簿、貼上、更正觀察與核心程序生命週期。 |
| `core/src/raphael_core` | 以 App 組裝 Brain 與 Device；處理辨識、整理、資料儲存與費用計算。 |
| `eval` | 可重複使用的聽寫提示與文字整理評估案例。 |

核心的 stdout 專供 JSON-RPC，診斷訊息走 stderr。桌面層管理程序重啟、呼叫期限與 session 狀態，避免取消或晚到的回覆污染後續聽寫。

## 資料與隱私

- OpenRouter 金鑰透過 App 設定，儲存在 Windows Credential Manager；不需要建立 `.env`。
- 設定、詞典與聽寫歷史儲存在本機使用者資料目錄，歷史與詞典使用 SQLite。
- 雲端辨識會將錄音與詞典提示傳給選定的服務商；本機辨識使用下載到電腦的語音模型。
- **本機辨識不代表全程離線。** 目前有可用金鑰且未達預算上限時，程式會自動透過 OpenRouter 整理文字，傳送辨識結果、詞典提示／替換規則、語言設定與目標程式名稱；目前沒有獨立的整理開關。沒有可用金鑰或達到上限時，則保留未經雲端整理的文字。
- 模型、私人素材、錄音、使用者資料、憑證及建置產物皆不應提交到 Git。沒有私人素材時，程式使用儲存庫內的公開備用圖案。

## 開發

需要 Windows x64、Node.js 22（至少 22.12）、Rust 1.98、uv，以及 `core/.python-version` 指定的 Python 3.12.14。Windows 建置另需 MSVC C++ build tools 與 WebView2 runtime。

在專案根目錄執行：

```powershell
cd core
uv sync --locked --dev
cd ../app
npm ci
npm run tauri -- dev
```

第一次啟動請完成引導；後續啟動主要留在系統匣，可由系統匣開啟主視窗。模型初次使用需要下載。

在 `app/` 建置開發用執行檔：

```powershell
node scripts/tauri.mjs build --debug --no-bundle -- --locked
```

目前 Python 核心仍從原始碼 checkout 啟動，此指令不會產生可獨立安裝的完整安裝程式。私人圖案設定請參考[本機素材說明](docs/private-icons.md)。

## 品質檢查

GitHub Actions 會執行三組 Windows CI。檢查範圍如下：

| 範圍 | 檢查 |
| --- | --- |
| 前端 | Vitest、TypeScript strict／未使用程式碼檢查、Vite build、既有文字配色對比檢查。 |
| Python | 完整預設 pytest suite，warnings 視為錯誤；測試使用替身服務與隔離 keyring。 |
| Rust／桌面 | rustfmt、所有 targets 的 cargo check 與 Clippy、單元測試、patched keyboard listener 測試、公開素材的 Tauri 執行檔建置。 |

目前沒有另設 ESLint 或 Python 專用 linter。需要下載模型、實體鍵盤或真實桌面的測試，與預設 CI 分開執行。測試通過表示上述範圍已驗證，仍需在實際麥克風、IME、編輯器與螢幕環境驗收。

詳細指令與限制見 [CI 說明](docs/ci.md)、[Windows 手動驗收](docs/windows-manual-checks.md)及[第一階段驗證紀錄](docs/phase1-validation.md)。語音與模型比較工具見[評估工具說明](eval/README.md)。
