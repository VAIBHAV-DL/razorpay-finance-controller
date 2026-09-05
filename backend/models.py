from sqlalchemy import Column, String, Integer, Boolean, Text
from database import Base


class Order(Base):
    __tablename__ = "orders"

    id = Column(String(50), primary_key=True)
    amount = Column(Integer)
    currency = Column(String(10))
    status = Column(String(30))
    created_at = Column(Integer)


class Payment(Base):
    __tablename__ = "payments"

    id = Column(String(50), primary_key=True)
    order_id = Column(String(50))
    amount = Column(Integer)
    currency = Column(String(10))
    status = Column(String(30))
    method = Column(String(30))
    fee = Column(Integer)
    tax = Column(Integer)
    captured = Column(Boolean)
    created_at = Column(Integer)


class Refund(Base):
    __tablename__ = "refunds"

    id = Column(String(50), primary_key=True)
    payment_id = Column(String(50))
    amount = Column(Integer)
    currency = Column(String(10))
    status = Column(String(30))
    created_at = Column(Integer)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(100))
    payload = Column(Text)
    received_at = Column(Integer)

class SettlementRecord(Base):
    __tablename__ = "settlement_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    payment_id = Column(String(50))
    settlement_id = Column(String(50))
    settled_amount = Column(Integer)
    status = Column(String(30))
    created_at = Column(Integer)