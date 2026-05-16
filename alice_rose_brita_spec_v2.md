# אפיון אתר בריתה — אליס רוז בנוזיו (גרסה 2.0)

## מטרת האתר

אתר הזמנה ואישורי הגעה לבריתה / זבד הבת של אליס רוז בנוזיו.

האתר ישמש עבור:

- עמוד הזמנה מעוצב למשפחה ולאורחים
- אישורי הגעה (RSVP)
- עריכת אישור הגעה על ידי האורחים באמצעות קוד ייחודי
- ניהול מוזמנים
- שליחת תזכורות WhatsApp (קישורי wa.me ידניים)
- הוספה ליומן (Google Calendar URL + ICS download)
- מידע על הגעה, מפה וחניה
- עמוד אדמין לניהול האירוע והתוכן באתר
- QR Code להדפסה על הזמנות פיזיות
- תמיכה בשיתוף WhatsApp עם OG preview מלא

---

## מודלי נתונים

### EventSettings (singleton — רשומה אחת בלבד)

| שדה | סוג | תיאור |
|-----|-----|--------|
| babyName | Text | שם התינוקת (ברירת מחדל: "אליס רוז בנוזיו") |
| eventTitle | Text | כותרת קצרה לטאב הדפדפן ול-OG title |
| mainTitle | Text | כותרת ראשית בעמוד הבית |
| subtitle | Text | כותרת משנה |
| introText | LongText | טקסט הקדמה לאירוע |
| heroImage | File (Base44 File Upload) | תמונה ראשית — מועלית דרך Base44 file upload |
| galleryImages | File[] (Base44 File Upload Array) | מערך תמונות גלריה — עד 10 תמונות |
| eventDate | Date | תאריך האירוע (פורמט ISO 8601: YYYY-MM-DD) |
| startTime | Text | שעת התחלה (פורמט HH:mm, לדוגמה "18:00") |
| endTime | Text | שעת סיום (פורמט HH:mm, לדוגמה "21:00") |
| venueName | Text | שם האולם / מקום האירוע |
| venueDescription | LongText | תיאור המקום |
| address | Text | כתובת מלאה |
| googleMapsUrl | URL | קישור ל-Google Maps |
| wazeUrl | URL | קישור ל-Waze |
| mapEmbedUrl | URL | קישור להטמעת מפה (Google Maps embed iframe src) |
| parkingTitle | Text | כותרת קטע חניה |
| parkingInstructions | LongText | הוראות חניה |
| rsvpButtonText | Text | טקסט כפתור RSVP (ברירת מחדל: "אישור הגעה") |
| rsvpFormTitle | Text | כותרת טופס RSVP |
| rsvpFormIntroText | LongText | טקסט הקדמה לטופס |
| thankYouMessage | LongText | הודעת תודה לאחר RSVP |
| rsvpClosedMessage | LongText | הודעה כאשר RSVP סגור |
| contactName | Text | שם איש קשר |
| contactPhone | Text | טלפון ליצירת קשר (פורמט: 05X-XXXXXXX) |
| reminderTemplateNoResponse | LongText | תבנית הודעת WhatsApp לאורחים שלא הגיבו. משתנים: `{{name}}`, `{{eventDate}}`, `{{editLink}}` |
| reminderTemplateMaybe | LongText | תבנית הודעה לאורחים שענו "אולי". משתנים זהים. |
| reminderTemplateDayBefore | LongText | תבנית הודעת תזכורת יום לפני. משתנים: `{{name}}`, `{{time}}`, `{{address}}` |
| shareInviteMessage | LongText | הודעת שיתוף ברירת מחדל ל-WhatsApp. משתנה: `{{siteUrl}}` |
| rsvpOpen | Boolean | האם RSVP פתוח לשליחה חדשה (ברירת מחדל: true) |
| ogDescription | LongText | תיאור לתגי OG / WhatsApp preview (160 תווים מקסימום) |
| adminEmail | Email | כתובת אימייל האדמין |
| adminPasswordHash | Text | סיסמת האדמין — מאוחסנת כ-bcrypt hash (מייוצר בצד השרת) |
| adminSessionToken | Text | טוקן session פעיל (UUID, מאופס בהתנתקות) |
| updatedAt | DateTime | עדכון אחרון |

---

### RSVP

| שדה | סוג | תיאור |
|-----|-----|--------|
| fullName | Text | שם מלא (חובה) |
| phone | Text | מספר טלפון (חובה, ייחודי; פורמט ישראלי: 05X-XXXXXXX עם ולידציה) |
| email | Email | אימייל (אופציונלי) |
| status | Enum | "מגיע" / "לא מגיע" / "אולי" |
| adultCount | Number | מספר מבוגרים (מינימום 0, ברירת מחדל 1) |
| childCount | Number | מספר ילדים (מינימום 0, ברירת מחדל 0) |
| veganCount | Number | מספר אנשים טבעוניים (מינימום 0) |
| glutenFreeCount | Number | מספר אנשים ללא גלוטן (מינימום 0) |
| notes | LongText | הערות חופשיות (אופציונלי, מקסימום 500 תווים) |
| editCode | Text | קוד עריכה ייחודי — UUID v4, מייוצר אוטומטית בעת יצירת הרשומה, אינו משתנה |
| remindersSent | JSON | מערך של `{ type: string, sentAt: DateTime }` — מתעד כל תזכורת שנשלחה |
| createdAt | DateTime | תאריך יצירה |
| updatedAt | DateTime | עדכון אחרון |

