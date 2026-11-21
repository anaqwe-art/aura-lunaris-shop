import os
import json
import uuid
import time
import base64
import hashlib
import sqlite3
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ==========================================
# 基本設定（支援雲端環境變數與本機測試）
# ==========================================
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
DB_PATH = os.getenv("DB_PATH", "shop.db")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.path.join(STATIC_DIR, "uploads")
TOKEN_EXPIRE_DAYS = 7

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 免運門檻與運費設定
FREE_SHIPPING_THRESHOLD = 1000
SHIPPING_FEE_CVS = 60
SHIPPING_FEE_HOME = 100

# 綠界測試環境設定 (AIO V5 刷卡 & B2C 電子地圖)
ECPAY_AIO_URL = "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5"
ECPAY_AIO_MERCHANT_ID = "3002607"
ECPAY_AIO_HASH_KEY = "pwFHCqoQZGmho4w6"
ECPAY_AIO_HASH_IV = "EkRm7iFT261dpevs"

ECPAY_MAP_URL = "https://logistics-stage.ecpay.com.tw/Express/map"
ECPAY_MAP_MERCHANT_ID = "2000132"
ECPAY_MAP_HASH_KEY = "5294y06JbISpM5x9"
ECPAY_MAP_HASH_IV = "v77hoKGq4kWxNNIS"

