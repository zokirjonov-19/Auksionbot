#!/usr/bin/env python3
"""
====================================================================================
🌟 PAXTAGRAM STARS AUCTION BOT — PRODUCTION SINGLE-FILE EDITION (v4.0 FINAL)
====================================================================================
Barcha v3.0 va v4.0 audit punktlari 100% hal etilgan:
  ✅ FIX #1 (PostgreSQL-safe `case()`): `func.max(0, ...)` o'rniga SQL `CASE WHEN`
  ✅ FIX #2 (Atomic Withdrawal Approve/Reject): Ikki marta NFT ketishining oldi olindi
  ✅ FIX #3 (`place_bid` vs `finish_expired_auctions` Lock + Atomic Status Guard):
     Ikkala funksiya ham `get_auction_lock(auction_id)` oladi va `status == 'active'`
     sharti bilan atomik `UPDATE` qiladi
  ✅ FIX #4 (Relayer Gift Idempotency): `Transaction.external_ref_id` (`UNIQUE`) orqali
     webhook qayta kelganda yoki gift qayta forward qilinganda dublikat Stars berilmaydi
  ✅ FIX #5 (Strict CORS): `allow_origins` faqat aniq `PUBLIC_URL` uchun `credentials=True`
  ✅ FIX #6 (`WeakValueDictionary` Lock GC): `_auction_locks` xotirada to'planib qolmaydi
  ✅ FIX #7 (DEMO_MODE Invoice Guard): Demo rejimda `send_invoice` xatolik bermaydi
  ✅ `/health` va `/api/health` monitoring endpointlari qo'shildi

🚀 SERVERDA ISHGA TUSHIRISH:
  pip install aiogram fastapi "uvicorn[standard]" "sqlalchemy[asyncio]" aiosqlite asyncpg apscheduler httpx python-jose
  python3 paxta_stars_auction.py
====================================================================================
"""

import asyncio
import enum
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl
from weakref import WeakValueDictionary

import httpx
import uvicorn
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BusinessConnection,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
    TelegramObject,
    WebAppInfo,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    case,
    desc,
    event,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload

# ====================================================================================
# 1. QAT'IY KONFIGURATSIYA VA XAVFSIZLIK TEKSHIRUVI
# ====================================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("PaxtaStarsAuction")

DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"
API_BASE = os.getenv("API_BASE", "https://paxta.online").rstrip("/")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "StarsAuctionPaxtaBot")
RELAYER_USERNAME = os.getenv("RELAYER_USERNAME", "PaxtaAuctionRelayer")
RELAYER_BUSINESS_CONNECTION_ID = os.getenv("RELAYER_BUSINESS_CONNECTION_ID", "")

PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
WEBAPP_URL = os.getenv("WEBAPP_URL", f"{PUBLIC_URL or 'https://yourdomain.com'}/app")
ADMIN_PANEL_URL = os.getenv("ADMIN_PANEL_URL", f"{PUBLIC_URL or 'https://yourdomain.com'}/admin")

ADMIN_TELEGRAM_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_TELEGRAM_IDS", "123456789").split(",")
    if x.strip().isdigit()
}

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

if not DEMO_MODE:
    if not ADMIN_PASSWORD:
        raise RuntimeError(
            "KRITIK XAVFSIZLIK XATOSI: ADMIN_PASSWORD environment o'zgaruvchisi majburiy! "
            "(Yoki test uchun DEMO_MODE=true qo'ying)"
        )
    if not JWT_SECRET_KEY or len(JWT_SECRET_KEY) < 32:
        raise RuntimeError(
            "KRITIK XAVFSIZLIK XATOSI: JWT_SECRET_KEY kamida 32 belgidan iborat bo'lishi shart!"
        )
    if not BOT_TOKEN or "your_" in BOT_TOKEN:
        raise RuntimeError(
            "KRITIK XAVFSIZLIK XATOSI: BOT_TOKEN majburiy (@PaxtaFather dan olingan tokenni kiriting)!"
        )
else:
    ADMIN_PASSWORD = ADMIN_PASSWORD or "demo_admin_pass_2026"
    JWT_SECRET_KEY = JWT_SECRET_KEY or "demo_mode_jwt_secret_key_at_least_32_bytes_long_2026"
    BOT_TOKEN = BOT_TOKEN or "000000000:DEMO_TOKEN"
    logger.warning("⚠️ DEMO_MODE=true yoqilgan! Production serverda DEMO_MODE=false qiling.")

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 1440

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./stars_auction.db")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

DEFAULT_COMMISSION_PERCENT = int(os.getenv("DEFAULT_COMMISSION_PERCENT", "3"))
DEFAULT_MIN_WITHDRAW = int(os.getenv("DEFAULT_MIN_WITHDRAW", "50"))
DEFAULT_MIN_BID_STEP = int(os.getenv("DEFAULT_MIN_BID_STEP", "10"))
ANTI_SNIPE_WINDOW_SEC = int(os.getenv("ANTI_SNIPE_WINDOW_SEC", "60"))
ANTI_SNIPE_EXTEND_SEC = int(os.getenv("ANTI_SNIPE_EXTEND_SEC", "60"))

TELEGRAM_API_BASE = f"{API_BASE}/bot{BOT_TOKEN}"


# ====================================================================================
# 2. RATE LIMITER VA WEAKVALUE AUCTION LOCKS (FIX #6)
# ====================================================================================

class MemoryRateLimiter:
    def __init__(self) -> None:
        self._locks: Dict[str, float] = {}
        self._counters: Dict[str, List[float]] = {}
        self._last_gc: float = time.time()

    def _gc(self, now: float) -> None:
        if now - self._last_gc < 60.0:
            return
        self._last_gc = now
        self._locks = {k: v for k, v in self._locks.items() if v > now}
        cleaned_counters: Dict[str, List[float]] = {}
        for k, hits in self._counters.items():
            valid = [t for t in hits if now - t < 120.0]
            if valid:
                cleaned_counters[k] = valid
        self._counters = cleaned_counters

    def check_cooldown(self, key: str, cooldown_sec: float) -> bool:
        now = time.time()
        self._gc(now)
        exp = self._locks.get(key, 0.0)
        if now < exp:
            return False
        self._locks[key] = now + cooldown_sec
        return True

    def check_window_limit(self, key: str, max_hits: int, window_sec: float) -> bool:
        now = time.time()
        self._gc(now)
        hits = [t for t in self._counters.get(key, []) if now - t < window_sec]
        if len(hits) >= max_hits:
            self._counters[key] = hits
            return False
        hits.append(now)
        self._counters[key] = hits
        return True


rate_limiter = MemoryRateLimiter()

# Fix #6: WeakValueDictionary automatically garbage-collects unused per-auction locks
_auction_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


def get_auction_lock(auction_id: int) -> asyncio.Lock:
    lock = _auction_locks.get(auction_id)
    if lock is None:
        lock = asyncio.Lock()
        _auction_locks[auction_id] = lock
    return lock


# ====================================================================================
# 3. DATABASE ENGINE (SQLITE WAL + BUSY_TIMEOUT) & MODELS (FIX #4: external_ref_id)
# ====================================================================================

connect_args = {"timeout": 30} if "sqlite" in DATABASE_URL else {}
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    connect_args=connect_args,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _connection_record):
    if "sqlite" in DATABASE_URL:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


class AuctionStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class WithdrawalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class DepositStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class TransactionType(str, enum.Enum):
    BID = "bid"
    WIN = "win"
    DEPOSIT = "deposit"
    WITHDRAW = "withdraw"
    PROMO = "promo"
    ADMIN_ADJUST = "admin_adjust"
    REFUND = "refund"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128), default="User", nullable=False)
    balance_stars: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    total_won: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_bet: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    device_fingerprint: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    bids: Mapped[List["Bid"]] = relationship("Bid", back_populates="user")
    transactions: Mapped[List["Transaction"]] = relationship("Transaction", back_populates="user")
    withdrawals: Mapped[List["Withdrawal"]] = relationship("Withdrawal", back_populates="user")
    deposit_requests: Mapped[List["DepositRequest"]] = relationship(
        "DepositRequest", back_populates="user"
    )


class Auction(Base):
    __tablename__ = "auctions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    start_price: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    min_step: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    duration_sec: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default=AuctionStatus.PENDING.value, nullable=False, index=True
    )
    winner_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    total_pool: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    commission_taken: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    winner: Mapped[Optional["User"]] = relationship("User", foreign_keys=[winner_id], lazy="joined")
    bids: Mapped[List["Bid"]] = relationship(
        "Bid", back_populates="auction", cascade="all, delete-orphan"
    )


class Bid(Base):
    __tablename__ = "bids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    auction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("auctions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    telegram_payment_charge_id: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    auction: Mapped["Auction"] = relationship("Auction", back_populates="bids")
    user: Mapped["User"] = relationship("User", back_populates="bids")


# Fix #4: Added `external_ref_id` (unique, indexed) for idempotent gift/payment deposits
class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    external_ref_id: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="transactions")


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    stars_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    only_new_users: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class PromoUse(Base):
    __tablename__ = "promo_uses"
    __table_args__ = (UniqueConstraint("promo_id", "user_id", name="uq_promo_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    promo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("promo_codes.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class NftRate(Base):
    __tablename__ = "nft_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gift_type: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    deposit_stars: Mapped[int] = mapped_column(Integer, nullable=False)
    withdraw_stars: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class DepositRequest(Base):
    __tablename__ = "deposit_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nft_type: Mapped[str] = mapped_column(String(64), nullable=False)
    stars_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=DepositStatus.PENDING.value, nullable=False, index=True
    )
    admin_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="deposit_requests")


class Withdrawal(Base):
    __tablename__ = "withdrawals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stars_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    nft_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=WithdrawalStatus.PENDING.value, nullable=False, index=True
    )
    admin_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="withdrawals")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


# ====================================================================================
# 4. PYDANTIC SCHEMAS
# ====================================================================================

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    telegram_id: int
    username: Optional[str] = None
    first_name: str
    balance_stars: int
    is_banned: bool
    is_admin: bool
    total_won: int
    total_bet: int
    created_at: datetime


class BidOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    auction_id: int
    user_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    amount: int
    created_at: datetime


class AuctionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    image_url: Optional[str] = None
    start_price: int
    min_step: int
    duration_sec: int
    started_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    status: str
    winner_id: Optional[int] = None
    winner_name: Optional[str] = None
    highest_bid: int = 0
    total_pool: int
    commission_taken: int
    commission_percent: int = 3
    bids_count: int = 0


class BidRequest(BaseModel):
    amount: int = Field(..., gt=0)
    use_balance: bool = True


class PromoRedeemRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=64)


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    type: str
    amount: int
    external_ref_id: Optional[str] = None
    meta: Dict[str, Any]
    created_at: datetime


class WithdrawalCreateRequest(BaseModel):
    nft_type: str
    note: Optional[str] = None


class WithdrawalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    telegram_id: Optional[int] = None
    username: Optional[str] = None
    stars_amount: int
    nft_type: str
    status: str
    admin_id: Optional[int] = None
    note: Optional[str] = None
    created_at: datetime
    processed_at: Optional[datetime] = None


class NftRateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    gift_type: str
    deposit_stars: int
    withdraw_stars: int
    is_active: bool
    updated_at: datetime


class AdminLoginRequest(BaseModel):
    telegram_id: int
    password: str


class AdminBalanceAdjustRequest(BaseModel):
    amount: int
    note: str = Field(..., min_length=2, max_length=255)


class AdminUserPatchRequest(BaseModel):
    is_banned: Optional[bool] = None
    is_admin: Optional[bool] = None


class AdminPromoCreateRequest(BaseModel):
    code: str
    stars_amount: int = Field(..., gt=0)
    max_uses: int = Field(default=100, gt=0)
    expires_at: Optional[datetime] = None
    only_new_users: bool = False


class AdminPromoPatchRequest(BaseModel):
    stars_amount: Optional[int] = Field(default=None, gt=0)
    max_uses: Optional[int] = Field(default=None, gt=0)
    only_new_users: Optional[bool] = None


class AdminNftRateRequest(BaseModel):
    gift_type: str
    deposit_stars: int = Field(..., gt=0)
    withdraw_stars: int = Field(..., gt=0)
    is_active: bool = True


class AdminAuctionCreateRequest(BaseModel):
    title: str
    image_url: Optional[str] = None
    start_price: int = Field(default=50, gt=0)
    min_step: int = Field(default=10, gt=0)
    duration_sec: int = Field(default=3600, gt=60)
    start_immediately: bool = True


class AdminAuctionPatchRequest(BaseModel):
    title: Optional[str] = None
    image_url: Optional[str] = None
    min_step: Optional[int] = None
    status: Optional[str] = None


class AdminSettingsPatchRequest(BaseModel):
    commission_percent: Optional[int] = Field(default=None, ge=0, le=50)
    min_withdraw: Optional[int] = Field(default=None, ge=1)
    min_bid_step: Optional[int] = Field(default=None, ge=1)
    maintenance_mode: Optional[bool] = None
    relayer_username: Optional[str] = None
    relayer_business_connection_id: Optional[str] = None


# ====================================================================================
# 5. CORE SERVISLAR (POSTGRES-SAFE CASE() FIX #1, STRICT AUTH, RETRY HTTPX)
# ====================================================================================

security_bearer = HTTPBearer(auto_error=False)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def post_telegram_api(
    method: str, payload: Dict[str, Any], retries: int = 3, timeout: float = 10.0
) -> Optional[Dict[str, Any]]:
    if DEMO_MODE or not BOT_TOKEN or "DEMO_TOKEN" in BOT_TOKEN or "your_" in BOT_TOKEN:
        return None
    url = f"{TELEGRAM_API_BASE}/{method}"
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if resp.status_code < 500:
                    return data
        except Exception as exc:
            logger.warning("API %s urinish %d/%d xatosi: %s", method, attempt + 1, retries, exc)
        if attempt < retries - 1:
            await asyncio.sleep(0.6 * (2 ** attempt))
    return None


def verify_telegram_init_data(init_data: str) -> Dict[str, Any]:
    if not init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="PaxtaGram / Telegram initData majburiy",
        )
    parsed_data = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed_data.pop("hash", None)
    if not received_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="initData ichida hash topilmadi",
        )
    auth_date = int(parsed_data.get("auth_date", "0"))
    if abs(time.time() - auth_date) > 86400:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="initData sessiya muddati tugagan",
        )
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed_data.items(), key=lambda x: x[0])
    )
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="initData imzosi noto'g'ri",
        )
    user_json = parsed_data.get("user")
    if not user_json:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="initData ichida user ma'lumoti yo'q",
        )
    return json.loads(user_json)


async def get_current_webapp_user(
    request: Request,
    x_telegram_init_data: Optional[str] = Header(default=None, alias="X-Telegram-Init-Data"),
    db: AsyncSession = Depends(get_db),
) -> User:
    if DEMO_MODE and (not x_telegram_init_data or x_telegram_init_data == "demo"):
        tg_user = {"id": 100001, "username": "paxta_demo", "first_name": "Demo Player"}
    elif not x_telegram_init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Telegram-Init-Data majburiy (Ilovani PaxtaGram ichida oching)",
        )
    else:
        tg_user = verify_telegram_init_data(x_telegram_init_data)

    telegram_id = int(tg_user["id"])
    username = tg_user.get("username")
    first_name = tg_user.get("first_name") or "User"
    client_ip = request.headers.get("X-Real-IP") or (
        request.client.host if request.client else "0.0.0.0"
    )
    fingerprint = request.headers.get("X-Device-Fingerprint", "")

    stmt = select(User).where(User.telegram_id == telegram_id)
    user = (await db.execute(stmt)).scalar_one_or_none()

    if not user:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            balance_stars=0,
            is_admin=telegram_id in ADMIN_TELEGRAM_IDS,
            last_ip=client_ip,
            device_fingerprint=fingerprint or None,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    else:
        user.username = username
        user.first_name = first_name
        user.last_ip = client_ip
        if fingerprint:
            user.device_fingerprint = fingerprint
        await db.commit()
        await db.refresh(user)

    if user.is_banned:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sizning akkauntingiz bloklangan (Banned).",
        )
    return user


