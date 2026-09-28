from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends, Form, File, UploadFile, responses, status
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
import json
import os
import shutil

from database import SessionLocal, Product, Order, OrderItem, User

app = FastAPI()

# Yüklenen görseller için statik klasör tanımı
UPLOAD_DIR = "static/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- WebSocket Yöneticisi ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except Exception:
                pass

manager = ConnectionManager()

# --- Başlangıç Verileri ---
@app.on_event("startup")
def startup_db():
    db = SessionLocal()
    if not db.query(User).filter(User.username == "admin").first():
        admin_user = User(username="admin", password="admin123", role="superuser")
        db.add(admin_user)
        db.commit()

    if not db.query(Product).first():
        sample_products = [
            Product(
                name="Izgara Köfte", 
                price=280.0, 
                category="Ana Yemekler",
                image_url="https://images.unsplash.com/photo-1529042410759-befb1204b468?w=500",
                description="Özel baharatlarla harmanlanmış 200gr ızgara dana köfte, patates püre ve közlenmiş biber ile."
            ),
            Product(
                name="Cheeseburger", 
                price=260.0, 
                category="Ana Yemekler",
                image_url="https://images.unsplash.com/photo-1568901346375-23c9450c58cd?w=500",
                description="180gr dana hamburger köftesi, cheddar peyniri, karamelize soğan, özel sos ve çıtır patates."
            ),
            Product(
                name="San Sebastian", 
                price=150.0, 
                category="Tatlılar",
                image_url="https://images.unsplash.com/photo-1533134242443-d4fd215305ad?w=500",
                description="Akışkan Belçika çikolatası sosu ile servis edilen orijinal fırınlanmış cheesecake."
            ),
            Product(
                name="Caffè Latte", 
                price=85.0, 
                category="Sıcak İçecekler",
                image_url="https://images.unsplash.com/photo-1534778101976-62847782c213?w=500",
                description="Taze çekilmiş espresso çekirdekleri ve kadifemsi süt köpüğü."
            ),
        ]
        db.add_all(sample_products)
        db.commit()
    db.close()

def get_current_user(request: Request):
    return request.cookies.get("session_user")

# --- Auth ---
@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={})

@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username, User.password == password).first()
    if not user:
        return templates.TemplateResponse(request=request, name="login.html", context={"error": "Kullanıcı adı veya şifre hatalı."})
    
    response = responses.RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="session_user", value=user.username, httponly=True)
    return response

@app.get("/logout")
def logout():
    response = responses.RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("session_user")
    return response

# --- Sayfalar ---
@app.get("/menu/{table_no}")
def get_menu(table_no: int, request: Request, db: Session = Depends(get_db)):
    products = db.query(Product).all()
    categories = list(set([p.category for p in products]))
    return templates.TemplateResponse(
        request=request, 
        name="menu.html", 
        context={"table_no": table_no, "products": products, "categories": categories}
    )

@app.get("/kitchen")
def get_kitchen(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return responses.RedirectResponse(url="/login")
    
    orders = db.query(Order).filter(Order.status == "Yeni").order_by(Order.id.desc()).all()
    return templates.TemplateResponse(
        request=request, 
        name="kitchen.html", 
        context={"orders": orders, "user": user}
    )

@app.get("/waiter")
def get_waiter(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return responses.RedirectResponse(url="/login")

    orders = db.query(Order).filter(Order.status == "Hazır").order_by(Order.id.desc()).all()
    return templates.TemplateResponse(
        request=request, 
        name="waiter.html", 
        context={"orders": orders, "user": user}
    )

@app.get("/admin")
def get_admin_panel(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return responses.RedirectResponse(url="/login")
    
    products = db.query(Product).order_by(Product.id.desc()).all()
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"products": products, "user": user}
    )

# --- Admin Ürün Ekleme (Dosya Yükleme Destekli) ---
@app.post("/admin/product/add")
async def add_product(
    name: str = Form(...),
    price: float = Form(...),
    category: str = Form(...),
    image_url: Optional[str] = Form(None),
    image_file: Optional[UploadFile] = File(None),
    description: str = Form(""),
    db: Session = Depends(get_db)
):
    final_image_url = "https://images.unsplash.com/photo-1546069901-ba9599a7e63c"

    # Eğer bilgisayardan dosya yüklenmişse
    if image_file and image_file.filename:
        file_ext = os.path.splitext(image_file.filename)[1]
        filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{image_file.filename}"
        file_path = os.path.join(UPLOAD_DIR, filename)
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(image_file.file, buffer)
        
        final_image_url = f"/static/uploads/{filename}"
    elif image_url and image_url.strip():
        final_image_url = image_url.strip()

    new_prod = Product(
        name=name,
        price=price,
        category=category,
        image_url=final_image_url,
        description=description
    )
    db.add(new_prod)
    db.commit()
    return responses.RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)

