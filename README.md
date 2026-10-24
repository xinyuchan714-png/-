# 舊振南後端

只需要 Python 3.8 以上，不必安裝任何套件。

## 啟動
    python3 server.py --seed        （Windows 請用 python server.py --seed）
然後開啟 http://127.0.0.1:8000/admin ，預設管理密碼為 change-me。
`--seed` 會建立示範資料，正式使用時請拿掉這個參數。

## 正式使用前
- 設定密碼：`ADMIN_TOKEN=你的長密碼 python3 server.py`（Windows：`set ADMIN_TOKEN=...`）
- 對外服務請放在 HTTPS 反向代理（Nginx、Caddy 等）後面，並設定 `HOST=0.0.0.0`
- 備份 `bakery.db` 即可備份全部資料

## 接上網站
1. 在網站 `</body>` 前加入：`<script src="https://你的網址/tracker.js"></script>`，即可統計瀏覽與點擊。
2. 結帳時呼叫 `POST /api/orders`：
       {"customer_name":"王小明","phone":"0912345678","address":"…","items":["綠豆椪","鳳梨酥","蛋黃酥","綠豆椪","鳳梨酥","蛋黃酥"],
        "sender_name":"小明","message":"中秋快樂","glyph":"囍"}
   回傳 `order_no`、`total`（金額由後端計算）與 `gift_url`，把 `gift_url` 做成 QR Code 印在小卡上。
3. 收禮者掃 QR → `/g/<代碼>`：自動記錄掃描數，並可留言回覆；勾選「我也想回送禮物」會列入回禮統計。

## 指標定義
- QR 掃描數：祝福頁被開啟的次數（同一人重複掃會重複計算）
- 回禮人數：在祝福頁留下回覆的人數；回覆率 = 回覆數 ÷ 已被掃描的祝福數

## 連接前端（已幫您接好）
- `index.html` 就是已接上後端的網站。放在同一個資料夾，啟動後開啟 http://127.0.0.1:8000/ 即可。
- 在網站排好禮盒、填資料、按「送出訂單」，後台的「訂購狀況」就會出現這筆訂單，並產生真正的 QR Code。
- 前端與後端放在不同網址時（例如網站放 GitHub Pages）：把 `index.html` 裡的 `API_BASE` 改成後端網址，
  並把 `<script src="/tracker.js">` 改成 `<script src="https://後端網址/tracker.js">`。
