from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime

DATABASE_URL = "sqlite:///./restoran.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    role = Column(String, default="superuser")

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    price = Column(Float)
    category = Column(String)
    image_url = Column(String, default="https://images.unsplash.com/photo-1546069901-ba9599a7e63c")
    description = Column(String, default="")

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    table_no = Column(Integer)
    status = Column(String, default="Yeni")
    total_price = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.now)
    items = relationship("OrderItem", back_populates="order")

class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    product_name = Column(String)
    price = Column(Float, default=0.0)
    quantity = Column(Integer)
    paid_quantity = Column(Integer, default=0)
    order = relationship("Order", back_populates="items")

Base.metadata.create_all(bind=engine)