app = FastAPI(title="AURA LUNARIS 時曜香殿 API")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_db(isolation_level: Optional[str] = ""):
    conn = sqlite3.connect(DB_PATH, timeout=15.0, isolation_level=isolation_level)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        sort_order INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        price INTEGER NOT NULL,
        prices_json TEXT DEFAULT '{}',
        stock INTEGER DEFAULT 20,
        description TEXT DEFAULT '',
        image_url TEXT DEFAULT '',
        images_json TEXT DEFAULT '[]',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        name TEXT NOT NULL,
        phone TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no TEXT UNIQUE NOT NULL,
        member_id INTEGER,
        customer_name TEXT NOT NULL,
        customer_phone TEXT NOT NULL,
        customer_email TEXT DEFAULT '',
        shipping_method TEXT NOT NULL,
        shipping_store TEXT DEFAULT '',
        shipping_address TEXT DEFAULT '',
        payment_method TEXT NOT NULL,
        items_json TEXT NOT NULL,
        subtotal INTEGER DEFAULT 0,
        shipping_fee INTEGER DEFAULT 0,
        total_amount INTEGER NOT NULL,
        status TEXT DEFAULT '待付款',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS auth_tokens (
        token TEXT PRIMARY KEY,
        role TEXT NOT NULL,
        member_id INTEGER,
        expires_at TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 舊資料庫欄位自動相容升級
    for col_sql in [
        "ALTER TABLE orders ADD COLUMN subtotal INTEGER DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN shipping_fee INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN prices_json TEXT DEFAULT '{}'",
        "ALTER TABLE products ADD COLUMN images_json TEXT DEFAULT '[]'",
    ]:
        try:
            cur.execute(col_sql)
        except sqlite3.OperationalError:
            pass

    # 預設 5 大香氛系列分類
    default_categories = [
        ("12 星座系列", 1),
        ("12 生肖系列", 2),
        ("森林系列", 3),
        ("果香系列", 4),
        ("花香系列", 5),
    ]
    for cname, sorder in default_categories:
        cur.execute(
            "INSERT OR IGNORE INTO categories (name, sort_order) VALUES (?, ?)",
            (cname, sorder),
        )

    # 若商品表為空，自動匯入品牌精選初始香款
    cur.execute("SELECT COUNT(*) FROM products")
    if cur.fetchone()[0] == 0:
        seed_products = [
            ("白羊座《初生赤焰》", "12 星座系列", 2680, {"30ml": 2680, "50ml": 3880, "100ml": 5680}, 25,
             "火象本命主香｜粉紅胡椒、血橙、生薑、焚香、雪松\n猶如劃破夜幕的第一道赤焰，辛辣血橙與溫潤雪松交織出果斷無畏的開創氣場。"),
            ("金牛座《青苔石階》", "12 星座系列", 2680, {"30ml": 2680, "50ml": 3880, "100ml": 5680}, 25,
             "土象本命主香｜佛手柑、無花果葉、橡木苔、廣藿香、鳶尾根\n晨霧中踏過百年青苔石階，沉穩廣藿香與粉質鳶尾根帶來大地般的溫厚療癒。"),
            ("雙子座《雙生之語》", "12 星座系列", 2680, {"30ml": 2680, "50ml": 3880, "100ml": 5680}, 25,
             "風象本命主香｜綠薄荷、羅勒、青檸、橙花、降龍涎香醚\n跳躍靈動的柑橘草本氣泡，在清冽與柔美之間自由切換雙面魅力。"),
            ("巨蟹座《月汐之擁》", "12 星座系列", 2680, {"30ml": 2680, "50ml": 3880, "100ml": 5680}, 25,
             "水象本命主香｜米香、椰奶、海鹽、洋甘菊、檀香木\n宛如月光下的溫柔潮汐，暖甜米香與細緻海鹽檀木輕輕包覆感性靈魂。"),
            ("獅子座《耀金王權》", "12 星座系列", 2880, {"30ml": 2880, "50ml": 4180, "100ml": 5980}, 20,
             "火象本命主香｜苦橙葉、琥珀、藏紅花、沒藥、岩蘭草\n熾熱藏紅花與珍稀沒藥琥珀綻放金色光芒，自帶無可忽視的領袖氣場。"),
            ("處女座《純白秩序》", "12 星座系列", 2680, {"30ml": 2680, "50ml": 3880, "100ml": 5680}, 25,
             "土象本命主香｜白松香、快樂鼠尾草、白茶、白麝香、冷杉\n極簡純淨的空氣感白茶與冷杉，為思緒梳理出安靜澄澈的秩序。"),
            ("天秤座《微風平衡》", "12 星座系列", 2680, {"30ml": 2680, "50ml": 3880, "100ml": 5680}, 25,
             "風象本命主香｜紫羅蘭葉、鈴蘭、水梨、喀什米爾木、白麝香\n優雅清透的水梨鈴蘭與羊絨木質，在任何距離都維持恰到好處的迷人平衡。"),
            ("天蠍座《深淵暗湧》", "12 星座系列", 2880, {"30ml": 2880, "50ml": 4180, "100ml": 5980}, 20,
             "水象本命主香｜黑加侖、大馬士革玫瑰、黑松露、烏木、皮革\n危險、深沉且具有致命吸引力的暗黑玫瑰，越靠近越令人沉醉其中。"),
            ("射手座《無垠旅途》", "12 星座系列", 2680, {"30ml": 2680, "50ml": 3880, "100ml": 5680}, 25,
             "火象本命主香｜杜松子、小荳蔻、黑胡椒、癒創木、煙草\n曠野焚火與辛香木質的自由靈魂，獻給永遠在路上的探索者。"),
            ("摩羯座《高崖深松》", "12 星座系列", 2880, {"30ml": 2880, "50ml": 4180, "100ml": 5980}, 20,
             "土象本命主香｜黑雲杉、冷岩礦物、乳香、沈香木、乾燥香根草\n孤高冷峻的岩壁古松與神聖乳香，沉著內斂卻蘊含不可動搖的力量。"),
            ("水瓶座《平流層之外》", "12 星座系列", 2680, {"30ml": 2680, "50ml": 3880, "100ml": 5680}, 25,
             "風象本命主香｜臭氧、粉紅胡椒、金屬冷感、醛香、雪松\n超脫塵囂的冷冽醛香與高空空氣感，屬於未來感與獨立靈魂的印記。"),
            ("雙魚座《夢境之洋》", "12 星座系列", 2680, {"30ml": 2680, "50ml": 3880, "100ml": 5680}, 25,
             "水象本命主香｜睡蓮、海風水生調、龍涎香、安息香、粉質鳶尾\n如夢似幻的深海睡蓮與溫柔安息香，將浪漫詩意化作貼膚水霧。"),
            ("子鼠《夜靈引》", "12 生肖系列", 2980, {"30ml": 2980, "50ml": 4280, "100ml": 6180}, 18,
             "東方典藏瓶｜微雕工藝：微瀾星露\n冷杉、睡蓮、沉香、龍涎香｜靈動靜謐的星夜水靈氣息。"),
            ("寅虎《嘯林破曉》", "12 生肖系列", 2980, {"30ml": 2980, "50ml": 4280, "100ml": 6180}, 18,
             "東方典藏瓶｜微雕工藝：層巒疊嶂\n黑胡椒、煙熏雪松、松針、岩蘭草｜山林破曉時的凜冽木質張力。"),
            ("卯兔《月宮掬露》", "12 生肖系列", 2980, {"30ml": 2980, "50ml": 4280, "100ml": 6180}, 18,
             "東方典藏瓶｜微雕工藝：修竹流雲\n白茶、白玉蘭、金桂、羊絨木｜清雅溫潤的東方茶花幽韻。"),
            ("辰龍《雲起雷淵》", "12 生肖系列", 3180, {"30ml": 3180, "50ml": 4480, "100ml": 6480}, 15,
             "東方典藏瓶｜微雕工藝：翻雲騰海\n海鹽、礦物、乳香、烏木、龍涎香｜壯闊深邃的雲海龍涎香調。"),
            ("《迷霧雨林》", "森林系列", 2280, {"30ml": 2280, "50ml": 3280, "100ml": 4680}, 30,
             "空間疊香｜濕潤蕨類、青苔、雨水、雪松\n為任何主香注入晨雨洗禮後的深林靜謐感。"),
            ("《古寺青檜》", "森林系列", 2380, {"30ml": 2380, "50ml": 3380, "100ml": 4880}, 30,
             "空間疊香｜日本檜木、線香灰燼、岩蘭草\n沉澱思緒的靜心禪意木質調，適合疊擦於東方或花香之上。"),
            ("《黑曜北境》", "森林系列", 2380, {"30ml": 2380, "50ml": 3380, "100ml": 4880}, 30,
             "空間疊香｜杜松、冷杉、煙燻樺木焦油\n築起冷峻深邃的針葉林木質骨架，增添神秘立體度。"),
            ("《晨摘無花果》", "果香系列", 2180, {"30ml": 2180, "50ml": 3180, "100ml": 4580}, 30,
             "空間疊香｜青綠無花果葉、無花果乳汁、白雪松\n鮮活清脆的青澀果意，瞬間點亮沉厚主香。"),
            ("《苦柚微醺》", "果香系列", 2180, {"30ml": 2180, "50ml": 3180, "100ml": 4580}, 30,
             "空間疊香｜日本柚子皮、葡萄柚、琴酒杜松子\n帶皮微苦的冰鎮柑橘氣泡感，俐落提振精神。"),
            ("《黑醋栗荊棘》", "果香系列", 2280, {"30ml": 2280, "50ml": 3280, "100ml": 4680}, 30,
             "空間疊香｜黑醋栗葉、野莓、廣藿香\n微酸多汁又帶著荊棘綠意的野生果韻。"),
            ("《冷浸白玉蘭》", "花香系列", 2380, {"30ml": 2380, "50ml": 3380, "100ml": 4880}, 30,
             "空間疊香｜冰鎮白玉蘭、白茶、水生白麝香\n不落俗套的冷感仙氣白花，柔和銳氣同時保有透明感。"),
            ("《十里秋桂》", "花香系列", 2380, {"30ml": 2380, "50ml": 3380, "100ml": 4880}, 30,
             "空間疊香｜金桂、杏桃、烏龍茶、檀香\n秋風裡飄散的細緻桂花茶香，溫柔且充滿東方詩意。"),
            ("《野薔薇刺》", "花香系列", 2380, {"30ml": 2380, "50ml": 3380, "100ml": 4880}, 30,
             "空間疊香｜野生玫瑰、粉紅胡椒、綠莖\n帶著露水與尖刺的野生薔薇，賦予香氣鮮活張力。"),
        ]
        for p in seed_products:
            cur.execute(
                """INSERT INTO products (name, category, price, prices_json, stock, description, image_url, images_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (p[0], p[1], p[2], json.dumps(p[3], ensure_ascii=False), p[4], p[5], "/hero.jpg", json.dumps(["/hero.jpg"])),
            )

    conn.commit()
    conn.close()


init_db()


# ==========================================
# Pydantic Models
# ==========================================
class AdminLoginReq(BaseModel):
    username: str
    password: str


class CategoryReq(BaseModel):
    name: str
    sort_order: Optional[int] = 0


class ProductReq(BaseModel):
    name: str
    category: str
    price: int
    prices: Optional[Dict[str, int]] = None
    stock: int = 20
    description: str = ""
    image_url: str = ""
    images_json: Optional[List[str]] = None
    images_base64: Optional[List[str]] = None


class MemberRegisterReq(BaseModel):
    email: str
    password: str
    name: str
    phone: str = ""


class MemberLoginReq(BaseModel):
    email: str
    password: str


class OrderItemReq(BaseModel):
    product_id: int
    size: str = "30ml"
    quantity: int


class OrderCreateReq(BaseModel):
    customer_name: str
    customer_phone: str
    customer_email: str = ""
    shipping_method: str
    shipping_store: str = ""
    shipping_address: str = ""
    payment_method: str
    items: List[OrderItemReq]


class OrderStatusReq(BaseModel):
    status: str


# ==========================================
# 持久化 Token 驗證 Helpers
# ==========================================
def issue_token(role: str, member_id: Optional[int] = None) -> str:
    token = uuid.uuid4().hex
    now_iso = datetime.now().isoformat()
    expires_iso = (datetime.now() + timedelta(days=TOKEN_EXPIRE_DAYS)).isoformat()
    conn = get_db()
    conn.execute("DELETE FROM auth_tokens WHERE expires_at <= ?", (now_iso,))
    conn.execute(
        "INSERT INTO auth_tokens (token, role, member_id, expires_at) VALUES (?, ?, ?, ?)",
        (token, role, member_id, expires_iso),
    )
    conn.commit()
    conn.close()
    return token


def verify_admin(x_admin_token: Optional[str] = Header(None)):
    if not x_admin_token:
        raise HTTPException(status_code=401, detail="請先登入後台管理員帳號")
    conn = get_db()
    row = conn.execute(
        "SELECT token FROM auth_tokens WHERE token = ? AND role = 'admin' AND expires_at > ?",
        (x_admin_token, datetime.now().isoformat()),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="管理員登入已過期，請重新登入")
    return True


def verify_member(x_member_token: Optional[str] = Header(None)):
    if not x_member_token:
        raise HTTPException(status_code=401, detail="請先登入會員帳號")
    conn = get_db()
    row = conn.execute(
        """SELECT m.id, m.email, m.name, m.phone
           FROM auth_tokens t
           JOIN members m ON t.member_id = m.id
           WHERE t.token = ? AND t.role = 'member' AND t.expires_at > ?""",
        (x_member_token, datetime.now().isoformat()),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="會員登入已過期，請重新登入")
    return dict(row)


def save_base64_images(b64_list: Optional[List[str]]) -> List[str]:
    saved_urls = []
    if not b64_list:
        return saved_urls
    for item in b64_list:
        if not item:
            continue
        if item.startswith("http") or item.startswith("/"):
            saved_urls.append(item)
            continue
        if "base64," in item:
            header, data = item.split("base64,", 1)
            ext = "png"
            if "jpeg" in header or "jpg" in header:
                ext = "jpg"
            elif "webp" in header:
                ext = "webp"
            filename = f"{uuid.uuid4().hex}.{ext}"
            filepath = os.path.join(UPLOAD_DIR, filename)
            with open(filepath, "wb") as f:
                f.write(base64.b64decode(data))
            saved_urls.append(f"/static/uploads/{filename}")
    return saved_urls


def calculate_shipping_fee(subtotal: int, shipping_method: str) -> int:
    if subtotal >= FREE_SHIPPING_THRESHOLD:
        return 0
    if "宅配" in shipping_method:
        return SHIPPING_FEE_HOME
    return SHIPPING_FEE_CVS


def generate_ecpay_mac(params: Dict[str, Any], hash_key: str, hash_iv: str) -> str:
    sorted_items = sorted((k, str(v)) for k, v in params.items() if k != "CheckMacValue")
    raw = f"HashKey={hash_key}&" + "&".join(f"{k}={v}" for k, v in sorted_items) + f"&HashIV={hash_iv}"
    encoded = urllib.parse.quote_plus(raw).lower()
    replacements = {
        "%2d": "-",
        "%5f": "_",
        "%2e": ".",
        "%21": "!",
        "%2a": "*",
        "%28": "(",
        "%29": ")",
    }
    for k, v in replacements.items():
        encoded = encoded.replace(k, v)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest().upper()


# ==========================================
# Pages & Static Assets
# ==========================================
@app.get("/", response_class=HTMLResponse)
def serve_index():
    path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(path):
        return HTMLResponse("<h1>找不到 static/index.html</h1>", status_code=404)
    return FileResponse(path)


@app.get("/admin", response_class=HTMLResponse)
def serve_admin():
    path = os.path.join(STATIC_DIR, "admin.html")
    if not os.path.exists(path):
        return HTMLResponse("<h1>找不到 static/admin.html</h1>", status_code=404)
    return FileResponse(path)


@app.get("/hero.jpg")
def serve_hero():
    for candidate in [
        os.path.join(STATIC_DIR, "hero.jpg"),
        os.path.join(STATIC_DIR, "hero.jpg.jpeg"),
        os.path.join(BASE_DIR, "hero.jpg"),
    ]:
        if os.path.exists(candidate):
            return FileResponse(candidate)
    raise HTTPException(status_code=404, detail="hero.jpg not found")


# ==========================================
# Categories API
# ==========================================
@app.get("/api/categories")
def list_categories():
    conn = get_db()
    rows = conn.execute("SELECT id, name, sort_order FROM categories ORDER BY sort_order ASC, id ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/categories")
def create_category(req: CategoryReq, x_admin_token: Optional[str] = Header(None)):
    verify_admin(x_admin_token)
    conn = get_db()
    try:
        conn.execute("INSERT INTO categories (name, sort_order) VALUES (?, ?)", (req.name.strip(), req.sort_order or 0))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="分類名稱已存在")
    conn.close()
    return {"message": "分類已新增"}


@app.delete("/api/categories/{cat_id}")
def delete_category(cat_id: int, x_admin_token: Optional[str] = Header(None)):
    verify_admin(x_admin_token)
    conn = get_db()
    conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
    conn.commit()
    conn.close()
    return {"message": "分類已刪除"}


# ==========================================
# Products API
# ==========================================
def serialize_product(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["prices"] = json.loads(d.get("prices_json") or "{}")
    except Exception:
        d["prices"] = {}
    if not d["prices"]:
        d["prices"] = {"30ml": d["price"]}
    try:
        d["images"] = json.loads(d.get("images_json") or "[]")
    except Exception:
        d["images"] = []
    if not d["images"] and d.get("image_url"):
        d["images"] = [d["image_url"]]
    return d


@app.get("/api/products")
def list_products(category: Optional[str] = None, q: Optional[str] = None):
    conn = get_db()
    sql = "SELECT * FROM products WHERE 1=1"
    params = []
    if category and category != "全部":
        sql += " AND category = ?"
        params.append(category)
    if q:
        sql += " AND (name LIKE ? OR description LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    sql += " ORDER BY id ASC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [serialize_product(r) for r in rows]


@app.post("/api/products")
def create_product(req: ProductReq, x_admin_token: Optional[str] = Header(None)):
    verify_admin(x_admin_token)
    uploaded_urls = save_base64_images(req.images_base64)
    all_images = list(req.images_json or []) + uploaded_urls
    if not all_images and req.image_url:
        all_images = [req.image_url]
    cover_url = all_images[0] if all_images else "/hero.jpg"
    prices_dict = req.prices if req.prices else {"30ml": req.price}
    base_price = min(prices_dict.values()) if prices_dict else req.price

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO products (name, category, price, prices_json, stock, description, image_url, images_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            req.name.strip(),
            req.category.strip(),
            base_price,
            json.dumps(prices_dict, ensure_ascii=False),
            req.stock,
            req.description,
            cover_url,
            json.dumps(all_images, ensure_ascii=False),
        ),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return {"id": new_id, "message": "商品已新增"}


@app.put("/api/products/{prod_id}")
def update_product(prod_id: int, req: ProductReq, x_admin_token: Optional[str] = Header(None)):
    verify_admin(x_admin_token)
    uploaded_urls = save_base64_images(req.images_base64)
    all_images = list(req.images_json or []) + uploaded_urls
    if not all_images and req.image_url:
        all_images = [req.image_url]
    cover_url = all_images[0] if all_images else "/hero.jpg"
    prices_dict = req.prices if req.prices else {"30ml": req.price}
    base_price = min(prices_dict.values()) if prices_dict else req.price

    conn = get_db()
    conn.execute(
        """UPDATE products
           SET name = ?, category = ?, price = ?, prices_json = ?, stock = ?, description = ?, image_url = ?, images_json = ?
           WHERE id = ?""",
        (
            req.name.strip(),
            req.category.strip(),
            base_price,
            json.dumps(prices_dict, ensure_ascii=False),
            req.stock,
            req.description,
            cover_url,
            json.dumps(all_images, ensure_ascii=False),
            prod_id,
        ),
    )
    conn.commit()
    conn.close()
    return {"message": "商品已更新"}


@app.delete("/api/products/{prod_id}")
def delete_product(prod_id: int, x_admin_token: Optional[str] = Header(None)):
    verify_admin(x_admin_token)
    conn = get_db()
    conn.execute("DELETE FROM products WHERE id = ?", (prod_id,))
    conn.commit()
    conn.close()
    return {"message": "商品已刪除"}


# ==========================================
# Admin Auth & Orders API
# ==========================================
@app.post("/api/admin/login")
def admin_login(req: AdminLoginReq):
    if req.username == "admin" and req.password == "123456":
        token = issue_token("admin", None)
        return {"token": token, "username": "admin"}
    raise HTTPException(status_code=401, detail="帳號或密碼錯誤")


@app.get("/api/admin/orders")
def admin_list_orders(x_admin_token: Optional[str] = Header(None)):
    verify_admin(x_admin_token)
    conn = get_db()
    rows = conn.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["items"] = json.loads(d["items_json"])
        except Exception:
            d["items"] = []
        result.append(d)
    return result


@app.put("/api/orders/{order_id}/status")
def update_order_status(order_id: int, req: OrderStatusReq, x_admin_token: Optional[str] = Header(None)):
    verify_admin(x_admin_token)
    conn = get_db()
    conn.execute("UPDATE orders SET status = ? WHERE id = ?", (req.status, order_id))
    conn.commit()
    conn.close()
    return {"message": "訂單狀態已更新"}


# ==========================================
# Member Auth & Orders API
# ==========================================
@app.post("/api/member/register")
def member_register(req: MemberRegisterReq):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO members (email, password_hash, name, phone) VALUES (?, ?, ?, ?)",
            (req.email.strip().lower(), hash_password(req.password), req.name.strip(), req.phone.strip()),
        )
        conn.commit()
        member_id = cur.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="此 Email 已被註冊")
    conn.close()

    token = issue_token("member", member_id)
    user_info = {"id": member_id, "email": req.email.strip().lower(), "name": req.name.strip(), "phone": req.phone.strip()}
    return {"token": token, "member": user_info}


@app.post("/api/member/login")
def member_login(req: MemberLoginReq):
    conn = get_db()
    row = conn.execute(
        "SELECT id, email, name, phone, password_hash FROM members WHERE email = ?",
        (req.email.strip().lower(),),
    ).fetchone()
    conn.close()
    if not row or row["password_hash"] != hash_password(req.password):
        raise HTTPException(status_code=401, detail="信箱或密碼錯誤")

    token = issue_token("member", row["id"])
    user_info = {"id": row["id"], "email": row["email"], "name": row["name"], "phone": row["phone"]}
    return {"token": token, "member": user_info}


@app.get("/api/member/orders")
def member_orders(x_member_token: Optional[str] = Header(None)):
    member = verify_member(x_member_token)
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM orders WHERE member_id = ? ORDER BY id DESC",
        (member["id"],),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["items"] = json.loads(d["items_json"])
        except Exception:
            d["items"] = []
        result.append(d)
    return result


# ==========================================
# Checkout & Orders API (原子防超賣交易鎖 + 自動運費)
# ==========================================
@app.post("/api/orders")
def create_order(req: OrderCreateReq, x_member_token: Optional[str] = Header(None)):
    member = verify_member(x_member_token)
    if not req.items:
        raise HTTPException(status_code=400, detail="購物車是空的")

    conn = get_db(isolation_level=None)
    cur = conn.cursor()

    try:
        cur.execute("BEGIN IMMEDIATE")
        subtotal = 0
        order_items = []

        for item in req.items:
            if item.quantity <= 0:
                raise HTTPException(status_code=400, detail="購買數量必須大於 0")

            row = cur.execute("SELECT * FROM products WHERE id = ?", (item.product_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"找不到商品 ID {item.product_id}")

            cur.execute(
                "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
                (item.quantity, row["id"], item.quantity),
            )
            if cur.rowcount != 1:
                raise HTTPException(status_code=400, detail=f"商品 {row['name']} 庫存不足（剩餘 {row['stock']} 件）")

            prices = json.loads(row["prices_json"] or "{}")
            unit_price = int(prices.get(item.size, row["price"]))
            line_total = unit_price * item.quantity
            subtotal += line_total

            order_items.append({
                "product_id": row["id"],
                "name": row["name"],
                "size": item.size,
                "quantity": item.quantity,
                "unit_price": unit_price,
                "line_total": line_total,
            })

        shipping_fee = calculate_shipping_fee(subtotal, req.shipping_method)
        total_amount = subtotal + shipping_fee

        order_no = f"AL{datetime.now().strftime('%y%m%d%H%M%S')}{uuid.uuid4().hex[:4].upper()}"
        init_status = "待出貨" if req.payment_method == "貨到付款" else "待付款"

        cur.execute(
            """INSERT INTO orders
               (order_no, member_id, customer_name, customer_phone, customer_email,
                shipping_method, shipping_store, shipping_address, payment_method,
                items_json, subtotal, shipping_fee, total_amount, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order_no,
                member["id"],
                req.customer_name.strip(),
                req.customer_phone.strip(),
                req.customer_email.strip() or member["email"],
                req.shipping_method,
                req.shipping_store.strip(),
                req.shipping_address.strip(),
                req.payment_method,
                json.dumps(order_items, ensure_ascii=False),
                subtotal,
                shipping_fee,
                total_amount,
                init_status,
            ),
        )
        order_id = cur.lastrowid
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"建立訂單失敗：{exc}")
    finally:
        conn.close()

    return {
        "order_id": order_id,
        "order_no": order_no,
        "subtotal": subtotal,
        "shipping_fee": shipping_fee,
        "total_amount": total_amount,
        "status": init_status,
    }