def create_admin_jwt(telegram_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {"sub": str(telegram_id), "role": "admin", "exp": expire}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


async def get_current_admin(
    paxta_adm_jwt: Optional[str] = Cookie(default=None),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
    db: AsyncSession = Depends(get_db),
) -> int:
    raw_token = paxta_adm_jwt or (credentials.credentials if credentials else None)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Admin sessiya topilmadi (Qayta kiring)")
    try:
        payload = jwt.decode(raw_token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        telegram_id = int(payload.get("sub", "0"))
        role = payload.get("role")
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Admin token muddati tugagan yoki yaroqsiz")

    if role != "admin":
        raise HTTPException(status_code=403, detail="Ruxsat yo'q")

    if telegram_id not in ADMIN_TELEGRAM_IDS:
        stmt = select(User).where(User.telegram_id == telegram_id, User.is_admin.is_(True))
        if not (await db.execute(stmt)).scalar_one_or_none():
            raise HTTPException(status_code=403, detail="Telegram ID admin oq ro'yxatida yo'q")
    return telegram_id


async def get_app_settings(db: AsyncSession) -> Dict[str, Any]:
    defaults = {
        "commission_percent": DEFAULT_COMMISSION_PERCENT,
        "min_withdraw": DEFAULT_MIN_WITHDRAW,
        "min_bid_step": DEFAULT_MIN_BID_STEP,
        "maintenance_mode": False,
        "relayer_business_connection_id": RELAYER_BUSINESS_CONNECTION_ID,
        "relayer_username": RELAYER_USERNAME,
    }
    stmt = select(Setting).where(Setting.key == "global")
    setting = (await db.execute(stmt)).scalar_one_or_none()
    if not setting:
        setting = Setting(key="global", value=defaults)
        db.add(setting)
        await db.commit()
        return defaults
    stored = dict(setting.value or {})
    return {**defaults, **stored}


async def update_app_settings(db: AsyncSession, patch_data: Dict[str, Any]) -> Dict[str, Any]:
    stmt = select(Setting).where(Setting.key == "global")
    setting = (await db.execute(stmt)).scalar_one_or_none()
    current = await get_app_settings(db)
    for k, v in patch_data.items():
        if v is not None:
            current[k] = v
    if not setting:
        setting = Setting(key="global", value=current)
        db.add(setting)
    else:
        setting.value = dict(current)
    await db.commit()
    return current


def build_safe_bid_payload(auction_id: int, telegram_id: int, amount: int) -> str:
    payload = f"bid:{auction_id}:{telegram_id}:{amount}"
    if len(payload.encode("utf-8")) > 64:
        raise ValueError("Invoice payload 64 baytdan oshib ketdi")
    return payload


async def create_stars_invoice_link(
    title: str, description: str, payload: str, amount_stars: int
) -> Optional[str]:
    if len(payload.encode("utf-8")) > 64:
        raise HTTPException(status_code=400, detail="Invoice payload juda uzun")
    data = await post_telegram_api(
        "createInvoiceLink",
        {
            "title": title,
            "description": description,
            "payload": payload,
            "provider_token": "",
            "currency": "XTR",
            "prices": [{"label": title, "amount": amount_stars}],
        },
    )
    if data and data.get("ok"):
        return data["result"]
    return None


async def refund_star_payment(telegram_user_id: int, telegram_payment_charge_id: str) -> bool:
    if not telegram_payment_charge_id or telegram_payment_charge_id.startswith("bal_"):
        return False
    data = await post_telegram_api(
        "refundStarPayment",
        {
            "user_id": telegram_user_id,
            "telegram_payment_charge_id": telegram_payment_charge_id,
        },
    )
    return bool(data and data.get("ok"))


async def get_relayer_business_gifts(business_connection_id: str) -> List[Dict[str, Any]]:
    if not business_connection_id:
        return []
    data = await post_telegram_api(
        "getBusinessAccountGifts",
        {"business_connection_id": business_connection_id},
    )
    if data and data.get("ok"):
        result = data.get("result", {})
        return result.get("gifts", []) if isinstance(result, dict) else []
    return []


async def transfer_relayer_gift(
    business_connection_id: str,
    owned_gift_id: str,
    new_owner_chat_id: int,
    star_count: int = 0,
) -> bool:
    if not business_connection_id or not owned_gift_id:
        return False
    payload: Dict[str, Any] = {
        "business_connection_id": business_connection_id,
        "owned_gift_id": owned_gift_id,
        "new_owner_chat_id": new_owner_chat_id,
    }
    if star_count > 0:
        payload["star_count"] = star_count
    data = await post_telegram_api("transferGift", payload)
    return bool(data and data.get("ok"))


async def send_telegram_notification(telegram_id: int, text: str) -> None:
    await post_telegram_api(
        "sendMessage",
        {"chat_id": telegram_id, "text": text, "parse_mode": "HTML"},
        retries=2,
        timeout=8.0,
    )


async def notify_all_admins_http(text: str) -> None:
    for adm_id in ADMIN_TELEGRAM_IDS:
        await send_telegram_notification(adm_id, text)


# Fix #1 (v4.0): PostgreSQL & SQLite compatible `case()` instead of `func.max(0, ...)`
async def adjust_user_balance(
    db: AsyncSession,
    user: User,
    amount: int,
    tx_type: str,
    meta: Optional[Dict[str, Any]] = None,
    external_ref_id: Optional[str] = None,
) -> Transaction:
    if amount < 0:
        await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(
                balance_stars=case(
                    (User.balance_stars + amount < 0, 0),
                    else_=User.balance_stars + amount,
                )
            )
        )
    else:
        await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(balance_stars=User.balance_stars + amount)
        )
    tx = Transaction(
        user_id=user.id,
        type=tx_type,
        amount=amount,
        external_ref_id=external_ref_id,
        meta=meta or {},
    )
    db.add(tx)
    return tx


# ====================================================================================
# 6. AUCTION & WEBSOCKET ENGINE (FIX #3: LOCK + ATOMIC STATUS GUARD IN BOTH FUNCTIONS)
# ====================================================================================

class AuctionWebSocketManager:
    def __init__(self) -> None:
        self.connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, auction_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.setdefault(auction_id, []).append(websocket)

    def disconnect(self, auction_id: int, websocket: WebSocket) -> None:
        if auction_id in self.connections and websocket in self.connections[auction_id]:
            self.connections[auction_id].remove(websocket)

    async def broadcast(self, auction_id: int, payload: Dict[str, Any]) -> None:
        dead = []
        for ws in self.connections.get(auction_id, []):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(auction_id, ws)


ws_manager = AuctionWebSocketManager()


async def serialize_auction(
    db: AsyncSession, auction: Auction, commission_percent: Optional[int] = None
) -> Dict[str, Any]:
    if commission_percent is None:
        settings = await get_app_settings(db)
        commission_percent = int(settings.get("commission_percent", 3))

    highest_stmt = select(func.max(Bid.amount), func.count(Bid.id)).where(
        Bid.auction_id == auction.id
    )
    highest_bid, bids_count = (await db.execute(highest_stmt)).one()
    highest_bid = highest_bid or 0
    bids_count = bids_count or 0

    winner_name = None
    if auction.winner is not None:
        winner_name = (
            f"@{auction.winner.username}"
            if auction.winner.username
            else auction.winner.first_name
        )
    elif auction.winner_id:
        w_user = (
            await db.execute(select(User).where(User.id == auction.winner_id))
        ).scalar_one_or_none()
        if w_user:
            winner_name = f"@{w_user.username}" if w_user.username else w_user.first_name

    started_dt = ensure_utc(auction.started_at)
    ends_dt = ensure_utc(auction.ends_at)

    return {
        "id": auction.id,
        "title": auction.title,
        "image_url": auction.image_url,
        "start_price": auction.start_price,
        "min_step": auction.min_step,
        "duration_sec": auction.duration_sec,
        "started_at": started_dt.isoformat() if started_dt else None,
        "ends_at": ends_dt.isoformat() if ends_dt else None,
        "status": auction.status,
        "winner_id": auction.winner_id,
        "winner_name": winner_name,
        "highest_bid": highest_bid,
        "total_pool": auction.total_pool,
        "commission_taken": auction.commission_taken,
        "commission_percent": commission_percent,
        "bids_count": bids_count,
    }


# Fix #3 (v4.0): Holds `get_auction_lock(auction_id)` AND verifies `Auction.status == 'active'` in the atomic UPDATE
async def place_bid(
    db: AsyncSession,
    auction_id: int,
    user: User,
    amount: int,
    payment_charge_id: Optional[str] = None,
    use_balance: bool = True,
) -> Dict[str, Any]:
    if not rate_limiter.check_cooldown(f"bid:{user.id}", 1.0):
        raise HTTPException(
            status_code=429,
            detail="Stavka qilish tezligi cheklangan (1 soniyada 1 marta).",
        )

    async with get_auction_lock(auction_id):
        if payment_charge_id:
            dup_stmt = select(Bid).where(Bid.telegram_payment_charge_id == payment_charge_id)
            if (await db.execute(dup_stmt)).scalar_one_or_none():
                auc_res = await db.execute(
                    select(Auction).options(selectinload(Auction.winner)).where(Auction.id == auction_id)
                )
                return await serialize_auction(db, auc_res.scalar_one())

        stmt = select(Auction).options(selectinload(Auction.winner)).where(Auction.id == auction_id)
        auction = (await db.execute(stmt)).scalar_one_or_none()
        if not auction or auction.status != AuctionStatus.ACTIVE.value:
            raise HTTPException(status_code=400, detail="Auksion faol emas yoki yakunlangan.")

        now = datetime.now(timezone.utc)
        ends_at = ensure_utc(auction.ends_at)
        if ends_at and now >= ends_at:
            raise HTTPException(status_code=400, detail="Auksion vaqti tugagan.")

        max_stmt = select(func.max(Bid.amount)).where(Bid.auction_id == auction.id)
        current_highest = (await db.execute(max_stmt)).scalar() or 0
        required_min = (
            auction.start_price
            if current_highest == 0
            else current_highest + auction.min_step
        )
        if amount < required_min:
            raise HTTPException(
                status_code=400, detail=f"Minimal stavka miqdori: {required_min} ⭐"
            )

        if use_balance and not payment_charge_id:
            bal_res = await db.execute(
                update(User)
                .where(User.id == user.id, User.balance_stars >= amount)
                .values(
                    balance_stars=User.balance_stars - amount,
                    total_bet=User.total_bet + amount,
                )
            )
            if bal_res.rowcount == 0:
                await db.refresh(user)
                raise HTTPException(
                    status_code=400,
                    detail=f"Balansingizda yetarli Stars yo'q ({user.balance_stars} ⭐). Kerak: {amount} ⭐",
                )
            payment_charge_id = f"bal_{uuid.uuid4().hex}"
        else:
            await db.execute(
                update(User)
                .where(User.id == user.id)
                .values(total_bet=User.total_bet + amount)
            )

        timer_extended = False
        new_ends_at = ends_at
        if ends_at:
            remaining = (ends_at - now).total_seconds()
            if 0 < remaining <= ANTI_SNIPE_WINDOW_SEC:
                new_ends_at = ends_at + timedelta(seconds=ANTI_SNIPE_EXTEND_SEC)
                timer_extended = True

        # Fix #3B: Atomic status guard on Auction update — rolls back if auction finished concurrently
        upd_auc = await db.execute(
            update(Auction)
            .where(
                Auction.id == auction.id,
                Auction.status == AuctionStatus.ACTIVE.value,
            )
            .values(
                total_pool=Auction.total_pool + amount,
                winner_id=user.id,
                ends_at=new_ends_at,
            )
        )
        if upd_auc.rowcount == 0:
            await db.rollback()
            raise HTTPException(
                status_code=400,
                detail="Auksion ayni damda yakunlandi. Stars qaytariladi.",
            )

        bid = Bid(
            auction_id=auction.id,
            user_id=user.id,
            amount=amount,
            telegram_payment_charge_id=payment_charge_id,
        )
        db.add(bid)

        tx = Transaction(
            user_id=user.id,
            type=TransactionType.BID.value,
            amount=-amount,
            external_ref_id=payment_charge_id,
            meta={
                "auction_id": auction.id,
                "auction_title": auction.title,
                "charge_id": payment_charge_id,
                "timer_extended": timer_extended,
            },
        )
        db.add(tx)
        await db.commit()

        await db.refresh(user)
        await db.refresh(auction)
        await db.refresh(bid)

        serialized = await serialize_auction(db, auction)
        ws_payload = {
            "event": "new_bid",
            "auction": serialized,
            "bid": {
                "id": bid.id,
                "auction_id": auction.id,
                "user_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "amount": bid.amount,
                "created_at": ensure_utc(bid.created_at).isoformat(),
            },
            "timer_extended": timer_extended,
        }
        await ws_manager.broadcast(auction.id, ws_payload)
        return serialized


async def _notify_participants_bg(tg_ids: List[int], message_html: str) -> None:
    for tg_id in tg_ids:
        await send_telegram_notification(tg_id, message_html)


# Fix #3A (v4.0): Acquires `get_auction_lock(auction.id)` AND re-checks `ends_at <= now`
# (in case anti-snipe extended `ends_at` while waiting for the lock)
async def finish_expired_auctions(db: AsyncSession) -> List[int]:
    now = datetime.now(timezone.utc)
    stmt = select(Auction.id, Auction.ends_at).where(
        Auction.status == AuctionStatus.ACTIVE.value
    )
    rows = (await db.execute(stmt)).all()
    candidate_ids = [
        aid for aid, ends_at in rows if ends_at and ensure_utc(ends_at) <= now
    ]
    if not candidate_ids:
        return []

    settings = await get_app_settings(db)
    commission_percent = int(settings.get("commission_percent", 3))
    winner_percent = 100 - commission_percent
    finished_ids: List[int] = []
    pending_notifications: List[Tuple[int, Dict[str, Any], List[int], str]] = []

    for aid in candidate_ids:
        async with get_auction_lock(aid):
            now_inner = datetime.now(timezone.utc)
            fresh = (
                await db.execute(
                    select(Auction)
                    .options(selectinload(Auction.winner))
                    .where(Auction.id == aid)
                )
            ).scalar_one_or_none()
            if not fresh or fresh.status != AuctionStatus.ACTIVE.value:
                continue
            # If an anti-snipe bid extended `ends_at` into the future while waiting for lock, skip!
            if fresh.ends_at and ensure_utc(fresh.ends_at) > now_inner:
                continue

            top_bid_stmt = (
                select(Bid)
                .where(Bid.auction_id == fresh.id)
                .order_by(desc(Bid.amount), Bid.created_at.asc())
                .limit(1)
            )
            top_bid = (await db.execute(top_bid_stmt)).scalar_one_or_none()
            winner_user: Optional[User] = None
            payout = 0
            commission = 0

            if top_bid and fresh.total_pool > 0:
                commission = round(fresh.total_pool * commission_percent / 100)
                payout = max(0, fresh.total_pool - commission)

            # Atomically transition status from ACTIVE -> FINISHED
            upd = await db.execute(
                update(Auction)
                .where(
                    Auction.id == fresh.id,
                    Auction.status == AuctionStatus.ACTIVE.value,
                )
                .values(
                    status=AuctionStatus.FINISHED.value,
                    commission_taken=commission,
                    winner_id=top_bid.user_id if top_bid else None,
                )
            )
            if upd.rowcount == 0:
                continue

            if top_bid and payout > 0:
                winner_user = (
                    await db.execute(select(User).where(User.id == top_bid.user_id))
                ).scalar_one_or_none()
                if winner_user:
                    await db.execute(
                        update(User)
                        .where(User.id == winner_user.id)
                        .values(
                            balance_stars=User.balance_stars + payout,
                            total_won=User.total_won + payout,
                        )
                    )
                    db.add(
                        Transaction(
                            user_id=winner_user.id,
                            type=TransactionType.WIN.value,
                            amount=payout,
                            external_ref_id=f"win_auc_{fresh.id}",
                            meta={
                                "auction_id": fresh.id,
                                "auction_title": fresh.title,
                                "total_pool": fresh.total_pool,
                                "commission_taken": commission,
                            },
                        )
                    )

            await db.commit()
            await db.refresh(fresh)
            finished_ids.append(fresh.id)

            part_stmt = (
                select(User.telegram_id)
                .join(Bid, Bid.user_id == User.id)
                .where(Bid.auction_id == fresh.id)
                .distinct()
            )
            participant_tg_ids = [r[0] for r in (await db.execute(part_stmt)).all()]
            winner_label = (
                f"@{winner_user.username}"
                if (winner_user and winner_user.username)
                else (winner_user.first_name if winner_user else "Aniqlanmadi")
            )
            summary_msg = (
                f"🏁 <b>Auksion yakunlandi!</b>\n\n"
                f"🎯 Nomi: <b>{fresh.title}</b>\n"
                f"🏆 G'olib: <b>{winner_label}</b>\n"
                f"💰 Jami bank (Pool): <b>{fresh.total_pool} ⭐</b>\n"
                f"🎁 G'olib mukofoti ({winner_percent}%): <b>{payout} ⭐</b>"
            )
            serialized = await serialize_auction(
                db, fresh, commission_percent=commission_percent
            )
            pending_notifications.append(
                (fresh.id, serialized, participant_tg_ids, summary_msg)
            )

    for auc_id, serialized_data, tg_ids, msg in pending_notifications:
        await ws_manager.broadcast(
            auc_id, {"event": "auction_finished", "auction": serialized_data}
        )
        if tg_ids:
            asyncio.create_task(_notify_participants_bg(tg_ids, msg))

    return finished_ids