> **הערה:** מודל `AdminUser` הוסר. אימות האדמין מנוהל דרך `adminEmail` + `adminPasswordHash` + `adminSessionToken` ב-EventSettings.

---

## עמודים ונתיבים

| נתיב | עמוד | גישה |
|------|------|-------|
| `/` | עמוד בית — הזמנה | ציבורי |
| `/rsvp` | טופס RSVP | ציבורי |
| `/rsvp/edit?code=UUID` | עריכת RSVP | ציבורי (עם editCode תקין) |
| `/rsvp/thank-you?code=UUID` | עמוד תודה | ציבורי |
| `/info` | מידע הגעה ומפה | ציבורי |
| `/qr` | QR Code להדפסה | ציבורי |
| `/admin` | כניסת אדמין | ציבורי (redirect לדשבורד אם מחובר) |
| `/admin/dashboard` | דשבורד אדמין | אדמין בלבד |
| `/admin/guests` | ניהול אורחים | אדמין בלבד |
| `/admin/settings` | עריכת תוכן האתר | אדמין בלבד |
| `/admin/reminders` | שליחת תזכורות | אדמין בלבד |
| `*` | דף 404 | ציבורי |

---

## עמוד בית (`/`)

### OG Meta Tags (ב-`<head>`)

```html
<html dir="rtl" lang="he">
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta property="og:title" content="{eventTitle}">
<meta property="og:description" content="{ogDescription}">
<meta property="og:image" content="{heroImage — URL מלא כולל https://}">
<meta property="og:url" content="{כתובת האתר המלאה}">
<meta property="og:type" content="website">
<meta property="og:locale" content="he_IL">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="{heroImage URL}" type="image/png">
```

### מבנה העמוד

1. **Hero Section**
   - תמונת hero בגודל מלא (100vw, מינימום 60vh)
   - overlay כהה עם gradient מלמטה
   - `mainTitle` בכותרת H1, `subtitle` ב-H2, `babyName` בולט
   - כפתור ראשי: `rsvpButtonText` → מנווט ל-`/rsvp`
   - כפתור שיתוף: "שתפו עם חברים" → פותח WhatsApp עם `shareInviteMessage`  
     URL: `https://wa.me/?text={encodeURIComponent(shareInviteMessage.replace('{{siteUrl}}', window.location.origin))}`

2. **Intro Section**
   - `introText` — טקסט עם RTL מלא
   - תאריך האירוע בפורמט עברי: "יום X, DD בחודש YYYY" + שעות

3. **Gallery Section** *(מוצגת רק אם `galleryImages` לא ריק)*
   - גריד תמונות responsive (3 עמודות desktop / 2 tablet / 1 mobile)
   - כל תמונה clickable → lightbox modal
   - אם אין תמונות — הסקשן לא מוצג כלל

4. **Info Preview Section**
   - כרטיס עם `venueName`, `address`
   - כפתורי ניווט: Google Maps + Waze (target blank)
   - קישור "מידע נוסף על ההגעה" → `/info`

5. **Add to Calendar Section**
   - כותרת: "הוסיפו ליומן"
   - שלושה כפתורים:
     - **Google Calendar** → URL מלא עם פרמטרים TEMPLATE, dates, details, location
     - **Apple / ICS** → מוריד קובץ `.ics` תקני
     - **Outlook** → אותו `.ics` download

6. **Footer**
   - `contactName` + כפתור WhatsApp → `https://wa.me/972{phone_ללא_0_מוביל}`

---

## עמוד RSVP (`/rsvp`)

### לוגיקת הכניסה

- אם `rsvpOpen = false` — מציג `rsvpClosedMessage` בלבד, ללא טופס
- אם `rsvpOpen = true` — מציג טופס מלא

### שדות הטופס

| שדה | סוג | dir | ולידציה | הודעת שגיאה |
|-----|-----|-----|---------|-------------|
| fullName | text | RTL | חובה, מינימום 2 תווים | "נא להזין שם מלא (לפחות 2 תווים)" |
| phone | tel | **LTR** | חובה, regex: `^05[0-9]{8}$` (אחרי strip של מקפים/רווחים) | "נא להזין מספר טלפון ישראלי תקין (לדוגמה: 0501234567)" |
| email | email | **LTR** | אופציונלי; אם מוזן — פורמט תקין | "כתובת האימייל אינה תקינה" |
| status | radio | — | חובה | "נא לבחור תשובה" |
| adultCount | number | **LTR** | חובה אם status≠"לא מגיע"; מינימום 1 | "נא להזין מספר מבוגרים (לפחות 1)" |
| childCount | number | **LTR** | חובה אם status≠"לא מגיע"; מינימום 0 | — |
| veganCount | number | **LTR** | אופציונלי; לא יעלה על adultCount+childCount | "מספר הטבעונים לא יכול לעלות על סך המוזמנים" |
| glutenFreeCount | number | **LTR** | אופציונלי; לא יעלה על adultCount+childCount | "מספר אנשים ללא גלוטן לא יכול לעלות על סך המוזמנים" |
| notes | textarea | RTL | אופציונלי, מקסימום 500 תווים | — |