# ==========================================
# 綠界 ECPay 測試環境串接
# ==========================================
@app.get("/api/ecpay/map", response_class=HTMLResponse)
def ecpay_map(logistics_sub_type: str = "UNIMARTC2C"):
    params = {
        "MerchantID": ECPAY_MAP_MERCHANT_ID,
        "MerchantTradeNo": f"MAP{int(time.time())}",
        "LogisticsType": "CVS",
        "LogisticsSubType": logistics_sub_type,
        "IsCollection": "N",
        "ServerReplyURL": f"{PUBLIC_BASE_URL.rstrip('/')}/api/ecpay/map-callback",
    }
    inputs_html = "\n".join(f'<input type="hidden" name="{k}" value="{v}">' for k, v in params.items())
    return HTMLResponse(f"""
    <html><body>
      <form id="ecpayMapForm" method="post" action="{ECPAY_MAP_URL}">
        {inputs_html}
      </form>
      <script>document.getElementById('ecpayMapForm').submit();</script>
    </body></html>
    """)


@app.post("/api/ecpay/map-callback", response_class=HTMLResponse)
async def ecpay_map_callback(request: Request):
    body_bytes = await request.body()
    parsed = urllib.parse.parse_qs(body_bytes.decode("utf-8", errors="ignore"))
    store_id = parsed.get("CVSStoreID", [""])[0]
    store_name = parsed.get("CVSStoreName", [""])[0]
    store_addr = parsed.get("CVSAddress", [""])[0]
    return HTMLResponse(f"""
    <html><body><script>
      if (window.opener) {{
        window.opener.postMessage({{
          type: 'ECPAY_STORE_SELECTED',
          storeId: {json.dumps(store_id)},
          storeName: {json.dumps(store_name)},
          storeAddress: {json.dumps(store_addr)}
        }}, '*');
        window.close();
      }}
    </script></body></html>
    """)


