from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends, Form, File, UploadFile, responses, status
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta, timezone
import json
import os
import shutil
import io
import csv

from database import SessionLocal, Product, Order, OrderItem, User, Base, engine

# Türkiye Saat Dilimi (UTC+3) Yardımcı Fonksiyonu
TURKEY_TZ = timezone(timedelta(hours=3))

def get_turkey_time():
    return datetime.now(TURKEY_TZ)

Base.metadata.create_all(bind=engine)

app = FastAPI()

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
        for connection in list(self.active_connections):
            try:
                await connection.send_text(json.dumps(message))
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()

@app.on_event("startup")
def startup_db():
    db = SessionLocal()
    try:
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
    except Exception as e:
        print("Startup hata:", e)
    finally:
        db.close()

def get_current_user(request: Request):
    return request.cookies.get("session_user")

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
@app.post("/admin/update-credentials")
def update_credentials(
    request: Request,
    new_username: str = Form(...),
    current_password: str = Form(...),
    new_password: str = Form(...),
    db: Session = Depends(get_db)
):
    current_user_name = get_current_user(request)
    if not current_user_name:
        return responses.RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    user = db.query(User).filter(User.username == current_user_name).first()
    
    # Mevcut şifre kontrolü
    if not user or user.password != current_password:
        products = db.query(Product).order_by(Product.id.desc()).all()
        return templates.TemplateResponse(
            request=request,
            name="admin.html",
            context={
                "products": products,
                "user": current_user_name,
                "error_msg": "Mevcut şifreniz hatalı!"
            }
        )

    # Kullanıcı adı ve şifre güncelleme
    user.username = new_username.strip()
    user.password = new_password.strip()
    db.commit()

    # Oturum çerezini yeni kullanıcı adıyla güncelle
    response = responses.RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="session_user", value=user.username, httponly=True)
    return response

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
    
    active_orders = db.query(Order.table_no).filter(
        Order.status.in_(["Yeni", "Hazır", "Teslim Edildi"])
    ).all()
    active_tables = list(set([o.table_no for o in active_orders]))

    return templates.TemplateResponse(
        request=request, 
        name="kitchen.html", 
        context={"orders": orders, "user": user, "active_tables": active_tables}
    )

