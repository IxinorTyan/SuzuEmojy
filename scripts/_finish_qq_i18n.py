import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]

entries = {
    "<b>文件名:</b><br/>{name}<br/><br/><b>格式:</b> {format}<br/><b>大小:</b> {size:.2f} KB<br/><br/><b>保存路径:</b><br/>{path}": (
        "<b>File name:</b><br/>{name}<br/><br/><b>Format:</b> {format}<br/><b>Size:</b> {size:.2f} KB<br/><br/><b>Path:</b><br/>{path}",
        "<b>檔案名稱：</b><br/>{name}<br/><br/><b>格式：</b>{format}<br/><b>大小：</b>{size:.2f} KB<br/><br/><b>儲存路徑：</b><br/>{path}",
        "<b>ファイル名：</b><br/>{name}<br/><br/><b>形式：</b>{format}<br/><b>サイズ：</b>{size:.2f} KB<br/><br/><b>保存先：</b><br/>{path}",
    ),
    "⚠️ 在目录 [{path}] 下未找到任何QQ号数据文件夹（纯数字命名且含有nt_qq）": (
        "⚠️ No QQ data folders (numeric names containing nt_qq) found under [{path}]",
        "⚠️ 在 [{path}] 下找不到任何 QQ 號資料夾（須為純數字命名且包含 nt_qq）",
        "⚠️ [{path}] にQQデータフォルダー（数字名かつnt_qqを含む）が見つかりません",
    ),
    "❌ 你还没有选择QQ号呢，请先选择一个QQ号！": (
        "❌ No QQ account selected. Please select an account first!",
        "❌ 尚未選擇 QQ 號，請先選擇一個 QQ 號！",
        "❌ QQアカウントが選択されていません。先に選択してください！",
    ),
    "❌ 你还没有选择保存路径呢，请先选择保存路径！": (
        "❌ No save path selected. Please select one first!",
        "❌ 尚未選擇儲存路徑，請先選擇儲存路徑！",
        "❌ 保存先が選択されていません。先に選択してください！",
    ),
    "❌ 你还没有选择表情分类呢，请先选择一个分类！": (
        "❌ No emoji category selected. Please select one first!",
        "❌ 尚未選擇表情分類，請先選擇一個分類！",
        "❌ 絵文字カテゴリが選択されていません。先に選択してください！",
    ),
    "❌ 导出文件时出错: {error}": (
        "❌ Error exporting files: {error}",
        "❌ 匯出檔案時發生錯誤：{error}",
        "❌ ファイルのエクスポート中にエラーが発生しました：{error}",
    ),
    "❌ 您尚未选择任何表情！请先在右侧预览区选中表情后再导入。": (
        "❌ No emojis selected. Select emojis in the preview area before importing.",
        "❌ 尚未選擇任何表情！請先在右側預覽區選取表情，再進行匯入。",
        "❌ 絵文字が選択されていません。インポートする前にプレビューで選択してください。",
    ),
    "❌ 您尚未选择任何表情！请先在右侧预览区选中表情后再导出。": (
        "❌ No emojis selected. Select emojis in the preview area before exporting.",
        "❌ 尚未選擇任何表情！請先在右側預覽區選取表情，再進行匯出。",
        "❌ 絵文字が選択されていません。エクスポートする前にプレビューで選択してください。",
    ),
    "致谢：基于 <a href=\"https://github.com/VanillaNahida\" style=\"color: #0078d4; text-decoration: underline;\">VanillaNahida</a> 的项目二次开发": (
        "Thanks: Further development based on the project by <a href=\"https://github.com/VanillaNahida\" style=\"color: #0078d4; text-decoration: underline;\">VanillaNahida</a>",
        "致謝：基於 <a href=\"https://github.com/VanillaNahida\" style=\"color: #0078d4; text-decoration: underline;\">VanillaNahida</a> 的專案二次開發",
        "謝辞：<a href=\"https://github.com/VanillaNahida\" style=\"color: #0078d4; text-decoration: underline;\">VanillaNahida</a> のプロジェクトを基に開発",
    ),
    "请先在右侧预览区选中表情后再导入！": (
        "Select emojis in the preview area before importing!",
        "請先在右側預覽區選取表情，再進行匯入！",
        "インポートする前にプレビューで絵文字を選択してください！",
    ),
    "请先在右侧预览区选中表情后再导出！": (
        "Select emojis in the preview area before exporting!",
        "請先在右側預覽區選取表情，再進行匯出！",
        "エクスポートする前にプレビューで絵文字を選択してください！",
    ),
    "💬 开始智能扫描分类 [{folder}] 表情包路径...": (
        "💬 Starting smart scan of emoji paths in category [{folder}]...",
        "💬 開始智慧掃描分類 [{folder}] 的表情包路徑……",
        "💬 カテゴリ [{folder}] の絵文字パスをスマートスキャン中……",
    ),
    "💬 正在加载预览图 {start} - {end} ...": (
        "💬 Loading preview images {start} - {end}...",
        "💬 正在載入預覽圖 {start} - {end}……",
        "💬 プレビュー画像 {start} - {end} を読み込み中……",
    ),
}

for lang, index in (("en", 0), ("zh_TW", 1), ("ja", 2)):
    path = root / "translations" / f"{lang}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update({key: value[index] for key, value in entries.items()})
    path.write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")

print(f"Added {len(entries)} missing QQ translation keys to en, zh_TW, and ja.")