> **חשוב:** שדות מספריים (adultCount, childCount, veganCount, glutenFreeCount) ושדה הטלפון חייבים לקבל `dir="ltr"` ו-`inputmode="numeric"` כדי שהספרות יוצגו נכון בממשק RTL.

**Radio status:**
- "מגיע / מגיעה"
- "לא מגיע / לא מגיעה" ← אם נבחר, שדות כמות נסתרים
- "אולי"

### לוגיקת שליחה

1. ולידציית client-side לפני שליחה
2. נרמול טלפון: הסרת מקפים ורווחים
3. שליחה לשרת; השרת מחפש RSVP קיים עם אותו מספר טלפון:
   - **כפול** → מציג: "מצאנו אישור הגעה קיים עם מספר זה. האם ברצונך לערוך אותו?" + כפתור ל-`/rsvp/edit?code={editCode}`
   - **חדש** → יוצר רשומה, `editCode` = UUID v4, מנווט ל-`/rsvp/thank-you?code={editCode}`
4. שגיאת רשת → toast: "אירעה שגיאה בשליחה, אנא נסו שוב"
5. בזמן שליחה: כפתור מציג spinner + "שולח..." ומושבת

---

## עמוד תודה (`/rsvp/thank-you?code=UUID`)

- מציג `thankYouMessage`
- כפתור: "רוצים לשנות משהו?" → `/rsvp/edit?code={code}`
- כפתור: "חזרה לעמוד הבית" → `/`
- אם הגיעו ללא `code` — הודעת תודה גנרית בלבד

---

## עמוד עריכת RSVP (`/rsvp/edit?code=UUID`)

1. קריאת `code` מ-query string; חיפוש RSVP לפי `editCode`
2. **לא נמצא** → "קוד העריכה לא תקין. לעזרה פנו ל-{contactName}: {contactPhone}" + כפתור חזרה
3. **נמצא + `rsvpOpen = false`** → טופס read-only עם הודעה: "RSVP סגור — לא ניתן לערוך כרגע"
4. **נמצא + `rsvpOpen = true`** → טופס עריכה מאוכלס עם הנתונים הקיימים
5. לאחר עדכון מוצלח: toast "עודכן בהצלחה!" + נשאר באותו עמוד

---

## עמוד מידע הגעה (`/info`)

1. `venueName` (H1), `address`
2. מפה מוטמעת: `<iframe src="{mapEmbedUrl}" width="100%" height="300" frameborder="0" allowfullscreen loading="lazy">` — fallback: placeholder אפור עם כפתורי ניווט
3. כפתורי ניווט גדולים (מינימום 56px): Google Maps + Waze (target blank)
4. קטע חניה: `parkingTitle`, `parkingInstructions`
5. הוספה ליומן (זהה לעמוד הבית)
6. יצירת קשר: `contactName`, כפתור WhatsApp

---

## עמוד QR Code להדפסה (`/qr`)

**מטרה:** הדפסה על הזמנות פיזיות.

**תוכן:**
- QR Code המפנה ל-`{siteUrl}/rsvp` — מיוצר ב-client-side (`react-qr-code` או שקול)
- כותרת: "לאישור הגעה — סרקו את הקוד"
- כתובת URL בטקסט
- `mainTitle` ו-`babyName`
- תאריך, שעות, `venueName`, `address`
- כפתור "הדפס"

**CSS הדפסה:**
```css
@media print {
  .no-print { display: none; }
  nav { display: none; }
}
```

---

## עמוד Admin Login (`/admin`)

**שדות:** email (LTR) + password

**לוגיקת כניסה:**
1. השרת משווה email ל-`EventSettings.adminEmail` וסיסמה ל-`adminPasswordHash` (bcrypt.compare)
2. **תקין** → `adminSessionToken` = UUID v4 חדש נשמר ב-EventSettings + מוחזר ל-client → נשמר ב-`localStorage.adminToken` → redirect ל-`/admin/dashboard`
3. **לא תקין** → "פרטי הכניסה שגויים" (לא לפרט איזה שדה)
4. לאחר 5 ניסיונות כושלים — נעילה ל-15 דקות עם countdown
5. אם כבר מחובר (token תקין ב-localStorage) → redirect מיידי לדשבורד

### הגנת עמודי Admin

כל `/admin/*` (מלבד `/admin`):
- קריאת `adminToken` מ-localStorage
- אם אין → redirect ל-`/admin`
- כל בקשת API: `Authorization: Bearer {adminToken}`
- אם 401 → נקה localStorage + redirect ל-`/admin` + toast "פג תוקף החיבור, אנא התחברו מחדש"

**כפתור התנתקות:** נקה token ב-localStorage + אפס `adminSessionToken` ב-EventSettings + redirect ל-`/admin`

---

## דשבורד אדמין (`/admin/dashboard`)

### כרטיסי סטטיסטיקות (live)