@app.get("/waiter")
def get_waiter(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return responses.RedirectResponse(url="/login")

    orders = db.query(Order).filter(Order.status == "Hazır").order_by(Order.id.desc()).all()
    products = db.query(Product).all()

    active_orders = db.query(Order.table_no).filter(
        Order.status.in_(["Yeni", "Hazır", "Teslim Edildi"])
    ).all()
    active_tables = list(set([o.table_no for o in active_orders]))

    return templates.TemplateResponse(
        request=request, 
        name="waiter.html", 
        context={"orders": orders, "user": user, "products": products, "active_tables": active_tables}
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

# --- GEÇMİŞ SİPARİŞLER RAPORU SAYFASI ---
@app.get("/admin/history")
def get_order_history(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return responses.RedirectResponse(url="/login")

    try:
        all_orders = db.query(Order).order_by(Order.id.desc()).all()

        grouped_dict = {}
        for order in all_orders:
            if order.created_at:
                created_time = order.created_at + timedelta(hours=3)
                date_str = created_time.strftime("%d.%m.%Y")
                time_str = created_time.strftime("%H:%M")
            else:
                date_str = "Tarihsiz Kayıtlar"
                time_str = "--:--"

            if date_str not in grouped_dict:
                grouped_dict[date_str] = {
                    "date": date_str,
                    "orders": [],
                    "daily_total": 0.0
                }

            items_data = []
            for item in order.items:
                items_data.append({
                    "product_name": item.product_name or "Ürün",
                    "quantity": item.quantity or 1,
                    "price": float(item.price or 0.0)
                })

            order_dict = {
                "id": order.id,
                "table_no": order.table_no,
                "status": order.status or "İşlendi",
                "total_price": float(order.total_price or 0.0),
                "time_str": time_str,
                "order_items": items_data
            }

            grouped_dict[date_str]["orders"].append(order_dict)
            
            if order.status in ["Tamamı Ödendi", "Teslim Edildi", "Hazır", "Yeni"]:
                grouped_dict[date_str]["daily_total"] += float(order.total_price or 0.0)

        history_list = []
        for key in grouped_dict:
            history_list.append(grouped_dict[key])

        return templates.TemplateResponse(
            request=request,
            name="history.html",
            context={"history_list": history_list, "user": user}
        )
    except Exception as e:
        print("History Okuma Hatasi:", e)
        return templates.TemplateResponse(
            request=request,
            name="history.html",
            context={"history_list": [], "user": user, "error": str(e)}
        )

# --- GEÇMİŞ SİPARİŞLERİ EXCEL / CSV OLARAK İNDİRME ---
@app.get("/admin/export-excel")
def export_orders_to_excel(db: Session = Depends(get_db)):
    all_orders = db.query(Order).order_by(Order.id.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')

    writer.writerow(["Siparis ID", "Tarih", "Saat", "Masa No", "Urunler", "Toplam Tutar (TL)", "Durum"])

    for order in all_orders:
        if order.created_at:
            created_time = order.created_at + timedelta(hours=3)
            date_str = created_time.strftime("%d.%m.%Y")
            time_str = created_time.strftime("%H:%M")
        else:
            date_str = "Tarihsiz"
            time_str = "--:--"

        items_str = ", ".join([f"{item.product_name} (x{item.quantity})" for item in order.items])

        writer.writerow([
            order.id,
            date_str,
            time_str,
            f"Masa {order.table_no}",
            items_str,
            str(order.total_price).replace('.', ','),
            order.status
        ])

    output.seek(0)
    
    filename = f"gecmis_siparisler_{get_turkey_time().strftime('%Y%m%d_%H%M')}.csv"
    
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

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

    if image_file and image_file.filename:
        filename = f"{get_turkey_time().strftime('%Y%m%d%H%M%S')}_{image_file.filename}"
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

@app.post("/admin/product/update/{product_id}")
async def update_product(
    product_id: int,
    name: str = Form(...),
    price: float = Form(...),
    category: str = Form(...),
    image_url: Optional[str] = Form(None),
    image_file: Optional[UploadFile] = File(None),
    description: str = Form(""),
    db: Session = Depends(get_db)
):
    prod = db.query(Product).filter(Product.id == product_id).first()
    if not prod:
        return responses.RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)

    prod.name = name
    prod.price = price
    prod.category = category
    prod.description = description

    if image_file and image_file.filename:
        filename = f"{get_turkey_time().strftime('%Y%m%d%H%M%S')}_{image_file.filename}"
        file_path = os.path.join(UPLOAD_DIR, filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(image_file.file, buffer)
        prod.image_url = f"/static/uploads/{filename}"
    elif image_url and image_url.strip():
        prod.image_url = image_url.strip()

    db.commit()
    return responses.RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)

@app.post("/admin/product/delete/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    prod = db.query(Product).filter(Product.id == product_id).first()
    if prod:
        db.delete(prod)
        db.commit()
    return responses.RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)

@app.post("/api/order")
async def create_order(data: dict, db: Session = Depends(get_db)):
    table_no = data.get("table_no")
    cart = data.get("cart", [])

    total_price = sum(float(item["price"]) * int(item["quantity"]) for item in cart)

    now_tr = get_turkey_time().replace(tzinfo=None)
    new_order = Order(table_no=table_no, status="Yeni", total_price=total_price, created_at=now_tr)
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
        "created_at": now_tr.strftime("%H:%M"),
        "items": items_summary
    }
    await manager.broadcast(order_data)

    return {"status": "success", "order_id": new_order.id}

@app.get("/api/table/{table_no}/bill")
def get_table_bill(table_no: int, db: Session = Depends(get_db)):
    try:
        active_orders = db.query(Order).filter(
            Order.table_no == table_no, 
            Order.status.in_(["Yeni", "Hazır", "Teslim Edildi"])
        ).all()

        all_items = []
        total_remaining_bill = 0.0

        for order in active_orders:
            for item in order.items:
                price = float(item.price if item.price is not None else 0.0)
                qty = int(item.quantity if item.quantity is not None else 0)
                paid_qty = int(item.paid_quantity if item.paid_quantity is not None else 0)
                unpaid_qty = qty - paid_qty
                
                if unpaid_qty < 0:
                    unpaid_qty = 0

                total_remaining_bill += unpaid_qty * price
                
                all_items.append({
                    "item_id": item.id,
                    "product_name": item.product_name or "Ürün",
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
    except Exception as e:
        print("Adisyon okuma hatasi:", e)
        return {
            "table_no": table_no,
            "total_remaining_bill": 0.0,
            "items": []
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
        "time": get_turkey_time().strftime("%H:%M")
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
        
        event_data = {
            "event": "status_update",
            "id": order_id,
            "status": "Silindi"
        }
    else:
        order.status = new_status
        db.commit()

        items_summary = [{"name": i.product_name, "quantity": i.quantity, "price": i.price} for i in order.items]
        
        event_data = {
            "event": "status_update",
            "id": order.id,
            "table_no": order.table_no,
            "status": new_status,
            "total_price": order.total_price,
            "created_at": order.created_at.strftime("%H:%M") if order.created_at else get_turkey_time().strftime("%H:%M"),
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