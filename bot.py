"""
מנהל עסקי טלגרם — Bot + FastAPI Backend
הגדרות ראשיות
"""

import asyncio
import logging
import os
import shutil
import threading
from datetime import date, datetime, timedelta
from typing import Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, Integer,
                        String, Text, create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardRemove, Update, WebAppInfo)
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          MessageHandler, filters)

# ─────────────────────────────────────────────
#  הגדרות — שנה כאן לפני הפעלה
# ─────────────────────────────────────────────
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"       # הכנס את הטוקן שלך מ-BotFather
ALLOWED_USER_ID = 123456789  # הכנס את ה-ID שלך מ-@userinfobot                 # הכנס את ה-ID שלך מ-@userinfobot
WEBAPP_URL = "https://your-app.netlify.app"
API_PORT = 8000                      # פורט ה-FastAPI
DB_PATH = "business.db"
BACKUP_DIR = "backups"

# ─────────────────────────────────────────────
#  לוגינג
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  מסד נתונים — מודלים
# ─────────────────────────────────────────────
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

# אפשר WAL mode לביצועים טובים יותר ב-SQLite
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, _):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()

class Base(DeclarativeBase):
    pass

class Client(Base):
    """לקוחות"""
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    phone = Column(String, default="")
    notes = Column(Text, default="")
    status = Column(String, default="active")  # active / pending / done
    created_at = Column(DateTime, default=datetime.utcnow)
    projects = relationship("Project", back_populates="client")

class Project(Base):
    """פרויקטים — בוטים וקבוצות"""
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)   # bot / group
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    currency = Column(String, default="USD")
    cost_usd = Column(Float, default=0)          # עלות בדולר
    advance_paid_usd = Column(Float, default=0)  # מקדמה ששולמה על העלות
    revenue_total_usd = Column(Float, default=0) # מחיר מכירה מוסכם
    revenue_received_usd = Column(Float, default=0)  # התקבל בפועל
    status = Column(String, default="active")    # active / done / paused
    deadline = Column(DateTime, nullable=True)
    deadline_alert_sent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    client = relationship("Client", back_populates="projects")
    payments = relationship("Payment", back_populates="project")

class Payment(Base):
    """תשלומים לפי פרויקט"""
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    amount_usd = Column(Float)
    note = Column(String, default="")
    paid_at = Column(DateTime, default=datetime.utcnow)
    project = relationship("Project", back_populates="payments")

class RecurringCost(Base):
    """הוצאות קבועות — עצמאי לחלוטין מפרויקטים"""
    __tablename__ = "recurring_costs"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    amount_usd = Column(Float)
    frequency = Column(String, default="monthly")  # monthly / weekly / yearly
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class MonthlyGoal(Base):
    """יעדים חודשיים"""
    __tablename__ = "monthly_goals"
    id = Column(Integer, primary_key=True)
    year = Column(Integer)
    month = Column(Integer)
    target_usd = Column(Float)
    met = Column(Boolean, nullable=True)  # None=שוטף, True/False=הסתיים
    created_at = Column(DateTime, default=datetime.utcnow)

class AuditLog(Base):
    """היסטוריה מלאה של כל פעולה"""
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    action = Column(String)         # add_project / edit_project / delete_project / add_payment / set_goal / ...
    entity_type = Column(String)    # project / client / recurring / goal
    entity_id = Column(Integer, nullable=True)
    entity_name = Column(String, default="")
    detail = Column(Text, default="")   # JSON עם לפני/אחרי
    created_at = Column(DateTime, default=datetime.utcnow)

class Settings(Base):
    """הגדרות משתמש"""
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(String)

# יצירת כל הטבלאות אם לא קיימות
Base.metadata.create_all(engine)

# ─────────────────────────────────────────────
#  עזרים
# ─────────────────────────────────────────────
_exchange_cache: dict = {"rate": 3.71, "updated": datetime.utcnow() - timedelta(hours=2)}