async def cancel_auction_and_refund(db: AsyncSession, auction_id: int) -> Auction:
    async with get_auction_lock(auction_id):
        stmt = (
            select(Auction)
            .options(
                selectinload(Auction.bids).selectinload(Bid.user),
                selectinload(Auction.winner),
            )
            .where(Auction.id == auction_id)
        )
        auction = (await db.execute(stmt)).scalar_one_or_none()
        if not auction:
            raise HTTPException(status_code=404, detail="Auksion topilmadi")

        upd = await db.execute(
            update(Auction)
            .where(
                Auction.id == auction_id,
                Auction.status.notin_(
                    [AuctionStatus.FINISHED.value, AuctionStatus.CANCELLED.value]
                ),
            )
            .values(status=AuctionStatus.CANCELLED.value)
        )
        if upd.rowcount == 0:
            raise HTTPException(
                status_code=400, detail="Auksion allaqachon tugagan yoki bekor qilingan"
            )

        notify_tasks: List[Tuple[int, str]] = []
        for bid in auction.bids:
            u = bid.user
            refunded_via_tg = False
            if bid.telegram_payment_charge_id and not bid.telegram_payment_charge_id.startswith("bal_"):
                refunded_via_tg = await refund_star_payment(
                    u.telegram_id, bid.telegram_payment_charge_id
                )
            if not refunded_via_tg:
                await db.execute(
                    update(User)
                    .where(User.id == u.id)
                    .values(balance_stars=User.balance_stars + bid.amount)
                )

            db.add(
                Transaction(
                    user_id=u.id,
                    type=TransactionType.REFUND.value,
                    amount=bid.amount,
                    external_ref_id=f"refund_bid_{bid.id}",
                    meta={
                        "auction_id": auction.id,
                        "auction_title": auction.title,
                        "refunded_via_paxtagram": refunded_via_tg,
                    },
                )
            )
            notify_tasks.append(
                (
                    u.telegram_id,
                    f"♻️ <b>{auction.title}</b> auksioni bekor qilindi. Sizning <b>{bid.amount} ⭐</b> stavkangiz qaytarildi.",
                )
            )

        await db.commit()
        await db.refresh(auction)

    serialized = await serialize_auction(db, auction)
    await ws_manager.broadcast(
        auction.id, {"event": "auction_cancelled", "auction": serialized}
    )
    for tg_id, text in notify_tasks:
        asyncio.create_task(send_telegram_notification(tg_id, text))

    return auction


# ====================================================================================
# 7. PROMO KOD VA NFT KURSLARI SERVISLARI (FIX #2: ATOMIC WITHDRAWAL APPROVE/REJECT)
# ====================================================================================

DEFAULT_NFT_RATES = [
    {"gift_type": "Teddy Bear 🧸", "deposit_stars": 45, "withdraw_stars": 55},
    {"gift_type": "Red Heart ❤️", "deposit_stars": 45, "withdraw_stars": 55},
    {"gift_type": "Rocket 🚀", "deposit_stars": 90, "withdraw_stars": 110},
    {"gift_type": "Diamond Ring 💍", "deposit_stars": 220, "withdraw_stars": 250},
    {"gift_type": "Golden Trophy 🏆", "deposit_stars": 450, "withdraw_stars": 500},
]


async def ensure_default_nft_rates(db: AsyncSession) -> List[NftRate]:
    res = await db.execute(select(NftRate).order_by(NftRate.deposit_stars.asc()))
    rates = list(res.scalars().all())
    if not rates:
        for item in DEFAULT_NFT_RATES:
            db.add(NftRate(**item, is_active=True))
        await db.commit()
        res = await db.execute(select(NftRate).order_by(NftRate.deposit_stars.asc()))
        rates = list(res.scalars().all())
    return rates


async def ensure_demo_auction_and_promo(db: AsyncSession) -> None:
    auc_cnt = (await db.execute(select(func.count(Auction.id)))).scalar() or 0
    if auc_cnt == 0:
        now = datetime.now(timezone.utc)
        db.add(
            Auction(
                title="Golden Paxta NFT & Stars Jackpot 🏆",
                image_url="https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=900&auto=format&fit=crop&q=80",
                start_price=50,
                min_step=10,
                duration_sec=7200,
                started_at=now,
                ends_at=now + timedelta(seconds=7200),
                status=AuctionStatus.ACTIVE.value,
            )
        )
    promo_cnt = (await db.execute(select(func.count(PromoCode.id)))).scalar() or 0
    if promo_cnt == 0:
        db.add(
            PromoCode(
                code="PAXTA2026",
                stars_amount=100,
                max_uses=500,
                only_new_users=False,
            )
        )
    await db.commit()


async def redeem_promo_code(db: AsyncSession, user: User, raw_code: str) -> int:
    if not rate_limiter.check_window_limit(f"promo:{user.id}", 5, 60.0):
        raise HTTPException(
            status_code=429,
            detail="Promo kod kiritish limiti oshdi (daqiqasiga 5 ta urinish).",
        )
    code_str = raw_code.strip().upper()
    stmt = select(PromoCode).where(func.upper(PromoCode.code) == code_str)
    promo = (await db.execute(stmt)).scalar_one_or_none()
    if not promo:
        raise HTTPException(status_code=404, detail="Bunday promo kod mavjud emas.")

    now = datetime.now(timezone.utc)
    if promo.expires_at and now > ensure_utc(promo.expires_at):
        raise HTTPException(status_code=400, detail="Promo kod muddati tugagan.")

    if promo.only_new_users:
        age_h = (now - ensure_utc(user.created_at)).total_seconds() / 3600
        bid_cnt = (
            await db.execute(select(func.count(Bid.id)).where(Bid.user_id == user.id))
        ).scalar() or 0
        if age_h > 24 or bid_cnt > 0:
            raise HTTPException(
                status_code=400,
                detail="Ushbu promo kod faqat yangi foydalanuvchilar uchun.",
            )

    use_stmt = select(PromoUse).where(
        PromoUse.promo_id == promo.id, PromoUse.user_id == user.id
    )
    if (await db.execute(use_stmt)).scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail="Siz ushbu promo koddan avval foydalangansiz."
        )

    upd_promo = await db.execute(
        update(PromoCode)
        .where(PromoCode.id == promo.id, PromoCode.used_count < PromoCode.max_uses)
        .values(used_count=PromoCode.used_count + 1)
    )
    if upd_promo.rowcount == 0:
        raise HTTPException(status_code=400, detail="Promo kod foydalanish limiti tugagan.")

    await db.execute(
        update(User)
        .where(User.id == user.id)
        .values(balance_stars=User.balance_stars + promo.stars_amount)
    )
    db.add(PromoUse(promo_id=promo.id, user_id=user.id))
    db.add(
        Transaction(
            user_id=user.id,
            type=TransactionType.PROMO.value,
            amount=promo.stars_amount,
            external_ref_id=f"promo_{promo.id}_user_{user.id}",
            meta={"code": promo.code, "promo_id": promo.id},
        )
    )
    await db.commit()
    await db.refresh(user)
    return promo.stars_amount


async def create_nft_withdrawal_request(
    db: AsyncSession, user: User, nft_type: str, note: Optional[str] = None
) -> Withdrawal:
    settings = await get_app_settings(db)
    min_withdraw = int(settings.get("min_withdraw", 50))

    stmt = select(NftRate).where(NftRate.gift_type == nft_type, NftRate.is_active.is_(True))
    rate = (await db.execute(stmt)).scalar_one_or_none()
    if not rate:
        raise HTTPException(status_code=404, detail="Tanlangan NFT turi faol emas.")

    cost = rate.withdraw_stars
    if cost < min_withdraw:
        raise HTTPException(
            status_code=400, detail=f"Minimal yechib olish miqdori: {min_withdraw} ⭐"
        )

    res = await db.execute(
        update(User)
        .where(User.id == user.id, User.balance_stars >= cost)
        .values(balance_stars=User.balance_stars - cost)
    )
    if res.rowcount == 0:
        await db.refresh(user)
        raise HTTPException(
            status_code=400,
            detail=f"Balansingizda yetarli Stars yo'q ({user.balance_stars} ⭐). Kerak: {cost} ⭐",
        )

    withdrawal = Withdrawal(
        user_id=user.id,
        stars_amount=cost,
        nft_type=rate.gift_type,
        status=WithdrawalStatus.PENDING.value,
        note=note,
    )
    db.add(withdrawal)
    db.add(
        Transaction(
            user_id=user.id,
            type=TransactionType.WITHDRAW.value,
            amount=-cost,
            meta={"nft_type": rate.gift_type, "status": WithdrawalStatus.PENDING.value},
        )
    )
    await db.commit()
    await db.refresh(user)
    await db.refresh(withdrawal)
    return withdrawal


# Fix #2 (v4.0): Atomic status transition (`WHERE id = :id AND status = 'pending'`) BEFORE any NFT transfer or refund
async def process_withdrawal_decision(
    db: AsyncSession,
    withdrawal_id: int,
    admin_telegram_id: int,
    approve: bool,
    note: Optional[str] = None,
) -> Withdrawal:
    target_status = (
        WithdrawalStatus.APPROVED.value if approve else WithdrawalStatus.REJECTED.value
    )
    upd = await db.execute(
        update(Withdrawal)
        .where(
            Withdrawal.id == withdrawal_id,
            Withdrawal.status == WithdrawalStatus.PENDING.value,
        )
        .values(
            status=target_status,
            admin_id=admin_telegram_id,
            processed_at=datetime.now(timezone.utc),
            note=note if note is not None else Withdrawal.note,
        )
    )
    if upd.rowcount == 0:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Ushbu so'rov allaqachon ko'rib chiqilgan yoki topilmadi",
        )

    # Commit the atomic lock on status immediately so no concurrent request can pass
    await db.commit()

    withdrawal = (
        await db.execute(
            select(Withdrawal)
            .options(selectinload(Withdrawal.user))
            .where(Withdrawal.id == withdrawal_id)
        )
    ).scalar_one()
    user = withdrawal.user

    if approve:
        settings = await get_app_settings(db)
        b_conn_id = settings.get("relayer_business_connection_id", "")
        auto_transferred = False
        if b_conn_id:
            gifts = await get_relayer_business_gifts(b_conn_id)
            for g in gifts:
                if g.get("owned_gift_id"):
                    auto_transferred = await transfer_relayer_gift(
                        business_connection_id=b_conn_id,
                        owned_gift_id=g["owned_gift_id"],
                        new_owner_chat_id=user.telegram_id,
                    )
                    if auto_transferred:
                        break

        if auto_transferred:
            msg_extra = "⚡️ Relayer orqali avtomatik ravishda PaxtaGram profilingizga yetkazildi!"
            asyncio.create_task(
                notify_all_admins_http(
                    f"✅ <b>Withdrawal #{withdrawal.id}</b> Relayer orqali avtomatik yuborildi (@{user.username or user.telegram_id})."
                )
            )
        else:
            msg_extra = "⏳ So'rovingiz tasdiqlandi! NFT sovg'a admin tomonidan qo'lda yuboriladi (1-2 soat ichida)."
            asyncio.create_task(
                notify_all_admins_http(
                    f"⚠️ <b>DIQQAT: Withdrawal #{withdrawal.id}</b> tasdiqlandi, lekin Relayer auto-transfer amalga oshmadi!\n"
                    f"👤 Foydalanuvchi: <b>{user.first_name}</b> (@{user.username or 'no_username'}, ID: <code>{user.telegram_id}</code>)\n"
                    f"🎁 NFT: <b>{withdrawal.nft_type}</b> — Iltimos, qo'lda yuboring!"
                )
            )

        asyncio.create_task(
            send_telegram_notification(
                user.telegram_id,
                f"✅ <b>Yechib olish so'rovingiz #{withdrawal.id} tasdiqlandi!</b>\n\n"
                f"🎁 NFT: <b>{withdrawal.nft_type}</b> ({withdrawal.stars_amount} ⭐)\n{msg_extra}",
            )
        )
    else:
        await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(balance_stars=User.balance_stars + withdrawal.stars_amount)
        )
        db.add(
            Transaction(
                user_id=user.id,
                type=TransactionType.REFUND.value,
                amount=withdrawal.stars_amount,
                external_ref_id=f"refund_wd_{withdrawal.id}",
                meta={"reason": "withdrawal_rejected", "withdrawal_id": withdrawal.id},
            )
        )
        await db.commit()
        await db.refresh(withdrawal)
        asyncio.create_task(
            send_telegram_notification(
                user.telegram_id,
                f"❌ <b>Yechib olish so'rovingiz #{withdrawal.id} rad etildi.</b>\n"
                f"♻️ <b>{withdrawal.stars_amount} ⭐</b> balansingizga qaytarildi.",
            )
        )

    return withdrawal


# ====================================================================================
# 8. AIOGRAM 3.x BOT HANDLERLARI (FIX #4 GIFT IDEMPOTENCY, FIX #7 DEMO GUARD)
# ====================================================================================

bot_router = Router(name="paxta_stars_bot")


class BotStates(StatesGroup):
    waiting_custom_bid = State()
    waiting_promo = State()


class DbAndAuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: Dict[str, Any]) -> Any:
        async with AsyncSessionLocal() as db:
            data["db"] = db
            tg_user = data.get("event_from_user")
            if tg_user:
                if not rate_limiter.check_cooldown(f"flood:{tg_user.id}", 0.35):
                    if isinstance(event, CallbackQuery):
                        await event.answer("⏳ Iltimos, juda tez bosmang!", show_alert=False)
                    return None
                stmt = select(User).where(User.telegram_id == tg_user.id)
                user = (await db.execute(stmt)).scalar_one_or_none()
                if not user:
                    user = User(
                        telegram_id=tg_user.id,
                        username=tg_user.username,
                        first_name=tg_user.first_name or "User",
                        balance_stars=0,
                        is_admin=tg_user.id in ADMIN_TELEGRAM_IDS,
                    )
                    db.add(user)
                    await db.commit()
                    await db.refresh(user)
                else:
                    user.username = tg_user.username
                    user.first_name = tg_user.first_name or user.first_name
                    if tg_user.id in ADMIN_TELEGRAM_IDS:
                        user.is_admin = True
                    await db.commit()
                if user.is_banned:
                    if isinstance(event, Message):
                        await event.answer("⛔ Sizning akkauntingiz bloklangan.")
                    return None
                data["user"] = user
            return await handler(event, data)


def get_reply_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [
            KeyboardButton(text="🎯 Auksion"),
            KeyboardButton(text="💰 Balans"),
            KeyboardButton(text="🎁 Promo kod"),
        ],
        [
            KeyboardButton(text="💎 NFT depozit"),
            KeyboardButton(text="📤 Yechib olish"),
            KeyboardButton(text="🌐 Web App", web_app=WebAppInfo(url=WEBAPP_URL)),
        ],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="🛠 Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def get_auction_kb(auction_id: int, next_min: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⭐ Stavka qilish ({next_min} Stars Invoice)",
                    callback_data=f"bid_inv:{auction_id}:{next_min}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"💰 Balansdan stavka ({next_min} ⭐)",
                    callback_data=f"bid_bal:{auction_id}:{next_min}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✍️ Boshqa summa", callback_data=f"bid_custom:{auction_id}"
                ),
                InlineKeyboardButton(
                    text="🔄 Yangilash", callback_data=f"auc_ref:{auction_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🌐 Liquid Glass Mini-App",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                )
            ],
        ]
    )


