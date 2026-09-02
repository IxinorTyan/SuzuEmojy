import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
help_zh = (
    "使用帮助：\n\n"
    "1. 使用时请确保已登录过QQ并加载过全部表情包\n"
    "2. 提取的时候会自动创建以QQ号开头的文件夹\n"
    "3. 选择一个账号后，点击'扫描表情包预览'获取表情包，然后选择表情，最后'导出选中表情'或'导出全部表情'。\n"
    "4. 导出的表情包比账号内实际的表情包要多属正常现象，因为QQ会缓存一些表情包\n\n"
    "  注意：如果没有找到任何用户，请确保QQ已经在本地登录过。并确保路径正确\n"
    "  可以尝试手动指定聊天数据文件夹的所在位置"
)
help_en = (
    "Help:\n\n"
    "1. Make sure you have logged in to QQ and loaded all emoji packs.\n"
    "2. Folders starting with the QQ number will be created automatically during extraction.\n"
    "3. Select an account, click 'Scan Emoji Preview', select emojis, then click 'Export Selected' or 'Export All'.\n"
    "4. It is normal to find more emojis than are currently shown in the account because QQ caches some packs.\n\n"
    "Note: If no users are found, make sure QQ has been logged in locally and the path is correct.\n"
    "You can also select the chat data directory manually."
)
help_tw = (
    "使用說明：\n\n"
    "1. 請確認已登入 QQ 並載入所有表情包。\n"
    "2. 擷取時會自動建立以 QQ 號碼開頭的資料夾。\n"
    "3. 選擇帳號後按下「掃描表情包預覽」，選取表情，再按下「匯出所選」或「匯出全部」。\n"
    "4. 擷取到的表情包多於帳號目前顯示的數量屬正常情況，因為 QQ 會快取部分表情包。\n\n"
    "注意：若找不到任何使用者，請確認 QQ 曾在本機登入且路徑正確。\n"
    "也可以手動指定聊天資料資料夾。"
)
help_ja = (
    "ヘルプ：\n\n"
    "1. QQにログインし、すべての絵文字パックを読み込んでください。\n"
    "2. 抽出時にはQQ番号で始まるフォルダーが自動的に作成されます。\n"
    "3. アカウントを選択し、「絵文字をスキャンしてプレビュー」をクリックしてから絵文字を選択し、「選択項目をエクスポート」または「すべてエクスポート」をクリックします。\n"
    "4. QQが一部のパックをキャッシュするため、アカウントに表示される数より多く見つかる場合があります。\n\n"
    "ユーザーが見つからない場合は、QQにローカルでログイン済みで、パスが正しいことを確認してください。\n"
    "チャットデータディレクトリを手動で指定することもできます。"
)

