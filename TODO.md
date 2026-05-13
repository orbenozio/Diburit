# TODO

רשימת דברים שנרצה להוסיף / לתקן ב-Diburit. סדר חופשי — נסמן ✅ כשמשהו מסתיים.

## פתוחים

(אין משימות פתוחות כרגע)

## הושלמו

### ✅ ריענון cache של האייקון אחרי build — postbuild.sh v1.6.0 (13.5.2026)
- ה-`.icns` שמיוצר על ידי [build_icon.py](build_icon.py) היה תקין: הרצת `iconutil --convert iconset` על `Diburit.icns` הראתה את כל 10 הגדלים (16/16@2x/32/32@2x/128/128@2x/256/256@2x/512/512@2x) שמורים בפנים. הבעיה לא הייתה בקובץ עצמו אלא במטמון האייקונים של macOS — Finder/Dock/System Settings קיווסטים את האייקון לפי `(path, mtime)` ולא טוענים מחדש אחרי rebuild שלא נוגע ב-mtime של ה-`.app`.
- [postbuild.sh](postbuild.sh) הורחב לכלול שלושה שלבים אחרי החתימה: (1) sanity-check שה-`.icns` באמת ב-`Contents/Resources/`, (2) `touch "$APP"` כדי לבמפ את ה-mtime, (3) `lsregister -f "$APP"` כדי לרשום מחדש את ה-bundle ב-LaunchServices — כך ש-System Settings (Login Items, Privacy & Security) מציגים את האייקון החדש בלי צורך ב-logout.
- האייקון בסרגל התפריטים (`ICON_IDLE` וחבריו) הושאר כאמוג'י בכוונה — האייקון מתחלף בין מצבים (`🎙` / `🎙 🔴` / `🎙 …` / `🎙 ⊘` / `🎙 ✋`) ומעביר מידע מצבי בלי שכבת רנדור נוספת.

### ✅ תפריט Settings ב-UI: Max Recordings, Prune Now, Open Folder — v1.6.0 (13.5.2026)
- [diburit.py](diburit.py) קיבל שלושה פריטי תפריט חדשים אחרי `Push-to-Talk Mode`:
  - **Max Recordings** submenu עם presets (25 / 50 / 100 / 250 / 500 / 1000) ו-`Custom…` שפותח `rumps.Window` ל-input. אותו clamp של `[10, 10000]` כמו ב-`_load_settings`, כך ש-UI לא יכול לעקוף את ה-guardrail.
  - **Prune Recordings Now** — מפעיל ידנית את אותו `_prune_recordings` שרץ אוטומטית אחרי כל תמלול מוצלח. שימושי מיד אחרי הקטנת keep-count, כי ה-prune האוטומטי רץ רק אחרי ה-recording הבא.
  - **Open Diburit Folder…** — פותח את `~/Diburit/` ב-Finder כך שאפשר לגשת ל-`.env`, `settings.json`, `recordings/`, ו-`tts_debug.log` בלי לזכור את הנתיב.
- כל ששת הקריאות הכפולות ל-`_save_settings({...})` (ב-`on_voice_selected` / `_apply_hotkey` / `on_toggle_ptt_mode` / `on_volume_selected` / `on_speed_selected` + הנתיב החדש) הופשטו ל-method יחיד `_persist_settings()`. הוספת setting חדש מצריכה עכשיו עדכון של מקום אחד במקום שישה.
- שינוי `hotkey` כבר היה קיים דרך [hotkey_submenu](diburit.py) כולל `Custom…`, אז לא היה צורך להוסיף שם.

### ✅ סינון בלוקי קוד ב-TTS readback — tts_assistant v1.3.0 (13.5.2026)
- נוסף `strip_code_blocks(text)` ב-[tts_assistant.py](tts_assistant.py) שמחליף `\`\`\`...\`\`\`` ב-`"בלוק קוד"` ו-inline code שאורכו מעל 20 תווים ב-`"סקריפט"`. inline code קצר (`ls`, `git`, שמות קבצים) נשאר inline כי הוא קריא בהקראה.
- `strip_markdown` עכשיו מתחיל ב-`strip_code_blocks`, כך שכל שלושת המסלולים מסונכרנים: **SHORT** (≤220 תווים) מקבל טקסט עם פלייסהולדרים; **PUNCHLINE** מקבל cleaned text + פילטר חדש `_is_just_placeholder` שמונע בחירת פסקה/משפט שכולו `"בלוק קוד."` כ-punchline; **COMPLEX** מעביר ל-`summarize_via_groq` את הטקסט אחרי `strip_code_blocks`, וה-system prompt עודכן להנחות את המודל להתייחס לפלייסהולדרים כקוד מושמט.
- בדיקות שעברו: code fence עם טקסט מקיף → cleaned: `"הנה הפתרון: בלוק קוד. זה אמור לעבוד."`; only-code → `"בלוק קוד."`; inline `ls` נשמר, inline ארוך הוחלף ב-`סקריפט`; מקרה complex עם 4 fence-ים → `looks_complex=True` ו-`strip_code_blocks(raw)` מנקה לפני הסיכום.