def format_auction_card(d: Dict[str, Any]) -> str:
    rem_str = "00:00:00"
    if d.get("ends_at"):
        ends_dt = datetime.fromisoformat(d["ends_at"])
        rem = max(0, int((ends_dt - datetime.now(timezone.utc)).total_seconds()))
        h, r = divmod(rem, 3600)
        m, s = divmod(r, 60)
        rem_str = f"{h:02d}:{m:02d}:{s:02d}"
    highest = d["highest_bid"]
    next_min = d["start_price"] if highest == 0 else highest + d["min_step"]
    leader = d.get("winner_name") or "Hali yo'q"
    comm_pct = int(d.get("commission_percent", 3))
    win_pct = 100 - comm_pct
    win_amount = round(d["total_pool"] * win_pct / 100)
    return (
        f"🎯 <b>Auksion #{d['id']}: {d['title']}</b>\n\n"
        f"💎 Start narx: <b>{d['start_price']} ⭐</b> (Qadam: +{d['min_step']} ⭐)\n"
        f"🔥 Eng yuqori stavka: <b>{highest} ⭐</b> ({leader})\n"
        f"💰 Jami bank (Pool): <b>{d['total_pool']} ⭐</b>\n"
        f"🏆 G'olib oladi ({win_pct}%): <b>{win_amount} ⭐</b>\n"
        f"⏱ Qolgan vaqt: <code>{rem_str}</code>\n\n"
        f"⚡ Keyingi minimal stavka: <b>{next_min} ⭐</b>"
    )


@bot_router.message(CommandStart())
async def cmd_start(message: Message, user: User, db: AsyncSession):
    settings = await get_app_settings(db)
    win_pct = 100 - int(settings.get("commission_percent", 3))
    await message.answer(
        f"✨ <b>Assalomu alaykum, {user.first_name}!</b>\n\n"
        f"🌟 <b>PaxtaGram Stars Auction Bot</b>ga xush kelibsiz!\n"
        f"• Har bir stavka umumiy bankka (Pool) qo'shiladi.\n"
        f"• Oxirgi 60 soniyada stavka qilinsa, taymer <b>+60s</b> ga uzayadi.\n"
        f"• G'olib jami Poolning <b>{win_pct}%</b> qismini oladi!\n\n"
        f"💰 <b>Joriy balansingiz:</b> <code>{user.balance_stars} ⭐</code>\n"
        f"🎁 Promo kod: <code>PAXTA2026</code>",
        reply_markup=get_reply_kb(is_admin=user.is_admin),
    )


@bot_router.message(F.text == "🎯 Auksion")
async def msg_auctions(message: Message, db: AsyncSession):
    stmt = (
        select(Auction)
        .options(selectinload(Auction.winner))
        .where(Auction.status == AuctionStatus.ACTIVE.value)
        .order_by(Auction.ends_at.asc())
    )
    auctions = (await db.execute(stmt)).scalars().all()
    if not auctions:
        await message.answer("📭 <b>Hozirda faol auksionlar yo'q.</b>")
        return
    for a in auctions:
        d = await serialize_auction(db, a)
        next_min = d["start_price"] if d["highest_bid"] == 0 else d["highest_bid"] + d["min_step"]
        await message.answer(format_auction_card(d), reply_markup=get_auction_kb(a.id, next_min))


@bot_router.callback_query(F.data.startswith("auc_ref:"))
async def cb_auc_refresh(call: CallbackQuery, db: AsyncSession):
    aid = int(call.data.split(":")[1])
    a = (
        await db.execute(
            select(Auction).options(selectinload(Auction.winner)).where(Auction.id == aid)
        )
    ).scalar_one_or_none()
    if not a:
        await call.answer("Topilmadi", show_alert=True)
        return
    d = await serialize_auction(db, a)
    next_min = d["start_price"] if d["highest_bid"] == 0 else d["highest_bid"] + d["min_step"]
    try:
        await call.message.edit_text(format_auction_card(d), reply_markup=get_auction_kb(a.id, next_min))
    except Exception:
        pass
    await call.answer("🔄 Yangilandi")


# Fix #7 (v4.0): Guard against calling `bot.send_invoice` in DEMO_MODE
@bot_router.callback_query(F.data.startswith("bid_inv:"))
async def cb_bid_invoice(call: CallbackQuery, user: User, db: AsyncSession, bot: Bot):
    if DEMO_MODE:
        await call.answer(
            "⚠️ Demo rejimida Stars Invoice ishlamaydi. 'Balansdan stavka' dan foydalaning.",
            show_alert=True,
        )
        return

    _, aid_s, amt_s = call.data.split(":")
    aid, amount = int(aid_s), int(amt_s)
    a = (
        await db.execute(
            select(Auction).options(selectinload(Auction.winner)).where(Auction.id == aid)
        )
    ).scalar_one_or_none()
    if not a or a.status != AuctionStatus.ACTIVE.value:
        await call.answer("Auksion faol emas!", show_alert=True)
        return

    max_stmt = select(func.max(Bid.amount)).where(Bid.auction_id == aid)
    current_highest = (await db.execute(max_stmt)).scalar() or 0
    required_min = a.start_price if current_highest == 0 else current_highest + a.min_step
    if amount < required_min:
        d = await serialize_auction(db, a)
        try:
            await call.message.edit_text(
                format_auction_card(d), reply_markup=get_auction_kb(aid, required_min)
            )
        except Exception:
            pass
        await call.answer(
            f"⚠️ Narx o'zgardi! Yangi minimal stavka: {required_min} ⭐", show_alert=True
        )
        return

    payload = build_safe_bid_payload(a.id, user.telegram_id, amount)
    await call.answer("⭐ PaxtaGram Stars to'lov oynasi ochildi")
    await bot.send_invoice(
        chat_id=user.telegram_id,
        title=f"🎯 Stavka: {a.title}",
        description=f"{a.title} auksionida {amount} Stars stavka qilish",
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"Stavka ({amount} ⭐)", amount=amount)],
    )


@bot_router.callback_query(F.data.startswith("bid_bal:"))
async def cb_bid_balance(call: CallbackQuery, user: User, db: AsyncSession):
    _, aid_s, amt_s = call.data.split(":")
    aid, amount = int(aid_s), int(amt_s)
    try:
        d = await place_bid(db, aid, user, amount, use_balance=True)
        await call.answer(f"✅ {amount} ⭐ stavka qabul qilindi!", show_alert=True)
        next_min = d["start_price"] if d["highest_bid"] == 0 else d["highest_bid"] + d["min_step"]
        await call.message.edit_text(format_auction_card(d), reply_markup=get_auction_kb(aid, next_min))
    except Exception as e:
        await call.answer(f"⚠️ {getattr(e, 'detail', str(e))}", show_alert=True)


@bot_router.callback_query(F.data.startswith("bid_custom:"))
async def cb_bid_custom(call: CallbackQuery, state: FSMContext):
    aid = int(call.data.split(":")[1])
    await state.set_state(BotStates.waiting_custom_bid)
    await state.update_data(auction_id=aid)
    await call.answer()
    await call.message.answer("✍️ <b>Stavka miqdorini kiriting (Stars soni):</b>")


@bot_router.message(BotStates.waiting_custom_bid)
async def msg_custom_bid(message: Message, state: FSMContext, user: User, db: AsyncSession, bot: Bot):
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ Musbat butun son kiriting.")
        return
    amount = int(raw)
    st = await state.get_data()
    aid = int(st["auction_id"])
    await state.clear()

    if DEMO_MODE:
        await message.answer(
            "⚠️ Demo rejimida Stars Invoice ishlamaydi. 'Balansdan stavka' tugmasidan foydalaning."
        )
        return

    a = (await db.execute(select(Auction).where(Auction.id == aid))).scalar_one_or_none()
    if not a or a.status != AuctionStatus.ACTIVE.value:
        await message.answer("⚠️ Auksion yakunlangan.")
        return

    max_stmt = select(func.max(Bid.amount)).where(Bid.auction_id == aid)
    current_highest = (await db.execute(max_stmt)).scalar() or 0
    required_min = a.start_price if current_highest == 0 else current_highest + a.min_step
    if amount < required_min:
        await message.answer(f"⚠️ Minimal stavka miqdori: <b>{required_min} ⭐</b>")
        return

    payload = build_safe_bid_payload(a.id, user.telegram_id, amount)
    await bot.send_invoice(
        chat_id=user.telegram_id,
        title=f"🎯 Stavka: {a.title}",
        description=f"{a.title} auksionida {amount} Stars stavka qilish",
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"Stavka ({amount} ⭐)", amount=amount)],
    )


@bot_router.pre_checkout_query()
async def on_pre_checkout(q: PreCheckoutQuery, db: AsyncSession):
    payload = q.invoice_payload or ""
    if payload.startswith("bid:"):
        parts = payload.split(":")
        if len(parts) != 4 or not parts[1].isdigit() or not parts[3].isdigit():
            await q.answer(ok=False, error_message="Noto'g'ri stavka ma'lumoti.")
            return
        aid = int(parts[1])
        amount = int(parts[3])
        a = (await db.execute(select(Auction).where(Auction.id == aid))).scalar_one_or_none()
        if not a or a.status != AuctionStatus.ACTIVE.value:
            await q.answer(ok=False, error_message="Ushbu auksion yakunlangan.")
            return
        max_stmt = select(func.max(Bid.amount)).where(Bid.auction_id == aid)
        current_highest = (await db.execute(max_stmt)).scalar() or 0
        required_min = a.start_price if current_highest == 0 else current_highest + a.min_step
        if amount < required_min:
            await q.answer(
                ok=False,
                error_message=f"Narx yangilandi! Minimal stavka: {required_min} Stars.",
            )
            return
        await q.answer(ok=True)
    else:
        await q.answer(ok=False, error_message="Noma'lum invoice turi.")


@bot_router.message(F.successful_payment)
async def on_successful_payment(message: Message, user: User, db: AsyncSession):
    sp = message.successful_payment
    if not sp:
        return
    payload = sp.invoice_payload or ""
    amount = sp.total_amount
    charge_id = sp.telegram_payment_charge_id
    if payload.startswith("bid:"):
        aid = int(payload.split(":")[1])
        try:
            d = await place_bid(db, aid, user, amount, payment_charge_id=charge_id, use_balance=False)
            await message.answer(
                f"🎉 <b>Stavkangiz qabul qilindi!</b>\n"
                f"🎯 Auksion: <b>{d['title']}</b>\n"
                f"⭐ Stavka: <b>{amount} ⭐</b> | Pool: <b>{d['total_pool']} ⭐</b>"
            )
        except Exception as e:
            reason = getattr(e, "detail", str(e))
            refund_ok = await refund_star_payment(user.telegram_id, charge_id)
            if refund_ok:
                db.add(
                    Transaction(
                        user_id=user.id,
                        type=TransactionType.REFUND.value,
                        amount=amount,
                        external_ref_id=f"refund_charge_{charge_id}",
                        meta={"charge_id": charge_id, "reason": reason, "refunded_real_stars": True},
                    )
                )
                await db.commit()
                await message.answer(
                    f"♻️ Stavka vaqtida narx yoki holat o'zgardi ({reason}). "
                    f"To'langan <b>{amount} ⭐</b> real Stars hisobingizga qaytarildi!"
                )
            else:
                logger.error(
                    "CRITICAL: refundStarPayment failed for user=%s charge_id=%s amount=%s",
                    user.telegram_id,
                    charge_id,
                    amount,
                )
                await adjust_user_balance(
                    db,
                    user,
                    amount,
                    TransactionType.REFUND.value,
                    {"charge_id": charge_id, "reason": reason, "fallback_internal": True},
                    external_ref_id=f"fallback_charge_{charge_id}",
                )
                await db.commit()
                await message.answer(
                    f"⚠️ Stavka qabul qilinmadi ({reason}). "
                    f"<b>{amount} ⭐</b> ichki balansingizga saqlandi. Admin bilan bog'laning."
                )


@bot_router.message(F.text == "💰 Balans")
async def msg_balance(message: Message, user: User, db: AsyncSession):
    txs = (
        await db.execute(
            select(Transaction)
            .where(Transaction.user_id == user.id)
            .order_by(desc(Transaction.created_at))
            .limit(8)
        )
    ).scalars().all()
    hist = "\n".join(
        f"• <code>{ensure_utc(t.created_at).strftime('%d.%m %H:%M')}</code> | {t.type.upper()}: <b>{'+' if t.amount >= 0 else ''}{t.amount} ⭐</b>"
        for t in txs
    ) or "Tranzaksiyalar hali yo'q"
    await message.answer(
        f"💰 <b>Sizning Stars Balansingiz:</b> <code>{user.balance_stars} ⭐</code>\n"
        f"🏆 Jami yutuq: <b>{user.total_won} ⭐</b> | Tikilgan: <b>{user.total_bet} ⭐</b>\n\n"
        f"📜 <b>Oxirgi amallar:</b>\n{hist}"
    )


@bot_router.message(F.text == "🎁 Promo kod")
async def msg_promo(message: Message, state: FSMContext):
    await state.set_state(BotStates.waiting_promo)
    await message.answer("🎁 <b>Promo kodni yuboring</b> (Masalan: <code>PAXTA2026</code>):")


@bot_router.message(BotStates.waiting_promo)
async def msg_promo_redeem(message: Message, state: FSMContext, user: User, db: AsyncSession):
    await state.clear()
    try:
        added = await redeem_promo_code(db, user, message.text or "")
        await message.answer(
            f"🎉 <b>Promo kod faollashtirildi!</b>\n➕ Qo'shildi: <b>+{added} ⭐</b>\n"
            f"💰 Balans: <b>{user.balance_stars} ⭐</b>"
        )
    except Exception as e:
        await message.answer(f"❌ {getattr(e, 'detail', str(e))}")


@bot_router.business_connection()
async def on_relayer_business_connection(conn: BusinessConnection, db: AsyncSession):
    if conn.is_enabled:
        await update_app_settings(
            db,
            {
                "relayer_business_connection_id": conn.id,
                "relayer_username": conn.user.username or RELAYER_USERNAME,
            },
        )
        await notify_all_admins_http(
            f"✅ <b>PaxtaGram Relayer ulandi!</b> ID: <code>{conn.id}</code> (@{conn.user.username})"
        )
    else:
        await update_app_settings(db, {"relayer_business_connection_id": ""})
        await notify_all_admins_http("⚠️ <b>PaxtaGram Relayer uzildi!</b>")