def get_usd_ils_rate() -> float:
    """שער חליפין USD→ILS — עם cache של 30 דקות"""
    global _exchange_cache
    if (datetime.utcnow() - _exchange_cache["updated"]).seconds < 1800:
        return _exchange_cache["rate"]
    try:
        r = requests.get(
            "https://api.exchangerate-api.com/v4/latest/USD", timeout=5
        )
        rate = r.json()["rates"]["ILS"]
        _exchange_cache = {"rate": rate, "updated": datetime.utcnow()}
        return rate
    except Exception:
        logger.warning("שגיאה בטעינת שער חליפין — משתמש בשער האחרון")
        return _exchange_cache["rate"]

def fmt(usd: float) -> str:
    """פורמט כפול: דולר + שקל"""
    rate = get_usd_ils_rate()
    return f"${usd:,.2f} | ₪{usd * rate:,.2f}"

def log_action(db: Session, action: str, entity_type: str,
               entity_id: int = None, entity_name: str = "", detail: str = ""):
    db.add(AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_name=entity_name,
        detail=detail
    ))
    db.commit()

def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(Settings, key)
    return row.value if row else default

def set_setting(db: Session, key: str, value: str):
    row = db.get(Settings, key)
    if row:
        row.value = value
    else:
        db.add(Settings(key=key, value=value))
    db.commit()

def calc_roi(cost: float, revenue: float) -> float:
    """ROI = (רווח / עלות) * 100"""
    if cost <= 0:
        return 0.0
    return round(((revenue - cost) / cost) * 100, 1)

def monthly_recurring_total(db: Session) -> float:
    """סך הוצאות קבועות לחודש (בדולר)"""
    total = 0.0
    for rc in db.query(RecurringCost).filter_by(active=True).all():
        if rc.frequency == "monthly":
            total += rc.amount_usd
        elif rc.frequency == "weekly":
            total += rc.amount_usd * 4.33
        elif rc.frequency == "yearly":
            total += rc.amount_usd / 12
    return round(total, 2)