### ✅ תמיכה בעברית עם מילים באנגלית באמצע — v1.6.0 (13.5.2026)
- נוסף `GROQ_PROMPT` ב-[diburit.py](diburit.py): hint בעברית ל-Whisper שמפרט מונחים טכניים נפוצים באנגלית (commit, git, terminal, install, function, class, repo, branch, pull request, debug, script, file, folder, server, build, deploy, log, hook, prompt, token, cache, queue, callback, README) ומבקש לשמר אותם בכתב לטיני במקום תעתיק.
- `language=he` נשאר קשיח כדי לא לפגוע ב-baseline של עברית טהורה, וה-prompt חייב להיות באותה שפה כמו `language=` לפי חוזה ה-API של OpenAI Whisper. ה-prompt עוצב להיות "vocabulary hint" שמכוון את המודל לטוקנים האנגליים בלי לדחוף אותו לטרנסקריפט אנגלי שלם.

## הושלמו (היסטוריה)

### ✅ ה-TTS Stop hook הקריא לפעמים תשובה קודמת ולא את האחרונה — tts_assistant v1.2.2 (13.5.2026)
- הבעיה: ב-[tts_assistant.py](tts_assistant.py) הרצנו `latest_user_text` ואז `latest_assistant_text` כשתי קריאות נפרדות על ה-JSONL. כש-Stop נורה מוקדם מדי (לפני ש-Claude Code flush-יל את ה-assistant message של ה-turn הנוכחי), הפונקציה השנייה החזירה את התשובה האחרונה שכבר נכתבה — כלומר את ה-turn הקודם.
- הפתרון: פונקציה משולבת `latest_user_and_assistant` שמחזירה רק `assistant_text` שה-line index שלו ב-JSONL גבוה מזה של הודעת ה-user האחרונה. `main` עושה poll קצר (עד 3 שניות, צעדים של 100ms) עד שיש assistant text "טרי", ורק אז קורא ל-`read_and_consume_metadata` — כך שכישלון לא מבזבז את המטא-דאטה.
- בדיקות שעברו: (1) JSONL עם user-ללא-assistant מחזיר `assistant_text=""` ולא דולף לתשובה קודמת; (2) אחרי flush של ה-assistant, הפונקציה מרימה את הטקסט הנכון; (3) הודעות assistant של tool-use בלבד מדולגות לטובת ה-text הסופי.

### ✅ אינדיקציה חזותית למצב PTT vs Toggle — v1.3.1 (12.5.2026)
- אייקון idle ב-menubar מתחלף בין `🎙` (toggle) ל-`🎙 ✋` (PTT) כך שהמצב נראה בלי לפתוח את התפריט.
- `_refresh_menu` ב-[diburit.py](diburit.py) עודכן להחליט על האייקון לפי שילוב של `enabled` + `recording` + `transcribing` + `hotkey_mode`. ICONים של recording/transcribing/off לא השתנו (הם כבר מבטאים מצב חד-משמעי).

### ✅ מצב Push-To-Talk (PTT) — v1.3.0 (12.5.2026)
- הגדרה חדשה `hotkey_mode: "toggle"|"ptt"` ב-[settings.json](~/Diburit/settings.json) (ברירת מחדל toggle, תאימות אחורה).
- `_QuartzHotkey` הורחב לקבל `on_released` אופציונלי — נרשם ל-`KeyUp` + `FlagsChanged` רק כשצריך, ויורה release על שחרור של ה-keycode *או* של מודיפייר נדרש (מה שקורה קודם).
- מצב PTT עוקף את ה-toggle handler ופונה ישירות ל-`_start_recording`/`_stop_recording` דרך ה-`_main_queue`.
- פריט תפריט "Push-to-Talk Mode" עם checkmark; שינוי מצב באמצע הקלטה מסיים אותה נקי.
- guard נגד הקשות מקריות: holds מתחת ל-`PTT_MIN_HOLD_SEC` (180ms) נזרקים לפני שליחה ל-Groq.
- ראה [CHANGELOG.md](CHANGELOG.md) ל-`1.3.0` לפרטים מלאים.