# Fix #4 (v4.0): Idempotent Relayer Gift processing using `Transaction.external_ref_id`
@bot_router.business_message()
async def on_relayer_business_gift(message: Message, db: AsyncSession, bot: Bot):
    settings = await get_app_settings(db)
    expected_conn_id = settings.get("relayer_business_connection_id", "")
    msg_conn_id = getattr(message, "business_connection_id", None)
    if expected_conn_id and msg_conn_id and msg_conn_id != expected_conn_id:
        logger.warning(
            "Ignored business_message from unexpected connection_id=%s (expected=%s)",
            msg_conn_id,
            expected_conn_id,
        )
        return

    raw = message.model_dump(exclude_none=True)
    gift_obj = raw.get("unique_gift") or raw.get("gift")
    owned_gift_id = raw.get("owned_gift_id") or (
        gift_obj.get("owned_gift_id") if isinstance(gift_obj, dict) else None
    )
    if not gift_obj and not owned_gift_id:
        return
    sender = message.from_user
    if not sender:
        return

    # Construct deterministic external_ref_id to guarantee idempotency across retries/forwards
    ref_id = str(owned_gift_id) if owned_gift_id else f"bmsg_{msg_conn_id}_{message.message_id}"
    dup_tx = (
        await db.execute(select(Transaction).where(Transaction.external_ref_id == ref_id))
    ).scalar_one_or_none()
    if dup_tx:
        logger.warning("Duplicate Relayer gift skipped: external_ref_id=%s", ref_id)
        return

    user = (
        await db.execute(select(User).where(User.telegram_id == sender.id))
    ).scalar_one_or_none()
    if not user:
        user = User(
            telegram_id=sender.id,
            username=sender.username,
            first_name=sender.first_name or "User",
            balance_stars=0,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    rates = await ensure_default_nft_rates(db)
    gift_title = ""
    if isinstance(gift_obj, dict):
        gift_title = str(
            gift_obj.get("title") or gift_obj.get("name") or gift_obj.get("id") or ""
        )

    matched_rate: Optional[NftRate] = None
    for r in rates:
        if r.is_active and gift_title and (
            r.gift_type.lower() in gift_title.lower()
            or gift_title.lower() in r.gift_type.lower()
        ):
            matched_rate = r
            break

    if not matched_rate:
        logger.warning("Unknown gift from user=%s: title='%s'", sender.id, gift_title)
        try:
            await bot.send_message(
                user.telegram_id,
                "⚠️ <b>Yuborgan NFT sovg'angiz turi avtomatik ro'yxatdan topilmadi.</b>\n"
                "So'rovingiz adminga yuborildi — admin tekshirib balansingizga qo'shadi.",
            )
        except Exception:
            pass
        await notify_all_admins_http(
            f"⚠️ <b>Noma'lum Relayer Gift keldi!</b>\n"
            f"👤 Kimdan: {user.first_name} (@{user.username}, ID: <code>{user.telegram_id}</code>)\n"
            f"🎁 Gift nomi/ID: <code>{gift_title or ref_id}</code>\n"
            f"Admin paneldan foydalanuvchi balansini tekshirib qo'shing."
        )
        return

    stars_credit = matched_rate.deposit_stars
    await adjust_user_balance(
        db,
        user,
        stars_credit,
        TransactionType.DEPOSIT.value,
        {
            "source": "paxtagram_relayer",
            "gift": matched_rate.gift_type,
            "owned_gift_id": ref_id,
        },
        external_ref_id=ref_id,
    )
    await db.commit()
    await db.refresh(user)
    try:
        await bot.send_message(
            user.telegram_id,
            f"🎉 <b>Relayer orqali yuborgan {matched_rate.gift_type} sovg'angiz qabul qilindi!</b>\n"
            f"➕ Balansga qo'shildi: <b>+{stars_credit} ⭐</b>\n"
            f"💰 Joriy balans: <b>{user.balance_stars} ⭐</b>",
        )
    except Exception:
        pass


@bot_router.message(F.text == "💎 NFT depozit")
async def msg_deposit_nft(message: Message, db: AsyncSession):
    rates = await ensure_default_nft_rates(db)
    settings = await get_app_settings(db)
    relayer_user = settings.get("relayer_username", RELAYER_USERNAME)
    buttons = [
        [
            InlineKeyboardButton(
                text=f"🎁 {r.gift_type} → +{r.deposit_stars} ⭐",
                callback_data=f"dep_req:{r.id}",
            )
        ]
        for r in rates
        if r.is_active
    ]
    buttons.append(
        [
            InlineKeyboardButton(
                text="✍️ Relayer / Adminga yuborish",
                url=f"https://paxta.online/{relayer_user}",
            )
        ]
    )
    await message.answer(
        f"💎 <b>NFT Gift orqali depozit qilish</b>\n\n"
        f"⚡️ Sovg'ani to'g'ridan-to'g'ri <b>@{relayer_user}</b> akkauntiga yuboring yoki quyidagi tugmani bosing:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@bot_router.callback_query(F.data.startswith("dep_req:"))
async def cb_dep_req(call: CallbackQuery, user: User, db: AsyncSession, bot: Bot):
    rid = int(call.data.split(":")[1])
    r = (await db.execute(select(NftRate).where(NftRate.id == rid))).scalar_one_or_none()
    if not r or not r.is_active:
        await call.answer("Topilmadi", show_alert=True)
        return

    dep = DepositRequest(
        user_id=user.id,
        nft_type=r.gift_type,
        stars_amount=r.deposit_stars,
        status=DepositStatus.PENDING.value,
    )
    db.add(dep)
    await db.commit()
    await db.refresh(dep)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Tasdiqlash (+{dep.stars_amount} ⭐)",
                    callback_data=f"adm_dep_ok:{dep.id}",
                ),
                InlineKeyboardButton(
                    text="❌ Rad etish", callback_data=f"adm_dep_no:{dep.id}"
                ),
            ]
        ]
    )
    for adm_id in ADMIN_TELEGRAM_IDS:
        try:
            await bot.send_message(
                adm_id,
                f"📥 <b>NFT Depozit so'rovi #{dep.id}:</b>\n"
                f"👤 {user.first_name} (@{user.username or 'no_user'}, ID: <code>{user.telegram_id}</code>)\n"
                f"🎁 NFT: <b>{dep.nft_type}</b> (+{dep.stars_amount} ⭐)",
                reply_markup=kb,
            )
        except Exception:
            pass
    await call.answer("✅ So'rov adminga yuborildi!", show_alert=True)


@bot_router.callback_query(F.data.startswith("adm_dep_ok:"))
async def cb_adm_dep_ok(call: CallbackQuery, user: User, db: AsyncSession, bot: Bot):
    if not user.is_admin:
        await call.answer("Faqat adminlar uchun!", show_alert=True)
        return
    dep_id = int(call.data.split(":")[1])

    upd = await db.execute(
        update(DepositRequest)
        .where(
            DepositRequest.id == dep_id,
            DepositRequest.status == DepositStatus.PENDING.value,
        )
        .values(
            status=DepositStatus.APPROVED.value,
            admin_id=user.telegram_id,
            processed_at=datetime.now(timezone.utc),
        )
    )
    if upd.rowcount == 0:
        await call.answer("⚠️ Bu so'rov allaqachon ko'rib chiqilgan!", show_alert=True)
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    dep = (
        await db.execute(
            select(DepositRequest)
            .options(selectinload(DepositRequest.user))
            .where(DepositRequest.id == dep_id)
        )
    ).scalar_one()
    target = dep.user

    await adjust_user_balance(
        db,
        target,
        dep.stars_amount,
        TransactionType.DEPOSIT.value,
        {"deposit_request_id": dep.id, "nft_type": dep.nft_type, "approved_by": user.telegram_id},
        external_ref_id=f"dep_req_{dep.id}",
    )
    await db.commit()
    await db.refresh(target)

    try:
        await call.message.edit_text(
            call.message.text + f"\n\n✅ <b>TASDIQLANDI (+{dep.stars_amount} ⭐)</b>",
            reply_markup=None,
        )
    except Exception:
        pass
    await bot.send_message(
        target.telegram_id,
        f"🎉 <b>NFT Depozitingiz #{dep.id} tasdiqlandi!</b>\n"
        f"➕ Qo'shildi: <b>+{dep.stars_amount} ⭐</b>\n"
        f"💰 Joriy balans: <b>{target.balance_stars} ⭐</b>",
    )
    await call.answer("✅ Tasdiqlandi!")


@bot_router.callback_query(F.data.startswith("adm_dep_no:"))
async def cb_adm_dep_no(call: CallbackQuery, user: User, db: AsyncSession, bot: Bot):
    if not user.is_admin:
        await call.answer("Faqat adminlar uchun!", show_alert=True)
        return
    dep_id = int(call.data.split(":")[1])
    upd = await db.execute(
        update(DepositRequest)
        .where(
            DepositRequest.id == dep_id,
            DepositRequest.status == DepositStatus.PENDING.value,
        )
        .values(
            status=DepositStatus.REJECTED.value,
            admin_id=user.telegram_id,
            processed_at=datetime.now(timezone.utc),
        )
    )
    if upd.rowcount == 0:
        await call.answer("⚠️ Bu so'rov allaqachon ko'rib chiqilgan!", show_alert=True)
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    dep = (
        await db.execute(
            select(DepositRequest)
            .options(selectinload(DepositRequest.user))
            .where(DepositRequest.id == dep_id)
        )
    ).scalar_one()
    await db.commit()

    try:
        await call.message.edit_text(
            call.message.text + "\n\n❌ <b>RAD ETILDI</b>",
            reply_markup=None,
        )
    except Exception:
        pass
    if dep.user:
        await bot.send_message(
            dep.user.telegram_id,
            f"❌ <b>NFT Depozit so'rovingiz #{dep.id} rad etildi.</b>",
        )
    await call.answer("Rad etildi")


@bot_router.message(F.text == "📤 Yechib olish")
async def msg_withdraw(message: Message, user: User, db: AsyncSession):
    rates = await ensure_default_nft_rates(db)
    buttons = [
        [
            InlineKeyboardButton(
                text=f"📤 {r.gift_type} ({r.withdraw_stars} ⭐)",
                callback_data=f"wd_create:{r.id}",
            )
        ]
        for r in rates
        if r.is_active
    ]
    await message.answer(
        f"📤 <b>Stars balansni NFT Gift orqali yechib olish</b>\n"
        f"💰 Balansingiz: <b>{user.balance_stars} ⭐</b>\n"
        f"Quyidagi NFT sovg'alardan birini tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@bot_router.callback_query(F.data.startswith("wd_create:"))
async def cb_wd_create(call: CallbackQuery, user: User, db: AsyncSession, bot: Bot):
    rid = int(call.data.split(":")[1])
    r = (await db.execute(select(NftRate).where(NftRate.id == rid))).scalar_one_or_none()
    if not r:
        return
    try:
        wd = await create_nft_withdrawal_request(db, user, r.gift_type, note="Bot orqali")
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"adm_wd_ok:{wd.id}"),
                    InlineKeyboardButton(text="❌ Rad etish", callback_data=f"adm_wd_no:{wd.id}"),
                ]
            ]
        )
        for adm_id in ADMIN_TELEGRAM_IDS:
            try:
                await bot.send_message(
                    adm_id,
                    f"📤 <b>Yechib olish so'rovi #{wd.id}</b>\n"
                    f"👤 {user.first_name} (@{user.username})\n"
                    f"🎁 NFT: <b>{wd.nft_type}</b> ({wd.stars_amount} ⭐)",
                    reply_markup=kb,
                )
            except Exception:
                pass
        await call.answer("✅ Yechib olish so'rovi yaratildi!", show_alert=True)
    except Exception as e:
        await call.answer(f"⚠️ {getattr(e, 'detail', str(e))}", show_alert=True)


@bot_router.callback_query(F.data.startswith("adm_wd_ok:"))
async def cb_adm_wd_ok(call: CallbackQuery, user: User, db: AsyncSession):
    if not user.is_admin:
        return
    wid = int(call.data.split(":")[1])
    try:
        await process_withdrawal_decision(db, wid, user.telegram_id, approve=True)
        await call.message.edit_text(
            call.message.text + "\n\n✅ <b>TASDIQLANDI</b>", reply_markup=None
        )
    except Exception as e:
        await call.answer(str(getattr(e, "detail", e)), show_alert=True)


@bot_router.callback_query(F.data.startswith("adm_wd_no:"))
async def cb_adm_wd_no(call: CallbackQuery, user: User, db: AsyncSession):
    if not user.is_admin:
        return
    wid = int(call.data.split(":")[1])
    try:
        await process_withdrawal_decision(db, wid, user.telegram_id, approve=False)
        await call.message.edit_text(
            call.message.text + "\n\n❌ <b>RAD ETILDI (Stars qaytarildi)</b>",
            reply_markup=None,
        )
    except Exception as e:
        await call.answer(str(getattr(e, "detail", e)), show_alert=True)


@bot_router.message(F.text == "🛠 Admin Panel")
@bot_router.message(Command("admin"))
async def cmd_admin(message: Message, user: User):
    if not user.is_admin:
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌐 Web Admin Panelni ochish", url=ADMIN_PANEL_URL)]
        ]
    )
    await message.answer(
        f"🛠 <b>Admin Panel:</b> <code>{ADMIN_PANEL_URL}</code>\n"
        f"🔑 Admin ID: <code>{user.telegram_id}</code>",
        reply_markup=kb,
    )


# ====================================================================================
# 9. EMBEDDED LIQUID GLASS WEBAPP (/app) & ADMIN PANEL (/admin)
# ====================================================================================