@app.get("/api/ecpay/checkout/{order_id}", response_class=HTMLResponse)
def ecpay_checkout(order_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="找不到訂單")

    items = json.loads(row["items_json"] or "[]")
    item_names = "#".join(f"{it['name']}({it['size']})x{it['quantity']}" for it in items) or "AURA LUNARIS 香氛"
    if row["shipping_fee"] and row["shipping_fee"] > 0:
        item_names += f"#運費 NT${row['shipping_fee']}"

    trade_date = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    base = PUBLIC_BASE_URL.rstrip("/")

    params = {
        "MerchantID": ECPAY_AIO_MERCHANT_ID,
        "MerchantTradeNo": row["order_no"],
        "MerchantTradeDate": trade_date,
        "PaymentType": "aio",
        "TotalAmount": str(row["total_amount"]),
        "TradeDesc": "AURA LUNARIS 時曜香殿訂單",
        "ItemName": item_names[:200],
        "ReturnURL": f"{base}/api/ecpay/notify",
        "ClientBackURL": f"{base}/",
        "ChoosePayment": "Credit",
        "EncryptType": "1",
    }
    params["CheckMacValue"] = generate_ecpay_mac(params, ECPAY_AIO_HASH_KEY, ECPAY_AIO_HASH_IV)

    inputs_html = "\n".join(f'<input type="hidden" name="{k}" value="{v}">' for k, v in params.items())
    return HTMLResponse(f"""
    <html>
    <body style="background:#070909;color:#e6c786;display:flex;justify-content:center;align-items:center;height:100vh;font-family:sans-serif;">
      <div>正在導向綠界安全金流頁面，請稍候...</div>
      <form id="ecpayForm" method="post" action="{ECPAY_AIO_URL}">
        {inputs_html}
      </form>
      <script>document.getElementById('ecpayForm').submit();</script>
    </body>
    </html>
    """)


@app.post("/api/ecpay/notify")
async def ecpay_notify(request: Request):
    body_bytes = await request.body()
    parsed = urllib.parse.parse_qs(body_bytes.decode("utf-8", errors="ignore"))
    merchant_trade_no = parsed.get("MerchantTradeNo", [""])[0]
    rtn_code = parsed.get("RtnCode", [""])[0]

    if rtn_code == "1" and merchant_trade_no:
        conn = get_db()
        conn.execute("UPDATE orders SET status = '已付款待出貨' WHERE order_no = ?", (merchant_trade_no,))
        conn.commit()
        conn.close()

    return HTMLResponse("1|OK")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)