@app.post("/admin/product/delete/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    prod = db.query(Product).filter(Product.id == product_id).first()
    if prod:
        db.delete(prod)
        db.commit()
    return responses.RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)

# --- API Endpoints ---
@app.post("/api/order")
async def create_order(data: dict, db: Session = Depends(get_db)):
    table_no = data.get("table_no")
    cart = data.get("cart")

    total_price = sum(float(item["price"]) * int(item["quantity"]) for item in cart)

    new_order = Order(table_no=table_no, status="Yeni", total_price=total_price)
    db.add(new_order)
    db.commit()
    db.refresh(new_order)

    items_summary = []
    for item in cart:
        item_price = float(item["price"])
        item_qty = int(item["quantity"])
        order_item = OrderItem(
            order_id=new_order.id, 
            product_name=item["name"], 
            price=item_price, 
            quantity=item_qty,
            paid_quantity=0
        )
        db.add(order_item)
        items_summary.append({"name": item["name"], "quantity": item_qty, "price": item_price})
    
    db.commit()

    order_data = {
        "event": "new_order",
        "id": new_order.id,
        "table_no": table_no,
        "status": "Yeni",
        "total_price": total_price,
        "created_at": new_order.created_at.strftime("%H:%M"),
        "items": items_summary
    }
    await manager.broadcast(order_data)

    return {"status": "success", "order_id": new_order.id}

@app.get("/api/table/{table_no}/bill")
def get_table_bill(table_no: int, db: Session = Depends(get_db)):
    active_orders = db.query(Order).filter(
        Order.table_no == table_no, 
        Order.status.in_(["Yeni", "Hazır", "Teslim Edildi"])
    ).all()

    all_items = []
    total_remaining_bill = 0.0

    for order in active_orders:
        for item in order.items:
            price = float(item.price or 0.0)
            qty = int(item.quantity or 0)
            paid_qty = int(item.paid_quantity or 0)
            unpaid_qty = qty - paid_qty
            
            if unpaid_qty < 0:
                unpaid_qty = 0

            total_remaining_bill += unpaid_qty * price
            
            all_items.append({
                "item_id": item.id,
                "product_name": item.product_name,
                "price": price,
                "quantity": qty,
                "paid_quantity": paid_qty,
                "unpaid_quantity": unpaid_qty,
                "is_fully_paid": unpaid_qty == 0
            })

    return {
        "table_no": table_no,
        "total_remaining_bill": total_remaining_bill,
        "items": all_items
    }

@app.post("/api/table/{table_no}/pay-items")
async def pay_table_items(table_no: int, data: dict, db: Session = Depends(get_db)):
    payments = data.get("payments", [])

    for p in payments:
        item = db.query(OrderItem).filter(OrderItem.id == int(p["item_id"])).first()
        if item:
            item.paid_quantity += int(p["count"])
            if item.paid_quantity > item.quantity:
                item.paid_quantity = item.quantity
    
    db.commit()

    active_orders = db.query(Order).filter(
        Order.table_no == table_no, 
        Order.status.in_(["Yeni", "Hazır", "Teslim Edildi"])
    ).all()

    all_done = True
    for order in active_orders:
        for item in order.items:
            if item.paid_quantity < item.quantity:
                all_done = False
                break

    if all_done and active_orders:
        for order in active_orders:
            order.status = "Tamamı Ödendi"
        db.commit()

    await manager.broadcast({"event": "bill_updated", "table_no": table_no, "all_done": all_done})

    return {"status": "success", "all_done": all_done}

@app.post("/api/call-waiter")
async def call_waiter(data: dict):
    table_no = data.get("table_no")
    event_data = {
        "event": "call_waiter",
        "table_no": table_no,
        "time": datetime.now().strftime("%H:%M")
    }
    await manager.broadcast(event_data)
    return {"status": "success"}

@app.post("/api/order/{order_id}/status")
async def update_order_status(order_id: int, data: dict, db: Session = Depends(get_db)):
    new_status = data.get("status")
    order = db.query(Order).filter(Order.id == order_id).first()
    
    if not order:
        return {"status": "error", "message": "Sipariş bulunamadı"}

    if new_status == "Silindi":
        db.query(OrderItem).filter(OrderItem.order_id == order_id).delete()
        db.delete(order)
        db.commit()
    else:
        order.status = new_status
        db.commit()

    items_summary = [{"name": i.product_name, "quantity": i.quantity, "price": i.price} for i in order.items] if new_status != "Silindi" else []
    
    event_data = {
        "event": "status_update",
        "id": order_id,
        "table_no": order.table_no if new_status != "Silindi" else None,
        "status": new_status,
        "total_price": order.total_price if new_status != "Silindi" else 0,
        "created_at": order.created_at.strftime("%H:%M") if new_status != "Silindi" else "",
        "items": items_summary
    }
    await manager.broadcast(event_data)

    return {"status": "success", "new_status": new_status}

@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)