WEBAPP_HTML = """<!DOCTYPE html>
<html lang="uz" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
  <title>PaxtaGram Stars Auction — Liquid Glass Mini-App</title>
  <script src="https://paxta.online/js/paxtagram-web-app.js"></script>
  <script src="https://cdn.tailwindcss.com"></script>
  <script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
  <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
  <style>
    body {
      background: radial-gradient(circle at 15% 15%, rgba(147, 51, 234, 0.28), transparent 45%),
                  radial-gradient(circle at 85% 25%, rgba(59, 130, 246, 0.24), transparent 45%),
                  radial-gradient(circle at 50% 85%, rgba(236, 72, 153, 0.22), transparent 50%),
                  #05050a;
      min-height: 100vh;
      color: #fff;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Inter", sans-serif;
    }
    .liquid-glass {
      background: rgba(255, 255, 255, 0.05);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid rgba(255, 255, 255, 0.15);
      box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.45), inset 0 1px 1px 0 rgba(255, 255, 255, 0.22);
    }
    .liquid-glass-glow {
      background: linear-gradient(135deg, rgba(147, 51, 234, 0.18), rgba(59, 130, 246, 0.12), rgba(236, 72, 153, 0.18));
      backdrop-filter: blur(24px);
      -webkit-backdrop-filter: blur(24px);
      border: 1px solid rgba(255, 255, 255, 0.24);
      box-shadow: 0 0 28px rgba(168, 85, 247, 0.3), inset 0 1px 1px rgba(255, 255, 255, 0.3);
    }
    .neon-btn {
      background: linear-gradient(90deg, #9333ea 0%, #3b82f6 50%, #ec4899 100%);
      box-shadow: 0 0 20px rgba(236, 72, 153, 0.45);
      transition: transform 0.15s ease;
    }
    .neon-btn:active { transform: scale(0.97); }
  </style>
</head>
<body>
  <div id="root"></div>
  <script type="text/babel">
    const { useState, useEffect } = React;

    const tg = window.PaxtaGram?.WebApp || window.Telegram?.WebApp;
    const initData = tg?.initData || "";

    async function api(path, { method = "GET", body = null } = {}) {
      const res = await fetch(path, {
        method,
        headers: {
          "Content-Type": "application/json",
          "X-Telegram-Init-Data": initData,
          "X-Device-Fingerprint": navigator.userAgent
        },
        body: body ? JSON.stringify(body) : undefined
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Xatolik yuz berdi");
      return data;
    }

    function App() {
      const [tab, setTab] = useState("auction");
      const [user, setUser] = useState(null);
      const [authError, setAuthError] = useState(null);
      const [auctions, setAuctions] = useState([]);
      const [selected, setSelected] = useState(null);
      const [bids, setBids] = useState([]);
      const [bidAmount, setBidAmount] = useState("");
      const [remSec, setRemSec] = useState(0);
      const [msg, setMsg] = useState(null);
      const [txs, setTxs] = useState([]);
      const [rates, setRates] = useState([]);
      const [leaders, setLeaders] = useState([]);
      const [promo, setPromo] = useState("");

      const loadMe = () =>
        api("/api/me")
          .then(u => { setUser(u); setAuthError(null); })
          .catch(err => setAuthError(err.message));

      const loadAuctions = async () => {
        try {
          const list = await api("/api/auctions/active");
          setAuctions(list);
          if (list.length > 0) {
            const cur = selected ? list.find(x => x.id === selected.id) || list[0] : list[0];
            setSelected(cur);
          } else {
            setSelected(null);
          }
        } catch (e) {}
      };

      useEffect(() => {
        tg?.ready?.();
        tg?.expand?.();
        loadMe();
        loadAuctions();
        api("/api/nft-rates").then(setRates).catch(() => {});
        api("/api/leaderboard").then(setLeaders).catch(() => {});
      }, []);

      useEffect(() => {
        if (!selected) return;
        const nextMin = selected.highest_bid === 0 ? selected.start_price : selected.highest_bid + selected.min_step;
        setBidAmount(String(nextMin));
        api(`/api/auctions/${selected.id}/bids`).then(setBids).catch(() => {});

        let ws = null;
        let retryCount = 0;
        let reconnectTimer = null;
        let unmounted = false;

        const connectWS = () => {
          if (unmounted) return;
          const proto = location.protocol === "https:" ? "wss:" : "ws:";
          const qs = `?init_data=${encodeURIComponent(initData)}`;
          ws = new WebSocket(`${proto}//${location.host}/ws/auction/${selected.id}${qs}`);

          ws.onopen = () => { retryCount = 0; };

          ws.onmessage = (ev) => {
            try {
              const d = JSON.parse(ev.data);
              if (d.event === "auction_cancelled" || d.event === "auction_finished") {
                setMsg({ ok: false, text: d.event === "auction_cancelled" ? "♻️ Auksion bekor qilindi va stavkalar qaytarildi." : "🏁 Auksion yakunlandi!" });
                loadAuctions();
                loadMe();
                return;
              }
              if (d.auction) {
                setSelected(d.auction);
                const nm = d.auction.highest_bid === 0 ? d.auction.start_price : d.auction.highest_bid + d.auction.min_step;
                setBidAmount(String(nm));
              }
              if (d.bid) {
                setBids(prev => [d.bid, ...prev.filter(x => x.id !== d.bid.id)].slice(0, 25));
              }
            } catch (err) {}
          };

          ws.onerror = () => { try { ws.close(); } catch {} };

          ws.onclose = () => {
            if (unmounted) return;
            const delay = Math.min(1000 * (2 ** retryCount++), 30000);
            reconnectTimer = setTimeout(connectWS, delay);
          };
        };

        connectWS();
        return () => {
          unmounted = true;
          if (reconnectTimer) clearTimeout(reconnectTimer);
          if (ws) try { ws.close(); } catch {}
        };
      }, [selected?.id]);

      useEffect(() => {
        if (!selected?.ends_at) return;
        const t = setInterval(() => {
          const diff = Math.max(0, Math.floor((new Date(selected.ends_at).getTime() - Date.now()) / 1000));
          setRemSec(diff);
        }, 1000);
        return () => clearInterval(t);
      }, [selected?.ends_at]);

      useEffect(() => {
        if (tab === "balance") {
          api("/api/balance/transactions").then(setTxs).catch(() => {});
        } else if (tab === "leaderboard") {
          api("/api/leaderboard").then(setLeaders).catch(() => {});
        }
      }, [tab]);

      const fmtTime = (sec) => {
        const h = String(Math.floor(sec / 3600)).padStart(2, "0");
        const m = String(Math.floor((sec % 3600) / 60)).padStart(2, "0");
        const s = String(sec % 60).padStart(2, "0");
        return `${h}:${m}:${s}`;
      };

      const placeBid = async (useBal) => {
        setMsg(null);
        try {
          const r = await api(`/api/auctions/${selected.id}/bid`, {
            method: "POST",
            body: { amount: Number(bidAmount), use_balance: useBal }
          });
          if (r.mode === "invoice") {
            if (!r.invoice_link || !tg?.openInvoice) {
              setMsg({ ok: false, text: "Stars Invoice faqat PaxtaGram / Telegram ichida ishlaydi." });
              return;
            }
            tg.openInvoice(r.invoice_link, (st) => {
              if (st === "paid") {
                setMsg({ ok: true, text: "✅ Stars to'lov muvaffaqiyatli qabul qilindi!" });
                loadAuctions();
                loadMe();
              } else if (st === "cancelled") {
                setMsg({ ok: false, text: "ℹ️ To'lov foydalanuvchi tomonidan bekor qilindi." });
              } else {
                setMsg({ ok: false, text: `❌ To'lov amalga oshmadi (${st || "xatolik"}).` });
              }
            });
          } else if (r.auction) {
            setSelected(r.auction);
            loadMe();
            setMsg({ ok: true, text: `✅ Stavka qabul qilindi: ${bidAmount} ⭐` });
          }
        } catch (e) {
          setMsg({ ok: false, text: e.message });
        }
      };

      if (authError) {
        return (
          <div className="max-w-md mx-auto p-6 mt-12 liquid-glass rounded-3xl text-center space-y-3">
            <p className="text-2xl">🔐</p>
            <h2 className="text-base font-bold">Autentifikatsiya talab qilinadi</h2>
            <p className="text-xs text-white/70">{authError}</p>
          </div>
        );
      }

      const winPct = selected ? 100 - (selected.commission_percent ?? 3) : 97;

      return (
        <div className="max-w-md mx-auto px-4 pt-4 pb-28">
          <header className="liquid-glass rounded-2xl px-4 py-3 mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className="w-9 h-9 rounded-xl neon-btn flex items-center justify-center font-black">★</div>
              <div>
                <p className="text-xs font-extrabold tracking-wider">PAXTAGRAM AUCTION</p>
                <p className="text-[10px] text-white/60">Liquid Glass Edition</p>
              </div>
            </div>
            <div className="px-3 py-1 rounded-full bg-amber-400/15 border border-amber-400/40 text-amber-300 text-xs font-black">
              {user?.balance_stars ?? 0} ⭐
            </div>
          </header>

          {tab === "auction" && (
            selected ? (
              <div className="space-y-4">
                <div className="liquid-glass-glow rounded-3xl p-5">
                  <div className="flex items-center justify-between mb-3">
                    <span className="px-3 py-1 rounded-full text-[11px] font-bold bg-fuchsia-500/20 border border-fuchsia-400/40 text-fuchsia-200">
                      ● JONLI AUKSION #{selected.id}
                    </span>
                    <span className={`px-3 py-1 rounded-full text-xs font-mono font-bold border ${remSec <= 60 ? "bg-rose-500/30 border-rose-400 text-rose-200 animate-pulse" : "bg-white/10 border-white/20"}`}>
                      ⏱ {fmtTime(remSec)}
                    </span>
                  </div>

                  {selected.image_url && (
                    <img src={selected.image_url} className="w-full h-44 object-cover rounded-2xl mb-3 border border-white/15" />
                  )}

                  <h1 className="text-lg font-extrabold mb-3">{selected.title}</h1>

                  <div className="grid grid-cols-2 gap-3 mb-4">
                    <div className="liquid-glass rounded-2xl p-3">
                      <p className="text-[11px] text-white/60">Eng yuqori stavka</p>
                      <p className="text-2xl font-black mt-0.5">{selected.highest_bid} ⭐</p>
                      <p className="text-[11px] text-fuchsia-300 truncate">Lider: {selected.winner_name || "Hali yo'q"}</p>
                    </div>
                    <div className="liquid-glass rounded-2xl p-3">
                      <p className="text-[11px] text-white/60">Jami Pool ({winPct}% yutuq)</p>
                      <p className="text-2xl font-black text-amber-300 mt-0.5">{selected.total_pool} ⭐</p>
                      <p className="text-[11px] text-emerald-300">G'olibga: +{Math.round(selected.total_pool * winPct / 100)} ⭐</p>
                    </div>
                  </div>

                  <div className="flex gap-2 mb-2.5">
                    <input
                      type="number"
                      value={bidAmount}
                      onChange={e => setBidAmount(e.target.value)}
                      className="flex-1 liquid-glass rounded-xl px-4 py-3 text-base font-bold focus:outline-none"
                    />
                    <button
                      onClick={() => setBidAmount(String(Number(bidAmount) + selected.min_step))}
                      className="liquid-glass px-3.5 rounded-xl text-xs font-bold text-fuchsia-300"
                    >
                      +{selected.min_step}⭐
                    </button>
                  </div>

                  <div className="grid grid-cols-2 gap-2.5">
                    <button onClick={() => placeBid(true)} className="neon-btn py-3 rounded-xl font-bold text-xs">
                      ⚡ Balansdan ({bidAmount} ⭐)
                    </button>
                    <button onClick={() => placeBid(false)} className="liquid-glass border-amber-400/40 py-3 rounded-xl font-bold text-xs text-amber-300">
                      🔥 Stars Invoice
                    </button>
                  </div>

                  {msg && (
                    <div className={`mt-3 p-2.5 rounded-xl text-xs ${msg.ok ? "bg-emerald-500/20 text-emerald-200" : "bg-rose-500/20 text-rose-200"}`}>
                      {msg.text}
                    </div>
                  )}
                </div>

                <div className="liquid-glass rounded-3xl p-4">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-white/70 mb-2.5">
                    Oxirgi Stavkalar ({bids.length})
                  </h3>
                  <div className="space-y-2 max-h-56 overflow-y-auto">
                    {bids.map((b, i) => (
                      <div key={b.id} className={`flex justify-between items-center px-3.5 py-2 rounded-xl border ${i === 0 ? "bg-purple-500/20 border-fuchsia-400/40" : "bg-white/5 border-white/10"}`}>
                        <span className="text-xs font-bold">{b.username ? `@${b.username}` : b.first_name}</span>
                        <span className="text-xs font-black text-amber-300">{b.amount} ⭐</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <div className="liquid-glass rounded-3xl p-8 text-center">Hozirda faol auksion yo'q</div>
            )
          )}

          {tab === "balance" && (
            <div className="space-y-4">
              <div className="liquid-glass-glow rounded-3xl p-6 text-center">
                <p className="text-xs text-white/60">Joriy Balansingiz</p>
                <p className="text-4xl font-black text-amber-300 my-2">{user?.balance_stars ?? 0} ⭐</p>
                <p className="text-xs text-white/70">Yutuq: +{user?.total_won ?? 0} ⭐ • Tikilgan: {user?.total_bet ?? 0} ⭐</p>
              </div>

              <form onSubmit={async e => {
                e.preventDefault();
                try {
                  const r = await api("/api/promo/redeem", { method: "POST", body: { code: promo } });
                  alert(r.message);
                  setPromo("");
                  loadMe();
                } catch (err) { alert(err.message); }
              }} className="liquid-glass rounded-2xl p-3.5 flex gap-2">
                <input value={promo} onChange={e => setPromo(e.target.value)} placeholder="🎁 Promo kod (PAXTA2026)" className="flex-1 bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs" />
                <button className="neon-btn px-4 py-2 rounded-xl text-xs font-bold">OK</button>
              </form>

              <div className="liquid-glass rounded-3xl p-4 space-y-2">
                <h3 className="text-xs font-bold uppercase text-white/70 mb-2">NFT Orqali Yechib Olish</h3>
                {rates.map(r => (
                  <div key={r.id} className="flex justify-between items-center bg-white/5 rounded-xl px-3 py-2 border border-white/10">
                    <div>
                      <p className="text-xs font-bold">{r.gift_type}</p>
                      <p className="text-[10px] text-white/50">Depozit: +{r.deposit_stars}⭐ • Yechish: {r.withdraw_stars}⭐</p>
                    </div>
                    <button onClick={async () => {
                      try {
                        await api("/api/withdrawals", { method: "POST", body: { nft_type: r.gift_type } });
                        alert("Yechib olish so'rovi yuborildi!");
                        loadMe();
                      } catch (err) { alert(err.message); }
                    }} className="px-3 py-1.5 rounded-lg bg-fuchsia-500/25 border border-fuchsia-400/40 text-xs font-bold">
                      Yechish ({r.withdraw_stars}⭐)
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {tab === "profile" && (
            <div className="space-y-4">
              <div className="liquid-glass-glow rounded-3xl p-5 flex items-center gap-4">
                <div className="w-14 h-14 rounded-2xl neon-btn flex items-center justify-center text-xl font-black">
                  {(user?.first_name || "P")[0]}
                </div>
                <div>
                  <h2 className="text-base font-extrabold">{user?.first_name}</h2>
                  <p className="text-xs text-white/60">@{user?.username || "paxta_user"} • ID: {user?.telegram_id}</p>
                </div>
              </div>
              <div className="liquid-glass rounded-3xl p-4">
                <h3 className="text-xs font-bold uppercase text-white/70 mb-3">NFT Gift Galereyasi</h3>
                <div className="grid grid-cols-2 gap-2.5">
                  {rates.map(r => (
                    <div key={r.id} className="liquid-glass rounded-2xl p-3">
                      <p className="text-xs font-bold">{r.gift_type}</p>
                      <p className="text-[11px] text-amber-300 mt-1">Qiymati: {r.withdraw_stars} ⭐</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {tab === "leaderboard" && (
            <div className="liquid-glass rounded-3xl p-4 space-y-2">
              <h2 className="text-sm font-black text-center mb-3">🏆 TOP-10 G'OLIBLAR</h2>
              {leaders.map(u => (
                <div key={u.id} className="flex justify-between items-center bg-white/5 px-3.5 py-2.5 rounded-xl border border-white/10">
                  <span className="text-xs font-bold">#{u.rank} {u.first_name}</span>
                  <span className="text-xs font-black text-emerald-300">+{u.total_won} ⭐</span>
                </div>
              ))}
            </div>
          )}

          <nav className="fixed bottom-4 left-4 right-4 max-w-md mx-auto liquid-glass rounded-2xl p-2 flex justify-around">
            {[
              ["auction", "🎯 Auksion"],
              ["balance", "💰 Balans"],
              ["profile", "💎 Profil"],
              ["leaderboard", "🏆 Top-10"]
            ].map(([id, label]) => (
              <button
                key={id}
                onClick={() => setTab(id)}
                className={`px-3 py-2 rounded-xl text-xs font-bold transition ${tab === id ? "neon-btn text-white" : "text-white/60"}`}
              >
                {label}
              </button>
            ))}
          </nav>
        </div>
      );
    }

    ReactDOM.createRoot(document.getElementById("root")).render(<App />);
  </script>
</body>
</html>
"""