- סה"כ אורחים (כל הרשומות)
- מגיעים — כרטיס ירוק
- לא מגיעים — כרטיס אדום
- אולי — כרטיס כתום
- סה"כ מבוגרים (sum adultCount where status="מגיע")
- סה"כ ילדים (sum childCount where status="מגיע")
- טבעונים (sum veganCount where status="מגיע")
- ללא גלוטן (sum glutenFreeCount where status="מגיע")

### מתג RSVP

מתג גדול ובולט: "RSVP פתוח / RSVP סגור" — משנה `rsvpOpen` ב-EventSettings בזמן אמת.

---

## ניהול אורחים (`/admin/guests`)

### טבלה

עמודות: שם מלא | טלפון | סטטוס | מבוגרים | ילדים | טבעוני | ללא גלוטן | הערות | תאריך רישום | פעולות

### פיצ'רים

- **חיפוש** (RTL) — על שם / טלפון / אימייל, client-side
- **סינון סטטוס** — dropdown: הכל / מגיע / לא מגיע / אולי
- **מיון** — לחיצה על כותרת עמודה
- **עריכה** → modal עם טופס מלא
- **מחיקה** → dialog: "למחוק את {fullName}? פעולה זו אינה ניתנת לביטול" + כפתורי "מחק" (אדום) / "ביטול"
- **העתק קישור עריכה** → מעתיק `{siteUrl}/rsvp/edit?code={editCode}` + toast "הקישור הועתק!"
- **+ הוסף אורח** → modal עם טופס ריק (ולידציה זהה, editCode=UUID v4 חדש)
- **ייצוא CSV** → `rsvp_export_{YYYY-MM-DD}.csv`

### CSV Export

- קידוד: UTF-8 עם BOM (`﻿`) לתמיכה ב-Excel ישראלי
- עמודות: שם מלא, טלפון, אימייל, סטטוס, מבוגרים, ילדים, טבעונים, ללא גלוטן, הערות, תאריך רישום

### מצב ריק

"עדיין אין אורחים רשומים. שתפו את קישור ההזמנה ואורחים יתחילו להירשם!" + כפתור שיתוף

### מצב טעינה

Skeleton rows — שורות placeholder אפורות מונפשות בזמן טעינת הנתונים.

---

## שליחת תזכורות (`/admin/reminders`)

### הסבר למשתמש

"שליחת תזכורות מתבצעת דרך WhatsApp שלך. לחיצה על 'שלח' תפתח WhatsApp עם ההודעה מוכנה לכל אורח בנפרד."

### טאבים

- **לא השיבו** — אורחים ללא status ברשומה
- **ענו אולי** — status = "אולי"
- **תזכורת יום לפני** — כל status = "מגיע"

לכל אורח:
- שם, טלפון, תאריך רישום
- כפתור "שלח תזכורת WhatsApp" → פותח:  
  `https://wa.me/972{phone_ללא_0_מוביל}?text={encodeURIComponent(הודעה_מוכנה)}`
- לאחר לחיצה: מציג "נשלח ב-DD/MM/YYYY HH:mm" + שומר ב-`remindersSent` של הרשומה

### נרמול טלפון לwa.me

`0501234567` → `972501234567` (הסרת 0 מוביל + קידומת 972)

### החלפת משתנים בתבנית

- `{{name}}` → `fullName`
- `{{eventDate}}` → תאריך בפורמט עברי
- `{{time}}` → `startTime`
- `{{address}}` → `address`
- `{{editLink}}` → `{siteUrl}/rsvp/edit?code={editCode}`

---

## עריכת תוכן (`/admin/settings`)

שישה טאבים:

**1. פרטי האירוע**
eventDate (date picker), startTime/endTime (time picker), venueName, address, venueDescription, googleMapsUrl, wazeUrl, mapEmbedUrl, parkingTitle, parkingInstructions

**2. תוכן האתר**
babyName, eventTitle, mainTitle, subtitle, introText, ogDescription (עם מונה תווים, מקסימום 160)

**3. תמונות**
- heroImage: העלאת קובץ יחיד (jpg/png/webp, מקסימום 5MB) + thumbnail + כפתור "החלף תמונה"
- galleryImages: העלאת עד 10 קבצים + גריד thumbnails + כפתור ✕ למחיקה כל תמונה

**4. הודעות ותבניות**
thankYouMessage, rsvpClosedMessage, shareInviteMessage, reminderTemplateNoResponse, reminderTemplateMaybe, reminderTemplateDayBefore
(עם הסבר על משתנים זמינים ליד כל שדה)

**5. RSVP והגדרות**
rsvpButtonText, rsvpFormTitle, rsvpFormIntroText, rsvpOpen (מתג), contactName, contactPhone

**6. חשבון אדמין**
- שינוי אימייל: שדה email חדש
- שינוי סיסמה: "סיסמה נוכחית" + "סיסמה חדשה" + "אימות סיסמה"
- שמירה → bcrypt hash לפני אחסון

**כל טאב:** כפתור "שמור" + toast "נשמר בהצלחה!" / "שגיאה בשמירה, נסה שוב"

> **אזהרה:** אם `adminPasswordHash` תואם את ה-hash של ברירת המחדל "admin123" — הצג banner: "סיסמת ברירת המחדל עדיין בשימוש — אנא שנה אותה עכשיו"

---