entries = {
"扫描QQ文件": ("Scan QQ Files","掃描 QQ 檔案","QQファイルをスキャン"), "QQ 数据配置": ("QQ Data Configuration","QQ 資料設定","QQデータ設定"),
"自动定位中，或手动选择...": ("Locating automatically, or select manually...","正在自動定位，或手動選擇...","自動検索中、または手動で選択..."), "定位目录": ("Locate Directory","定位資料夾","ディレクトリを指定"),
"数据路径:": ("Data Path:","資料路徑：","データパス："), "请选择表情包保存路径...": ("Please select an emoji save path...","請選擇表情包儲存路徑...","絵文字の保存先を選択..."),
"使用帮助": ("Help","使用說明","ヘルプ"), "选择账号:": ("Select Account:","選擇帳號：","アカウントを選択："), "选择分类:": ("Select Category:","選擇分類：","カテゴリを選択："),
"扫描表情包预览": ("Scan Emoji Preview","掃描表情包預覽","絵文字をスキャンしてプレビュー"), "表情包预览区": ("Emoji Preview","表情包預覽區","絵文字プレビュー"),
"表情详细预览": ("Emoji Details","表情詳細預覽","絵文字の詳細"), "表情包保存路径": ("Emoji Save Path","表情包儲存路徑","絵文字の保存先"),
"💬 QQNT表情包批量提取工具启动成功": ("💬 QQNT batch emoji extractor is ready","💬 QQNT 表情包批次擷取工具已啟動","💬 QQNT絵文字一括抽出ツールの準備が完了しました"),
"💡建议在使用前提前打开要提取表情包的账户，随便选择一个聊天窗口，将表情全部加载出来，这样提取的表情包更齐全。": ("💡 For best results, open the target QQ account and load all emojis in a chat before scanning.","💡 建議使用前先開啟目標 QQ 帳號，並在聊天視窗載入全部表情，這樣擷取結果會更完整。","💡 スキャン前に対象のQQアカウントでチャットを開き、絵文字をすべて読み込むと、より完全に抽出できます。"),
"个人表情 (personal_emoji)": ("Personal Emojis (personal_emoji)","個人表情（personal_emoji）","個人絵文字（personal_emoji）"), "接收到表情[谨慎加载,内含巨量表情] (emoji-recv)": ("Received Emojis [Caution: huge number] (emoji-recv)","接收的表情［請謹慎載入，內含大量表情］（emoji-recv）","受信した絵文字［大量に含まれるため注意］（emoji-recv）"),
"商店表情 (marketface)": ("Store Emojis (marketface)","商店表情（marketface）","ショップ絵文字（marketface）"), "候选表情[打字时系统推荐] (emoji-related)": ("Suggested Emojis [recommended while typing] (emoji-related)","候選表情［輸入時由系統推薦］（emoji-related）","候補絵文字［入力時にシステムが推薦］（emoji-related）"),
"系统表情[已支持APNG动图转GIF导出] (BaseEmojiSyastems)": ("System Emojis [APNG to GIF export supported] (BaseEmojiSyastems)","系統表情［支援 APNG 動圖轉 GIF 匯出］（BaseEmojiSyastems）","システム絵文字［APNGからGIFへの変換に対応］（BaseEmojiSyastems）"),
"{folder} (其他分类)": ("{folder} (Other Category)","{folder}（其他分類）","{folder}（その他のカテゴリ）"), " (其他分类)": (" (Other Category)","（其他分類）","（その他のカテゴリ）"),
"💬 请选择表情包保存路径": ("💬 Please select a path for saving emojis","💬 請選擇表情包儲存路徑","💬 絵文字の保存先を選択してください"), "选择QQ聊天记录所在目录（即包含QQ号数字文件夹的 Tencent Files 目录）": ("Select the QQ chat data directory (Tencent Files containing numeric QQ folders)","選擇 QQ 聊天資料資料夾（包含 QQ 號數字資料夾的 Tencent Files）","QQチャットデータのディレクトリを選択（数字のQQフォルダーを含むTencent Files）"),
"✅ 已选择数据目录: {path}": ("✅ Data directory selected: {path}","✅ 已選擇資料目錄：{path}","✅ データディレクトリを選択しました：{path}"), "💬 取消选择数据目录": ("💬 Data directory selection cancelled","💬 已取消選擇資料目錄","💬 データディレクトリの選択をキャンセルしました"),
"✅ 成功加载了 {count} 个QQ用户文件夹": ("✅ Loaded {count} QQ user folders","✅ 已載入 {count} 個 QQ 使用者資料夾","✅ {count} 個のQQユーザーフォルダーを読み込みました"), "✅ 扫描并筛选完毕，共发现 {count} 个有效表情图片。": ("✅ Scan complete: found {count} valid emoji images.","✅ 掃描完成，共找到 {count} 個有效表情圖片。","✅ スキャン完了：有効な絵文字画像 {count} 件が見つかりました。"),
"✅ 已全选当前加载的 {count} 个表情": ("✅ Selected all {count} loaded emojis","✅ 已全選目前載入的 {count} 個表情","✅ 読み込み済みの絵文字 {count} 件をすべて選択しました"),
"✅ 已加载表情预览：{loaded}/{total}": ("✅ Emoji preview loaded: {loaded}/{total}","✅ 已載入表情預覽：{loaded}/{total}","✅ 絵文字プレビューを読み込みました：{loaded}/{total}"),
"✅ 已清空当前的选择": ("✅ Current selection cleared","✅ 已清除目前選取","✅ 現在の選択を解除しました"), "文件不存在": ("File does not exist","檔案不存在","ファイルが存在しません"), "未知": ("Unknown","未知","不明"),
"APNG (动态图片)": ("APNG (Animated Image)","APNG（動態圖片）","APNG（アニメーション画像）"), "图片加载失败": ("Failed to load image","圖片載入失敗","画像の読み込みに失敗しました"),
"❌ 未找到QQ数据路径": ("❌ QQ data path not found","❌ 找不到 QQ 資料路徑","❌ QQデータパスが見つかりません"), "提示": ("Notice","提示","お知らせ"), "警告": ("Warning","警告","警告"), "错误": ("Error","錯誤","エラー"), "完成": ("Complete","完成","完了"),
"未选中表情": ("No emoji selected","未選取表情","絵文字が選択されていません"), "💬 用户取消了导出操作": ("💬 Export cancelled by user","💬 使用者取消了匯出操作","💬 ユーザーがエクスポートをキャンセルしました"), "💬 用户取消了导入操作": ("💬 Import cancelled by user","💬 使用者取消了匯入操作","💬 ユーザーがインポートをキャンセルしました"),
"导出选中": ("Export Selected","匯出所選","選択項目をエクスポート"), "导出全部": ("Export All","匯出全部","すべてエクスポート"), "入库选中": ("Import Selected","匯入所選","選択項目をインポート"), "入库全部": ("Import All","匯入全部","すべてインポート"),
"确认导出选中": ("Confirm Export Selected","確認匯出所選","選択項目のエクスポートを確認"), "确认导出全部": ("Confirm Export All","確認匯出全部","すべてエクスポートを確認"), "确认导入选中": ("Confirm Import Selected","確認匯入所選","選択項目のインポートを確認"), "确认导入全部": ("Confirm Import All","確認匯入全部","すべてインポートを確認"),
"❌ 该表情分类下未发现任何有效的图片文件，无法导出！": ("❌ No valid image files found in this category; cannot export.","❌ 此分類找不到有效圖片檔案，無法匯出！","❌ このカテゴリに有効な画像がないため、エクスポートできません。"),
"❌ 该表情分类下未发现任何有效的图片文件，无法导入！": ("❌ No valid image files found in this category; cannot import.","❌ 此分類找不到有效圖片檔案，無法匯入！","❌ このカテゴリに有効な画像がないため、インポートできません。"),
"该分类下未发现任何有效的表情图片文件！": ("No valid emoji image files found in this category!","此分類找不到有效的表情圖片檔案！","このカテゴリに有効な絵文字画像が見つかりません！"),
"❌ 未筛选出任何有效的表情包图片": ("❌ No valid emoji images found","❌ 找不到有效的表情包圖片","❌ 有効な絵文字画像が見つかりません"),
"确定导出当前选中的 {count} 个表情？": ("Export the {count} selected emojis?","要匯出目前選取的 {count} 個表情嗎？","選択した絵文字 {count} 件をエクスポートしますか？"),
"确定将当前选中的 {count} 个表情导入到资源库？（会经过自动清洗和去重过滤）": ("Import the {count} selected emojis? They will be cleaned and deduplicated automatically.","要將目前選取的 {count} 個表情匯入資源庫嗎？（將自動清理並去重）","選択した絵文字 {count} 件をインポートしますか？自動的に整理・重複排除されます。"),
"当前不管界面是否完全加载，将直接导出扫描到的该分类下所有 {count} 个表情？": ("Export all {count} scanned emojis in this category, regardless of preview loading?","不論介面是否完全載入，都要匯出此分類掃描到的全部 {count} 個表情嗎？","プレビューの読み込み状況に関係なく、このカテゴリの絵文字 {count} 件をすべてエクスポートしますか？"),
"当前不管界面是否完全加载，将直接导入扫描到的该分类下所有 {count} 个表情到资源库？（自动清洗和去重）": ("Import all {count} scanned emojis in this category? They will be cleaned and deduplicated automatically.","不論介面是否完全載入，都要將此分類掃描到的全部 {count} 個表情匯入資源庫嗎？（將自動清理並去重）","このカテゴリの絵文字 {count} 件をすべてインポートしますか？自動的に整理・重複排除されます。"),
"✅ 成功导出 {count} 个表情文件！": ("✅ Successfully exported {count} emoji files!","✅ 成功匯出 {count} 個表情檔案！","✅ 絵文字ファイルを {count} 件エクスポートしました！"), "全部提取成功！": ("All emojis extracted successfully!","全部擷取成功！","すべての絵文字を抽出しました！"), "选中表情提取成功！": ("Selected emojis extracted successfully!","所選表情擷取成功！","選択した絵文字を抽出しました！"),
"导出 [{done}/{total}]: {source} -> {destination}": ("Export [{done}/{total}]: {source} -> {destination}","匯出 [{done}/{total}]：{source} → {destination}","エクスポート [{done}/{total}]：{source} → {destination}"), "导出(APNG转GIF) [{done}/{total}]: {source} -> {destination}": ("Export (APNG to GIF) [{done}/{total}]: {source} -> {destination}","匯出（APNG 轉 GIF）[{done}/{total}]：{source} → {destination}","エクスポート（APNGからGIF）[{done}/{total}]：{source} → {destination}"), "导出(回退PNG) [{done}/{total}]: {source} -> {destination}": ("Export (fallback PNG) [{done}/{total}]: {source} -> {destination}","匯出（退回 PNG）[{done}/{total}]：{source} → {destination}","エクスポート（PNGにフォールバック）[{done}/{total}]：{source} → {destination}"),
"导入 [{done}/{total}]: {filename} -> {category} {duplicate}": ("Import [{done}/{total}]: {filename} -> {category} {duplicate}","匯入 [{done}/{total}]：{filename} → {category} {duplicate}","インポート [{done}/{total}]：{filename} → {category} {duplicate}"), "(重复已被合并)": ("(duplicate merged)","（重複已合併）","（重複を統合済み）"),
"✅ 导入完成！成功导入并分类 {imported} 个表情，其中 {duplicated} 个重复已被合并过滤，失败 {failed} 个。": ("✅ Import complete! Added {imported}, merged {duplicated} duplicates, {failed} failed.","✅ 匯入完成！成功加入 {imported} 個表情，合併 {duplicated} 個重複項目，失敗 {failed} 個。","✅ インポート完了！{imported} 件を追加、重複 {duplicated} 件を統合、{failed} 件が失敗しました。"),
"表情导入成功！\n分类: {category}\n共导入并去重处理: {count} 个": ("Emojis imported successfully!\nCategory: {category}\nAdded after deduplication: {count}","表情匯入成功！\n分類：{category}\n去重後加入：{count} 個","絵文字をインポートしました！\nカテゴリ：{category}\n重複排除後の追加数：{count}"),
"❌ 导入失败，无法获取表情包资源库存储服务！": ("❌ Import failed: emoji library storage service unavailable!","❌ 匯入失敗：無法取得表情包資源庫儲存服務！","❌ インポート失敗：絵文字ライブラリの保存サービスを利用できません！"), "无法获取表情包资源库存储服务！": ("Emoji library storage service unavailable!","無法取得表情包資源庫儲存服務！","絵文字ライブラリの保存サービスを利用できません！"),
"💬 开始导入表情到资源库，分类: [{category}]...": ("💬 Importing emojis to library, category: [{category}]...","💬 開始匯入表情至資源庫，分類：[{category}]...","💬 絵文字をライブラリにインポート中、カテゴリ：[{category}]..."),
"❌ 未找到该用户的表情分类目录: {path}": ("❌ Emoji category directory not found: {path}","❌ 找不到此使用者的表情分類資料夾：{path}","❌ このユーザーの絵文字カテゴリが見つかりません：{path}"), "❌ 无法打开资源管理器: {error}": ("❌ Could not open File Explorer: {error}","❌ 無法開啟檔案總管：{error}","❌ エクスプローラーを開けません：{error}"),
"获取信息失败: {error}": ("Failed to get information: {error}","取得資訊失敗：{error}","情報の取得に失敗しました：{error}"), "预览失败: {error}": ("Preview failed: {error}","預覽失敗：{error}","プレビューに失敗しました：{error}"), "格式: {format}\n路径: {path}": ("Format: {format}\nPath: {path}","格式：{format}\n路徑：{path}","形式：{format}\nパス：{path}"),
"导出文件时出错: {error}": ("Error exporting files: {error}","匯出檔案時發生錯誤：{error}","ファイルのエクスポート中にエラー：{error}"), "❌ 导入到资源库时出错: {error}": ("❌ Error importing to library: {error}","❌ 匯入資源庫時發生錯誤：{error}","❌ ライブラリへのインポート中にエラー：{error}"), "导入出错: {error}": ("Import error: {error}","匯入錯誤：{error}","インポートエラー：{error}"),
"⚠️ 读取表情分类出错: {error}": ("⚠️ Error reading emoji categories: {error}","⚠️ 讀取表情分類時發生錯誤：{error}","⚠️ 絵文字カテゴリの読み込みエラー：{error}"),
"⚠️ 未找到该账户的 Emoji 目录: {path}": ("⚠️ Emoji directory not found for this account: {path}","⚠️ 找不到此帳號的 Emoji 資料夾：{path}","⚠️ このアカウントのEmojiディレクトリが見つかりません：{path}"),
"⚠️ 未能自动定位到QQ聊天数据文件夹，请手动点击按钮 [选择数据目录] 指定！": ("⚠️ Could not locate QQ chat data automatically. Please select it manually.","⚠️ 無法自動定位 QQ 聊天資料夾，請手動選擇！","⚠️ QQチャットデータを自動検出できません。手動で選択してください！"),
"✅ 正在复制选中的表情文件到: {path}": ("✅ Copying selected emoji files to: {path}","✅ 正在複製所選表情檔案至：{path}","✅ 選択した絵文字ファイルをコピー中：{path}"), "✅ 正在复制所有表情文件到: {path}": ("✅ Copying all emoji files to: {path}","✅ 正在複製全部表情檔案至：{path}","✅ すべての絵文字ファイルをコピー中：{path}"),
"✅ 完成！正在打开输出文件夹……": ("✅ Complete! Opening output folder...","✅ 完成！正在開啟輸出資料夾……","✅ 完了！出力フォルダーを開いています……"),
"未找到该分类的本地目录，可能是该账号在本地未生成对应分类，或者路径不正确。": ("The local category directory was not found. The category may not exist locally or the path may be incorrect.","找不到此分類的本機資料夾，可能是帳號尚未在本機建立該分類，或路徑不正確。","カテゴリのローカルディレクトリが見つかりません。ローカルに作成されていないか、パスが正しくない可能性があります。"),
"使用帮助："+help_zh[5:]: (help_en, help_tw, help_ja),
}

for lang, index in (("en", 0), ("zh_TW", 1), ("ja", 2)):
    path = root / "translations" / f"{lang}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for key, values in entries.items():
        data.setdefault(key, values[index])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    json.loads(path.read_text(encoding="utf-8"))
print(f"Added/verified {len(entries)} QQ translation keys.")