ADMIN_HTML = """<!DOCTYPE html>
<html lang="uz" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>PaxtaGram Stars Auction — Web Admin Panel</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
  <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
  <style>
    body { background: #070711; color: #fff; font-family: Inter, sans-serif; }
    .glass { background: rgba(255,255,255,0.04); backdrop-filter: blur(16px); border: 1px solid rgba(255,255,255,0.12); }
  </style>
</head>
<body>
  <div id="root"></div>
  <script type="text/babel">
    const { useState, useEffect } = React;

    function AdminApp() {
      const [loggedIn, setLoggedIn] = useState(false);
      const [tgId, setTgId] = useState("");
      const [pass, setPass] = useState("");
      const [sec, setSec] = useState("stats");
      const [stats, setStats] = useState(null);
      const [users, setUsers] = useState([]);
      const [auctions, setAuctions] = useState([]);
      const [wds, setWds] = useState([]);
      const [promos, setPromos] = useState([]);
      const [rates, setRates] = useState([]);
      const [settings, setSettings] = useState({ commission_percent: 3, min_withdraw: 50, min_bid_step: 10 });
      const [newAuc, setNewAuc] = useState({ title: "", start_price: 50, min_step: 10, duration_sec: 3600 });
      const [newPromo, setNewPromo] = useState({ code: "", stars_amount: 100, max_uses: 100 });
      const [newNft, setNewNft] = useState({ gift_type: "", deposit_stars: 50, withdraw_stars: 60 });

      const adminApi = async (path, { method = "GET", body = null } = {}) => {
        const res = await fetch(path, {
          method,
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: body ? JSON.stringify(body) : undefined
        });
        const data = await res.json().catch(() => ({}));
        if (res.status === 401 || res.status === 403) {
          setLoggedIn(false);
          throw new Error(data.detail || "Sessiya muddati tugadi, qayta kiring.");
        }
        if (!res.ok) throw new Error(data.detail || "Admin xatoligi");
        return data;
      };

      const load = async () => {
        try {
          if (sec === "stats") setStats(await adminApi("/api/admin/stats"));
          if (sec === "users") setUsers(await adminApi("/api/admin/users"));
          if (sec === "auctions") setAuctions(await adminApi("/api/admin/auctions"));
          if (sec === "withdrawals") setWds(await adminApi("/api/admin/withdrawals"));
          if (sec === "promo") setPromos(await adminApi("/api/admin/promo"));
          if (sec === "nft") setRates(await adminApi("/api/admin/nft-rates"));
          if (sec === "settings") setSettings(await adminApi("/api/admin/settings"));
          setLoggedIn(true);
        } catch (e) {}
      };

      useEffect(() => { load(); }, [sec]);

      if (!loggedIn) {
        return (
          <div className="min-h-screen flex items-center justify-center p-4">
            <form onSubmit={async e => {
              e.preventDefault();
              try {
                await adminApi("/api/auth/admin-login", {
                  method: "POST",
                  body: { telegram_id: Number(tgId), password: pass }
                });
                setLoggedIn(true);
                load();
              } catch (err) { alert(err.message); }
            }} className="glass rounded-3xl p-8 w-full max-w-sm space-y-4">
              <h1 className="text-xl font-black">🛠 PaxtaGram Admin</h1>
              <p className="text-xs text-white/50">HttpOnly Cookie + Whitelist himoyalangan</p>
              <input type="number" required placeholder="Admin Telegram ID" value={tgId} onChange={e => setTgId(e.target.value)} className="w-full bg-white/5 border border-white/15 rounded-xl px-3.5 py-2.5 text-sm" />
              <input type="password" required placeholder="Admin Parol" value={pass} onChange={e => setPass(e.target.value)} className="w-full bg-white/5 border border-white/15 rounded-xl px-3.5 py-2.5 text-sm" />
              <button className="w-full py-3 rounded-xl font-bold text-sm bg-gradient-to-r from-purple-600 to-pink-600">Kirish</button>
            </form>
          </div>
        );
      }

      return (
        <div className="min-h-screen flex flex-col md:flex-row">
          <aside className="w-full md:w-60 glass p-5 space-y-2">
            <h2 className="font-black text-base mb-4">★ PAXTA ADMIN</h2>
            {[
              ["stats", "📊 Statistika"],
              ["auctions", "🎯 Auksionlar"],
              ["users", "👥 Foydalanuvchilar"],
              ["withdrawals", "📤 Yechib olishlar"],
              ["promo", "🎁 Promo kodlar"],
              ["nft", "💎 NFT Kurslar"],
              ["settings", "⚙️ Sozlamalar"]
            ].map(([id, label]) => (
              <button key={id} onClick={() => setSec(id)} className={`w-full text-left px-3.5 py-2 rounded-xl text-xs font-bold block ${sec === id ? "bg-purple-600 text-white" : "text-white/60 hover:bg-white/5"}`}>
                {label}
              </button>
            ))}
            <button onClick={async () => {
              await adminApi("/api/auth/admin-logout", { method: "POST" }).catch(() => {});
              setLoggedIn(false);
            }} className="text-xs text-rose-400 pt-6 block">Chiqish</button>
          </aside>

          <main className="flex-1 p-6 space-y-6">
            {sec === "stats" && stats && (
              <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div className="glass p-4 rounded-2xl"><p className="text-xs text-white/60">Foydalanuvchilar</p><p className="text-2xl font-black">{stats.users_count}</p></div>
                <div className="glass p-4 rounded-2xl"><p className="text-xs text-white/60">Jami Oborot</p><p className="text-2xl font-black text-amber-300">{stats.total_turnover} ⭐</p></div>
                <div className="glass p-4 rounded-2xl"><p className="text-xs text-white/60">Komissiya Foydasi</p><p className="text-2xl font-black text-emerald-300">{stats.total_commission} ⭐</p></div>
                <div className="glass p-4 rounded-2xl"><p className="text-xs text-white/60">Faol Auksionlar</p><p className="text-2xl font-black text-fuchsia-300">{stats.active_auctions}</p></div>
              </div>
            )}

            {sec === "auctions" && (
              <div className="space-y-4">
                <form onSubmit={async e => {
                  e.preventDefault();
                  await adminApi("/api/admin/auctions", { method: "POST", body: { ...newAuc, start_price: Number(newAuc.start_price), min_step: Number(newAuc.min_step), duration_sec: Number(newAuc.duration_sec), start_immediately: true } });
                  setNewAuc({ title: "", start_price: 50, min_step: 10, duration_sec: 3600 });
                  load();
                }} className="glass p-4 rounded-2xl flex flex-wrap gap-2">
                  <input required placeholder="Auksion nomi" value={newAuc.title} onChange={e => setNewAuc({ ...newAuc, title: e.target.value })} className="bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs flex-1" />
                  <input type="number" placeholder="Start" value={newAuc.start_price} onChange={e => setNewAuc({ ...newAuc, start_price: e.target.value })} className="bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs w-24" />
                  <input type="number" placeholder="Qadam" value={newAuc.min_step} onChange={e => setNewAuc({ ...newAuc, min_step: e.target.value })} className="bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs w-24" />
                  <button className="bg-purple-600 px-4 py-2 rounded-xl text-xs font-bold">+ Boshlash</button>
                </form>
                {auctions.map(a => (
                  <div key={a.id} className="glass p-4 rounded-2xl flex justify-between items-center">
                    <div>
                      <b className="text-sm">#{a.id} {a.title} ({a.status})</b>
                      <p className="text-xs text-white/60">Pool: {a.total_pool} ⭐ • Top: {a.highest_bid} ⭐</p>
                    </div>
                    {a.status === "active" && (
                      <button onClick={async () => {
                        await adminApi(`/api/admin/auctions/${a.id}`, { method: "PATCH", body: { status: "cancelled" } });
                        load();
                      }} className="px-3 py-1.5 rounded-xl bg-rose-500/20 border border-rose-400 text-xs text-rose-200">Bekor qilish (Refund)</button>
                    )}
                  </div>
                ))}
              </div>
            )}

            {sec === "users" && (
              <div className="space-y-2">
                {users.map(u => (
                  <div key={u.id} className="glass p-3.5 rounded-2xl flex flex-wrap justify-between items-center gap-2">
                    <div>
                      <b className="text-sm">{u.first_name} (@{u.username || "-"})</b>
                      <p className="text-xs text-amber-300">Balans: {u.balance_stars} ⭐ (TG ID: {u.telegram_id})</p>
                    </div>
                    <div className="flex gap-2">
                      <button onClick={async () => {
                        const amt = prompt("Stars qo'shish/ayirish (masalan 100 yoki -50):");
                        if (!amt) return;
                        const note = prompt("Izoh:", "Admin panel") || "Admin panel";
                        await adminApi(`/api/admin/users/${u.id}/balance`, { method: "POST", body: { amount: Number(amt), note } });
                        load();
                      }} className="px-3 py-1.5 rounded-xl bg-amber-500/20 border border-amber-400 text-xs font-bold">+/- Balans</button>
                      <button onClick={async () => {
                        await adminApi(`/api/admin/users/${u.id}`, { method: "PATCH", body: { is_banned: !u.is_banned } });
                        load();
                      }} className={`px-3 py-1.5 rounded-xl text-xs font-bold border ${u.is_banned ? "bg-emerald-500/20 border-emerald-400 text-emerald-200" : "bg-rose-500/20 border-rose-400 text-rose-200"}`}>
                        {u.is_banned ? "✅ Unban" : "⛔ Ban"}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {sec === "withdrawals" && (
              <div className="space-y-2">
                {wds.map(w => (
                  <div key={w.id} className="glass p-3.5 rounded-2xl flex justify-between items-center">
                    <div>
                      <b className="text-sm">#{w.id} {w.nft_type} ({w.stars_amount} ⭐)</b>
                      <p className="text-xs text-white/60">
                        User #{w.user_id} (@{w.username || "no_user"}, TG ID: {w.telegram_id || "-"}) • Status: <b>{w.status}</b>
                      </p>
                    </div>
                    {w.status === "pending" && (
                      <div className="flex gap-2">
                        <button onClick={async () => { await adminApi(`/api/admin/withdrawals/${w.id}/approve`, { method: "POST" }); load(); }} className="px-3 py-1 rounded-lg bg-emerald-600 text-xs font-bold">✅ Tasdiqlash</button>
                        <button onClick={async () => { await adminApi(`/api/admin/withdrawals/${w.id}/reject`, { method: "POST" }); load(); }} className="px-3 py-1 rounded-lg bg-rose-600 text-xs font-bold">❌ Rad etish</button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {sec === "promo" && (
              <div className="space-y-4">
                <form onSubmit={async e => {
                  e.preventDefault();
                  await adminApi("/api/admin/promo", { method: "POST", body: { code: newPromo.code, stars_amount: Number(newPromo.stars_amount), max_uses: Number(newPromo.max_uses), only_new_users: false } });
                  setNewPromo({ code: "", stars_amount: 100, max_uses: 100 });
                  load();
                }} className="glass p-4 rounded-2xl flex flex-wrap gap-2">
                  <input required placeholder="KOD" value={newPromo.code} onChange={e => setNewPromo({ ...newPromo, code: e.target.value })} className="bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs" />
                  <input type="number" placeholder="Stars" value={newPromo.stars_amount} onChange={e => setNewPromo({ ...newPromo, stars_amount: e.target.value })} className="bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs w-28" />
                  <input type="number" placeholder="Max uses" value={newPromo.max_uses} onChange={e => setNewPromo({ ...newPromo, max_uses: e.target.value })} className="bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs w-28" />
                  <button className="bg-purple-600 px-4 py-2 rounded-xl text-xs font-bold">+ Yaratish</button>
                </form>
                {promos.map(p => (
                  <div key={p.id} className="glass p-3.5 rounded-2xl flex justify-between items-center">
                    <div>
                      <b>{p.code} (+{p.stars_amount} ⭐)</b>
                      <p className="text-xs text-white/60">Ishlatildi: {p.used_count} / {p.max_uses}</p>
                    </div>
                    <button onClick={async () => {
                      const newAmt = prompt("Yangi Stars miqdori:", p.stars_amount);
                      const newMax = prompt("Yangi limit (max_uses):", p.max_uses);
                      if (!newAmt || !newMax) return;
                      await adminApi(`/api/admin/promo/${p.id}`, {
                        method: "PATCH",
                        body: { stars_amount: Number(newAmt), max_uses: Number(newMax) }
                      });
                      load();
                    }} className="px-3 py-1.5 rounded-xl bg-white/10 border border-white/20 text-xs font-bold">
                      ✏️ Tahrirlash
                    </button>
                  </div>
                ))}
              </div>
            )}

            {sec === "nft" && (
              <div className="space-y-4">
                <form onSubmit={async e => {
                  e.preventDefault();
                  await adminApi("/api/admin/nft-rates", {
                    method: "POST",
                    body: {
                      gift_type: newNft.gift_type,
                      deposit_stars: Number(newNft.deposit_stars),
                      withdraw_stars: Number(newNft.withdraw_stars),
                      is_active: true
                    }
                  });
                  setNewNft({ gift_type: "", deposit_stars: 50, withdraw_stars: 60 });
                  load();
                }} className="glass p-4 rounded-2xl flex flex-wrap gap-2">
                  <input required placeholder="NFT nomi (masalan: Rocket 🚀)" value={newNft.gift_type} onChange={e => setNewNft({ ...newNft, gift_type: e.target.value })} className="bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs flex-1" />
                  <input type="number" required placeholder="Depozit ⭐" value={newNft.deposit_stars} onChange={e => setNewNft({ ...newNft, deposit_stars: e.target.value })} className="bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs w-28" />
                  <input type="number" required placeholder="Yechish ⭐" value={newNft.withdraw_stars} onChange={e => setNewNft({ ...newNft, withdraw_stars: e.target.value })} className="bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs w-28" />
                  <button className="bg-purple-600 px-4 py-2 rounded-xl text-xs font-bold">+ Qo'shish / Yangilash</button>
                </form>

                {rates.map(r => (
                  <div key={r.id} className="glass p-3.5 rounded-2xl flex flex-wrap justify-between items-center gap-2">
                    <div>
                      <b className="text-sm">{r.gift_type}</b>
                      <p className="text-xs text-white/60">Depozit: <b>+{r.deposit_stars} ⭐</b> • Yechish: <b>{r.withdraw_stars} ⭐</b></p>
                    </div>
                    <div className="flex gap-2">
                      <button onClick={async () => {
                        const dep = prompt(`${r.gift_type} — yangi Depozit kursi (⭐):`, r.deposit_stars);
                        const wd = prompt(`${r.gift_type} — yangi Yechish kursi (⭐):`, r.withdraw_stars);
                        if (!dep || !wd) return;
                        await adminApi(`/api/admin/nft-rates/${r.id}`, {
                          method: "PATCH",
                          body: {
                            gift_type: r.gift_type,
                            deposit_stars: Number(dep),
                            withdraw_stars: Number(wd),
                            is_active: r.is_active
                          }
                        });
                        load();
                      }} className="px-3 py-1.5 rounded-xl bg-amber-500/20 border border-amber-400/40 text-amber-200 text-xs font-bold">
                        ✏️ Tahrirlash
                      </button>
                      <button onClick={async () => {
                        await adminApi(`/api/admin/nft-rates/${r.id}`, {
                          method: "PATCH",
                          body: {
                            gift_type: r.gift_type,
                            deposit_stars: r.deposit_stars,
                            withdraw_stars: r.withdraw_stars,
                            is_active: !r.is_active
                          }
                        });
                        load();
                      }} className={`px-3 py-1.5 rounded-xl text-xs font-bold border ${r.is_active ? "bg-emerald-500/20 border-emerald-400 text-emerald-200" : "bg-white/10 border-white/20 text-white/50"}`}>
                        {r.is_active ? "Faol" : "O'chirilgan"}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {sec === "settings" && (
              <form onSubmit={async e => {
                e.preventDefault();
                await adminApi("/api/admin/settings", { method: "PATCH", body: { commission_percent: Number(settings.commission_percent), min_withdraw: Number(settings.min_withdraw), min_bid_step: Number(settings.min_bid_step) } });
                alert("Saqlandi!");
              }} className="glass p-5 rounded-2xl max-w-md space-y-3">
                <div><label className="text-xs text-white/60">Komissiya (%)</label><input type="number" value={settings.commission_percent} onChange={e => setSettings({ ...settings, commission_percent: e.target.value })} className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-sm mt-1" /></div>
                <div><label className="text-xs text-white/60">Minimal Yechib Olish (⭐)</label><input type="number" value={settings.min_withdraw} onChange={e => setSettings({ ...settings, min_withdraw: e.target.value })} className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-sm mt-1" /></div>
                <button className="w-full py-2.5 rounded-xl bg-purple-600 font-bold text-xs">Saqlash</button>
              </form>
            )}
          </main>
        </div>
      );
    }

    ReactDOM.createRoot(document.getElementById("root")).render(<AdminApp />);
  </script>
</body>
</html>
"""


# ====================================================================================
# 10. FASTAPI ILOVASI, CORS FIX #5, HEALTH CHECK VA BOT POLLING SUPERVISION
# ====================================================================================

scheduler = AsyncIOScheduler()
bot_polling_task: Optional[asyncio.Task] = None


async def scheduled_finish_auctions() -> None:
    async with AsyncSessionLocal() as db:
        await finish_expired_auctions(db)


async def run_paxtagram_bot_polling() -> None:
    if DEMO_MODE or not BOT_TOKEN or "DEMO_TOKEN" in BOT_TOKEN or "your_" in BOT_TOKEN:
        logger.warning("BOT_TOKEN demo rejimida: faqat FastAPI va WebApp ishlamoqda.")
        return

    api_server = TelegramAPIServer.from_base(API_BASE)
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(DbAndAuthMiddleware())
    dp.include_router(bot_router)

    while True:
        session = AiohttpSession(api=api_server)
        bot = Bot(
            token=BOT_TOKEN,
            session=session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            logger.info("PaxtaGram Bot polling boshlandi (API_BASE=%s)...", API_BASE)
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot, handle_signals=False)
        except asyncio.CancelledError:
            await bot.session.close()
            raise
        except Exception as exc:
            logger.exception("Bot polling uzildi (%s). 10 soniyadan so'ng qayta ulanadi...", exc)
            await asyncio.sleep(10)
        finally:
            try:
                await bot.session.close()
            except Exception:
                pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_polling_task
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        await get_app_settings(db)
        await ensure_default_nft_rates(db)
        await ensure_demo_auction_and_promo(db)

    scheduler.add_job(
        scheduled_finish_auctions,
        "interval",
        seconds=5,
        id="finish_expired_auctions",
        replace_existing=True,
    )
    scheduler.start()
    bot_polling_task = asyncio.create_task(run_paxtagram_bot_polling())
    yield
    scheduler.shutdown(wait=False)
    if bot_polling_task:
        bot_polling_task.cancel()


app = FastAPI(title="PaxtaGram Stars Auction v4.0", version="4.0.0", lifespan=lifespan)