## דף 404

- כותרת: "404 — הדף לא נמצא"
- טקסט: "נראה שהדף שחיפשתם לא קיים"
- כפתור: "חזרה לעמוד הבית" → `/`

---

## פיצ'רים נוספים

### מצב סגירת RSVP

מתג "rsvpOpen" ב-EventSettings — כשסגור, עמוד `/rsvp` מציג `rsvpClosedMessage`.
אורח עם editCode תקין יכול לראות את האישור שלו ב-read-only אבל לא לערוך.

### סיכום לקייטרינג

כפתור "העתק סיכום קייטרינג" בדשבורד — מייצר טקסט מוכן:

```
סיכום קייטרינג — {eventTitle}
תאריך: {eventDate}
---
סה"כ מגיעים: X
מבוגרים: X
ילדים: X
טבעונים: X
ללא גלוטן: X
---
הערות מיוחדות:
[רשימת הערות אורחים שלא ריקות]
```

---

## דרישות טכניות

### RTL וממשק

- `<html dir="rtl" lang="he">` על כל עמוד
- גופן עברי: Heebo, Noto Sans Hebrew, או Assistant (Google Fonts)
- שדות מספריים וטלפון: `dir="ltr"`, `inputmode="numeric"`, `text-align: left`
- שדות אימייל ו-URL: `dir="ltr"`

### Responsive / Mobile First

- breakpoints: 320px / 768px / 1024px
- כפתורי CTA: גובה מינימום 48px (tap target)
- גודל טקסט מינימלי: 16px (מניעת zoom אוטומטי ב-iOS)
- טופס RSVP: שדות ברוחב מלא ב-mobile

### מצבי טעינה

- Skeleton UI (לא spinner גנרי) בזמן טעינת EventSettings
- spinner על כפתור השליחה בזמן POST
- placeholder אפור בגודל התמונה לפני טעינת gallery

### אבטחה

- adminSessionToken = UUID v4, מאופס בכל login
- editCode = UUID v4, לא ניתן לחיזוי
- sanitize inputs לפני אחסון (XSS)
- 401 מהשרת → נקה session + redirect

### ביצועים

- hero image: `loading="lazy"`, srcset לגדלים שונים
- gallery: `loading="lazy"`

---

## נתוני ברירת מחדל (seeding)

בטעינה ראשונה, אם EventSettings לא קיים — צור עם:

```json
{
  "babyName": "אליס רוז בנוזיו",
  "eventTitle": "הזמנה לזבד הבת — אליס רוז",
  "mainTitle": "ברוכים הבאים לזבד הבת של",
  "rsvpButtonText": "אישור הגעה",
  "rsvpOpen": true,
  "adminEmail": "admin@alicerose.co.il",
  "adminPasswordHash": "<bcrypt hash of 'admin123'>",
  "reminderTemplateNoResponse": "שלום {{name}}, רצינו לוודא שקיבלתם את ההזמנה לזבד הבת של אליס רוז. נשמח לדעת אם תוכלו להגיע ב-{{eventDate}}. לאישור הגעה: {{editLink}}",
  "reminderTemplateMaybe": "שלום {{name}}, ראינו שענית 'אולי' להזמנה שלנו. עדכנו אותנו אם תוכלו להגיע ב-{{eventDate}}: {{editLink}}",
  "reminderTemplateDayBefore": "שלום {{name}}, תזכורת ידידותית — מחכים לכם מחר ב-{{time}} ב-{{address}}! מחכים בקוצר רוח 💕",
  "shareInviteMessage": "הוזמנתם לזבד הבת של אליס רוז בנוזיו! לפרטים ואישור הגעה: {{siteUrl}}",
  "ogDescription": "הוזמנתם לחגוג את בואה של אליס רוז בנוזיו! לחצו לפרטים ולאישור הגעה."
}
```

---

## כלי עזר (Shared Utilities)

### ולידציית טלפון ישראלי

```javascript
function normalizePhone(phone) {
  return phone.replace(/[\s\-]/g, '');
}

function isValidIsraeliPhone(phone) {
  return /^05[0-9]{8}$/.test(normalizePhone(phone));
}

function toWaMePhone(phone) {
  const normalized = normalizePhone(phone);
  return '972' + normalized.slice(1); // 0501234567 → 972501234567
}
```

### יצירת קובץ ICS

```javascript
function generateICS(settings) {
  const formatDT = (dateStr, timeStr) => {
    const [y, m, d] = dateStr.split('-');
    const [hh, mm] = timeStr.split(':');
    return `${y}${m}${d}T${hh}${mm}00`;
  };
  return [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//AliceRose//BritaInvite//HE',
    'BEGIN:VEVENT',
    `DTSTART:${formatDT(settings.eventDate, settings.startTime)}`,
    `DTEND:${formatDT(settings.eventDate, settings.endTime)}`,
    `SUMMARY:${settings.eventTitle}`,
    `DESCRIPTION:${settings.introText?.replace(/\n/g, '\\n') ?? ''}`,
    `LOCATION:${settings.address}`,
    'END:VEVENT',
    'END:VCALENDAR'
  ].join('\r\n');
}
```

---

# Base44 Prompt (English)

