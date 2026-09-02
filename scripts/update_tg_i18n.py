import json
from pathlib import Path

translations_dir = Path("translations")

static_translations = {
    "Telegram 贴纸下载器 - 使用说明与高级配置教程": {
        "en": "Telegram Sticker Downloader - User Guide and Advanced Configuration",
        "zh_TW": "Telegram 貼圖下載器 - 使用說明與進階設定教學",
        "ja": "Telegram ステッカーダウンローダー - 使用説明と高度な設定",
    },
    "📘 贴纸链接获取": {
        "en": "📘 Get Sticker Links",
        "zh_TW": "📘 取得貼圖連結",
        "ja": "📘 ステッカーリンクの取得",
    },
    "☁️ Cloudflare 代理教程": {
        "en": "☁️ Cloudflare Proxy Guide",
        "zh_TW": "☁️ Cloudflare 代理教學",
        "ja": "☁️ Cloudflare プロキシガイド",
    },
    "🤖 Bot Token 说明": {
        "en": "🤖 Bot Token Guide",
        "zh_TW": "🤖 Bot Token 說明",
        "ja": "🤖 Bot Token 說明",
    },
    "📋 复制上方 Worker 部署代码": {
        "en": "📋 Copy Worker Deployment Code",
        "zh_TW": "📋 複製上方 Worker 部署程式碼",
        "ja": "📋 上の Worker デプロイコードをコピー",
    },
    "关闭": {"en": "Close", "zh_TW": "關閉", "ja": "閉じる"},
    "复制成功": {"en": "Copied Successfully", "zh_TW": "複製成功", "ja": "コピー成功"},
    "Cloudflare Worker 脚本已成功复制到剪贴板！": {
        "en": "The Cloudflare Worker script was copied to the clipboard!",
        "zh_TW": "Cloudflare Worker 程式碼已成功複製到剪貼簿！",
        "ja": "Cloudflare Worker スクリプトをクリップボードにコピーしました！",
    },
    "返回主面板": {"en": "Back to Main Panel", "zh_TW": "返回主面板", "ja": "メインパネルに戻る"},
    "下载TG贴纸": {"en": "Download TG Stickers", "zh_TW": "下載 TG 貼圖", "ja": "TG ステッカーをダウンロード"},
    "Telegram 贴纸配置": {"en": "Telegram Sticker Configuration", "zh_TW": "Telegram 貼圖設定", "ja": "Telegram ステッカー設定"},
    "贴纸链接或包名，如: animals 或 https://t.me/addstickers/xxx": {
        "en": "Sticker link or pack name, e.g. animals or https://t.me/addstickers/xxx",
        "zh_TW": "貼圖連結或套組名稱，例如：animals 或 https://t.me/addstickers/xxx",
        "ja": "ステッカーリンクまたはパック名（例：animals、https://t.me/addstickers/xxx）",
    },
    "使用帮助与教程": {"en": "Help and Guide", "zh_TW": "使用說明與教學", "ja": "ヘルプとガイド"},
    "贴纸链接:": {"en": "Sticker link:", "zh_TW": "貼圖連結：", "ja": "ステッカーリンク："},
    "请选择贴纸保存路径...": {"en": "Please select a sticker save path...", "zh_TW": "請選擇貼圖儲存路徑...", "ja": "ステッカーの保存先を選択してください..."},
    "浏览...": {"en": "Browse...", "zh_TW": "瀏覽...", "ja": "参照..."},
    "保存路径:": {"en": "Save path:", "zh_TW": "儲存路徑：", "ja": "保存先："},
    "PNG (静态图片 / 动图首帧)": {"en": "PNG (Static image / first frame)", "zh_TW": "PNG（靜態圖片／動圖首幀）", "ja": "PNG（静止画／アニメーションの最初のフレーム）"},
    "GIF (动图 / 视频贴纸)": {"en": "GIF (Animated / video sticker)", "zh_TW": "GIF（動圖／影片貼圖）", "ja": "GIF（アニメーション／動画ステッカー）"},
    "原始格式 (WebP / TGS / WebM)": {"en": "Original format (WebP / TGS / WebM)", "zh_TW": "原始格式（WebP／TGS／WebM）", "ja": "元の形式（WebP／TGS／WebM）"},
    "智能适配 (自动匹配最佳格式)": {"en": "Smart (Automatically select the best format)", "zh_TW": "智慧適配（自動選擇最佳格式）", "ja": "スマート（最適な形式を自動選択）"},
    "导出格式:": {"en": "Export format:", "zh_TW": "匯出格式：", "ja": "エクスポート形式："},
    "导出完成后打包为 ZIP 压缩文件": {"en": "Package as a ZIP archive after export", "zh_TW": "匯出完成後打包為 ZIP 壓縮檔", "ja": "エクスポート後に ZIP 圧縮ファイルにまとめる"},
    "ZIP 打包:": {"en": "ZIP archive:", "zh_TW": "ZIP 打包：", "ja": "ZIP 圧縮："},
    "▶ 展开高级设置 (Token / 网络代理)": {"en": "▶ Expand Advanced Settings (Token / Network Proxy)", "zh_TW": "▶ 展開進階設定（Token／網路代理）", "ja": "▶ 高度な設定を展開（Token／ネットワークプロキシ）"},
    "▼ 收起高级设置 (Token / 网络代理)": {"en": "▼ Collapse Advanced Settings (Token / Network Proxy)", "zh_TW": "▼ 收起進階設定（Token／網路代理）", "ja": "▼ 高度な設定を折りたたむ（Token／ネットワークプロキシ）"},
    "内置默认 Token，可填自定义 Token": {"en": "Built-in default Token; you may enter a custom Token", "zh_TW": "內建預設 Token，可填寫自訂 Token", "ja": "内蔵デフォルト Token（カスタム Token も入力可能）"},
    "👁️ 显示": {"en": "👁️ Show", "zh_TW": "👁️ 顯示", "ja": "👁️ 表示"},
    "🔒 隐藏": {"en": "🔒 Hide", "zh_TW": "🔒 隱藏", "ja": "🔒 非表示"},
    "CF 代理 URL:": {"en": "CF Proxy URL:", "zh_TW": "CF 代理 URL：", "ja": "CF プロキシ URL："},
    "如 http://127.0.0.1:7890 (留空为官方直连/CF路由)": {
        "en": "e.g. http://127.0.0.1:7890 (leave empty for direct/CF routing)",
        "zh_TW": "例如 http://127.0.0.1:7890（留空則使用官方直連／CF 路由）",
        "ja": "例：http://127.0.0.1:7890（空欄で公式直接接続／CF ルーティング）",
    },
    "本地代理:": {"en": "Local proxy:", "zh_TW": "本機代理：", "ja": "ローカルプロキシ："},
    "测试连通性": {"en": "Test Connection", "zh_TW": "測試連線", "ja": "接続テスト"},
    "恢复默认": {"en": "Restore Defaults", "zh_TW": "恢復預設", "ja": "デフォルトに戻す"},
    "操作与提取": {"en": "Actions and Extraction", "zh_TW": "操作與擷取", "ja": "操作と抽出"},
    "解析贴纸包预览": {"en": "Parse Sticker Pack Preview", "zh_TW": "解析貼圖套組預覽", "ja": "ステッカーパックを解析してプレビュー"},
    "导出选中": {"en": "Export Selected", "zh_TW": "匯出所選", "ja": "選択項目をエクスポート"},
    "导出全部": {"en": "Export All", "zh_TW": "匯出全部", "ja": "すべてエクスポート"},
    "入库选中": {"en": "Import Selected", "zh_TW": "匯入所選", "ja": "選択項目をインポート"},
    "入库全部": {"en": "Import All", "zh_TW": "匯入全部", "ja": "すべてインポート"},
    "致谢：基于 <a href=\"https://github.com/Kiowx\" style=\"color: #0078d4; text-decoration: underline;\">Kiowx</a> 的项目二次开发": {
        "en": "Thanks: Based on the project by <a href=\"https://github.com/Kiowx\" style=\"color: #0078d4; text-decoration: underline;\">Kiowx</a>",
        "zh_TW": "致謝：基於 <a href=\"https://github.com/Kiowx\" style=\"color: #0078d4; text-decoration: underline;\">Kiowx</a> 的專案二次開發",
        "ja": "謝辞：<a href=\"https://github.com/Kiowx\" style=\"color: #0078d4; text-decoration: underline;\">Kiowx</a> のプロジェクトを基に開発",
    },
    "贴纸预览区 (未加载)": {"en": "Sticker Preview (Not Loaded)", "zh_TW": "貼圖預覽區（未載入）", "ja": "ステッカープレビュー（未読み込み）"},
    "全选已加载": {"en": "Select All Loaded", "zh_TW": "全選已載入", "ja": "読み込み済みをすべて選択"},
    "清空选择": {"en": "Clear Selection", "zh_TW": "清除選取", "ja": "選択を解除"},
    "反选": {"en": "Invert Selection", "zh_TW": "反向選取", "ja": "選択を反転"},
    "贴纸详细预览": {"en": "Sticker Details", "zh_TW": "貼圖詳細預覽", "ja": "ステッカー詳細プレビュー"},
    "未选中贴纸": {"en": "No sticker selected", "zh_TW": "未選取貼圖", "ja": "ステッカーが選択されていません"},
    "提示": {"en": "Notice", "zh_TW": "提示", "ja": "お知らせ"},
    "错误": {"en": "Error", "zh_TW": "錯誤", "ja": "エラー"},
    "连通性测试": {"en": "Connection Test", "zh_TW": "連線測試", "ja": "接続テスト"},
    "解析失败": {"en": "Parse Failed", "zh_TW": "解析失敗", "ja": "解析失敗"},
    "导出完成": {"en": "Export Complete", "zh_TW": "匯出完成", "ja": "エクスポート完了"},
    "导出出错": {"en": "Export Error", "zh_TW": "匯出錯誤", "ja": "エクスポートエラー"},
    "确认导出选中": {"en": "Confirm Export Selected", "zh_TW": "確認匯出所選", "ja": "選択項目のエクスポートを確認"},
    "确认导出全部": {"en": "Confirm Export All", "zh_TW": "確認匯出全部", "ja": "すべてエクスポートを確認"},
    "导入完成": {"en": "Import Complete", "zh_TW": "匯入完成", "ja": "インポート完了"},
    "导入出错": {"en": "Import Error", "zh_TW": "匯入錯誤", "ja": "インポートエラー"},
    "确认导入选中": {"en": "Confirm Import Selected", "zh_TW": "確認匯入所選", "ja": "選択項目のインポートを確認"},
    "确认导入全部": {"en": "Confirm Import All", "zh_TW": "確認匯入全部", "ja": "すべてインポートを確認"},
}

for lang in ("en", "zh_TW", "ja"):
    path = translations_dir / f"{lang}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for source, values in static_translations.items():
        data[source] = values[lang]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    print(f"updated {path}")
