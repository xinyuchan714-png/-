#!/usr/bin/env python3
"""舊振南後端：訂單、物流、網站點擊、QR 掃描、回禮統計。
只用 Python 標準函式庫，不需安裝任何套件。  執行：python3 server.py --seed
"""
import os, sys, re, json, html, hmac, random, sqlite3, secrets
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("DB_PATH", os.path.join(BASE, "bakery.db"))
def _arg(name):
    a = sys.argv
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else None
# 管理密碼優先順序：命令列 --token > 環境變數 ADMIN_TOKEN > 預設 change-me
TOKEN = (_arg("--token") or os.environ.get("ADMIN_TOKEN") or "change-me").strip()
TOKEN_SRC = "命令列 --token" if _arg("--token") else ("環境變數 ADMIN_TOKEN" if os.environ.get("ADMIN_TOKEN") else "預設值")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
PRICES = {"綠豆椪": 45, "鳳梨酥": 40, "蛋黃酥": 50, "抹茶咖啡": 55, "養生龍眼": 55}  # 與前端一致，價格以後端為準
ORDER_ST = ["待付款", "已付款", "製作中", "已出貨", "已完成", "已取消"]
SHIP_ST = ["待出貨", "已取貨", "運送中", "配送中", "已送達", "異常"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, order_no TEXT UNIQUE, customer_name TEXT, phone TEXT, email TEXT,
 recipient_name TEXT, recipient_phone TEXT, address TEXT, box_size INTEGER, items TEXT, total INTEGER, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS shipments(id INTEGER PRIMARY KEY, order_id INTEGER UNIQUE REFERENCES orders(id), carrier TEXT DEFAULT '',
 tracking_no TEXT DEFAULT '', status TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS shipment_events(id INTEGER PRIMARY KEY, shipment_id INTEGER REFERENCES shipments(id), status TEXT, note TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS gifts(id INTEGER PRIMARY KEY, code TEXT UNIQUE, order_id INTEGER REFERENCES orders(id), sender_name TEXT,
 message TEXT, glyph TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, type TEXT, path TEXT, target TEXT, gift_code TEXT, session TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS replies(id INTEGER PRIMARY KEY, gift_code TEXT, name TEXT, message TEXT, return_gift INTEGER, ts TEXT);
CREATE INDEX IF NOT EXISTS ix_ev ON events(type, ts);
CREATE INDEX IF NOT EXISTS ix_ev_gift ON events(gift_code);
"""

def now(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c
def rows(c, sql, a=()): return [dict(r) for r in c.execute(sql, a).fetchall()]
def one(c, sql, a=()):
    r = c.execute(sql, a).fetchone(); return dict(r) if r else None
def init():
    c = db(); c.execute("PRAGMA journal_mode=WAL"); c.executescript(SCHEMA); c.commit(); c.close()

# ---------- 業務邏輯 ----------
def clean(v, n): return str(v or "").strip()[:n]

def create_order(c, d, ts=None):
    ts = ts or now()
    items = d.get("items")
    if not isinstance(items, list) or len(items) not in (4, 6, 9): raise ValueError("禮盒必須是 4、6 或 9 格")
    if any(i not in PRICES for i in items): raise ValueError("含有未知的餅品")
    name, phone = clean(d.get("customer_name"), 30), clean(d.get("phone"), 20)
    if not name or not re.fullmatch(r"[0-9+\-\s]{8,20}", phone): raise ValueError("請填寫訂購人姓名與正確電話")
    total = sum(PRICES[i] for i in items)  # 金額一律由後端計算，不信任前端
    no = "HY" + ts[:10].replace("-", "") + "%04d" % random.randint(0, 9999)
    cur = c.execute("INSERT INTO orders(order_no,customer_name,phone,email,recipient_name,recipient_phone,address,box_size,items,total,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (no, name, phone, clean(d.get("email"), 80), clean(d.get("recipient_name"), 30) or name, clean(d.get("recipient_phone"), 20) or phone,
         clean(d.get("address"), 120), len(items), json.dumps(items, ensure_ascii=False), total, "待付款", ts))
    oid = cur.lastrowid
    c.execute("INSERT INTO shipments(order_id,status,updated_at) VALUES(?,?,?)", (oid, "待出貨", ts))
    code = None
    if clean(d.get("message"), 80) or clean(d.get("glyph"), 2):
        code = secrets.token_urlsafe(6).replace("-", "a").replace("_", "b")[:8]  # 隨機、不可猜測
        c.execute("INSERT INTO gifts(code,order_id,sender_name,message,glyph,created_at) VALUES(?,?,?,?,?,?)",
            (code, oid, clean(d.get("sender_name"), 12) or name, clean(d.get("message"), 80), clean(d.get("glyph"), 2) or "囍", ts))
    return no, code, total

def summary(c):
    s = {}
    o = one(c, "SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN status!='已取消' THEN total END),0) r FROM orders")
    s["orders"], s["revenue"] = o["n"], o["r"]
    s["order_status"] = {r["status"]: r["n"] for r in rows(c, "SELECT status,COUNT(*) n FROM orders GROUP BY status")}
    s["ship_status"] = {r["status"]: r["n"] for r in rows(c, "SELECT status,COUNT(*) n FROM shipments GROUP BY status")}
    p = one(c, "SELECT COUNT(*) n, COUNT(DISTINCT session) u FROM events WHERE type='pageview'")
    s["pageviews"], s["visitors"] = p["n"], p["u"]
    s["clicks"] = one(c, "SELECT COUNT(*) n FROM events WHERE type='click'")["n"]
    s["top_clicks"] = rows(c, "SELECT target, COUNT(*) n FROM events WHERE type='click' AND target!='' GROUP BY target ORDER BY n DESC LIMIT 8")
    s["gifts"] = one(c, "SELECT COUNT(*) n FROM gifts")["n"]
    q = one(c, "SELECT COUNT(*) n, COUNT(DISTINCT gift_code) g FROM events WHERE type='qr_scan'")
    s["scans"], s["gifts_scanned"] = q["n"], q["g"]
    r = one(c, "SELECT COUNT(*) n, COALESCE(SUM(return_gift),0) rg FROM replies")
    s["replies"], s["return_gifts"] = r["n"], r["rg"]
    days = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, -1, -1)]
    def per(sql): return {r["d"]: r["n"] for r in rows(c, sql)}
    pv = per("SELECT substr(ts,1,10) d,COUNT(*) n FROM events WHERE type='pageview' GROUP BY d")
    sc = per("SELECT substr(ts,1,10) d,COUNT(*) n FROM events WHERE type='qr_scan' GROUP BY d")
    od = per("SELECT substr(created_at,1,10) d,COUNT(*) n FROM orders GROUP BY d")
    rp = per("SELECT substr(ts,1,10) d,COUNT(*) n FROM replies GROUP BY d")
    s["daily"] = [{"d": d, "pageviews": pv.get(d, 0), "scans": sc.get(d, 0), "orders": od.get(d, 0), "replies": rp.get(d, 0)} for d in days]
    return s

GIFT_PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>一份來自 {{FROM}} 的祝福</title><style>
body{margin:0;background:#efe7d8;color:#2a1f17;font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif;line-height:1.8;display:grid;place-items:center;min-height:100vh;padding:20px}
main{max-width:420px;width:100%;background:#f8f2e6;border:1px solid #d8cbb5;padding:28px 22px;text-align:center}
.g{width:96px;height:96px;margin:0 auto 12px;border-radius:50%;background:radial-gradient(circle at 35% 30%,#f2dfb8,#cfa86f);display:grid;place-items:center;font:700 2.4rem serif;color:#c23a32}
h1{font:700 1.3rem serif;letter-spacing:.1em;margin:0 0 6px}p.m{white-space:pre-wrap;word-break:break-word}
label{display:block;text-align:left;margin-top:10px;font-weight:500}input[type=text],textarea{width:100%;font:inherit;padding:8px;border:1.5px solid #d8cbb5;background:#fff;color:#2a1f17;box-sizing:border-box}
button{margin-top:14px;font:inherit;font-weight:700;background:#2a1d14;color:#e9cf8a;border:0;padding:10px 24px;cursor:pointer}
.ck{display:flex;gap:8px;align-items:center;text-align:left;margin-top:10px}#ok{color:#1f5a45;font-weight:700}
</style></head><body><main><div class="g">{{GLYPH}}</div><h1>來自 {{FROM}} 的祝福</h1><p class="m">{{MSG}}</p>
<form id="f"><label for="n">您的大名</label><input type="text" id="n" maxlength="20" required>
<label for="t">想回覆的話</label><textarea id="t" rows="3" maxlength="100"></textarea>
<label class="ck"><input type="checkbox" id="r"> 我也想回送禮物給對方</label><button>送出回覆</button><p id="ok" role="status"></p></form></main>
<script>document.getElementById("f").onsubmit=function(e){e.preventDefault();fetch("/api/gifts/{{CODE}}/reply",{method:"POST",body:JSON.stringify({name:n.value,message:t.value,return_gift:r.checked})})
.then(function(x){document.getElementById("ok").textContent=x.ok?"已送出，謝謝您！":"送出失敗，請稍後再試"})}</script></body></html>"""

# ---------- HTTP ----------
class H(BaseHTTPRequestHandler):
    server_version = "Heyue/1.0"
    def log_message(self, *a): pass
    def out(self, code, data, ctype="application/json; charset=utf-8", cors=False):
        if code == 204: data = b""
        elif isinstance(data, (dict, list)): data = json.dumps(data, ensure_ascii=False).encode()
        elif isinstance(data, str): data = data.encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Cache-Control", "no-store")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers(); self.wfile.write(data)
    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 65536: raise ValueError("內容過大")
        d = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
        if not isinstance(d, dict): raise ValueError("格式錯誤")
        return d
    def is_admin(self):
        return hmac.compare_digest(self.headers.get("X-Admin-Token", "").strip().encode("utf-8"), TOKEN.encode("utf-8"))
    def file(self, name, ctype, cors=False):
        p = os.path.join(BASE, name)
        if not os.path.exists(p): return self.out(404, {"error": "not found"})
        self.out(200, open(p, "rb").read(), ctype, cors)

    def route(self, m):
        p, q = urlparse(self.path).path, parse_qs(urlparse(self.path).query)
        try:
            if m == "OPTIONS": return self.out(204, b"", cors=True)
            if m == "GET":
                if p in ("/", "/index.html"): return self.file("index.html", "text/html; charset=utf-8")
                if p == "/admin": return self.file("admin.html", "text/html; charset=utf-8")
                if p == "/tracker.js": return self.file("tracker.js", "application/javascript; charset=utf-8", True)
                g = re.fullmatch(r"/g/([A-Za-z0-9]{6,16})", p)
                if g: return self.gift_page(g.group(1))
            if p.startswith("/api/admin/"):
                if not self.is_admin(): return self.out(401, {"error": "unauthorized"})
                return self.admin(m, p, q)
            if m == "POST":
                if p == "/api/track":
                    d = self.body(); t = d.get("type")
                    if t not in ("pageview", "click"): raise ValueError("type 不正確")
                    c = db()
                    with c: c.execute("INSERT INTO events(type,path,target,session,ts) VALUES(?,?,?,?,?)", (t, clean(d.get("path"), 120), clean(d.get("target"), 40), clean(d.get("session"), 40), now()))
                    c.close(); return self.out(204, b"", cors=True)
                if p == "/api/orders":
                    c = db()
                    with c: no, code, total = create_order(c, self.body())
                    c.close()
                    return self.out(201, {"order_no": no, "total": total, "gift_url": ("/g/" + code) if code else None}, cors=True)
                r = re.fullmatch(r"/api/gifts/([A-Za-z0-9]{6,16})/reply", p)
                if r:
                    d = self.body(); c = db()
                    if not one(c, "SELECT 1 x FROM gifts WHERE code=?", (r.group(1),)): c.close(); return self.out(404, {"error": "not found"})
                    if not clean(d.get("name"), 20): c.close(); raise ValueError("請填寫姓名")
                    with c: c.execute("INSERT INTO replies(gift_code,name,message,return_gift,ts) VALUES(?,?,?,?,?)", (r.group(1), clean(d.get("name"), 20), clean(d.get("message"), 100), 1 if d.get("return_gift") else 0, now()))
                    c.close(); return self.out(201, {"ok": True}, cors=True)
            self.out(404, {"error": "not found"})
        except (ValueError, json.JSONDecodeError) as e: self.out(400, {"error": str(e)}, cors=True)
        except Exception as e:
            print("ERROR", repr(e)); self.out(500, {"error": "server error"})

    def gift_page(self, code):
        c = db(); g = one(c, "SELECT * FROM gifts WHERE code=?", (code,))
        if not g: c.close(); return self.out(404, "<p>找不到這份祝福</p>", "text/html; charset=utf-8")
        with c: c.execute("INSERT INTO events(type,path,gift_code,session,ts) VALUES('qr_scan',?,?,?,?)", ("/g/" + code, code, "", now()))
        c.close()
        page = GIFT_PAGE
        for k, v in (("FROM", g["sender_name"]), ("MSG", g["message"]), ("GLYPH", g["glyph"]), ("CODE", code)): page = page.replace("{{%s}}" % k, html.escape(v or ""))
        self.out(200, page, "text/html; charset=utf-8")

    def admin(self, m, p, q):
        c = db()
        try:
            if m == "GET":
                if p == "/api/admin/summary": return self.out(200, summary(c))
                if p == "/api/admin/orders":
                    st = q.get("status", [""])[0]
                    r = rows(c, "SELECT o.*, s.id ship_id, s.status ship_status FROM orders o LEFT JOIN shipments s ON s.order_id=o.id" + (" WHERE o.status=?" if st else "") + " ORDER BY o.id DESC LIMIT 200", (st,) if st else ())
                    for x in r: x["items"] = json.loads(x["items"])
                    return self.out(200, {"rows": r, "statuses": ORDER_ST})
                if p == "/api/admin/shipments":
                    return self.out(200, {"rows": rows(c, "SELECT s.*, o.order_no, o.recipient_name, o.recipient_phone, o.address FROM shipments s JOIN orders o ON o.id=s.order_id ORDER BY s.updated_at DESC LIMIT 200"), "statuses": SHIP_ST})
                if p == "/api/admin/gifts":
                    return self.out(200, rows(c, "SELECT g.code,g.sender_name,g.message,g.created_at,o.order_no,(SELECT COUNT(*) FROM events e WHERE e.type='qr_scan' AND e.gift_code=g.code) scans,(SELECT COUNT(*) FROM replies r WHERE r.gift_code=g.code) replies FROM gifts g JOIN orders o ON o.id=g.order_id ORDER BY g.id DESC LIMIT 200"))
                if p == "/api/admin/replies":
                    return self.out(200, rows(c, "SELECT * FROM replies ORDER BY id DESC LIMIT 200"))
            if m == "PATCH":
                d = self.body(); mo = re.fullmatch(r"/api/admin/orders/(\d+)", p); ms = re.fullmatch(r"/api/admin/shipments/(\d+)", p)
                if mo:
                    if d.get("status") not in ORDER_ST: raise ValueError("狀態不正確")
                    with c: c.execute("UPDATE orders SET status=? WHERE id=?", (d["status"], mo.group(1)))
                    return self.out(200, {"ok": True})
                if ms:
                    s = one(c, "SELECT * FROM shipments WHERE id=?", (ms.group(1),))
                    if not s: return self.out(404, {"error": "not found"})
                    st = d.get("status", s["status"])
                    if st not in SHIP_ST: raise ValueError("狀態不正確")
                    with c:
                        c.execute("UPDATE shipments SET status=?,carrier=?,tracking_no=?,updated_at=? WHERE id=?", (st, clean(d.get("carrier", s["carrier"]), 30), clean(d.get("tracking_no", s["tracking_no"]), 40), now(), s["id"]))
                        if st != s["status"]:
                            c.execute("INSERT INTO shipment_events(shipment_id,status,note,ts) VALUES(?,?,?,?)", (s["id"], st, clean(d.get("note"), 80), now()))
                            nxt = {"已取貨": "已出貨", "運送中": "已出貨", "已送達": "已完成"}.get(st)  # 物流進度同步到訂單狀態
                            if nxt: c.execute("UPDATE orders SET status=? WHERE id=? AND status!='已取消'", (nxt, s["order_id"]))
                    return self.out(200, {"ok": True})
            self.out(404, {"error": "not found"})
        finally: c.close()
    def do_GET(self): self.route("GET")
    def do_POST(self): self.route("POST")
    def do_PATCH(self): self.route("PATCH")
    def do_OPTIONS(self): self.route("OPTIONS")

# ---------- 範例資料 ----------
def seed():
    c = db()
    if one(c, "SELECT COUNT(*) n FROM orders")["n"]: print("已有資料，略過範例資料"); return
    random.seed(7); N = "王小明 陳雅婷 林志豪 黃怡君 張家豪 李佩珊 吳宗翰 劉子晴 蔡明哲 楊淑芬".split()
    A = ["台北市信義區松仁路100號", "新北市板橋區文化路一段50號", "台中市西屯區文心路三段200號", "高雄市前鎮區中山二路30號", "桃園市中壢區中正路88號"]
    MSG = ["中秋快樂，平安喜樂！", "謝謝您一直以來的照顧。", "新婚快樂，百年好合。", "祝福您闔家團圓。"]
    T = ["自選禮盒", "加入購物車", "專屬心意", "門市限定", "MENU", "隨機填滿", "線上購物"]
    t0 = datetime.now()
    for i in range(40):
        ts = t0 - timedelta(days=random.randint(0, 13), hours=random.randint(0, 20), minutes=random.randint(0, 59)); age = (t0 - ts).days
        a = random.choice(A); n = random.choice(N)
        with c:
            no, code, _ = create_order(c, {"customer_name": n, "phone": "09%08d" % random.randint(0, 99999999), "recipient_name": random.choice(N), "address": a,
                "items": [random.choice(list(PRICES)) for _ in range(random.choice([4, 6, 6, 9]))], "message": random.choice(MSG) if random.random() < .75 else "", "glyph": random.choice("囍福壽慶")}, ts.strftime("%Y-%m-%d %H:%M:%S"))
            stage = 5 if random.random() < .05 else min(4, age // 2 + random.randint(0, 2))
            os_, ss = [("待付款", "待出貨"), ("已付款", "待出貨"), ("製作中", "待出貨"), ("已出貨", random.choice(["已取貨", "運送中", "配送中"])), ("已完成", "已送達"), ("已取消", "待出貨")][stage]
            c.execute("UPDATE orders SET status=? WHERE order_no=?", (os_, no))
            c.execute("UPDATE shipments SET status=?,carrier=?,tracking_no=? WHERE order_id=(SELECT id FROM orders WHERE order_no=?)", (ss, random.choice(["黑貓宅急便", "新竹物流"]) if stage >= 3 else "", "%010d" % random.randint(0, 10**10 - 1) if stage >= 3 else "", no))
            if code and stage >= 3:
                for _ in range(random.randint(0, 4)):
                    st = (ts + timedelta(days=random.randint(1, 3), hours=random.randint(0, 23))).strftime("%Y-%m-%d %H:%M:%S")
                    if st <= now(): c.execute("INSERT INTO events(type,path,gift_code,ts) VALUES('qr_scan',?,?,?)", ("/g/" + code, code, st))
                if random.random() < .4: c.execute("INSERT INTO replies(gift_code,name,message,return_gift,ts) VALUES(?,?,?,?,?)", (code, random.choice(N), "謝謝，收到了！", 1 if random.random() < .4 else 0, now()))
    for d in range(14):
        for _ in range(random.randint(25, 70)):
            ts = (t0 - timedelta(days=d, hours=random.randint(0, 23), minutes=random.randint(0, 59))).strftime("%Y-%m-%d %H:%M:%S"); s = "s%d" % random.randint(1, 400)
            c.execute("INSERT INTO events(type,path,session,ts) VALUES('pageview','/',?,?)", (s, ts))
            if random.random() < .6: c.execute("INSERT INTO events(type,path,target,session,ts) VALUES('click','/',?,?,?)", (random.choice(T), s, ts))
    c.commit(); c.close(); print("已建立範例資料")

if __name__ == "__main__":
    init()
    if "--seed" in sys.argv: seed()
    if TOKEN == "change-me": print("注意：目前使用預設管理密碼 change-me，正式使用前請改掉（python server.py --token 您的密碼）")
    else: print("管理密碼已設定（來源：%s，共 %d 個字元）" % (TOKEN_SRC, len(TOKEN)))
    try: srv = ThreadingHTTPServer((HOST, PORT), H)
    except OSError:
        print("無法啟動：%d 號埠已被使用。請先關掉其他黑色視窗（舊的後端還在跑），再重試。" % PORT); sys.exit(1)
    print("後台：http://%s:%d/admin" % (HOST, PORT))
    srv.serve_forever()