def backup_db():
    """גיבוי יומי של מסד הנתונים"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dest = os.path.join(BACKUP_DIR, f"backup_{date.today()}.db")
    if not os.path.exists(dest):
        shutil.copy2(DB_PATH, dest)
        logger.info(f"גיבוי נשמר: {dest}")

# ─────────────────────────────────────────────
#  FastAPI — שרת REST עבור ה-Mini App
# ─────────────────────────────────────────────
app = FastAPI(title="Business Manager API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ── נקודות קצה ──────────────────────────────

@app.get("/api/dashboard")
def dashboard():
    """נתוני דשבורד ראשי"""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        projects = db.query(Project).all()
        month_projects = [p for p in projects if p.created_at >= start_month]

        total_revenue = sum(p.revenue_received_usd for p in projects)
        total_cost = sum(p.cost_usd for p in projects)
        month_revenue = sum(p.revenue_received_usd for p in month_projects)
        month_cost = sum(p.cost_usd for p in month_projects)
        month_profit = month_revenue - month_cost

        # חודש שעבר
        last_month_start = (start_month - timedelta(days=1)).replace(day=1)
        last_month_projects = [p for p in projects
                                if last_month_start <= p.created_at < start_month]
        last_profit = sum(p.revenue_received_usd - p.cost_usd for p in last_month_projects)
        trend = round(((month_profit - last_profit) / last_profit * 100), 1) if last_profit else 0

        # יעד חודשי
        goal = db.query(MonthlyGoal).filter_by(year=now.year, month=now.month).first()
        goal_pct = round((month_profit / goal.target_usd * 100), 1) if goal and goal.target_usd else 0

        # ROI לפי פרויקט
        roi_list = []
        for p in projects:
            roi = calc_roi(p.cost_usd, p.revenue_total_usd)
            roi_list.append({
                "id": p.id, "name": p.name, "category": p.category,
                "roi": roi, "profit": round(p.revenue_total_usd - p.cost_usd, 2)
            })
        roi_list.sort(key=lambda x: x["roi"], reverse=True)

        # תחזית
        day_of_month = now.day
        days_in_month = (start_month.replace(month=start_month.month % 12 + 1, day=1) - timedelta(days=1)).day
        forecast = round((month_profit / day_of_month) * days_in_month, 2) if day_of_month > 0 else 0

        # ממוצע חודשי
        all_months = {}
        for p in projects:
            key = (p.created_at.year, p.created_at.month)
            all_months.setdefault(key, 0)
            all_months[key] += p.revenue_received_usd - p.cost_usd
        avg_monthly = round(sum(all_months.values()) / len(all_months), 2) if all_months else 0

        # גבייה כללית
        total_agreed = sum(p.revenue_total_usd for p in projects)
        collection_rate = round((total_revenue / total_agreed * 100), 1) if total_agreed else 0
        outstanding = round(total_agreed - total_revenue, 2)

        # טיפים חכמים
        tips = []
        bots = [p for p in projects if p.category == "bot"]
        groups = [p for p in projects if p.category == "group"]
        bot_roi = sum(calc_roi(p.cost_usd, p.revenue_total_usd) for p in bots) / len(bots) if bots else 0
        grp_roi = sum(calc_roi(p.cost_usd, p.revenue_total_usd) for p in groups) / len(groups) if groups else 0
        if grp_roi > bot_roi and bot_roi > 0:
            diff = round(((grp_roi - bot_roi) / bot_roi) * 100, 1)
            tips.append({"icon": "📈", "text": f"הקבוצות שלך רווחיות ב-{diff}% יותר מבוטים — שקול להשקיע בהן יותר", "action": None})
        if collection_rate < 70:
            tips.append({"icon": "💳", "text": f"אחוז הגבייה שלך {collection_rate}% — שקול לשלוח תזכורות תשלום", "action": "reminder"})
        rec_total = monthly_recurring_total(db)
        if month_revenue > 0 and rec_total / month_revenue > 0.3:
            tips.append({"icon": "🔄", "text": "ההוצאות הקבועות שלך גבוהות ביחס להכנסות — כדאי לבדוק", "action": None})
        for p in projects:
            days_stuck = (now - p.created_at).days
            if p.status == "active" and days_stuck > 60:
                tips.append({"icon": "⚡", "text": f'פרויקט "{p.name}" תקוע {days_stuck} יום — כדאי לעדכן סטטוס', "action": None})
                break

        rate = get_usd_ils_rate()
        return {
            "rate": rate,
            "rate_updated": _exchange_cache["updated"].isoformat(),
            "month_profit": month_profit,
            "month_profit_display": fmt(month_profit),
            "trend": trend,
            "goal": {"target": goal.target_usd if goal else 0, "pct": goal_pct,
                     "remaining": round((goal.target_usd - month_profit), 2) if goal else 0} if goal else None,
            "forecast": forecast,
            "forecast_display": fmt(forecast),
            "avg_monthly": avg_monthly,
            "avg_monthly_display": fmt(avg_monthly),
            "outstanding": outstanding,
            "outstanding_display": fmt(outstanding),
            "collection_rate": collection_rate,
            "top3_roi": roi_list[:3],
            "star": roi_list[0] if roi_list else None,
            "tips": tips[:4],
            "total_projects": len(projects),
        }
    finally:
        db.close()

@app.get("/api/chart")
def chart_data():
    """נתוני גרף 6 חודשים אחרונים"""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        months = []
        for i in range(5, -1, -1):
            d = (now.replace(day=1) - timedelta(days=i * 28)).replace(day=1)
            months.append((d.year, d.month))

        labels, bots_data, groups_data = [], [], []
        hebrew_months = ["ינואר","פברואר","מרץ","אפריל","מאי","יוני",
                         "יולי","אוגוסט","ספטמבר","אוקטובר","נובמבר","דצמבר"]
        for y, m in months:
            start = datetime(y, m, 1)
            end = (start.replace(month=m % 12 + 1, day=1) if m < 12 else datetime(y + 1, 1, 1))
            labels.append(hebrew_months[m - 1])
            ps = db.query(Project).filter(Project.created_at >= start, Project.created_at < end).all()
            bots_data.append(round(sum(p.revenue_received_usd - p.cost_usd for p in ps if p.category == "bot"), 2))
            groups_data.append(round(sum(p.revenue_received_usd - p.cost_usd for p in ps if p.category == "group"), 2))
        return {"labels": labels, "bots": bots_data, "groups": groups_data}
    finally:
        db.close()

@app.get("/api/projects")
def list_projects(page: int = 1, category: str = None, status: str = None):
    """רשימת פרויקטים עם pagination"""
    db = SessionLocal()
    try:
        q = db.query(Project)
        if category:
            q = q.filter_by(category=category)
        if status:
            q = q.filter_by(status=status)
        total = q.count()
        projects = q.order_by(Project.created_at.desc()).offset((page - 1) * 10).limit(10).all()
        rate = get_usd_ils_rate()
        result = []
        for p in projects:
            result.append({
                "id": p.id, "name": p.name, "category": p.category,
                "status": p.status,
                "client": p.client.name if p.client else None,
                "cost_usd": p.cost_usd, "cost_ils": round(p.cost_usd * rate, 2),
                "advance_paid_usd": p.advance_paid_usd,
                "revenue_total_usd": p.revenue_total_usd,
                "revenue_total_ils": round(p.revenue_total_usd * rate, 2),
                "revenue_received_usd": p.revenue_received_usd,
                "revenue_received_ils": round(p.revenue_received_usd * rate, 2),
                "remaining_usd": round(p.revenue_total_usd - p.revenue_received_usd, 2),
                "collection_pct": round(p.revenue_received_usd / p.revenue_total_usd * 100, 1) if p.revenue_total_usd else 0,
                "roi": calc_roi(p.cost_usd, p.revenue_total_usd),
                "profit_usd": round(p.revenue_total_usd - p.cost_usd, 2),
                "deadline": p.deadline.isoformat() if p.deadline else None,
                "created_at": p.created_at.isoformat(),
            })
        return {"projects": result, "total": total, "pages": (total + 9) // 10}
    finally:
        db.close()

class ProjectCreate(BaseModel):
    name: str
    category: str
    client_id: Optional[int] = None
    currency: str = "USD"
    cost: float
    advance_paid: float = 0
    revenue_total: float
    revenue_received: float = 0
    status: str = "active"
    deadline: Optional[str] = None

@app.post("/api/projects")
def create_project(data: ProjectCreate):
    db = SessionLocal()
    try:
        rate = get_usd_ils_rate()
        factor = 1.0 if data.currency == "USD" else 1.0 / rate
        p = Project(
            name=data.name, category=data.category, client_id=data.client_id,
            currency=data.currency,
            cost_usd=round(data.cost * factor, 2),
            advance_paid_usd=round(data.advance_paid * factor, 2),
            revenue_total_usd=round(data.revenue_total * factor, 2),
            revenue_received_usd=round(data.revenue_received * factor, 2),
            status=data.status,
            deadline=datetime.fromisoformat(data.deadline) if data.deadline else None,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        log_action(db, "add_project", "project", p.id, p.name, f"עלות={p.cost_usd} הכנסה={p.revenue_total_usd}")
        return {"ok": True, "id": p.id}
    finally:
        db.close()

@app.get("/api/projects/{pid}")
def get_project(pid: int):
    db = SessionLocal()
    try:
        p = db.get(Project, pid)
        if not p:
            raise HTTPException(404, "לא נמצא")
        rate = get_usd_ils_rate()
        return {
            "id": p.id, "name": p.name, "category": p.category,
            "status": p.status, "currency": p.currency,
            "client_id": p.client_id,
            "client": p.client.name if p.client else None,
            "cost_usd": p.cost_usd,
            "revenue_total_usd": p.revenue_total_usd,
            "deadline": p.deadline.isoformat() if p.deadline else None,
            "notes": getattr(p, 'notes', ''),
        }
    finally:
        db.close()

@app.put("/api/projects/{pid}")
def update_project(pid: int, data: dict):
    db = SessionLocal()
    try:
        p = db.get(Project, pid)
        if not p:
            raise HTTPException(404, "לא נמצא")
        old = f"name={p.name} cost={p.cost_usd} revenue={p.revenue_total_usd} status={p.status}"
        for k, v in data.items():
            if hasattr(p, k):
                setattr(p, k, v)
        db.commit()
        log_action(db, "edit_project", "project", p.id, p.name, f"לפני: {old}")
        return {"ok": True}
    finally:
        db.close()

@app.delete("/api/projects/{pid}")
def delete_project(pid: int):
    db = SessionLocal()
    try:
        p = db.get(Project, pid)
        if not p:
            raise HTTPException(404, "לא נמצא")
        name = p.name
        db.delete(p)
        db.commit()
        log_action(db, "delete_project", "project", pid, name)
        return {"ok": True}
    finally:
        db.close()

class PaymentCreate(BaseModel):
    project_id: int
    amount: float
    currency: str = "USD"
    note: str = ""

@app.post("/api/payments")
def add_payment(data: PaymentCreate):
    db = SessionLocal()
    try:
        rate = get_usd_ils_rate()
        factor = 1.0 if data.currency == "USD" else 1.0 / rate
        amount_usd = round(data.amount * factor, 2)
        pay = Payment(project_id=data.project_id, amount_usd=amount_usd, note=data.note)
        db.add(pay)
        p = db.get(Project, data.project_id)
        p.revenue_received_usd = round(p.revenue_received_usd + amount_usd, 2)
        db.commit()
        log_action(db, "add_payment", "project", p.id, p.name, f"סכום={amount_usd} הערה={data.note}")
        return {"ok": True}
    finally:
        db.close()

@app.get("/api/clients")
def list_clients():
    db = SessionLocal()
    try:
        rate = get_usd_ils_rate()
        clients = db.query(Client).all()
        result = []
        for c in clients:
            total = sum(p.revenue_total_usd for p in c.projects)
            received = sum(p.revenue_received_usd for p in c.projects)
            result.append({
                "id": c.id, "name": c.name, "phone": c.phone,
                "notes": c.notes, "status": c.status,
                "projects_count": len(c.projects),
                "total_usd": total, "total_ils": round(total * rate, 2),
                "received_usd": received,
                "outstanding_usd": round(total - received, 2),
                "outstanding_ils": round((total - received) * rate, 2),
                "created_at": c.created_at.isoformat(),
            })
        return {"clients": result}
    finally:
        db.close()

class ClientCreate(BaseModel):
    name: str
    phone: str = ""
    notes: str = ""

@app.put("/api/clients/{cid}")
def update_client(cid: int, data: dict):
    db = SessionLocal()
    try:
        c = db.get(Client, cid)
        if not c:
            raise HTTPException(404, "לא נמצא")
        old = f"name={c.name} phone={c.phone} status={c.status}"
        for k, v in data.items():
            if hasattr(c, k):
                setattr(c, k, v)
        db.commit()
        log_action(db, "edit_client", "client", c.id, c.name, f"לפני: {old}")
        return {"ok": True}
    finally:
        db.close()

@app.post("/api/clients")
def create_client(data: ClientCreate):
    db = SessionLocal()
    try:
        c = Client(name=data.name, phone=data.phone, notes=data.notes)
        db.add(c)
        db.commit()
        db.refresh(c)
        log_action(db, "add_client", "client", c.id, c.name)
        return {"ok": True, "id": c.id}
    finally:
        db.close()

@app.get("/api/recurring")
def list_recurring():
    db = SessionLocal()
    try:
        rate = get_usd_ils_rate()
        items = db.query(RecurringCost).filter_by(active=True).all()
        monthly_total = monthly_recurring_total(db)
        return {
            "items": [{
                "id": r.id, "name": r.name, "amount_usd": r.amount_usd,
                "amount_ils": round(r.amount_usd * rate, 2),
                "frequency": r.frequency
            } for r in items],
            "monthly_total_usd": monthly_total,
            "monthly_total_ils": round(monthly_total * rate, 2),
            "yearly_total_usd": round(monthly_total * 12, 2),
            "yearly_total_ils": round(monthly_total * 12 * rate, 2),
        }
    finally:
        db.close()

class RecurringCreate(BaseModel):
    name: str
    amount: float
    currency: str = "USD"
    frequency: str = "monthly"

@app.post("/api/recurring")
def create_recurring(data: RecurringCreate):
    db = SessionLocal()
    try:
        rate = get_usd_ils_rate()
        factor = 1.0 if data.currency == "USD" else 1.0 / rate
        r = RecurringCost(name=data.name, amount_usd=round(data.amount * factor, 2), frequency=data.frequency)
        db.add(r)
        db.commit()
        log_action(db, "add_recurring", "recurring", r.id, r.name)
        return {"ok": True}
    finally:
        db.close()

@app.delete("/api/recurring/{rid}")
def delete_recurring(rid: int):
    db = SessionLocal()
    try:
        r = db.get(RecurringCost, rid)
        if not r:
            raise HTTPException(404)
        r.active = False
        db.commit()
        log_action(db, "delete_recurring", "recurring", rid, r.name)
        return {"ok": True}
    finally:
        db.close()

@app.get("/api/goal")
def get_goal():
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        goal = db.query(MonthlyGoal).filter_by(year=now.year, month=now.month).first()
        rate = get_usd_ils_rate()
        if not goal:
            return {"goal": None}
        return {"goal": {
            "target_usd": goal.target_usd,
            "target_ils": round(goal.target_usd * rate, 2),
        }}
    finally:
        db.close()

@app.post("/api/goal")
def set_goal(data: dict):
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        rate = get_usd_ils_rate()
        amount = data.get("amount", 0)
        currency = data.get("currency", "USD")
        amount_usd = amount if currency == "USD" else amount / rate
        goal = db.query(MonthlyGoal).filter_by(year=now.year, month=now.month).first()
        if goal:
            goal.target_usd = round(amount_usd, 2)
        else:
            db.add(MonthlyGoal(year=now.year, month=now.month, target_usd=round(amount_usd, 2)))
        db.commit()
        log_action(db, "set_goal", "goal", detail=f"יעד={amount_usd}")
        return {"ok": True}
    finally:
        db.close()

@app.get("/api/history")
def get_history(page: int = 1, category: str = None, action: str = None):
    db = SessionLocal()
    try:
        q = db.query(AuditLog)
        if action:
            q = q.filter(AuditLog.action.contains(action))
        total = q.count()
        logs = q.order_by(AuditLog.created_at.desc()).offset((page - 1) * 20).limit(20).all()
        return {
            "logs": [{
                "id": l.id, "action": l.action, "entity_type": l.entity_type,
                "entity_name": l.entity_name, "detail": l.detail,
                "created_at": l.created_at.isoformat()
            } for l in logs],
            "total": total
        }
    finally:
        db.close()

@app.get("/api/reports/{period}")
def reports(period: str):
    """דוח — weekly / monthly / quarterly"""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        if period == "weekly":
            start = now - timedelta(days=7)
        elif period == "monthly":
            start = now.replace(day=1, hour=0, minute=0, second=0)
        elif period == "quarterly":
            start = now - timedelta(days=90)
        else:
            raise HTTPException(400, "תקופה לא תקינה")

        projects = db.query(Project).filter(Project.created_at >= start).all()
        total_cost = sum(p.cost_usd for p in projects)
        total_revenue = sum(p.revenue_received_usd for p in projects)
        total_agreed = sum(p.revenue_total_usd for p in projects)
        profit = total_revenue - total_cost
        roi = calc_roi(total_cost, total_revenue)

        bots = [p for p in projects if p.category == "bot"]
        groups = [p for p in projects if p.category == "group"]
        bot_roi = sum(calc_roi(p.cost_usd, p.revenue_total_usd) for p in bots) / len(bots) if bots else 0
        grp_roi = sum(calc_roi(p.cost_usd, p.revenue_total_usd) for p in groups) / len(groups) if groups else 0
        winner = "קבוצות" if grp_roi > bot_roi else "בוטים"
        winner_diff = abs(round(((max(grp_roi, bot_roi) - min(grp_roi, bot_roi)) / min(grp_roi, bot_roi) * 100), 1)) if min(grp_roi, bot_roi) > 0 else 0

        roi_list = [{"name": p.name, "roi": calc_roi(p.cost_usd, p.revenue_total_usd)} for p in projects]
        roi_list.sort(key=lambda x: x["roi"], reverse=True)

        rate = get_usd_ils_rate()
        rec = monthly_recurring_total(db)

        return {
            "period": period,
            "total_cost_usd": total_cost, "total_cost_ils": round(total_cost * rate, 2),
            "total_revenue_usd": total_revenue, "total_revenue_ils": round(total_revenue * rate, 2),
            "profit_usd": profit, "profit_ils": round(profit * rate, 2),
            "roi": roi,
            "outstanding_usd": round(total_agreed - total_revenue, 2),
            "recurring_monthly_usd": rec,
            "bot_roi": bot_roi, "group_roi": grp_roi,
            "winner": winner, "winner_diff": winner_diff,
            "top3": roi_list[:3],
        }
    finally:
        db.close()

@app.get("/api/exchange")
def exchange():
    rate = get_usd_ils_rate()
    stale = (datetime.utcnow() - _exchange_cache["updated"]).seconds > 1800
    return {"rate": rate, "stale": stale, "updated": _exchange_cache["updated"].isoformat()}

class AIRequest(BaseModel):
    messages: list
    system: str = ""

ANTHROPIC_API_KEY = "YOUR_ANTHROPIC_API_KEY_HERE"  # הכנס את המפתח שלך מ-console.anthropic.com

@app.post("/api/ai")
def ai_proxy(data: AIRequest):
    """פרוקסי לקריאות Anthropic API — עוקף חסימות CORS"""
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1000,
                "system": data.system,
                "messages": data.messages,
            },
            timeout=30
        )
        return r.json()
    except Exception as e:
        raise HTTPException(500, str(e))

# ─────────────────────────────────────────────
#  טלגרם — Bot handlers
# ─────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """פקודת /start"""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER_ID:
        return  # התעלמות שקטה ממשתמשים לא מורשים

    rate = get_usd_ils_rate()
    text = (
        "👋 *ברוך הבא למנהל העסקי שלך*\n\n"
        f"📈 שער דולר: `${1} = ₪{rate:.2f}`\n\n"
        "לחץ על הכפתור למטה לפתיחת הדשבורד"
    )
    keyboard = [[InlineKeyboardButton(
        "פתח דשבורד 📊",
        web_app=WebAppInfo(url=WEBAPP_URL)
    )]]
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def send_monthly_summary(context: ContextTypes.DEFAULT_TYPE):
    """סיכום חודשי אוטומטי"""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        start = now.replace(day=1, hour=0, minute=0, second=0)
        projects = db.query(Project).filter(Project.created_at >= start).all()
        profit = sum(p.revenue_received_usd - p.cost_usd for p in projects)
        rec = monthly_recurring_total(db)
        goal = db.query(MonthlyGoal).filter_by(year=now.year, month=now.month).first()
        roi_list = sorted(projects, key=lambda p: calc_roi(p.cost_usd, p.revenue_total_usd), reverse=True)
        star = roi_list[0].name if roi_list else "אין"
        goal_status = ""
        if goal:
            if profit >= goal.target_usd:
                goal_status = f"✅ יעד הושג! ${profit:,.2f} / ${goal.target_usd:,.2f}"
                goal.met = True
            else:
                goal_status = f"❌ יעד לא הושג: ${profit:,.2f} / ${goal.target_usd:,.2f}"
                goal.met = False
            db.commit()
        rate = get_usd_ils_rate()
        msg = (
            f"📊 *סיכום חודשי — {now.strftime('%B %Y')}*\n\n"
            f"💰 רווח נקי: `${profit:,.2f} | ₪{profit*rate:,.2f}`\n"
            f"🔄 הוצאות קבועות: `${rec:,.2f}`\n"
            f"⭐ פרויקט הכוכב: {star}\n"
            f"🎯 {goal_status}\n\n"
            f"💡 _חודש חדש, יעדים חדשים!_"
        )
        await context.bot.send_message(
            chat_id=ALLOWED_USER_ID,
            text=msg,
            parse_mode="Markdown"
        )
    finally:
        db.close()

async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    """בדיקת תזכורות — תשלומים ודדליינים"""
    db = SessionLocal()
    try:
        reminder_days = int(get_setting(db, "reminder_days", "7"))
        now = datetime.utcnow()
        projects = db.query(Project).filter(Project.status == "active").all()
        for p in projects:
            outstanding = p.revenue_total_usd - p.revenue_received_usd
            if outstanding > 0:
                last_payment = db.query(Payment).filter_by(project_id=p.id).order_by(Payment.paid_at.desc()).first()
                last_date = last_payment.paid_at if last_payment else p.created_at
                days_since = (now - last_date).days
                if days_since >= reminder_days:
                    rate = get_usd_ils_rate()
                    client_name = p.client.name if p.client else "לקוח לא ידוע"
                    await context.bot.send_message(
                        chat_id=ALLOWED_USER_ID,
                        text=(
                            f"🔔 *תזכורת תשלום*\n\n"
                            f"פרויקט: *{p.name}*\n"
                            f"לקוח: {client_name}\n"
                            f"ממתין: `${outstanding:,.2f} | ₪{outstanding*rate:,.2f}`\n"
                            f"ימים ללא תשלום: {days_since}"
                        ),
                        parse_mode="Markdown"
                    )
            if p.deadline and not p.deadline_alert_sent:
                days_to_deadline = (p.deadline - now).days
                if 0 <= days_to_deadline <= 3:
                    await context.bot.send_message(
                        chat_id=ALLOWED_USER_ID,
                        text=(
                            f"⏰ *דדליין מתקרב!*\n\n"
                            f"פרויקט: *{p.name}*\n"
                            f"נותרו {days_to_deadline} ימים"
                        ),
                        parse_mode="Markdown"
                    )
                    p.deadline_alert_sent = True
                    db.commit()
    finally:
        db.close()

async def daily_backup(context: ContextTypes.DEFAULT_TYPE):
    """גיבוי יומי"""
    backup_db()

# ─────────────────────────────────────────────
#  הפעלה ראשית
# ─────────────────────────────────────────────
def run_api():
    """הפעלת FastAPI בthread נפרד"""
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    uvicorn.run(app, host="0.0.0.0", port=API_PORT, log_level="warning", loop="none")

async def post_init(application):
    """הגדרת משימות מתוזמנות"""
    job_queue = application.job_queue

    # בדיקת תזכורות כל 6 שעות
    job_queue.run_repeating(check_reminders, interval=21600, first=60)

    # גיבוי יומי בשעה 2:00 לפנות בוקר
    job_queue.run_daily(daily_backup, time=datetime.strptime("02:00", "%H:%M").time())

    # סיכום חודשי — בדיקה כל יום אם זה יום אחרון בחודש
    async def maybe_monthly_summary(ctx):
        now = datetime.utcnow()
        tomorrow = now + timedelta(days=1)
        if now.month != tomorrow.month:
            await send_monthly_summary(ctx)
    job_queue.run_daily(maybe_monthly_summary, time=datetime.strptime("20:00", "%H:%M").time())

def main():
    backup_db()
    logger.info("🚀 מנהל עסקי מופעל")

    # הפעלת FastAPI בthread נפרד
    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()
    logger.info(f"✅ FastAPI רץ על פורט {API_PORT}")

    # הפעלת בוט טלגרם עם uvloop
    import uvloop
    uvloop.install()
    loop = uvloop.new_event_loop()
    asyncio.set_event_loop(loop)

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    logger.info("✅ בוט טלגרם מחובר")

    loop.run_until_complete(run_bot(application))

async def run_bot(application):
    """הרצת הבוט כ-coroutine"""
    async with application:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        logger.info("✅ בוט פועל — ממתין להודעות")
        await asyncio.Event().wait()

if __name__ == "__main__":
    main()
