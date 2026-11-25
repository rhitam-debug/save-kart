import os, sqlite3, csv, io, secrets, hmac, hashlib, time
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, request, jsonify, render_template, send_file, abort, session
import qrcode
from qrcode.image.svg import SvgPathImage

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(APP_DIR, "savekart.db"))
ADMIN_PIN = os.environ.get("ADMIN_PIN", "2468")
KITCHEN_PIN = os.environ.get("KITCHEN_PIN", "1357")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:5000")
app = Flask(__name__)
APP_SECRET = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(48)
app.secret_key = APP_SECRET

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS tables (
          id INTEGER PRIMARY KEY AUTOINCREMENT, table_no INTEGER UNIQUE NOT NULL,
          token TEXT UNIQUE NOT NULL, active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS menu_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL,
          description TEXT DEFAULT '', price INTEGER NOT NULL, veg INTEGER DEFAULT 1,
          available INTEGER DEFAULT 1, prep_minutes INTEGER DEFAULT 10
        );
        CREATE TABLE IF NOT EXISTS orders (
          id INTEGER PRIMARY KEY AUTOINCREMENT, table_no INTEGER NOT NULL,
          customer_name TEXT DEFAULT '', note TEXT DEFAULT '', total INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'NEW', payment_status TEXT NOT NULL DEFAULT 'PENDING',
          payment_ref TEXT DEFAULT '', eta_minutes INTEGER DEFAULT 15,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS order_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL,
          menu_item_id INTEGER, item_name TEXT NOT NULL, quantity INTEGER NOT NULL,
          unit_price INTEGER NOT NULL, preference TEXT DEFAULT '',
          FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS reviews (
          id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL,
          item_name TEXT NOT NULL, rating INTEGER NOT NULL, comment TEXT DEFAULT '',
          created_at TEXT NOT NULL, UNIQUE(order_id,item_name),
          FOREIGN KEY(order_id) REFERENCES orders(id)
        );
        CREATE TABLE IF NOT EXISTS complaints (
          id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, category TEXT NOT NULL,
          message TEXT NOT NULL, created_at TEXT NOT NULL, resolved INTEGER DEFAULT 0
        );
        """)
        if c.execute("SELECT COUNT(*) FROM tables").fetchone()[0] == 0:
            for n in range(1, 11):
                token = hmac.new(APP_SECRET.encode(), f"save-kart-table-{n}".encode(), hashlib.sha256).hexdigest()[:32]
                c.execute("INSERT INTO tables(table_no,token) VALUES(?,?)", (n, token))
        if c.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0] == 0:
            sample = [
              ("Egg Roll","Rolls","Classic street-style egg roll",60,0,1,8),
              ("Paneer Roll","Rolls","Paneer, onion and house sauce",70,1,1,10),
              ("Chicken Roll","Rolls","Spiced chicken roll",85,0,1,12),
              ("Veg Chowmein","Noodles","Stir-fried noodles and vegetables",70,1,1,12),
              ("Chicken Chowmein","Noodles","Chicken noodles",95,0,1,15),
              ("Cold Coffee","Drinks","Chilled coffee",60,1,1,5),
              ("Masala Maggi","Quick Bites","Classic masala noodles",45,1,1,7)
            ]
            c.executemany("INSERT INTO menu_items(name,category,description,price,veg,available,prep_minutes) VALUES(?,?,?,?,?,?,?)", sample)

def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def kitchen_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("kitchen") and not session.get("admin"):
            return jsonify({"error":"Kitchen login required"}), 401
        return fn(*a, **kw)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return jsonify({"error":"Admin login required"}), 401
        return fn(*a, **kw)
    return wrapper

@app.get("/")
def home():
    return render_template("index.html")

@app.get("/api/menu")
def menu():
    with db() as c:
        rows = c.execute("SELECT * FROM menu_items WHERE available=1 ORDER BY category,name").fetchall()
        ratings = {r["item_name"]:r["avg"] for r in c.execute("SELECT item_name,ROUND(AVG(rating),1) avg FROM reviews GROUP BY item_name")}
        return jsonify([dict(r, rating=ratings.get(r["name"])) for r in rows])

@app.get("/api/table/<int:table_no>")
def table_info(table_no):
    with db() as c:
        t = c.execute("SELECT table_no,token,active FROM tables WHERE table_no=?", (table_no,)).fetchone()
        if not t or not t["active"]: return jsonify({"error":"Table unavailable"}),404
        return jsonify(dict(t))

@app.post("/api/order")
def create_order():
    data = request.get_json(force=True)
    table_no = int(data.get("table_no",0))
    items = data.get("items",[])
    if not items: return jsonify({"error":"Cart is empty"}),400
    with db() as c:
        t = c.execute("SELECT * FROM tables WHERE table_no=? AND active=1", (table_no,)).fetchone()
        supplied_token = str(data.get("table_token", ""))
        if not t or not supplied_token or not hmac.compare_digest(supplied_token, t["token"]):
            return jsonify({"error":"Invalid table QR token. Scan the QR code again."}),400
        total, cooked = 0, []
        for item in items:
            m = c.execute("SELECT * FROM menu_items WHERE id=? AND available=1", (int(item["id"]),)).fetchone()
            qty = max(1,min(20,int(item.get("quantity",1))))
            if not m: return jsonify({"error":"An item is unavailable"}),409
            total += m["price"]*qty
            cooked.append((m,qty,(item.get("preference","") or "")[:160]))
        stamp = now()
        # MVP payment mode is explicitly marked DEMO; no real money is collected.
        pay = data.get("payment_method","DEMO").upper()
        payment_status = "DEMO_PAID" if pay == "DEMO" else "PENDING"
        cur = c.execute("INSERT INTO orders(table_no,customer_name,note,total,status,payment_status,eta_minutes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (table_no,(data.get("customer_name","") or "")[:80],(data.get("note","") or "")[:250],total,"NEW",payment_status,15,stamp,stamp))
        oid = cur.lastrowid
        for m,qty,pref in cooked:
            c.execute("INSERT INTO order_items(order_id,menu_item_id,item_name,quantity,unit_price,preference) VALUES(?,?,?,?,?,?)",
                      (oid,m["id"],m["name"],qty,m["price"],pref))
        return jsonify({"order_id":oid,"total":total,"status":"NEW","payment_status":payment_status,"message":"Demo order placed"}),201

@app.get("/api/order/<int:oid>")
def get_order(oid):
    with db() as c:
        o = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not o: return jsonify({"error":"Order not found"}),404
        items = c.execute("SELECT * FROM order_items WHERE order_id=?", (oid,)).fetchall()
        reviews = c.execute("SELECT * FROM reviews WHERE order_id=?", (oid,)).fetchall()
        return jsonify({"order":dict(o),"items":[dict(x) for x in items],"reviews":[dict(x) for x in reviews]})

@app.post("/api/review")
def review():
    d=request.get_json(force=True); oid=int(d.get("order_id",0)); rating=int(d.get("rating",0))
    if rating<1 or rating>5: return jsonify({"error":"Rating must be 1–5"}),400
    with db() as c:
        o=c.execute("SELECT status FROM orders WHERE id=?",(oid,)).fetchone()
        if not o or o["status"]!="COMPLETED": return jsonify({"error":"Reviews unlock after order completion"}),403
        try:
            c.execute("INSERT INTO reviews(order_id,item_name,rating,comment,created_at) VALUES(?,?,?,?,?)",
                (oid,(d.get("item_name","") or "")[:100],rating,(d.get("comment","") or "")[:500],now()))
        except sqlite3.IntegrityError:
            return jsonify({"error":"This item has already been reviewed for this order"}),409
    return jsonify({"ok":True})

@app.post("/api/complaint")
def complaint():
    d=request.get_json(force=True)
    if not (d.get("message") or "").strip(): return jsonify({"error":"Please enter a message"}),400
    with db() as c:
        c.execute("INSERT INTO complaints(order_id,category,message,created_at) VALUES(?,?,?,?)",
          (d.get("order_id"),(d.get("category","Other") or "Other")[:60],d["message"][:1000],now()))
    return jsonify({"ok":True}),201

@app.post("/api/login")
def login():
    d=request.get_json(force=True); role=d.get("role"); pin=str(d.get("pin",""))
    if role=="admin" and hmac.compare_digest(pin,ADMIN_PIN): session["admin"]=True; return jsonify({"ok":True})
    if role=="kitchen" and hmac.compare_digest(pin,KITCHEN_PIN): session["kitchen"]=True; return jsonify({"ok":True})
    return jsonify({"error":"Incorrect PIN"}),401

@app.post("/api/logout")
def logout():
    session.clear(); return jsonify({"ok":True})

@app.get("/api/kitchen/orders")
@kitchen_required
def kitchen_orders():
    with db() as c:
        orders=c.execute("SELECT * FROM orders WHERE status NOT IN ('COMPLETED','CANCELLED') ORDER BY created_at ASC").fetchall()
        result=[]
        for o in orders:
            x=dict(o); x["items"]=[dict(i) for i in c.execute("SELECT * FROM order_items WHERE order_id=?",(o["id"],))]
            result.append(x)
        return jsonify(result)

@app.patch("/api/kitchen/order/<int:oid>")
@kitchen_required
def update_order(oid):
    d=request.get_json(force=True); status=d.get("status")
    allowed={"ACCEPTED","PREPARING","READY","COMPLETED","CANCELLED"}
    if status not in allowed: return jsonify({"error":"Invalid status"}),400
    eta=max(1,min(180,int(d.get("eta_minutes",15))))
    with db() as c:
        cur=c.execute("UPDATE orders SET status=?,eta_minutes=?,updated_at=? WHERE id=?",(status,eta,now(),oid))
        if not cur.rowcount:return jsonify({"error":"Order not found"}),404
    return jsonify({"ok":True})

@app.get("/api/admin/summary")
@admin_required
def summary():
    with db() as c:
        today=now()[:10]
        sales=c.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE substr(created_at,1,10)=? AND payment_status IN ('DEMO_PAID','PAID') AND status!='CANCELLED'",(today,)).fetchone()[0]
        count=c.execute("SELECT COUNT(*) FROM orders WHERE substr(created_at,1,10)=?",(today,)).fetchone()[0]
        popular=c.execute("SELECT item_name,SUM(quantity) qty FROM order_items GROUP BY item_name ORDER BY qty DESC LIMIT 5").fetchall()
        reviews=c.execute("SELECT item_name,ROUND(AVG(rating),1) rating,COUNT(*) count FROM reviews GROUP BY item_name ORDER BY rating DESC").fetchall()
        complaints=c.execute("SELECT * FROM complaints ORDER BY created_at DESC LIMIT 30").fetchall()
        recent=c.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 30").fetchall()
        return jsonify({"sales_today":sales,"orders_today":count,"popular":[dict(x) for x in popular],"ratings":[dict(x) for x in reviews],"complaints":[dict(x) for x in complaints],"recent":[dict(x) for x in recent]})

@app.post("/api/admin/menu")
@admin_required
def add_menu():
    d=request.get_json(force=True)
    if not d.get("name") or int(d.get("price",0))<1:return jsonify({"error":"Name and valid price required"}),400
    with db() as c:
        c.execute("INSERT INTO menu_items(name,category,description,price,veg,available,prep_minutes) VALUES(?,?,?,?,?,?,?)",
          (d["name"][:100],d.get("category","Other")[:60],d.get("description","")[:250],int(d["price"]),int(bool(d.get("veg",True))),1,max(1,int(d.get("prep_minutes",10)))))
    return jsonify({"ok":True}),201

@app.patch("/api/admin/menu/<int:item_id>")
@admin_required
def menu_toggle(item_id):
    d=request.get_json(force=True)
    with db() as c:
        c.execute("UPDATE menu_items SET available=? WHERE id=?",(int(bool(d.get("available"))),item_id))
    return jsonify({"ok":True})

@app.get("/api/admin/export.csv")
@admin_required
def export_csv():
    out=io.StringIO(); w=csv.writer(out)
    w.writerow(["Order ID","Date","Table","Total INR","Status","Payment Status","Item","Quantity","Unit Price","Preference"])
    with db() as c:
        rows=c.execute("""SELECT o.id,o.created_at,o.table_no,o.total,o.status,o.payment_status,i.item_name,i.quantity,i.unit_price,i.preference
          FROM orders o JOIN order_items i ON i.order_id=o.id ORDER BY o.id""").fetchall()
        for r in rows:w.writerow(list(r))
    mem=io.BytesIO(out.getvalue().encode("utf-8-sig"))
    return send_file(mem,mimetype="text/csv",as_attachment=True,download_name="save-kart-orders.csv")

@app.get("/qr/<int:table_no>.svg")
def qr(table_no):
    with db() as c:
        t=c.execute("SELECT token FROM tables WHERE table_no=? AND active=1",(table_no,)).fetchone()
    if not t:abort(404)
    url=f"{PUBLIC_BASE_URL}/?table={table_no}&key={t['token']}"
    img=qrcode.make(url,image_factory=SvgPathImage,box_size=8,border=3)
    b=io.BytesIO();img.save(b);b.seek(0)
    return send_file(b,mimetype="image/svg+xml")

@app.get("/health")
def health(): return jsonify({"status":"ok","app":"SAVE KART","mode":"demo"})

init_db()
if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=os.environ.get("FLASK_DEBUG")=="1")