```
Build a Hebrew RTL baby girl celebration (Zeved HaBat/Brita) invitation website for Alice Rose Benozio. Use React + Tailwind CSS. The entire UI must be in Hebrew, right-to-left (add `dir="rtl" lang="he"` to the html element). Use Heebo or Noto Sans Hebrew from Google Fonts. All data is stored in Base44 entities.

---

## DATA MODELS

### EventSettings (singleton — always exactly one record; auto-create on first load if missing)

Fields:
- babyName (Text, default: "אליס רוז בנוזיו")
- eventTitle (Text, default: "הזמנה לזבד הבת — אליס רוז")
- mainTitle (Text)
- subtitle (Text)
- introText (LongText)
- heroImage (File — Base44 file upload, single image, max 5MB, jpg/png/webp)
- galleryImages (File[] — Base44 file upload array, max 10 files, max 5MB each)
- eventDate (Date, ISO 8601 YYYY-MM-DD)
- startTime (Text, format "HH:mm", e.g. "18:00")
- endTime (Text, format "HH:mm")
- venueName (Text)
- venueDescription (LongText)
- address (Text)
- googleMapsUrl (URL)
- wazeUrl (URL)
- mapEmbedUrl (URL — Google Maps embed iframe src)
- parkingTitle (Text)
- parkingInstructions (LongText)
- rsvpButtonText (Text, default: "אישור הגעה")
- rsvpFormTitle (Text)
- rsvpFormIntroText (LongText)
- thankYouMessage (LongText)
- rsvpClosedMessage (LongText)
- contactName (Text)
- contactPhone (Text, Israeli format 05X-XXXXXXX)
- reminderTemplateNoResponse (LongText, placeholders: {{name}}, {{eventDate}}, {{editLink}})
- reminderTemplateMaybe (LongText, same placeholders)
- reminderTemplateDayBefore (LongText, placeholders: {{name}}, {{time}}, {{address}})
- shareInviteMessage (LongText, placeholder: {{siteUrl}})
- rsvpOpen (Boolean, default: true)
- ogDescription (LongText, max 160 chars)
- adminEmail (Email)
- adminPasswordHash (Text — store bcrypt hash, NEVER plaintext)
- adminSessionToken (Text — UUID v4, regenerated on each login, cleared on logout)
- updatedAt (DateTime)

### RSVP

Fields:
- fullName (Text, required)
- phone (Text, required, UNIQUE — store normalized: strip spaces/dashes, 10 digits starting with 05)
- email (Email, optional)
- status (Enum: "מגיע" | "לא מגיע" | "אולי", required)
- adultCount (Number, min 0, default 1)
- childCount (Number, min 0, default 0)
- veganCount (Number, min 0, optional)
- glutenFreeCount (Number, min 0, optional)
- notes (LongText, optional, max 500 chars)
- editCode (Text — UUID v4, auto-generated on create, immutable)
- remindersSent (JSON array of {type: string, sentAt: string ISO datetime})
- createdAt (DateTime, auto)
- updatedAt (DateTime, auto)

---

## ROUTES

- `/` — Public invitation home page
- `/rsvp` — Public RSVP form
- `/rsvp/edit?code=UUID` — Public RSVP edit (requires valid editCode)
- `/rsvp/thank-you?code=UUID` — Post-RSVP thank-you page
- `/info` — Public venue & directions
- `/qr` — Printable QR code page
- `/admin` — Admin login
- `/admin/dashboard` — Admin dashboard (protected)
- `/admin/guests` — Guest management (protected)
- `/admin/settings` — Content editor (protected)
- `/admin/reminders` — WhatsApp reminders (protected)
- `*` — 404

---

## PAGE: `/` (Home)

Add OG meta tags to <head>:
- og:title = eventTitle
- og:description = ogDescription
- og:image = heroImage absolute URL (must include https://)
- og:url = current site URL
- og:type = "website"
- og:locale = "he_IL"
- twitter:card = "summary_large_image"

Sections:
1. Hero: full-width image (min 60vh), dark gradient overlay. Show mainTitle (H1), subtitle (H2), babyName large. Two buttons: primary = rsvpButtonText → /rsvp; secondary = "שתפו עם חברים" → https://wa.me/?text={encodeURIComponent(shareInviteMessage with {{siteUrl}} replaced)}
2. Intro: introText + event date in Hebrew long format + start/end times.
3. Gallery: ONLY render if galleryImages array is non-empty. Responsive grid (3/2/1 cols). Lightbox on click.
4. Venue preview card: venueName, address, Google Maps button (target blank), Waze button (target blank), link to /info.
5. Add to Calendar: three buttons — Google Calendar URL, Apple ICS download, Outlook ICS download. Generate ICS with VCALENDAR standard (DTSTART, DTEND, SUMMARY, DESCRIPTION, LOCATION).
6. Footer: contactName + WhatsApp button → https://wa.me/972{contactPhone with leading 0 removed}.

---

## PAGE: `/rsvp`

If rsvpOpen = false: show only rsvpClosedMessage, no form.

Form fields (Hebrew labels, RTL layout):
- fullName: text, dir="rtl", required, min 2 chars
- phone: tel, dir="ltr", inputmode="numeric", required. Validate regex /^05[0-9]{8}$/ after stripping spaces and dashes. Error: "נא להזין מספר טלפון ישראלי תקין (לדוגמה: 0501234567)"
- email: email, dir="ltr", optional
- status: three styled radio buttons — "מגיע / מגיעה", "לא מגיע / לא מגיעה", "אולי"
- When status is "מגיע" or "אולי" (hidden when "לא מגיע"):
  - adultCount: number, dir="ltr", inputmode="numeric", min=0, max=20, default=1, required (min 1)
  - childCount: number, dir="ltr", inputmode="numeric", min=0, max=20, default=0
  - veganCount: number, dir="ltr", inputmode="numeric", min=0, validate ≤ adultCount+childCount
  - glutenFreeCount: number, dir="ltr", inputmode="numeric", min=0, validate ≤ adultCount+childCount
- notes: textarea, dir="rtl", optional, max 500 chars, show counter

CRITICAL: Number inputs and phone MUST have dir="ltr" and inputmode="numeric". Without this, digits appear reversed in RTL layout.

Submit button: shows spinner + "שולח..." while submitting, disabled during submission.

Submission logic:
1. Client-side validation.
2. Normalize phone: strip spaces and dashes.
3. POST to backend. Backend searches for existing RSVP with same normalized phone.
   - Duplicate: return 409 with existing editCode. Client shows: "מצאנו אישור הגעה קיים עם מספר זה. האם ברצונך לערוך אותו?" + button → /rsvp/edit?code={existingEditCode}
   - New: create record, generate editCode as UUID v4, navigate to /rsvp/thank-you?code={editCode}
4. Network error: toast "אירעה שגיאה בשליחה, אנא נסו שוב"

---

## PAGE: `/rsvp/thank-you?code=UUID`

Show thankYouMessage. If code param present: button "רוצים לשנות משהו?" → /rsvp/edit?code={code}. Button "חזרה לעמוד הבית" → /.

---

## PAGE: `/rsvp/edit?code=UUID`

Read code from URL query. Fetch RSVP where editCode = code.
- Not found: "קוד העריכה לא תקין. לעזרה פנו ל-{contactName}: {contactPhone}" + back button.
- Found + rsvpOpen = false: read-only form + "RSVP סגור — לא ניתן לערוך כרגע"
- Found + rsvpOpen = true: editable form pre-filled. Same validation. Submit: "עדכן אישור הגעה". On success: toast "עודכן בהצלחה!" — stay on page.

---

## PAGE: `/info`

1. venueName (H1), address
2. Map embed iframe if mapEmbedUrl set, else grey placeholder
3. Large navigation buttons (min 56px height): Google Maps, Waze — target blank
4. Parking: parkingTitle + parkingInstructions
5. Add to Calendar (same as home)
6. Contact: contactName + WhatsApp button

---

## PAGE: `/qr`

Generate QR code pointing to {window.location.origin}/rsvp using react-qr-code library.
Show: "לאישור הגעה — סרקו את הקוד", QR (256x256 min), URL text, mainTitle, babyName, date/time, venueName, address.
Print button (hidden in @media print). Navigation hidden in @media print.

---

## PAGE: `/admin` (Login)

email (dir="ltr") + password fields. Submit "כניסה".
Logic: POST to backend → bcrypt.compare password with adminPasswordHash.
Valid: generate new UUID adminSessionToken, save to EventSettings, return to client, store in localStorage key "adminToken", redirect to /admin/dashboard.
Invalid: "פרטי הכניסה שגויים"
After 5 failed attempts: "נחסמת לאחר 5 ניסיונות. נסה שוב בעוד 15 דקות"
If already logged in (valid token): redirect to /admin/dashboard immediately.

All /admin/* routes: read adminToken from localStorage. Missing → redirect to /admin. Every API call: Authorization: Bearer {token}. If 401: clear localStorage adminToken → redirect to /admin with toast "פג תוקף החיבור, אנא התחברו מחדש".

Show persistent warning banner if adminPasswordHash matches hash of default "admin123".

---

## PAGE: `/admin/dashboard`

Statistics cards (live queries):
- סה"כ אורחים | מגיעים (green) | לא מגיעים (red) | אולי (orange)
- סה"כ מבוגרים | סה"כ ילדים | טבעונים | ללא גלוטן
(count/sum only where status = "מגיע" for the numeric ones)

Large toggle: "RSVP פתוח / RSVP סגור" — updates EventSettings.rsvpOpen immediately.

Catering summary button: copies formatted text to clipboard:
"סה"כ מגיעים: X | מבוגרים: X | ילדים: X | טבעונים: X | ללא גלוטן: X" + notes list.

Quick links to /admin/guests, /admin/settings, /admin/reminders.

---

## PAGE: `/admin/guests`

Table: שם מלא | טלפון | סטטוס | מבוגרים | ילדים | טבעוני | ללא גלוטן | הערות | נרשם | פעולות

Features:
- Search (RTL) on name/phone/email — client-side
- Status filter dropdown
- Sortable columns
- Row actions: Edit (modal), Delete (confirmation dialog), Copy edit link (toast "הקישור הועתק!")
- "+ הוסף אורח" button → modal with empty form, generate editCode UUID v4 on save
- "ייצוא CSV" → file rsvp_export_{YYYY-MM-DD}.csv, UTF-8 with BOM (﻿) for Excel, columns: שם מלא,טלפון,אימייל,סטטוס,מבוגרים,ילדים,טבעונים,ללא גלוטן,הערות,תאריך רישום
- Empty state: "עדיין אין אורחים רשומים" + share button
- Loading state: animated skeleton rows

---

## PAGE: `/admin/reminders`

Info banner: "שליחת תזכורות מתבצעת דרך WhatsApp שלך. לחיצה על 'שלח' תפתח WhatsApp עם ההודעה מוכנה לכל אורח בנפרד."

Three tabs: "לא השיבו" | "ענו אולי" | "תזכורת יום לפני"

For each guest: name, phone, date. "שלח תזכורת WhatsApp" button → opens:
https://wa.me/972{phone with leading 0 removed}?text={encodeURIComponent(template with replacements)}

Template replacements: {{name}} → fullName, {{eventDate}} → date in Hebrew format, {{time}} → startTime, {{address}} → address, {{editLink}} → {origin}/rsvp/edit?code={editCode}

After clicking: show "נשלח ב-DD/MM/YYYY HH:mm" + append to RSVP.remindersSent array and save.

---

## PAGE: `/admin/settings`

Six tabs:

Tab 1 - פרטי האירוע: eventDate (date picker), startTime/endTime (time pickers), venueName, address, venueDescription, googleMapsUrl (dir=ltr), wazeUrl (dir=ltr), mapEmbedUrl (dir=ltr), parkingTitle, parkingInstructions

Tab 2 - תוכן האתר: babyName, eventTitle, mainTitle, subtitle, introText, ogDescription (max 160 chars, show counter)

Tab 3 - תמונות: heroImage single file upload (jpg/png/webp, max 5MB) + thumbnail preview. galleryImages multi-file upload (max 10, max 5MB each) + grid of thumbnails with ✕ delete buttons.

Tab 4 - הודעות ותבניות: thankYouMessage, rsvpClosedMessage, shareInviteMessage (hint: "השתמשו ב-{{siteUrl}} לקישור לאתר"), reminderTemplateNoResponse, reminderTemplateMaybe (hint: "משתנים: {{name}}, {{eventDate}}, {{editLink}}"), reminderTemplateDayBefore (hint: "משתנים: {{name}}, {{time}}, {{address}}")

Tab 5 - RSVP והגדרות: rsvpButtonText, rsvpFormTitle, rsvpFormIntroText, rsvpOpen toggle, contactName, contactPhone

Tab 6 - חשבון אדמין: change email field, change password (current password + new password + confirm password — must match; bcrypt hash before storing)

Each tab has "שמור" button. Success toast: "נשמר בהצלחה!". Error toast: "שגיאה בשמירה, נסה שוב"

---

## PAGE: `*` (404)

"404 — הדף לא נמצא" (H1), "נראה שהדף שחיפשתם לא קיים." button "חזרה לעמוד הבית" → /

---

## DESIGN SYSTEM

Colors (soft pink/gold for baby girl):
- Primary: rose pink (#E8B4C0)
- Accent: gold (#C9A84C)
- Background: cream (#FDFAF6)
- Text: dark charcoal (#2D2D2D)
- Error: #DC2626 | Success: #16A34A | Warning: #D97706

Typography: Heebo (Google Fonts). H1: 2.5rem, H2: 1.75rem, body: min 16px. Line height 1.6.
Buttons: min height 48px, border-radius 8px, all touch targets min 48x48px.
Toasts: appear top-center, auto-dismiss 4 seconds.
Loading skeleton: animated grey pulse matching content shape.

---

## CRITICAL IMPLEMENTATION NOTES

1. editCode is always UUID v4 — use crypto.randomUUID(). Never expose sequential ID.
2. Phone is the RSVP unique key — query by normalized phone before creating. Return 409 with existingEditCode if duplicate.
3. Number inputs in RTL MUST have dir="ltr" and inputmode="numeric" — without this, digits render reversed.
4. WhatsApp phone format — always https://wa.me/972XXXXXXXXX (no + sign, no dashes). Convert 0501234567 → 972501234567.
5. CSV Export — prepend ﻿ (UTF-8 BOM) for Israeli Excel compatibility.
6. OG image — must be absolute URL (https://). Use window.location.origin + relative path if needed.
7. Gallery — do NOT render the section at all if galleryImages is empty/null.
8. All visible text comes from EventSettings — never hardcode event-specific content in JSX.
9. Session expiry — 401 from any admin API → clear adminToken from localStorage → redirect to /admin.
10. Admin default password warning — show persistent banner if password still matches default.
11. QR code — encode full URL {origin}/rsvp using react-qr-code or equivalent.
12. ICS file — generate valid VCALENDAR with DTSTART, DTEND, SUMMARY, DESCRIPTION, LOCATION. Prepend "data:text/calendar;charset=utf-8," for download link.
13. remindersSent — append {type, sentAt: new Date().toISOString()} and save to RSVP on WhatsApp button click.
14. Loading states — Skeleton UI on public pages while EventSettings loads; skeleton table rows in admin while RSVP list loads.
```