# Fix #5 (v4.0): Spec-compliant CORS setup (never combine allow_origins=["*"] with allow_credentials=True)
ALLOWED_ORIGINS = (
    [PUBLIC_URL, f"https://{PUBLIC_URL.split('://')[-1]}"]
    if PUBLIC_URL
    else []
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS or ["*"],
    allow_credentials=bool(ALLOWED_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
@app.get("/api/health", tags=["health"])
async def health_check():
    return {"ok": True, "version": "4.0.0", "demo_mode": DEMO_MODE}


@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url="/app")


@app.get("/app", response_class=HTMLResponse, include_in_schema=False)
@app.get("/app/", response_class=HTMLResponse, include_in_schema=False)
async def serve_webapp():
    return HTMLResponse(content=WEBAPP_HTML)


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
@app.get("/admin/", response_class=HTMLResponse, include_in_schema=False)
async def serve_admin():
    return HTMLResponse(content=ADMIN_HTML)


# --- Public / WebApp API ---
@app.get("/api/me", response_model=UserOut)
async def api_get_me(user: User = Depends(get_current_webapp_user)):
    return user


@app.get("/api/auctions/active", response_model=List[AuctionOut])
async def api_active_auctions(db: AsyncSession = Depends(get_db)):
    settings = await get_app_settings(db)
    comm_pct = int(settings.get("commission_percent", 3))
    stmt = (
        select(Auction)
        .options(selectinload(Auction.winner))
        .where(Auction.status == AuctionStatus.ACTIVE.value)
        .order_by(Auction.ends_at.asc())
    )
    auctions = (await db.execute(stmt)).scalars().all()
    return [await serialize_auction(db, a, commission_percent=comm_pct) for a in auctions]


@app.get("/api/auctions/{auction_id}/bids", response_model=List[BidOut])
async def api_auction_bids(auction_id: int, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Bid)
        .options(selectinload(Bid.user))
        .where(Bid.auction_id == auction_id)
        .order_by(desc(Bid.amount), desc(Bid.created_at))
        .limit(30)
    )
    bids = (await db.execute(stmt)).scalars().all()
    return [
        BidOut(
            id=b.id,
            auction_id=b.auction_id,
            user_id=b.user_id,
            username=b.user.username if b.user else None,
            first_name=b.user.first_name if b.user else "User",
            amount=b.amount,
            created_at=ensure_utc(b.created_at),
        )
        for b in bids
    ]


@app.post("/api/auctions/{auction_id}/bid")
async def api_make_bid(
    auction_id: int,
    payload: BidRequest,
    user: User = Depends(get_current_webapp_user),
    db: AsyncSession = Depends(get_db),
):
    if payload.use_balance:
        serialized = await place_bid(db, auction_id, user, payload.amount, use_balance=True)
        return {"mode": "balance", "auction": serialized, "new_balance": user.balance_stars}

    if DEMO_MODE:
        raise HTTPException(
            status_code=400,
            detail="Demo rejimida Stars Invoice ishlamaydi. 'Balansdan' tugmasidan foydalaning.",
        )

    a = (await db.execute(select(Auction).where(Auction.id == auction_id))).scalar_one_or_none()
    if not a or a.status != AuctionStatus.ACTIVE.value:
        raise HTTPException(status_code=400, detail="Auksion faol emas")

    max_stmt = select(func.max(Bid.amount)).where(Bid.auction_id == auction_id)
    current_highest = (await db.execute(max_stmt)).scalar() or 0
    required_min = a.start_price if current_highest == 0 else current_highest + a.min_step
    if payload.amount < required_min:
        raise HTTPException(status_code=400, detail=f"Minimal stavka: {required_min} ⭐")

    inv_payload = build_safe_bid_payload(a.id, user.telegram_id, payload.amount)
    inv_link = await create_stars_invoice_link(
        title=f"Stavka: {a.title}",
        description=f"{a.title} auksionida {payload.amount} Stars stavka",
        payload=inv_payload,
        amount_stars=payload.amount,
    )
    return {"mode": "invoice", "invoice_link": inv_link, "payload": inv_payload}


@app.get("/api/balance/transactions", response_model=List[TransactionOut])
async def api_my_transactions(
    user: User = Depends(get_current_webapp_user), db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(Transaction)
        .where(Transaction.user_id == user.id)
        .order_by(desc(Transaction.created_at))
        .limit(50)
    )
    return list((await db.execute(stmt)).scalars().all())


@app.post("/api/promo/redeem")
async def api_redeem_promo(
    payload: PromoRedeemRequest,
    user: User = Depends(get_current_webapp_user),
    db: AsyncSession = Depends(get_db),
):
    added = await redeem_promo_code(db, user, payload.code)
    return {
        "ok": True,
        "added_stars": added,
        "balance_stars": user.balance_stars,
        "message": f"Tabriklaymiz! Hisobingizga +{added} ⭐ qo'shildi.",
    }


@app.get("/api/nft-rates", response_model=List[NftRateOut])
async def api_public_nft_rates(db: AsyncSession = Depends(get_db)):
    rates = await ensure_default_nft_rates(db)
    return [r for r in rates if r.is_active]


@app.post("/api/withdrawals", response_model=WithdrawalOut)
async def api_request_withdrawal(
    payload: WithdrawalCreateRequest,
    user: User = Depends(get_current_webapp_user),
    db: AsyncSession = Depends(get_db),
):
    w = await create_nft_withdrawal_request(db, user, payload.nft_type, payload.note)
    return WithdrawalOut(
        id=w.id,
        user_id=w.user_id,
        telegram_id=user.telegram_id,
        username=user.username,
        stars_amount=w.stars_amount,
        nft_type=w.nft_type,
        status=w.status,
        admin_id=w.admin_id,
        note=w.note,
        created_at=ensure_utc(w.created_at),
        processed_at=ensure_utc(w.processed_at),
    )


@app.get("/api/leaderboard")
async def api_leaderboard(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(User)
        .where(User.is_banned.is_(False))
        .order_by(desc(User.total_won), desc(User.total_bet))
        .limit(10)
    )
    users = (await db.execute(stmt)).scalars().all()
    return [
        {
            "rank": idx + 1,
            "id": u.id,
            "telegram_id": u.telegram_id,
            "username": u.username,
            "first_name": u.first_name,
            "total_won": u.total_won,
            "total_bet": u.total_bet,
        }
        for idx, u in enumerate(users)
    ]


# --- Admin API ---
@app.post("/api/auth/admin-login")
async def api_admin_login(
    payload: AdminLoginRequest, response: Response, db: AsyncSession = Depends(get_db)
):
    if payload.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Parol noto'g'ri.")
    if payload.telegram_id not in ADMIN_TELEGRAM_IDS:
        stmt = select(User).where(
            User.telegram_id == payload.telegram_id, User.is_admin.is_(True)
        )
        if not (await db.execute(stmt)).scalar_one_or_none():
            raise HTTPException(status_code=403, detail="Admin whitelistda yo'q.")
    token = create_admin_jwt(payload.telegram_id)
    response.set_cookie(
        key="paxta_adm_jwt",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=JWT_EXPIRE_MINUTES * 60,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "telegram_id": payload.telegram_id,
    }


@app.post("/api/auth/admin-logout")
async def api_admin_logout(response: Response):
    response.delete_cookie("paxta_adm_jwt")
    return {"ok": True}


@app.get("/api/admin/users", response_model=List[UserOut])
async def api_admin_users(
    q: Optional[str] = Query(default=None),
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(User).order_by(desc(User.created_at)).limit(200)
    if q and q.strip().isdigit():
        stmt = select(User).where(
            or_(User.telegram_id == int(q.strip()), User.id == int(q.strip()))
        )
    elif q:
        stmt = select(User).where(User.first_name.ilike(f"%{q.strip()}%"))
    return list((await db.execute(stmt)).scalars().all())


@app.patch("/api/admin/users/{user_id}", response_model=UserOut)
async def api_admin_patch_user(
    user_id: int,
    payload: AdminUserPatchRequest,
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="Topilmadi")
    if payload.is_banned is not None:
        u.is_banned = payload.is_banned
    if payload.is_admin is not None:
        u.is_admin = payload.is_admin
    await db.commit()
    await db.refresh(u)
    return u


@app.post("/api/admin/users/{user_id}/balance")
async def api_admin_user_balance(
    user_id: int,
    payload: AdminBalanceAdjustRequest,
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="Topilmadi")
    tx = await adjust_user_balance(
        db,
        u,
        payload.amount,
        TransactionType.ADMIN_ADJUST.value,
        {"admin_id": admin_id, "note": payload.note},
    )
    await db.commit()
    await db.refresh(u)
    return {"user": UserOut.model_validate(u), "transaction_id": tx.id}


@app.get("/api/admin/promo")
async def api_admin_list_promo(
    admin_id: int = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
):
    promos = (
        await db.execute(select(PromoCode).order_by(desc(PromoCode.created_at)))
    ).scalars().all()
    return [
        {
            "id": p.id,
            "code": p.code,
            "stars_amount": p.stars_amount,
            "max_uses": p.max_uses,
            "used_count": p.used_count,
            "only_new_users": p.only_new_users,
        }
        for p in promos
    ]


@app.post("/api/admin/promo")
async def api_admin_create_promo(
    payload: AdminPromoCreateRequest,
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    p = PromoCode(
        code=payload.code.strip().upper(),
        stars_amount=payload.stars_amount,
        max_uses=payload.max_uses,
        expires_at=payload.expires_at,
        only_new_users=payload.only_new_users,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


@app.patch("/api/admin/promo/{promo_id}")
async def api_admin_patch_promo(
    promo_id: int,
    payload: AdminPromoPatchRequest,
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    p = (await db.execute(select(PromoCode).where(PromoCode.id == promo_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Promo kod topilmadi")
    if payload.stars_amount is not None:
        p.stars_amount = payload.stars_amount
    if payload.max_uses is not None:
        p.max_uses = payload.max_uses
    if payload.only_new_users is not None:
        p.only_new_users = payload.only_new_users
    await db.commit()
    await db.refresh(p)
    return p


@app.get("/api/admin/nft-rates", response_model=List[NftRateOut])
async def api_admin_nft_rates(
    admin_id: int = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
):
    return await ensure_default_nft_rates(db)


@app.post("/api/admin/nft-rates", response_model=NftRateOut)
async def api_admin_upsert_nft(
    payload: AdminNftRateRequest,
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    r = (
        await db.execute(select(NftRate).where(NftRate.gift_type == payload.gift_type))
    ).scalar_one_or_none()
    if not r:
        r = NftRate(**payload.model_dump())
        db.add(r)
    else:
        r.deposit_stars = payload.deposit_stars
        r.withdraw_stars = payload.withdraw_stars
        r.is_active = payload.is_active
    await db.commit()
    await db.refresh(r)
    return r


@app.patch("/api/admin/nft-rates/{rate_id}", response_model=NftRateOut)
async def api_admin_patch_nft_rate(
    rate_id: int,
    payload: AdminNftRateRequest,
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    r = (await db.execute(select(NftRate).where(NftRate.id == rate_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="NFT kursi topilmadi")
    r.gift_type = payload.gift_type
    r.deposit_stars = payload.deposit_stars
    r.withdraw_stars = payload.withdraw_stars
    r.is_active = payload.is_active
    await db.commit()
    await db.refresh(r)
    return r


@app.get("/api/admin/auctions", response_model=List[AuctionOut])
async def api_admin_auctions(
    admin_id: int = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
):
    settings = await get_app_settings(db)
    comm_pct = int(settings.get("commission_percent", 3))
    auctions = (
        await db.execute(
            select(Auction)
            .options(selectinload(Auction.winner))
            .order_by(desc(Auction.created_at))
            .limit(100)
        )
    ).scalars().all()
    return [await serialize_auction(db, a, commission_percent=comm_pct) for a in auctions]


@app.post("/api/admin/auctions", response_model=AuctionOut)
async def api_admin_create_auction(
    payload: AdminAuctionCreateRequest,
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    a = Auction(
        title=payload.title,
        image_url=payload.image_url,
        start_price=payload.start_price,
        min_step=payload.min_step,
        duration_sec=payload.duration_sec,
        started_at=now if payload.start_immediately else None,
        ends_at=(now + timedelta(seconds=payload.duration_sec))
        if payload.start_immediately
        else None,
        status=AuctionStatus.ACTIVE.value if payload.start_immediately else AuctionStatus.PENDING.value,
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return await serialize_auction(db, a)


@app.patch("/api/admin/auctions/{auction_id}", response_model=AuctionOut)
async def api_admin_patch_auction(
    auction_id: int,
    payload: AdminAuctionPatchRequest,
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    if payload.status == AuctionStatus.CANCELLED.value:
        cancelled = await cancel_auction_and_refund(db, auction_id)
        return await serialize_auction(db, cancelled)
    a = (
        await db.execute(
            select(Auction).options(selectinload(Auction.winner)).where(Auction.id == auction_id)
        )
    ).scalar_one_or_none()
    if not a:
        raise HTTPException(status_code=404, detail="Topilmadi")
    if payload.title is not None:
        a.title = payload.title
    if payload.min_step is not None:
        a.min_step = payload.min_step
    await db.commit()
    await db.refresh(a)
    return await serialize_auction(db, a)


@app.get("/api/admin/withdrawals", response_model=List[WithdrawalOut])
async def api_admin_withdrawals(
    admin_id: int = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(Withdrawal)
        .options(selectinload(Withdrawal.user))
        .order_by(desc(Withdrawal.created_at))
        .limit(100)
    )
    items = (await db.execute(stmt)).scalars().all()
    return [
        WithdrawalOut(
            id=w.id,
            user_id=w.user_id,
            telegram_id=w.user.telegram_id if w.user else None,
            username=w.user.username if w.user else None,
            stars_amount=w.stars_amount,
            nft_type=w.nft_type,
            status=w.status,
            admin_id=w.admin_id,
            note=w.note,
            created_at=ensure_utc(w.created_at),
            processed_at=ensure_utc(w.processed_at),
        )
        for w in items
    ]


@app.post("/api/admin/withdrawals/{withdrawal_id}/approve", response_model=WithdrawalOut)
async def api_admin_wd_approve(
    withdrawal_id: int,
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    w = await process_withdrawal_decision(db, withdrawal_id, admin_id, approve=True)
    return WithdrawalOut(
        id=w.id,
        user_id=w.user_id,
        telegram_id=w.user.telegram_id if w.user else None,
        username=w.user.username if w.user else None,
        stars_amount=w.stars_amount,
        nft_type=w.nft_type,
        status=w.status,
        admin_id=w.admin_id,
        note=w.note,
        created_at=ensure_utc(w.created_at),
        processed_at=ensure_utc(w.processed_at),
    )


@app.post("/api/admin/withdrawals/{withdrawal_id}/reject", response_model=WithdrawalOut)
async def api_admin_wd_reject(
    withdrawal_id: int,
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    w = await process_withdrawal_decision(db, withdrawal_id, admin_id, approve=False)
    return WithdrawalOut(
        id=w.id,
        user_id=w.user_id,
        telegram_id=w.user.telegram_id if w.user else None,
        username=w.user.username if w.user else None,
        stars_amount=w.stars_amount,
        nft_type=w.nft_type,
        status=w.status,
        admin_id=w.admin_id,
        note=w.note,
        created_at=ensure_utc(w.created_at),
        processed_at=ensure_utc(w.processed_at),
    )


@app.get("/api/admin/stats")
async def api_admin_stats(
    admin_id: int = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
):
    users_count = (await db.execute(select(func.count(User.id)))).scalar() or 0
    total_turnover = (await db.execute(select(func.sum(Auction.total_pool)))).scalar() or 0
    total_commission = (
        await db.execute(select(func.sum(Auction.commission_taken)))
    ).scalar() or 0
    active_auctions = (
        await db.execute(
            select(func.count(Auction.id)).where(Auction.status == AuctionStatus.ACTIVE.value)
        )
    ).scalar() or 0
    return {
        "users_count": users_count,
        "total_turnover": int(total_turnover),
        "total_commission": int(total_commission),
        "active_auctions": active_auctions,
    }


@app.get("/api/admin/settings")
async def api_admin_get_settings(
    admin_id: int = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
):
    return await get_app_settings(db)


@app.patch("/api/admin/settings")
async def api_admin_patch_settings(
    payload: AdminSettingsPatchRequest,
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    return await update_app_settings(db, payload.model_dump(exclude_none=True))


@app.websocket("/ws/auction/{auction_id}")
async def ws_auction_endpoint(
    websocket: WebSocket,
    auction_id: int,
    init_data: Optional[str] = Query(default=None),
):
    if not DEMO_MODE:
        if not init_data:
            await websocket.close(code=4001)
            return
        try:
            verify_telegram_init_data(init_data)
        except HTTPException:
            await websocket.close(code=4001)
            return
    elif init_data and init_data != "demo":
        try:
            verify_telegram_init_data(init_data)
        except HTTPException:
            await websocket.close(code=4001)
            return

    await ws_manager.connect(auction_id, websocket)
    try:
        async with AsyncSessionLocal() as db:
            a = (
                await db.execute(
                    select(Auction)
                    .options(selectinload(Auction.winner))
                    .where(Auction.id == auction_id)
                )
            ).scalar_one_or_none()
            if a:
                await websocket.send_json(
                    {"event": "snapshot", "auction": await serialize_auction(db, a)}
                )
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_json({"event": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(auction_id, websocket)
    except Exception:
        ws_manager.disconnect(auction_id, websocket)


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)