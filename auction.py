#!/usr/bin/env python3
"""
====================================================================================
🌟 PAXTAGRAM STARS AUCTION BOT — PRODUCTION SINGLE-FILE EDITION (v4.3 SECURITY PATCH)
====================================================================================
Yangi qo'shilgan funksiyalar va tuzatishlar (v4.2):
  ✅ 1. STARS ORQALI DEPOZIT QILISH (`+` TUGMASI):
     - Mini-App (`/app`) tepasidagi balans yoniga `+` tugmasi va Balans bo'limiga
       "➕ Balansni to'ldirish (Stars)" oynasi qo'shildi.
     - Botning o'zida ham `➕ Stars depozit` tugmasi qo'shildi — foydalanuvchi
       tayyor summalardan birini (25, 50, 100, 250, 500 ⭐) yoki o'zi xohlagan summani
       kiritib, PaxtaGram Stars Invoice orqali o'z balansini to'ldira oladi.
  ✅ 2. MAJBURIY OBUNA KANALLARI (ADMIN PANEL + BOT + MINI-APP):
     - Admin panelga "📢 Kanallar & Obuna" bo'limi qo'shildi.
     - Majburiy obuna kanallarini qo'shish, yoqish/o'chirish va o'chirib tashlash mumkin.
     - Bot va Mini-App foydalanuvchi obunasini `getChatMember` orqali tekshiradi.
  ✅ 3. AUKSION JONLI KANALI VA DEPOZIT/YECHIB OLISH KANALI:
     - Admin panelning "📢 Kanallar & Obuna" bo'limida:
       a) 🎯 Auksion Jonli Kanali (`auction_channel_id`): Auksion boshlanganda,
          kimdir stavka qilganda (kim qancha tikkani, umumiy bank va tugashiga qancha
          vaqt qolgani) hamda auksion tugaganda kanalga avtomatik xabar yuboradi.
       b) 💸 Depozit va Yechib Olishlar Kanali (`finance_channel_id`): Foydalanuvchi
          Stars yoki NFT orqali depozit qilganda hamda yechib olish so'rovi yuborganda /
          tasdiqlanganda kanalga avtomatik ma'lumot yuboradi.
  ✅ 4. AUKSION 5 DAQIQA CHO'ZILISH CHEGARASI VA DAVOMIYLIKNI TAHRIRLASH:
     - Auksion anti-snipe orqali jami maksimal 5 daqiqa (`max_extension_sec = 300`)
       cho'zilgandan so'ng boshqa cho'zilmaydi.
     - Auksion ochilayotganda ham, faol auksion ketayotganda ham, umumiy sozlamalarda ham
       "Auksion davomiyligi (daqiqa)" va "Maksimal cho'zilish vaqti (daqiqa)" ni
       istalgancha o'zgartirish imkoniyati qo'shildi.
  ✅ 5. RENDER `asyncpg` XATOSI, ADMIN LOGIN VA PAXTAGRAM TOZA MATN (`<b>` siz) SAQLANDI.
====================================================================================
"""

import asyncio
import enum
import hashlib
import hmac
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
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
    delete,
    desc,
    event,
    func,
    or_,
    select,
    text as sql_text,
    update,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload

# ====================================================================================
# 1. KONFIGURATSIYA, AVTOMATIK URL VA BAZA HIMOYASI
# ====================================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("PaxtaStarsAuction")

HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def strip_html(text: str) -> str:
    """PaxtaGram klientida <b>, <code> kabi teglar xom ko'rinib qolmasligi uchun tozalaydi."""
    if not text:
        return ""
    return HTML_TAG_RE.sub("", text)


def format_hms(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds))
    h, r = divmod(total_seconds, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"
API_BASE = os.getenv("API_BASE", "https://paxta.online").rstrip("/")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "StarsAuctionPaxtaBot").strip().lstrip("@")
RELAYER_USERNAME = os.getenv("RELAYER_USERNAME", "PaxtaAuctionRelayer").strip().lstrip("@")
RELAYER_BUSINESS_CONNECTION_ID = os.getenv("RELAYER_BUSINESS_CONNECTION_ID", "").strip()

_PLACEHOLDER_DOMAINS = (
    "sizning-saytingiz",
    "yourdomain",
    "example.com",
    "your-app",
    "your_domain",
    "localhost",
    "127.0.0.1",
)


def _is_valid_public_url(url: str) -> bool:
    if not url:
        return False
    low = url.lower()
    return not any(p in low for p in _PLACEHOLDER_DOMAINS)


def _detect_initial_public_url() -> str:
    env_pub = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
    render_ext = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip().rstrip("/")
    if _is_valid_public_url(env_pub):
        return env_pub
    if _is_valid_public_url(render_ext):
        return render_ext
    if render_host and _is_valid_public_url(render_host):
        return f"https://{render_host}"
    return ""


RUNTIME_PUBLIC_URL = _detect_initial_public_url()


def get_public_url() -> str:
    if RUNTIME_PUBLIC_URL:
        return RUNTIME_PUBLIC_URL
    return "https://paxta.online"


def get_webapp_url(user_tg_id: Optional[int] = None) -> str:
    env_webapp = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
    base = env_webapp if _is_valid_public_url(env_webapp) else f"{get_public_url()}/app"
    if user_tg_id:
        tok = create_user_webapp_jwt(user_tg_id)
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}u_token={tok}"
    return base


def get_admin_panel_url(admin_tg_id: Optional[int] = None) -> str:
    env_admin = os.getenv("ADMIN_PANEL_URL", "").strip().rstrip("/")
    base = env_admin if _is_valid_public_url(env_admin) else f"{get_public_url()}/admin"
    if admin_tg_id:
        tok = create_admin_jwt(admin_tg_id)
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}token={tok}"
    return base


_raw_admin_ids = ",".join(
    [
        os.getenv("ADMIN_TELEGRAM_IDS", ""),
        os.getenv("ADMIN_IDS", ""),
        os.getenv("ADMIN_ID", ""),
        "115",
    ]
)
ADMIN_TELEGRAM_IDS = {
    int(x.strip()) for x in _raw_admin_ids.replace(";", ",").split(",") if x.strip().isdigit()
}

ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD") or "admin123").strip()
JWT_SECRET_KEY = (os.getenv("JWT_SECRET_KEY") or "").strip()
if len(JWT_SECRET_KEY) < 32:
    JWT_SECRET_KEY = (
        JWT_SECRET_KEY + "_paxta_stars_auction_default_secret_key_2026_secure"
    )[:64]

if not BOT_TOKEN or "your_" in BOT_TOKEN:
    DEMO_MODE = True
    BOT_TOKEN = BOT_TOKEN or "000000000:DEMO_TOKEN"
    logger.warning("⚠️ BOT_TOKEN topilmadi — DEMO_MODE=true rejimida ishga tushdi.")

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 1440 * 7  # 7 kun

SQLITE_FALLBACK_URL = "sqlite+aiosqlite:///./stars_auction.db"


def resolve_safe_database_url() -> str:
    raw_url = os.getenv("DATABASE_URL", "").strip()
    if not raw_url:
        return SQLITE_FALLBACK_URL

    if raw_url.startswith("postgres://"):
        raw_url = "postgresql+asyncpg://" + raw_url[len("postgres://") :]
    elif raw_url.startswith("postgresql://"):
        raw_url = "postgresql+asyncpg://" + raw_url[len("postgresql://") :]

    if "asyncpg" in raw_url:
        if importlib.util.find_spec("asyncpg") is None:
            logger.warning("⚠️ 'asyncpg' moduli topilmadi. Avtomatik o'rnatishga urinilmoqda...")
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--quiet", "asyncpg"],
                    check=True,
                    timeout=60,
                )
            except Exception as exc:
                logger.warning("⚠️ 'asyncpg' ni o'rnatib bo'lmadi (%s).", exc)

        if importlib.util.find_spec("asyncpg") is None:
            if importlib.util.find_spec("psycopg") is not None:
                logger.info("✅ 'psycopg' topildi, postgresql+psycopg:// drayveriga o'tilmoqda.")
                return raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
            logger.warning(
                "⚠️ PostgreSQL async drayveri ('asyncpg') mavjud emas! "
                "Server to'xtab qolmasligi uchun avtomatik SQLite bazasiga (%s) o'tildi.",
                SQLITE_FALLBACK_URL,
            )
            return SQLITE_FALLBACK_URL

    if "sqlite" in raw_url and "aiosqlite" not in raw_url:
        raw_url = raw_url.replace("sqlite://", "sqlite+aiosqlite://")

    return raw_url


DATABASE_URL = resolve_safe_database_url()
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

DEFAULT_COMMISSION_PERCENT = int(os.getenv("DEFAULT_COMMISSION_PERCENT", "3"))
DEFAULT_MIN_WITHDRAW = int(os.getenv("DEFAULT_MIN_WITHDRAW", "50"))
DEFAULT_MIN_BID_STEP = int(os.getenv("DEFAULT_MIN_BID_STEP", "10"))
DEFAULT_AUCTION_DURATION_SEC = int(os.getenv("DEFAULT_AUCTION_DURATION_SEC", "3600"))
DEFAULT_MAX_EXTENSION_SEC = int(os.getenv("DEFAULT_MAX_EXTENSION_SEC", "300"))  # 5 daqiqa (300s)
ANTI_SNIPE_WINDOW_SEC = int(os.getenv("ANTI_SNIPE_WINDOW_SEC", "60"))
ANTI_SNIPE_EXTEND_SEC = int(os.getenv("ANTI_SNIPE_EXTEND_SEC", "60"))

TELEGRAM_API_BASE = f"{API_BASE}/bot{BOT_TOKEN}"


# ====================================================================================
# 2. RATE LIMITER VA WEAKVALUE AUCTION LOCKS
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
_auction_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


def get_auction_lock(auction_id: int) -> asyncio.Lock:
    lock = _auction_locks.get(auction_id)
    if lock is None:
        lock = asyncio.Lock()
        _auction_locks[auction_id] = lock
    return lock


# ====================================================================================
# 3. DATABASE ENGINE & MODELS
# ====================================================================================

def _build_engine_and_session(db_url: str):
    c_args = {"timeout": 30} if "sqlite" in db_url else {}
    eng = create_async_engine(
        db_url,
        echo=False,
        pool_pre_ping=True,
        connect_args=c_args,
    )
    if "sqlite" in db_url:
        @event.listens_for(eng.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    sess_maker = async_sessionmaker(
        bind=eng,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return eng, sess_maker


engine, AsyncSessionLocal = _build_engine_and_session(DATABASE_URL)


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
    max_extension_sec: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    extended_sec: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
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


class MandatoryChannel(Base):
    __tablename__ = "mandatory_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False)
    invite_link: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


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
    max_extension_sec: int = 300
    extended_sec: int = 0
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


class StarsDepositCreateRequest(BaseModel):
    amount: int = Field(..., gt=0, le=100000)


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


class MandatoryChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    chat_id: str
    invite_link: str
    is_active: bool
    created_at: datetime


class AdminMandatoryChannelCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=128)
    chat_id: str = Field(..., min_length=2, max_length=128)
    invite_link: str = Field(..., min_length=4, max_length=512)
    is_active: bool = True


class AdminMandatoryChannelPatchRequest(BaseModel):
    title: Optional[str] = None
    chat_id: Optional[str] = None
    invite_link: Optional[str] = None
    is_active: Optional[bool] = None


class AdminLoginRequest(BaseModel):
    telegram_id: int
    password: str = Field(..., min_length=1)


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
    duration_sec: int = Field(default=3600, ge=60)
    max_extension_sec: int = Field(default=300, ge=0)
    start_immediately: bool = True


class AdminAuctionPatchRequest(BaseModel):
    title: Optional[str] = None
    image_url: Optional[str] = None
    min_step: Optional[int] = Field(default=None, gt=0)
    duration_sec: Optional[int] = Field(default=None, ge=60)
    remaining_minutes: Optional[int] = Field(default=None, ge=1)
    max_extension_sec: Optional[int] = Field(default=None, ge=0)
    status: Optional[str] = None


class AdminSettingsPatchRequest(BaseModel):
    commission_percent: Optional[int] = Field(default=None, ge=0, le=50)
    min_withdraw: Optional[int] = Field(default=None, ge=1)
    min_bid_step: Optional[int] = Field(default=None, ge=1)
    default_auction_duration_sec: Optional[int] = Field(default=None, ge=60)
    default_max_extension_sec: Optional[int] = Field(default=None, ge=0)
    auction_channel_id: Optional[str] = None
    finance_channel_id: Optional[str] = None
    maintenance_mode: Optional[bool] = None
    relayer_username: Optional[str] = None
    relayer_business_connection_id: Optional[str] = None


class AdminChannelTestRequest(BaseModel):
    channel_type: str = Field(..., pattern="^(auction|finance)$")


# ====================================================================================
# 5. CORE SERVISLAR, KANALLAR VA AVTORIZATSIYA
# ====================================================================================

security_bearer = HTTPBearer(auto_error=False)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def create_admin_jwt(telegram_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {"sub": str(telegram_id), "role": "admin", "exp": expire}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_user_webapp_jwt(telegram_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {"sub": str(telegram_id), "role": "webapp_user", "exp": expire}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


async def post_telegram_api(
    method: str, payload: Dict[str, Any], retries: int = 3, timeout: float = 10.0
) -> Optional[Dict[str, Any]]:
    if DEMO_MODE or not BOT_TOKEN or "DEMO_TOKEN" in BOT_TOKEN or "your_" in BOT_TOKEN:
        return None
    clean_payload = dict(payload)
    clean_payload.pop("parse_mode", None)
    if "text" in clean_payload and isinstance(clean_payload["text"], str):
        clean_payload["text"] = strip_html(clean_payload["text"])

    url = f"{TELEGRAM_API_BASE}/{method}"
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=clean_payload)
                data = resp.json()
                if resp.status_code < 500:
                    return data
        except Exception as exc:
            logger.warning("API %s urinish %d/%d xatosi: %s", method, attempt + 1, retries, exc)
        if attempt < retries - 1:
            await asyncio.sleep(0.6 * (2 ** attempt))
    return None


def normalize_channel_chat_id(raw_chat_id: str) -> Any:
    val = (raw_chat_id or "").strip()
    if not val:
        return ""
    if val.startswith("https://paxta.online/") or val.startswith("https://t.me/"):
        slug = val.rstrip("/").split("/")[-1].lstrip("@")
        return f"@{slug}"
    if val.lstrip("-").isdigit():
        return int(val)
    if not val.startswith("@"):
        return f"@{val}"
    return val


async def check_user_in_channel(chat_id_str: str, telegram_user_id: int) -> bool:
    if DEMO_MODE or telegram_user_id in ADMIN_TELEGRAM_IDS:
        return True
    norm_chat = normalize_channel_chat_id(chat_id_str)
    if not norm_chat:
        return True
    data = await post_telegram_api(
        "getChatMember",
        {"chat_id": norm_chat, "user_id": telegram_user_id},
        retries=2,
        timeout=6.0,
    )
    if not data or not data.get("ok"):
        # Agar bot kanalda admin bo'lmasa yoki kanal topilmasa foydalanuvchini bloklab qo'ymaslik uchun log yozamiz
        logger.warning("getChatMember xatosi (chat=%s, user=%s): %s", norm_chat, telegram_user_id, data)
        return True
    result = data.get("result", {})
    status_val = result.get("status", "")
    if status_val in ("creator", "administrator", "member"):
        return True
    if status_val == "restricted" and result.get("is_member", False):
        return True
    return False


async def get_unsubscribed_channels(db: AsyncSession, telegram_user_id: int) -> List[MandatoryChannel]:
    if telegram_user_id in ADMIN_TELEGRAM_IDS:
        return []
    stmt = select(MandatoryChannel).where(MandatoryChannel.is_active.is_(True)).order_by(MandatoryChannel.id.asc())
    channels = list((await db.execute(stmt)).scalars().all())
    if not channels:
        return []
    unsubscribed: List[MandatoryChannel] = []
    for ch in channels:
        is_sub = await check_user_in_channel(ch.chat_id, telegram_user_id)
        if not is_sub:
            unsubscribed.append(ch)
    return unsubscribed


async def send_channel_post(
    chat_id_str: str,
    text: str,
    button_text: Optional[str] = None,
    button_url: Optional[str] = None,
) -> bool:
    norm_chat = normalize_channel_chat_id(chat_id_str)
    if not norm_chat:
        return False
    payload: Dict[str, Any] = {
        "chat_id": norm_chat,
        "text": strip_html(text),
    }
    if button_text and button_url:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": button_text, "url": button_url}]]
        }
    res = await post_telegram_api("sendMessage", payload, retries=2, timeout=8.0)
    return bool(res and res.get("ok"))


def get_bot_auction_deeplink() -> str:
    """
    Kanaldagi '🎯 Auksionda qatnashish' tugmasi tashqi brauzerda initData'siz
    ochilib qolmasligi uchun foydalanuvchini botning o'ziga (?start=auction) yo'naltiradi.
    Bot ichida esa foydalanuvchi o'zining haqiqiy kriptografik tokeni bilan Mini-App ni ochadi.
    """
    return f"https://paxta.online/{BOT_USERNAME}?start=auction"


async def broadcast_to_auction_channel(text: str) -> None:
    try:
        async with AsyncSessionLocal() as db:
            settings = await get_app_settings(db)
            ch_id = (settings.get("auction_channel_id") or "").strip()
            if not ch_id:
                return
            await send_channel_post(
                ch_id,
                text,
                button_text="🎯 Auksionda qatnashish",
                button_url=get_bot_auction_deeplink(),
            )
    except Exception as exc:
        logger.warning("Auction channel broadcast error: %s", exc)


async def broadcast_to_finance_channel(text: str) -> None:
    try:
        async with AsyncSessionLocal() as db:
            settings = await get_app_settings(db)
            ch_id = (settings.get("finance_channel_id") or "").strip()
            if not ch_id:
                return
            await send_channel_post(
                ch_id,
                text,
                button_text="🌟 Botga kirish",
                button_url=f"https://paxta.online/{BOT_USERNAME}",
            )
    except Exception as exc:
        logger.warning("Finance channel broadcast error: %s", exc)


def verify_telegram_init_data(init_data: str) -> Dict[str, Any]:
    if not init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="PaxtaGram initData majburiy",
        )
    # Faqat botning o'zi imzolagan JWT token (`u_token`) yoki haqiqiy HMAC-SHA256 `initData` qabul qilinadi.
    # Hech qachon shunchaki ID (`tgid:...`) orqali birovning akkauntiga kirishga ruxsat berilmaydi!
    if init_data.startswith("jwt:"):
        raw_jwt = init_data[4:].strip()
        try:
            payload = jwt.decode(raw_jwt, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            role = payload.get("role")
            tg_id = int(payload.get("sub", "0"))
            if tg_id > 0 and role in ("webapp_user", "admin"):
                return {"id": tg_id, "first_name": f"User #{tg_id}"}
        except (JWTError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Kirish havolasi muddati tugagan yoki yaroqsiz. Botdan qayta kiring.",
            )

    parsed_data = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed_data.pop("hash", None)
    if not received_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="initData ichida hash topilmadi",
        )
    auth_date = int(parsed_data.get("auth_date", "0"))
    if auth_date and abs(time.time() - auth_date) > 86400 * 7:
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
        tg_user = {"id": 115, "username": "paxta_admin", "first_name": "Zokirjonov"}
    elif not x_telegram_init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Telegram-Init-Data majburiy (Ilovani PaxtaGram ichida oching yoki ID kiriting)",
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
        if username:
            user.username = username
        if first_name and first_name != f"User #{telegram_id}":
            user.first_name = first_name
        if telegram_id in ADMIN_TELEGRAM_IDS:
            user.is_admin = True
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


async def is_user_admin_in_db_or_whitelist(db: AsyncSession, telegram_id: int) -> bool:
    if telegram_id in ADMIN_TELEGRAM_IDS:
        return True
    stmt = select(User).where(User.telegram_id == telegram_id, User.is_admin.is_(True))
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def get_current_admin(
    paxta_adm_jwt: Optional[str] = Cookie(default=None),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    db: AsyncSession = Depends(get_db),
) -> int:
    raw_token = (
        (credentials.credentials if credentials else None)
        or x_admin_token
        or paxta_adm_jwt
    )
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

    if not await is_user_admin_in_db_or_whitelist(db, telegram_id):
        raise HTTPException(status_code=403, detail="Admin ID oq ro'yxatda yo'q")
    return telegram_id


async def get_app_settings(db: AsyncSession) -> Dict[str, Any]:
    defaults = {
        "commission_percent": DEFAULT_COMMISSION_PERCENT,
        "min_withdraw": DEFAULT_MIN_WITHDRAW,
        "min_bid_step": DEFAULT_MIN_BID_STEP,
        "default_auction_duration_sec": DEFAULT_AUCTION_DURATION_SEC,
        "default_max_extension_sec": DEFAULT_MAX_EXTENSION_SEC,
        "auction_channel_id": "",
        "finance_channel_id": "",
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


def build_safe_deposit_payload(telegram_id: int, amount: int) -> str:
    short_id = uuid.uuid4().hex[:8]
    payload = f"dep:{telegram_id}:{amount}:{short_id}"
    if len(payload.encode("utf-8")) > 64:
        raise ValueError("Deposit payload 64 baytdan oshib ketdi")
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


async def send_stars_invoice_to_chat(
    telegram_id: int, title: str, description: str, payload: str, amount_stars: int
) -> bool:
    data = await post_telegram_api(
        "sendInvoice",
        {
            "chat_id": telegram_id,
            "title": title,
            "description": description,
            "payload": payload,
            "provider_token": "",
            "currency": "XTR",
            "prices": [{"label": title, "amount": amount_stars}],
        },
    )
    return bool(data and data.get("ok"))


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
        {"chat_id": telegram_id, "text": strip_html(text)},
        retries=2,
        timeout=8.0,
    )


async def notify_all_admins_http(text: str) -> None:
    for adm_id in ADMIN_TELEGRAM_IDS:
        await send_telegram_notification(adm_id, strip_html(text))


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


async def process_stars_deposit_payment(
    db: AsyncSession,
    user: User,
    amount: int,
    charge_id: str,
) -> bool:
    """Foydalanuvchi Stars Invoice orqali balansini to'ldirganda chaqiriladi."""
    dup_stmt = select(Transaction).where(Transaction.external_ref_id == charge_id)
    if (await db.execute(dup_stmt)).scalar_one_or_none():
        return False

    await adjust_user_balance(
        db,
        user,
        amount,
        TransactionType.DEPOSIT.value,
        {"source": "stars_invoice", "charge_id": charge_id},
        external_ref_id=charge_id,
    )
    await db.commit()
    await db.refresh(user)

    user_label = f"@{user.username}" if user.username else user.first_name
    asyncio.create_task(
        broadcast_to_finance_channel(
            f"📥 Yangi Depozit (Stars)!\n\n"
            f"👤 Foydalanuvchi: {user.first_name} ({user_label}, ID: {user.telegram_id})\n"
            f"⭐ Miqdor: +{amount} ⭐\n"
            f"💳 Usul: PaxtaGram Stars Invoice\n"
            f"🕒 Vaqt: {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M UTC')}"
        )
    )
    return True


# ====================================================================================
# 6. AUCTION & WEBSOCKET ENGINE (5 DAQIQA MAX CHO'ZILISH + KANAL BROADCAST)
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
        "max_extension_sec": getattr(auction, "max_extension_sec", 300),
        "extended_sec": getattr(auction, "extended_sec", 0),
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

    unsub = await get_unsubscribed_channels(db, user.telegram_id)
    if unsub:
        ch_names = ", ".join(c.title for c in unsub)
        raise HTTPException(
            status_code=403,
            detail=f"Stavka qilishdan oldin majburiy kanallarga obuna bo'ling: {ch_names}",
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

        # 5 daqiqa (yoki max_extension_sec) cho'zilish chegarasini tekshiramiz:
        timer_extended = False
        added_sec = 0
        new_ends_at = ends_at
        cur_extended = getattr(auction, "extended_sec", 0) or 0
        max_ext = getattr(auction, "max_extension_sec", 300)
        if max_ext is None:
            max_ext = 300

        if ends_at:
            remaining = (ends_at - now).total_seconds()
            if 0 < remaining <= ANTI_SNIPE_WINDOW_SEC and cur_extended < max_ext:
                allowed_add = min(ANTI_SNIPE_EXTEND_SEC, max_ext - cur_extended)
                if allowed_add > 0:
                    new_ends_at = ends_at + timedelta(seconds=allowed_add)
                    added_sec = int(allowed_add)
                    timer_extended = True

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
                extended_sec=cur_extended + added_sec,
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
                "added_sec": added_sec,
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
            "added_sec": added_sec,
        }
        await ws_manager.broadcast(auction.id, ws_payload)

        # Auksion Jonli Kanaliga kim qancha tikkani va qancha vaqt qolganini yuboramiz:
        rem_after = 0
        if new_ends_at:
            rem_after = max(0, int((new_ends_at - datetime.now(timezone.utc)).total_seconds()))
        bidder_label = f"@{user.username}" if user.username else user.first_name
        ext_note = ""
        if timer_extended:
            ext_note = f"\n⚡ Anti-Snipe: Vaqt +{added_sec}s ga uzaytirildi ({auction.extended_sec}/{auction.max_extension_sec}s)"
        elif cur_extended >= max_ext and max_ext > 0:
            ext_note = f"\n🔒 Maksimal cho'zilish chegarasiga ({max_ext // 60} daqiqa) yetgan — vaqt boshqa cho'zilmaydi!"

        asyncio.create_task(
            broadcast_to_auction_channel(
                f"🔥 Auksionda yangi stavka!\n\n"
                f"🎯 Auksion #{auction.id}: {auction.title}\n"
                f"👤 Ishtirokchi: {user.first_name} ({bidder_label})\n"
                f"⭐ Tikilgan stavka: {amount} ⭐\n"
                f"💰 Jami yig'ilgan bank (Pool): {auction.total_pool} ⭐\n"
                f"⏱ Tugashiga qolgan vaqt: {format_hms(rem_after)}"
                f"{ext_note}"
            )
        )
        return serialized


async def _notify_participants_bg(tg_ids: List[int], message_text: str) -> None:
    for tg_id in tg_ids:
        await send_telegram_notification(tg_id, message_text)


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
                f"🏁 Auksion yakunlandi!\n\n"
                f"🎯 Nomi: {fresh.title}\n"
                f"🏆 G'olib: {winner_label}\n"
                f"💰 Jami bank (Pool): {fresh.total_pool} ⭐\n"
                f"🎁 G'olib mukofoti ({winner_percent}%): {payout} ⭐"
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
        asyncio.create_task(broadcast_to_auction_channel(msg))
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
                    f"♻️ {auction.title} auksioni bekor qilindi. Sizning {bid.amount} ⭐ stavkangiz qaytarildi.",
                )
            )

        await db.commit()
        await db.refresh(auction)

    serialized = await serialize_auction(db, auction)
    await ws_manager.broadcast(
        auction.id, {"event": "auction_cancelled", "auction": serialized}
    )
    asyncio.create_task(
        broadcast_to_auction_channel(
            f"♻️ Auksion #{auction.id} ({auction.title}) admin tomonidan bekor qilindi va barcha stavkalar egalariga qaytarildi."
        )
    )
    for tg_id, text in notify_tasks:
        asyncio.create_task(send_telegram_notification(tg_id, text))

    return auction


# ====================================================================================
# 7. PROMO KOD VA NFT KURSLARI SERVISLARI
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
                duration_sec=3600,
                max_extension_sec=300,
                extended_sec=0,
                started_at=now,
                ends_at=now + timedelta(seconds=3600),
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

    user_label = f"@{user.username}" if user.username else user.first_name
    asyncio.create_task(
        broadcast_to_finance_channel(
            f"📤 Yangi Yechib Olish So'rovi #{withdrawal.id}!\n\n"
            f"👤 Foydalanuvchi: {user.first_name} ({user_label}, ID: {user.telegram_id})\n"
            f"🎁 Tanlangan NFT: {withdrawal.nft_type}\n"
            f"⭐ Miqdor: {withdrawal.stars_amount} ⭐\n"
            f"⏳ Holat: Kutilmoqda (Pending)"
        )
    )
    return withdrawal


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

    await db.commit()

    withdrawal = (
        await db.execute(
            select(Withdrawal)
            .options(selectinload(Withdrawal.user))
            .where(Withdrawal.id == withdrawal_id)
        )
    ).scalar_one()
    user = withdrawal.user
    user_label = f"@{user.username}" if user.username else user.first_name

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
                    f"✅ Withdrawal #{withdrawal.id} Relayer orqali avtomatik yuborildi (@{user.username or user.telegram_id})."
                )
            )
        else:
            msg_extra = "⏳ So'rovingiz tasdiqlandi! NFT sovg'a admin tomonidan qo'lda yuboriladi (1-2 soat ichida)."
            asyncio.create_task(
                notify_all_admins_http(
                    f"⚠️ DIQQAT: Withdrawal #{withdrawal.id} tasdiqlandi, lekin Relayer auto-transfer amalga oshmadi!\n"
                    f"👤 Foydalanuvchi: {user.first_name} (@{user.username or 'no_username'}, ID: {user.telegram_id})\n"
                    f"🎁 NFT: {withdrawal.nft_type} — Iltimos, qo'lda yuboring!"
                )
            )

        asyncio.create_task(
            send_telegram_notification(
                user.telegram_id,
                f"✅ Yechib olish so'rovingiz #{withdrawal.id} tasdiqlandi!\n\n"
                f"🎁 NFT: {withdrawal.nft_type} ({withdrawal.stars_amount} ⭐)\n{msg_extra}",
            )
        )
        asyncio.create_task(
            broadcast_to_finance_channel(
                f"✅ Yechib Olish Tasdiqlandi #{withdrawal.id}!\n\n"
                f"👤 Foydalanuvchi: {user.first_name} ({user_label})\n"
                f"🎁 NFT: {withdrawal.nft_type}\n"
                f"⭐ Miqdor: {withdrawal.stars_amount} ⭐"
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
                f"❌ Yechib olish so'rovingiz #{withdrawal.id} rad etildi.\n"
                f"♻️ {withdrawal.stars_amount} ⭐ balansingizga qaytarildi.",
            )
        )
        asyncio.create_task(
            broadcast_to_finance_channel(
                f"❌ Yechib Olish Rad Etildi #{withdrawal.id} (Stars qaytarildi)\n\n"
                f"👤 Foydalanuvchi: {user.first_name} ({user_label})\n"
                f"🎁 NFT: {withdrawal.nft_type} ({withdrawal.stars_amount} ⭐)"
            )
        )

    return withdrawal


# ====================================================================================
# 8. AIOGRAM 3.x BOT HANDLERLARI (STARS DEPOZIT + MAJBURIY OBUNA + TOZA MATN)
# ====================================================================================

bot_router = Router(name="paxta_stars_bot")


class BotStates(StatesGroup):
    waiting_custom_bid = State()
    waiting_custom_deposit = State()
    waiting_promo = State()


def get_mandatory_sub_kb(channels: List[MandatoryChannel]) -> InlineKeyboardMarkup:
    rows = []
    for ch in channels:
        rows.append([InlineKeyboardButton(text=f"📢 {ch.title}", url=ch.invite_link)])
    rows.append([InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class DbAndAuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: Dict[str, Any]) -> Any:
        async with AsyncSessionLocal() as db:
            data["db"] = db
            tg_user = data.get("event_from_user")
            if tg_user:
                if not rate_limiter.check_cooldown(f"flood:{tg_user.id}", 0.3):
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

                # To'lov (pre_checkout_query, successful_payment) va /admin ni majburiy obuna to'smaydi
                is_payment_event = isinstance(event, PreCheckoutQuery) or (
                    isinstance(event, Message) and event.successful_payment is not None
                )
                is_check_sub_cb = isinstance(event, CallbackQuery) and event.data == "check_sub"
                is_admin_cmd = isinstance(event, Message) and (
                    (event.text or "").startswith("/admin") or event.text == "🛠 Admin Panel"
                )

                if not user.is_admin and not is_payment_event and not is_check_sub_cb and not is_admin_cmd:
                    unsub = await get_unsubscribed_channels(db, user.telegram_id)
                    if unsub:
                        text = (
                            "🔒 Botdan foydalanish uchun quyidagi rasmiy kanallarga obuna bo'ling "
                            "va '✅ Obunani tekshirish' tugmasini bosing:"
                        )
                        kb = get_mandatory_sub_kb(unsub)
                        if isinstance(event, Message):
                            await event.answer(text, reply_markup=kb)
                        elif isinstance(event, CallbackQuery):
                            await event.answer("⚠️ Avval kanallarga obuna bo'ling!", show_alert=True)
                            try:
                                await event.message.answer(text, reply_markup=kb)
                            except Exception:
                                pass
                        return None

            return await handler(event, data)


def get_reply_kb(is_admin: bool = False, user_tg_id: Optional[int] = None) -> ReplyKeyboardMarkup:
    rows = [
        [
            KeyboardButton(text="🎯 Auksion"),
            KeyboardButton(text="💰 Balans"),
            KeyboardButton(text="➕ Stars depozit"),
        ],
        [
            KeyboardButton(text="🎁 Promo kod"),
            KeyboardButton(text="💎 NFT depozit"),
            KeyboardButton(text="📤 Yechib olish"),
        ],
        [
            KeyboardButton(
                text="🌐 Web App",
                web_app=WebAppInfo(url=get_webapp_url(user_tg_id)),
            ),
        ],
    ]
    if is_admin:
        rows[-1].append(KeyboardButton(text="🛠 Admin Panel"))
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def get_stars_deposit_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ +25 Stars", callback_data="dep_stars:25"),
                InlineKeyboardButton(text="⭐ +50 Stars", callback_data="dep_stars:50"),
                InlineKeyboardButton(text="⭐ +100 Stars", callback_data="dep_stars:100"),
            ],
            [
                InlineKeyboardButton(text="⭐ +250 Stars", callback_data="dep_stars:250"),
                InlineKeyboardButton(text="⭐ +500 Stars", callback_data="dep_stars:500"),
                InlineKeyboardButton(text="⭐ +1000 Stars", callback_data="dep_stars:1000"),
            ],
            [
                InlineKeyboardButton(
                    text="✍️ Boshqa miqdor kiritish", callback_data="dep_stars_custom"
                )
            ],
        ]
    )


def get_auction_kb(
    auction_id: int, next_min: int, user_tg_id: Optional[int] = None
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💰 Balansdan stavka ({next_min} ⭐)",
                    callback_data=f"bid_bal:{auction_id}:{next_min}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"⭐ Stars Invoice ({next_min} ⭐)",
                    callback_data=f"bid_inv:{auction_id}:{next_min}",
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
                    web_app=WebAppInfo(url=get_webapp_url(user_tg_id)),
                )
            ],
        ]
    )


def format_auction_card(d: Dict[str, Any]) -> str:
    rem_str = "00:00:00"
    if d.get("ends_at"):
        ends_dt = datetime.fromisoformat(d["ends_at"])
        rem = max(0, int((ends_dt - datetime.now(timezone.utc)).total_seconds()))
        rem_str = format_hms(rem)
    highest = d["highest_bid"]
    next_min = d["start_price"] if highest == 0 else highest + d["min_step"]
    leader = d.get("winner_name") or "Hali yo'q"
    comm_pct = int(d.get("commission_percent", 3))
    win_pct = 100 - comm_pct
    win_amount = round(d["total_pool"] * win_pct / 100)
    ext_sec = int(d.get("extended_sec", 0))
    max_ext_sec = int(d.get("max_extension_sec", 300))
    return (
        f"🎯 Auksion #{d['id']}: {d['title']}\n\n"
        f"💎 Start narx: {d['start_price']} ⭐ (Qadam: +{d['min_step']} ⭐)\n"
        f"🔥 Eng yuqori stavka: {highest} ⭐ ({leader})\n"
        f"💰 Jami bank (Pool): {d['total_pool']} ⭐\n"
        f"🏆 G'olib oladi ({win_pct}%): {win_amount} ⭐\n"
        f"⏱ Qolgan vaqt: {rem_str}\n"
        f"⏳ Cho'zilish limiti: {ext_sec // 60}/{max_ext_sec // 60} daqiqa\n\n"
        f"⚡ Keyingi minimal stavka: {next_min} ⭐"
    )


@bot_router.callback_query(F.data == "check_sub")
async def cb_check_sub(call: CallbackQuery, user: User, db: AsyncSession):
    unsub = await get_unsubscribed_channels(db, user.telegram_id)
    if unsub:
        await call.answer("❌ Siz hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True)
        return
    await call.answer("✅ Obuna tasdiqlandi! Xush kelibsiz!", show_alert=True)
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.message.answer(
        f"✅ Rahmat, {user.first_name}! Barcha bo'limlar ochildi.",
        reply_markup=get_reply_kb(is_admin=user.is_admin, user_tg_id=user.telegram_id),
    )


@bot_router.message(CommandStart())
async def cmd_start(message: Message, user: User, db: AsyncSession):
    settings = await get_app_settings(db)
    win_pct = 100 - int(settings.get("commission_percent", 3))
    max_ext_min = int(settings.get("default_max_extension_sec", 300)) // 60
    personal_webapp_url = get_webapp_url(user.telegram_id)
    inline_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎯 Mini-App orqali Auksionda qatnashish",
                    web_app=WebAppInfo(url=personal_webapp_url),
                )
            ]
        ]
    )
    await message.answer(
        f"✨ Assalomu alaykum, {user.first_name}!\n\n"
        f"🌟 PaxtaGram Stars Auction Botga xush kelibsiz!\n"
        f"• Har bir stavka umumiy bankka (Pool) qo'shiladi.\n"
        f"• Oxirgi 60 soniyada stavka qilinsa, taymer +60s ga uzayadi (maksimal {max_ext_min} daqiqagacha).\n"
        f"• G'olib jami Poolning {win_pct}% qismini oladi!\n\n"
        f"💰 Joriy balansingiz: {user.balance_stars} ⭐\n"
        f"🎁 Promo kod: PAXTA2026",
        reply_markup=get_reply_kb(is_admin=user.is_admin, user_tg_id=user.telegram_id),
    )
    await message.answer(
        "👇 Shaxsiy himoyalangan Mini-App oynasini ochish uchun quyidagi tugmani bosing:",
        reply_markup=inline_kb,
    )


@bot_router.message(F.text == "🌐 Web App")
async def msg_webapp(message: Message, user: User):
    w_url = get_webapp_url(user.telegram_id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Mini-App ni ochish", web_app=WebAppInfo(url=w_url))],
            [InlineKeyboardButton(text="🌐 Brauzerda ochish", url=w_url)],
        ]
    )
    await message.answer(
        f"🌐 PaxtaGram Stars Auction Mini-App:\n{w_url}",
        reply_markup=kb,
    )


@bot_router.message(F.text == "➕ Stars depozit")
async def msg_stars_deposit(message: Message, user: User):
    await message.answer(
        f"➕ Stars orqali balansni to'ldirish\n\n"
        f"💰 Joriy balansingiz: {user.balance_stars} ⭐\n"
        f"Quyidagi miqdorlardan birini tanlang yoki '✍️ Boshqa miqdor kiritish' tugmasini bosing:",
        reply_markup=get_stars_deposit_kb(),
    )


@bot_router.callback_query(F.data == "open_stars_dep")
async def cb_open_stars_dep(call: CallbackQuery, user: User):
    await call.answer()
    await call.message.answer(
        f"➕ Stars orqali balansni to'ldirish\n\n"
        f"💰 Joriy balansingiz: {user.balance_stars} ⭐\n"
        f"Qancha Stars depozit qilmoqchisiz?",
        reply_markup=get_stars_deposit_kb(),
    )


@bot_router.callback_query(F.data.startswith("dep_stars:"))
async def cb_dep_stars_amount(call: CallbackQuery, user: User, bot: Bot):
    if DEMO_MODE:
        await call.answer(
            "⚠️ Demo rejimida Stars Invoice ishlamaydi (BOT_TOKEN ni kiriting).",
            show_alert=True,
        )
        return
    amount = int(call.data.split(":")[1])
    payload = build_safe_deposit_payload(user.telegram_id, amount)
    try:
        await bot.send_invoice(
            chat_id=user.telegram_id,
            title=f"➕ Balansni to'ldirish (+{amount} ⭐)",
            description=f"PaxtaGram Stars Auction balansiga +{amount} Stars depozit qilish",
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=f"Depozit (+{amount} ⭐)", amount=amount)],
        )
        await call.answer("⭐ To'lov oynasi yuborildi!")
    except Exception as exc:
        logger.warning("Deposit send_invoice xatosi: %s", exc)
        await call.answer("⚠️ Stars Invoice yuborishda xatolik yuz berdi.", show_alert=True)


@bot_router.callback_query(F.data == "dep_stars_custom")
async def cb_dep_stars_custom(call: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.waiting_custom_deposit)
    await call.answer()
    await call.message.answer("✍️ Qancha Stars depozit qilmoqchisiz? (Masalan: 150):")


@bot_router.message(BotStates.waiting_custom_deposit)
async def msg_custom_deposit(message: Message, state: FSMContext, user: User, bot: Bot):
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0 or int(raw) > 100000:
        await message.answer("⚠️ Iltimos, 1 dan 100000 gacha bo'lgan musbat butun son kiriting.")
        return
    amount = int(raw)
    await state.clear()
    if DEMO_MODE:
        await message.answer("⚠️ Demo rejimida Stars Invoice ishlamaydi.")
        return
    payload = build_safe_deposit_payload(user.telegram_id, amount)
    try:
        await bot.send_invoice(
            chat_id=user.telegram_id,
            title=f"➕ Balansni to'ldirish (+{amount} ⭐)",
            description=f"PaxtaGram Stars Auction balansiga +{amount} Stars depozit qilish",
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=f"Depozit (+{amount} ⭐)", amount=amount)],
        )
    except Exception as exc:
        logger.warning("Custom deposit send_invoice xatosi: %s", exc)
        await message.answer("⚠️ Stars Invoice yuborishda xatolik yuz berdi.")


@bot_router.message(F.text == "🎯 Auksion")
async def msg_auctions(message: Message, user: User, db: AsyncSession):
    stmt = (
        select(Auction)
        .options(selectinload(Auction.winner))
        .where(Auction.status == AuctionStatus.ACTIVE.value)
        .order_by(Auction.ends_at.asc())
    )
    auctions = (await db.execute(stmt)).scalars().all()
    if not auctions:
        await message.answer("📭 Hozirda faol auksionlar yo'q.")
        return
    for a in auctions:
        d = await serialize_auction(db, a)
        next_min = d["start_price"] if d["highest_bid"] == 0 else d["highest_bid"] + d["min_step"]
        await message.answer(
            format_auction_card(d),
            reply_markup=get_auction_kb(a.id, next_min, user_tg_id=user.telegram_id),
        )


@bot_router.callback_query(F.data.startswith("auc_ref:"))
async def cb_auc_refresh(call: CallbackQuery, user: User, db: AsyncSession):
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
        await call.message.edit_text(
            format_auction_card(d),
            reply_markup=get_auction_kb(a.id, next_min, user_tg_id=user.telegram_id),
        )
    except Exception:
        pass
    await call.answer("🔄 Yangilandi")


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
                format_auction_card(d),
                reply_markup=get_auction_kb(aid, required_min, user_tg_id=user.telegram_id),
            )
        except Exception:
            pass
        await call.answer(
            f"⚠️ Narx o'zgardi! Yangi minimal stavka: {required_min} ⭐", show_alert=True
        )
        return

    payload = build_safe_bid_payload(a.id, user.telegram_id, amount)
    try:
        await bot.send_invoice(
            chat_id=user.telegram_id,
            title=f"🎯 Stavka: {a.title}",
            description=f"{a.title} auksionida {amount} Stars stavka qilish",
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=f"Stavka ({amount} ⭐)", amount=amount)],
        )
        await call.answer("⭐ PaxtaGram Stars to'lov oynasi ochildi")
    except Exception as exc:
        logger.warning("send_invoice xatosi: %s", exc)
        await call.answer(
            "⚠️ Stars Invoice yuborishda xatolik. '💰 Balansdan stavka' tugmasidan foydalaning!",
            show_alert=True,
        )


@bot_router.callback_query(F.data.startswith("bid_bal:"))
async def cb_bid_balance(call: CallbackQuery, user: User, db: AsyncSession):
    _, aid_s, amt_s = call.data.split(":")
    aid, amount = int(aid_s), int(amt_s)
    try:
        d = await place_bid(db, aid, user, amount, use_balance=True)
        await call.answer(f"✅ {amount} ⭐ stavka qabul qilindi!", show_alert=True)
        next_min = d["start_price"] if d["highest_bid"] == 0 else d["highest_bid"] + d["min_step"]
        await call.message.edit_text(
            format_auction_card(d),
            reply_markup=get_auction_kb(aid, next_min, user_tg_id=user.telegram_id),
        )
    except Exception as e:
        await call.answer(f"⚠️ {getattr(e, 'detail', str(e))}", show_alert=True)


@bot_router.callback_query(F.data.startswith("bid_custom:"))
async def cb_bid_custom(call: CallbackQuery, state: FSMContext):
    aid = int(call.data.split(":")[1])
    await state.set_state(BotStates.waiting_custom_bid)
    await state.update_data(auction_id=aid)
    await call.answer()
    await call.message.answer("✍️ Stavka miqdorini kiriting (Stars soni, masalan: 100):")


@bot_router.message(BotStates.waiting_custom_bid)
async def msg_custom_bid(message: Message, state: FSMContext, user: User, db: AsyncSession):
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ Iltimos, musbat butun son kiriting.")
        return
    amount = int(raw)
    st = await state.get_data()
    aid = int(st["auction_id"])
    await state.clear()

    a = (await db.execute(select(Auction).where(Auction.id == aid))).scalar_one_or_none()
    if not a or a.status != AuctionStatus.ACTIVE.value:
        await message.answer("⚠️ Auksion yakunlangan yoki faol emas.")
        return

    max_stmt = select(func.max(Bid.amount)).where(Bid.auction_id == aid)
    current_highest = (await db.execute(max_stmt)).scalar() or 0
    required_min = a.start_price if current_highest == 0 else current_highest + a.min_step
    if amount < required_min:
        await message.answer(f"⚠️ Minimal stavka miqdori: {required_min} ⭐")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💰 Balansdan stavka ({amount} ⭐)",
                    callback_data=f"bid_bal:{aid}:{amount}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"⭐ Stars Invoice ({amount} ⭐)",
                    callback_data=f"bid_inv:{aid}:{amount}",
                )
            ],
        ]
    )
    await message.answer(
        f"🎯 Auksion: {a.title}\n"
        f"💎 Tanlangan stavka miqdori: {amount} ⭐\n"
        f"💰 Joriy balansingiz: {user.balance_stars} ⭐\n\n"
        f"Stavka turini tanlang:",
        reply_markup=kb,
    )


@bot_router.pre_checkout_query()
async def on_pre_checkout(q: PreCheckoutQuery, db: AsyncSession):
    payload = q.invoice_payload or ""
    if payload.startswith("dep:"):
        parts = payload.split(":")
        if len(parts) < 3 or not parts[2].isdigit() or int(parts[2]) <= 0:
            await q.answer(ok=False, error_message="Noto'g'ri depozit summasi.")
            return
        await q.answer(ok=True)
        return

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

    if payload.startswith("dep:"):
        credited = await process_stars_deposit_payment(db, user, amount, charge_id)
        if credited:
            await message.answer(
                f"🎉 Balansingiz muvaffaqiyatli to'ldirildi!\n\n"
                f"➕ Qo'shildi: +{amount} ⭐\n"
                f"💰 Joriy balansingiz: {user.balance_stars} ⭐"
            )
        return

    if payload.startswith("bid:"):
        aid = int(payload.split(":")[1])
        try:
            d = await place_bid(db, aid, user, amount, payment_charge_id=charge_id, use_balance=False)
            await message.answer(
                f"🎉 Stavkangiz qabul qilindi!\n"
                f"🎯 Auksion: {d['title']}\n"
                f"⭐ Stavka: {amount} ⭐ | Pool: {d['total_pool']} ⭐"
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
                    f"To'langan {amount} ⭐ real Stars hisobingizga qaytarildi!"
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
                    f"{amount} ⭐ ichki balansingizga saqlandi. Admin bilan bog'laning."
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
        f"• {ensure_utc(t.created_at).strftime('%d.%m %H:%M')} | {t.type.upper()}: {'+' if t.amount >= 0 else ''}{t.amount} ⭐"
        for t in txs
    ) or "Tranzaksiyalar hali yo'q"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Stars orqali balansni to'ldirish",
                    callback_data="open_stars_dep",
                )
            ]
        ]
    )
    await message.answer(
        f"💰 Sizning Stars Balansingiz: {user.balance_stars} ⭐\n"
        f"🏆 Jami yutuq: {user.total_won} ⭐ | Tikilgan: {user.total_bet} ⭐\n\n"
        f"📜 Oxirgi amallar:\n{hist}",
        reply_markup=kb,
    )


@bot_router.message(F.text == "🎁 Promo kod")
async def msg_promo(message: Message, state: FSMContext):
    await state.set_state(BotStates.waiting_promo)
    await message.answer("🎁 Promo kodni yuboring (Masalan: PAXTA2026):")


@bot_router.message(BotStates.waiting_promo)
async def msg_promo_redeem(message: Message, state: FSMContext, user: User, db: AsyncSession):
    await state.clear()
    try:
        added = await redeem_promo_code(db, user, message.text or "")
        await message.answer(
            f"🎉 Promo kod faollashtirildi!\n"
            f"➕ Qo'shildi: +{added} ⭐\n"
            f"💰 Balans: {user.balance_stars} ⭐"
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
                "relayer_username": (conn.user.username or RELAYER_USERNAME).lstrip("@"),
            },
        )
        await notify_all_admins_http(
            f"✅ PaxtaGram Relayer ulandi! ID: {conn.id} (@{conn.user.username})"
        )
    else:
        await update_app_settings(db, {"relayer_business_connection_id": ""})
        await notify_all_admins_http("⚠️ PaxtaGram Relayer uzildi!")


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
                "⚠️ Yuborgan NFT sovg'angiz turi avtomatik ro'yxatdan topilmadi.\n"
                "So'rovingiz adminga yuborildi — admin tekshirib balansingizga qo'shadi.",
            )
        except Exception:
            pass
        await notify_all_admins_http(
            f"⚠️ Noma'lum Relayer Gift keldi!\n"
            f"👤 Kimdan: {user.first_name} (@{user.username}, ID: {user.telegram_id})\n"
            f"🎁 Gift nomi/ID: {gift_title or ref_id}\n"
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
    user_label = f"@{user.username}" if user.username else user.first_name
    asyncio.create_task(
        broadcast_to_finance_channel(
            f"📥 Yangi Depozit (NFT Gift — Avtomatik)!\n\n"
            f"👤 Foydalanuvchi: {user.first_name} ({user_label}, ID: {user.telegram_id})\n"
            f"🎁 NFT: {matched_rate.gift_type}\n"
            f"⭐ Qo'shildi: +{stars_credit} ⭐"
        )
    )
    try:
        await bot.send_message(
            user.telegram_id,
            f"🎉 Relayer orqali yuborgan {matched_rate.gift_type} sovg'angiz qabul qilindi!\n"
            f"➕ Balansga qo'shildi: +{stars_credit} ⭐\n"
            f"💰 Joriy balans: {user.balance_stars} ⭐",
        )
    except Exception:
        pass


@bot_router.message(F.text == "💎 NFT depozit")
async def msg_deposit_nft(message: Message, db: AsyncSession):
    rates = await ensure_default_nft_rates(db)
    settings = await get_app_settings(db)
    relayer_user = str(settings.get("relayer_username", RELAYER_USERNAME)).lstrip("@")
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
        f"💎 NFT Gift orqali depozit qilish\n\n"
        f"⚡️ Sovg'ani to'g'ridan-to'g'ri @{relayer_user} akkauntiga yuboring va quyidagi tugmani bosing:",
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
                f"📥 NFT Depozit so'rovi #{dep.id}:\n"
                f"👤 {user.first_name} (@{user.username or 'no_user'}, ID: {user.telegram_id})\n"
                f"🎁 NFT: {dep.nft_type} (+{dep.stars_amount} ⭐)",
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

    target_label = f"@{target.username}" if target.username else target.first_name
    asyncio.create_task(
        broadcast_to_finance_channel(
            f"📥 Yangi Depozit (NFT Gift)!\n\n"
            f"👤 Foydalanuvchi: {target.first_name} ({target_label}, ID: {target.telegram_id})\n"
            f"🎁 NFT: {dep.nft_type}\n"
            f"⭐ Qo'shildi: +{dep.stars_amount} ⭐"
        )
    )

    try:
        await call.message.edit_text(
            (call.message.text or "") + f"\n\n✅ TASDIQLANDI (+{dep.stars_amount} ⭐)",
            reply_markup=None,
        )
    except Exception:
        pass
    await bot.send_message(
        target.telegram_id,
        f"🎉 NFT Depozitingiz #{dep.id} tasdiqlandi!\n"
        f"➕ Qo'shildi: +{dep.stars_amount} ⭐\n"
        f"💰 Joriy balans: {target.balance_stars} ⭐",
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
            (call.message.text or "") + "\n\n❌ RAD ETILDI",
            reply_markup=None,
        )
    except Exception:
        pass
    if dep.user:
        await bot.send_message(
            dep.user.telegram_id,
            f"❌ NFT Depozit so'rovingiz #{dep.id} rad etildi.",
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
        f"📤 Stars balansni NFT Gift orqali yechib olish\n"
        f"💰 Balansingiz: {user.balance_stars} ⭐\n"
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
                    f"📤 Yechib olish so'rovi #{wd.id}\n"
                    f"👤 {user.first_name} (@{user.username})\n"
                    f"🎁 NFT: {wd.nft_type} ({wd.stars_amount} ⭐)",
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
            (call.message.text or "") + "\n\n✅ TASDIQLANDI", reply_markup=None
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
            (call.message.text or "") + "\n\n❌ RAD ETILDI (Stars qaytarildi)",
            reply_markup=None,
        )
    except Exception as e:
        await call.answer(str(getattr(e, "detail", e)), show_alert=True)


@bot_router.message(F.text == "🛠 Admin Panel")
@bot_router.message(Command("admin"))
async def cmd_admin(message: Message, user: User):
    if not user.is_admin:
        await message.answer(
            f"ℹ️ Sizda admin huquqi yo'q.\n"
            f"🔑 Sizning PaxtaGram ID raqamingiz: {user.telegram_id}\n"
            f"(Render da ADMIN_TELEGRAM_IDS ga {user.telegram_id} ni qo'shing)"
        )
        return
    auto_login_url = get_admin_panel_url(admin_tg_id=user.telegram_id)
    clean_url = get_admin_panel_url()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Admin Panel (Avto-kirish)",
                    web_app=WebAppInfo(url=auto_login_url),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌐 Brauzerda ochish (Avto-kirish)",
                    url=auto_login_url,
                )
            ],
        ]
    )
    await message.answer(
        f"🛠 Admin Panel manzili:\n{clean_url}\n\n"
        f"🔑 Admin ID: {user.telegram_id}\n"
        f"⚡️ Quyidagi tugmani bossangiz parolsiz avtomatik tarzda Admin Panelga kirasiz:",
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
    const urlParams = new URLSearchParams(window.location.search);
    const urlToken = urlParams.get("u_token");
    if (urlToken) {
      localStorage.setItem("paxta_webapp_auth", `jwt:${urlToken}`);
      window.history.replaceState({}, document.title, window.location.pathname);
    }
    // Xavfsizlik: eski versiyadan qolgan soxta 'tgid:...' sessiyalarni darhol o'chiramiz!
    const existingAuth = localStorage.getItem("paxta_webapp_auth") || "";
    if (existingAuth.startsWith("tgid:")) {
      localStorage.removeItem("paxta_webapp_auth");
    }

    function getInitDataHeader() {
      if (tg?.initData && tg.initData.length > 0) return tg.initData;
      const saved = localStorage.getItem("paxta_webapp_auth") || "";
      return saved.startsWith("jwt:") ? saved : "";
    }

    async function api(path, { method = "GET", body = null } = {}) {
      const res = await fetch(path, {
        method,
        headers: {
          "Content-Type": "application/json",
          "X-Telegram-Init-Data": getInitDataHeader(),
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
      const [manualId, setManualId] = useState("");
      const [unsubChannels, setUnsubChannels] = useState([]);
      const [showDepositModal, setShowDepositModal] = useState(false);
      const [depStarsAmt, setDepStarsAmt] = useState("100");
      const [depLoading, setDepLoading] = useState(false);
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

      const checkSubStatus = () => {
        api("/api/subscription-status")
          .then(res => setUnsubChannels(res.unsubscribed || []))
          .catch(() => {});
      };

      const loadMe = () =>
        api("/api/me")
          .then(u => {
            setUser(u);
            setAuthError(null);
            checkSubStatus();
          })
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
          const qs = `?init_data=${encodeURIComponent(getInitDataHeader())}`;
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
        const tick = () => {
          const diff = Math.max(0, Math.floor((new Date(selected.ends_at).getTime() - Date.now()) / 1000));
          setRemSec(diff);
        };
        tick();
        const t = setInterval(tick, 1000);
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

      const handleStarsDeposit = async (amountVal) => {
        const amt = Number(amountVal || depStarsAmt);
        if (!amt || amt <= 0) return;
        setDepLoading(true);
        try {
          const r = await api("/api/balance/deposit-stars", {
            method: "POST",
            body: { amount: amt }
          });
          if (r.invoice_link && tg?.openInvoice) {
            tg.openInvoice(r.invoice_link, (st) => {
              if (st === "paid") {
                setShowDepositModal(false);
                loadMe();
                api("/api/balance/transactions").then(setTxs).catch(() => {});
                alert(`🎉 Hisobingiz +${amt} ⭐ ga to'ldirildi!`);
              } else if (st === "cancelled") {
                alert("ℹ️ To'lov bekor qilindi.");
              }
            });
          } else if (r.sent_to_chat) {
            setShowDepositModal(false);
            alert("⭐ To'lov hisobi (Invoice) bot chatiga yuborildi! Botga o'tib 'Оплатить / To'lash' tugmasini bosing.");
          } else if (r.invoice_link) {
            window.open(r.invoice_link, "_blank");
          }
        } catch (err) {
          alert(err.message);
        } finally {
          setDepLoading(false);
        }
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
              setMsg({ ok: false, text: "Stars Invoice faqat PaxtaGram ichida ishlaydi. 'Balansdan' tugmasidan foydalaning." });
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
          <div className="max-w-md mx-auto p-6 mt-12 liquid-glass rounded-3xl text-center space-y-4">
            <p className="text-3xl">🔒</p>
            <h2 className="text-base font-extrabold">Xavfsiz Avtorizatsiya Talab Qilinadi</h2>
            <p className="text-xs text-white/75 leading-relaxed">
              Boshqa foydalanuvchilar hisobiga ruxsatsiz kirishning oldini olish uchun ID yozib kirish o'chirilgan.
              Mini-App ni faqat <b>PaxtaGram Bot</b> ichidagi <b>"🌐 Web App"</b> tugmasi orqali oching!
            </p>
            <div className="p-3 rounded-2xl bg-white/5 border border-white/10 text-[11px] text-amber-300">
              💡 Botga o'tib <b>/start</b> yoki <b>🌐 Web App</b> tugmasini bosing — bot sizga shaxsiy himoyalangan seans ochib beradi.
            </div>
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
            <div className="flex items-center gap-2">
              {user?.is_admin && (
                <a href="/admin" className="px-2.5 py-1 rounded-full bg-purple-500/30 border border-purple-400/50 text-purple-200 text-[11px] font-bold">
                  🛠 Admin
                </a>
              )}
              <div className="flex items-center gap-1.5 pl-3 pr-1.5 py-1 rounded-full bg-amber-400/15 border border-amber-400/40 text-amber-300 text-xs font-black">
                <span>{user?.balance_stars ?? 0} ⭐</span>
                <button
                  onClick={() => setShowDepositModal(true)}
                  title="Stars orqali balansni to'ldirish"
                  className="w-6 h-6 rounded-full neon-btn text-white flex items-center justify-center text-sm font-black leading-none shadow"
                >
                  +
                </button>
              </div>
            </div>
          </header>

          {/* Majburiy obuna oynasi */}
          {unsubChannels.length > 0 && (
            <div className="liquid-glass-glow rounded-3xl p-5 mb-4 space-y-3 border border-rose-400/40">
              <h3 className="text-sm font-extrabold text-center">🔒 Majburiy Obuna</h3>
              <p className="text-xs text-white/75 text-center">
                Auksionda qatnashish uchun quyidagi rasmiy kanallarga obuna bo'ling:
              </p>
              <div className="space-y-2">
                {unsubChannels.map(ch => (
                  <a
                    key={ch.id}
                    href={ch.invite_link}
                    target="_blank"
                    className="block w-full text-center py-2.5 rounded-xl bg-white/10 border border-white/20 text-xs font-bold hover:bg-white/15"
                  >
                    📢 {ch.title} — Obuna bo'lish
                  </a>
                ))}
              </div>
              <button
                onClick={checkSubStatus}
                className="w-full neon-btn py-2.5 rounded-xl text-xs font-bold"
              >
                ✅ Obunani tekshirish
              </button>
            </div>
          )}

          {/* Stars Depozit Modal (+ bosilganda ochiladi) */}
          {showDepositModal && (
            <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-md flex items-center justify-center p-4">
              <div className="liquid-glass-glow rounded-3xl p-5 w-full max-w-sm space-y-4">
                <div className="flex justify-between items-center">
                  <h3 className="text-sm font-black">➕ Stars orqali depozit qilish</h3>
                  <button onClick={() => setShowDepositModal(false)} className="text-white/60 text-lg font-bold px-2">✕</button>
                </div>
                <p className="text-xs text-white/70">
                  O'z PaxtaGram Stars balansingizdan auksion hisobingizni darhol to'ldiring:
                </p>
                <div className="grid grid-cols-3 gap-2">
                  {[25, 50, 100, 250, 500, 1000].map(preset => (
                    <button
                      key={preset}
                      onClick={() => setDepStarsAmt(String(preset))}
                      className={`py-2 rounded-xl text-xs font-bold border ${Number(depStarsAmt) === preset ? "neon-btn border-transparent text-white" : "bg-white/5 border-white/15 text-amber-300"}`}
                    >
                      +{preset} ⭐
                    </button>
                  ))}
                </div>
                <div className="flex gap-2">
                  <input
                    type="number"
                    min="1"
                    value={depStarsAmt}
                    onChange={e => setDepStarsAmt(e.target.value)}
                    placeholder="Stars miqdori"
                    className="flex-1 bg-white/10 border border-white/20 rounded-xl px-3.5 py-2.5 text-sm font-bold"
                  />
                  <button
                    disabled={depLoading}
                    onClick={() => handleStarsDeposit(depStarsAmt)}
                    className="neon-btn px-5 py-2.5 rounded-xl text-xs font-bold"
                  >
                    {depLoading ? "..." : `To'ldirish (${depStarsAmt} ⭐)`}
                  </button>
                </div>
              </div>
            </div>
          )}

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

                  <h1 className="text-lg font-extrabold mb-1">{selected.title}</h1>
                  <p className="text-[11px] text-white/60 mb-3">
                    ⏳ Anti-snipe cho'zilishi: {Math.floor((selected.extended_sec || 0) / 60)} / {Math.floor((selected.max_extension_sec ?? 300) / 60)} daqiqa
                  </p>

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
              <div className="liquid-glass-glow rounded-3xl p-6 text-center space-y-3">
                <p className="text-xs text-white/60">Joriy Balansingiz</p>
                <p className="text-4xl font-black text-amber-300">{user?.balance_stars ?? 0} ⭐</p>
                <p className="text-xs text-white/70">Yutuq: +{user?.total_won ?? 0} ⭐ • Tikilgan: {user?.total_bet ?? 0} ⭐</p>
                <button
                  onClick={() => setShowDepositModal(true)}
                  className="w-full neon-btn py-3 rounded-xl text-xs font-bold"
                >
                  ➕ Stars orqali balansni to'ldirish
                </button>
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
  <script src="https://paxta.online/js/paxtagram-web-app.js"></script>
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

    const urlParams = new URLSearchParams(window.location.search);
    const urlToken = urlParams.get("token");
    if (urlToken) {
      localStorage.setItem("paxta_adm_token", urlToken);
      window.history.replaceState({}, document.title, window.location.pathname);
    }

    function AdminApp() {
      const [token, setToken] = useState(localStorage.getItem("paxta_adm_token") || "");
      const [loggedIn, setLoggedIn] = useState(Boolean(localStorage.getItem("paxta_adm_token")));
      const [tgId, setTgId] = useState("115");
      const [pass, setPass] = useState("");
      const [loginErr, setLoginErr] = useState("");
      const [sec, setSec] = useState("stats");
      const [stats, setStats] = useState(null);
      const [users, setUsers] = useState([]);
      const [auctions, setAuctions] = useState([]);
      const [wds, setWds] = useState([]);
      const [promos, setPromos] = useState([]);
      const [rates, setRates] = useState([]);
      const [channels, setChannels] = useState([]);
      const [settings, setSettings] = useState({
        commission_percent: 3,
        min_withdraw: 50,
        min_bid_step: 10,
        default_auction_duration_sec: 3600,
        default_max_extension_sec: 300,
        auction_channel_id: "",
        finance_channel_id: ""
      });

      const [newAuc, setNewAuc] = useState({
        title: "",
        start_price: 50,
        min_step: 10,
        duration_min: 60,
        max_extension_min: 5
      });
      const [newPromo, setNewPromo] = useState({ code: "", stars_amount: 100, max_uses: 100 });
      const [newNft, setNewNft] = useState({ gift_type: "", deposit_stars: 50, withdraw_stars: 60 });
      const [newCh, setNewCh] = useState({ title: "", chat_id: "", invite_link: "" });

      const adminApi = async (path, { method = "GET", body = null, customToken = null } = {}) => {
        const activeToken = customToken || token || localStorage.getItem("paxta_adm_token") || "";
        const headers = { "Content-Type": "application/json" };
        if (activeToken) {
          headers["Authorization"] = `Bearer ${activeToken}`;
          headers["X-Admin-Token"] = activeToken;
        }
        const res = await fetch(path, {
          method,
          credentials: "include",
          headers,
          body: body ? JSON.stringify(body) : undefined
        });
        const data = await res.json().catch(() => ({}));
        if (res.status === 401 || res.status === 403) {
          if (path !== "/api/auth/admin-login") {
            localStorage.removeItem("paxta_adm_token");
            setToken("");
            setLoggedIn(false);
          }
          throw new Error(data.detail || "Admin ruxsati topilmadi, qayta kiring.");
        }
        if (!res.ok) throw new Error(data.detail || "Admin xatoligi");
        return data;
      };

      const load = async (overrideToken = null) => {
        try {
          if (sec === "stats") setStats(await adminApi("/api/admin/stats", { customToken: overrideToken }));
          if (sec === "users") setUsers(await adminApi("/api/admin/users", { customToken: overrideToken }));
          if (sec === "auctions") setAuctions(await adminApi("/api/admin/auctions", { customToken: overrideToken }));
          if (sec === "withdrawals") setWds(await adminApi("/api/admin/withdrawals", { customToken: overrideToken }));
          if (sec === "promo") setPromos(await adminApi("/api/admin/promo", { customToken: overrideToken }));
          if (sec === "nft") setRates(await adminApi("/api/admin/nft-rates", { customToken: overrideToken }));
          if (sec === "channels") {
            setChannels(await adminApi("/api/admin/channels", { customToken: overrideToken }));
            setSettings(await adminApi("/api/admin/settings", { customToken: overrideToken }));
          }
          if (sec === "settings") setSettings(await adminApi("/api/admin/settings", { customToken: overrideToken }));
          setLoggedIn(true);
        } catch (e) {}
      };

      useEffect(() => {
        if (loggedIn) load();
      }, [sec, loggedIn]);

      if (!loggedIn) {
        return (
          <div className="min-h-screen flex items-center justify-center p-4">
            <form onSubmit={async e => {
              e.preventDefault();
              setLoginErr("");
              try {
                const res = await adminApi("/api/auth/admin-login", {
                  method: "POST",
                  body: { telegram_id: Number(tgId), password: pass }
                });
                if (res.access_token) {
                  localStorage.setItem("paxta_adm_token", res.access_token);
                  setToken(res.access_token);
                  setLoggedIn(true);
                  await load(res.access_token);
                }
              } catch (err) {
                setLoginErr(err.message);
              }
            }} className="glass rounded-3xl p-8 w-full max-w-sm space-y-4">
              <h1 className="text-xl font-black">🛠 PaxtaGram Admin Panel</h1>
              <p className="text-xs text-white/60">Admin ID raqamingizni kiriting (masalan: 115)</p>
              <div>
                <label className="text-xs text-white/60 block mb-1">Admin ID</label>
                <input
                  type="number"
                  required
                  placeholder="Admin ID (masalan: 115)"
                  value={tgId}
                  onChange={e => setTgId(e.target.value)}
                  className="w-full bg-white/5 border border-white/15 rounded-xl px-3.5 py-2.5 text-sm"
                />
              </div>
              <div>
                <label className="text-xs text-white/60 block mb-1">Admin Parol (Majburiy)</label>
                <input
                  type="password"
                  required
                  placeholder="ADMIN_PASSWORD ni kiriting"
                  value={pass}
                  onChange={e => setPass(e.target.value)}
                  className="w-full bg-white/5 border border-white/15 rounded-xl px-3.5 py-2.5 text-sm"
                />
              </div>
              {loginErr && (
                <div className="p-2.5 rounded-xl bg-rose-500/20 border border-rose-400/40 text-xs text-rose-200">
                  {loginErr}
                </div>
              )}
              <button className="w-full py-3 rounded-xl font-bold text-sm bg-gradient-to-r from-purple-600 to-pink-600">
                Kirish
              </button>
            </form>
          </div>
        );
      }

      return (
        <div className="min-h-screen flex flex-col md:flex-row">
          <aside className="w-full md:w-64 glass p-5 space-y-2">
            <h2 className="font-black text-base mb-4">★ PAXTA ADMIN</h2>
            {[
              ["stats", "📊 Statistika"],
              ["auctions", "🎯 Auksionlar & Vaqt"],
              ["channels", "📢 Kanallar & Obuna"],
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
              localStorage.removeItem("paxta_adm_token");
              setToken("");
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
                <div className="glass p-5 rounded-2xl space-y-3">
                  <h3 className="text-sm font-black">🎯 Yangi Auksion Ochish</h3>
                  <form onSubmit={async e => {
                    e.preventDefault();
                    await adminApi("/api/admin/auctions", {
                      method: "POST",
                      body: {
                        title: newAuc.title,
                        start_price: Number(newAuc.start_price),
                        min_step: Number(newAuc.min_step),
                        duration_sec: Math.max(60, Number(newAuc.duration_min) * 60),
                        max_extension_sec: Math.max(0, Number(newAuc.max_extension_min) * 60),
                        start_immediately: true
                      }
                    });
                    setNewAuc({ title: "", start_price: 50, min_step: 10, duration_min: 60, max_extension_min: 5 });
                    load();
                  }} className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <div className="md:col-span-3">
                      <label className="text-[11px] text-white/60">Auksion nomi</label>
                      <input required placeholder="Masalan: Golden Trophy NFT Jackpot 🏆" value={newAuc.title} onChange={e => setNewAuc({ ...newAuc, title: e.target.value })} className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs mt-1" />
                    </div>
                    <div>
                      <label className="text-[11px] text-white/60">Boshlang'ich narx (⭐)</label>
                      <input type="number" required value={newAuc.start_price} onChange={e => setNewAuc({ ...newAuc, start_price: e.target.value })} className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs mt-1" />
                    </div>
                    <div>
                      <label className="text-[11px] text-white/60">Minimal qadam (⭐)</label>
                      <input type="number" required value={newAuc.min_step} onChange={e => setNewAuc({ ...newAuc, min_step: e.target.value })} className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs mt-1" />
                    </div>
                    <div>
                      <label className="text-[11px] text-white/60">Davomiyligi (daqiqada)</label>
                      <input type="number" required min="1" value={newAuc.duration_min} onChange={e => setNewAuc({ ...newAuc, duration_min: e.target.value })} className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs mt-1" />
                    </div>
                    <div>
                      <label className="text-[11px] text-white/60">Maksimal cho'zilish vaqti (daqiqada, masalan: 5)</label>
                      <input type="number" required min="0" value={newAuc.max_extension_min} onChange={e => setNewAuc({ ...newAuc, max_extension_min: e.target.value })} className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs mt-1" />
                    </div>
                    <div className="md:col-span-2 flex items-end">
                      <button className="w-full bg-purple-600 py-2.5 rounded-xl text-xs font-bold">+ Auksionni Boshlash</button>
                    </div>
                  </form>
                </div>

                {auctions.map(a => (
                  <div key={a.id} className="glass p-4 rounded-2xl flex flex-wrap justify-between items-center gap-3">
                    <div>
                      <b className="text-sm">#{a.id} {a.title} ({a.status})</b>
                      <p className="text-xs text-white/60 mt-0.5">
                        Pool: <b>{a.total_pool} ⭐</b> • Top stavka: <b>{a.highest_bid} ⭐</b> • Davomiylik: <b>{Math.round(a.duration_sec / 60)} daq</b>
                      </p>
                      <p className="text-xs text-amber-300 mt-0.5">
                        ⏳ Cho'zilgan vaqt: {Math.floor((a.extended_sec || 0) / 60)} / {Math.floor((a.max_extension_sec ?? 300) / 60)} daqiqa
                      </p>
                    </div>
                    {a.status === "active" && (
                      <div className="flex flex-wrap gap-2">
                        <button onClick={async () => {
                          const remMin = prompt("Auksion tugashigacha necha daqiqa qolsin? (Masalan: 30):", Math.max(1, Math.round(a.duration_sec / 60)));
                          if (!remMin) return;
                          const maxExtMin = prompt("Maksimal cho'zilish chegarasi necha daqiqa bo'lsin? (Default: 5):", Math.round((a.max_extension_sec ?? 300) / 60));
                          if (maxExtMin === null) return;
                          await adminApi(`/api/admin/auctions/${a.id}`, {
                            method: "PATCH",
                            body: {
                              remaining_minutes: Number(remMin),
                              max_extension_sec: Math.max(0, Number(maxExtMin) * 60)
                            }
                          });
                          load();
                        }} className="px-3 py-1.5 rounded-xl bg-amber-500/20 border border-amber-400 text-xs text-amber-200 font-bold">
                          ⏱ Vaqt va Cho'zilishni Tahrirlash
                        </button>
                        <button onClick={async () => {
                          if (!confirm("Rostdan ham auksionni bekor qilib barcha stavkalarni qaytarmoqchimisiz?")) return;
                          await adminApi(`/api/admin/auctions/${a.id}`, { method: "PATCH", body: { status: "cancelled" } });
                          load();
                        }} className="px-3 py-1.5 rounded-xl bg-rose-500/20 border border-rose-400 text-xs text-rose-200 font-bold">
                          Bekor qilish (Refund)
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {sec === "channels" && (
              <div className="space-y-6">
                {/* 1. Auksion va Depozit/Yechib olish Bildirishnoma Kanallari */}
                <div className="glass p-5 rounded-2xl space-y-4">
                  <h3 className="text-sm font-black">📡 1. Avtomatik Xabar Yuboruvchi Kanallar</h3>
                  <p className="text-xs text-white/60">
                    Botni ushbu kanallarga Admin qilib qo'shing, shunda u avtomatik xabar yubora oladi.
                  </p>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="bg-white/5 p-4 rounded-2xl border border-white/10 space-y-2">
                      <label className="text-xs font-bold text-fuchsia-300 block">
                        🎯 Auksion Jonli Kanali (Kim qancha tikkani va qancha vaqt qolgani)
                      </label>
                      <input
                        placeholder="@auksion_kanali yoki -100123456789"
                        value={settings.auction_channel_id || ""}
                        onChange={e => setSettings({ ...settings, auction_channel_id: e.target.value })}
                        className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs"
                      />
                      <button
                        type="button"
                        onClick={async () => {
                          try {
                            await adminApi("/api/admin/settings", { method: "PATCH", body: { auction_channel_id: settings.auction_channel_id } });
                            const r = await adminApi("/api/admin/channels/test", { method: "POST", body: { channel_type: "auction" } });
                            alert(r.message);
                          } catch (err) { alert(err.message); }
                        }}
                        className="px-3 py-1.5 rounded-lg bg-fuchsia-600/40 border border-fuchsia-400/50 text-xs font-bold"
                      >
                        🧪 Saqlash va Test xabar yuborish
                      </button>
                    </div>

                    <div className="bg-white/5 p-4 rounded-2xl border border-white/10 space-y-2">
                      <label className="text-xs font-bold text-emerald-300 block">
                        💸 Depozit va Yechib Olishlar Kanali (To'lovlar tarixi)
                      </label>
                      <input
                        placeholder="@tolovlar_kanali yoki -100123456789"
                        value={settings.finance_channel_id || ""}
                        onChange={e => setSettings({ ...settings, finance_channel_id: e.target.value })}
                        className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs"
                      />
                      <button
                        type="button"
                        onClick={async () => {
                          try {
                            await adminApi("/api/admin/settings", { method: "PATCH", body: { finance_channel_id: settings.finance_channel_id } });
                            const r = await adminApi("/api/admin/channels/test", { method: "POST", body: { channel_type: "finance" } });
                            alert(r.message);
                          } catch (err) { alert(err.message); }
                        }}
                        className="px-3 py-1.5 rounded-lg bg-emerald-600/40 border border-emerald-400/50 text-xs font-bold"
                      >
                        🧪 Saqlash va Test xabar yuborish
                      </button>
                    </div>
                  </div>
                  <button
                    onClick={async () => {
                      await adminApi("/api/admin/settings", {
                        method: "PATCH",
                        body: {
                          auction_channel_id: settings.auction_channel_id,
                          finance_channel_id: settings.finance_channel_id
                        }
                      });
                      alert("Kanal sozlamalari saqlandi!");
                    }}
                    className="px-5 py-2.5 rounded-xl bg-purple-600 text-xs font-bold"
                  >
                    💾 Barcha Kanal Sozlamalarini Saqlash
                  </button>
                </div>

                {/* 2. Majburiy Obuna Kanallari */}
                <div className="glass p-5 rounded-2xl space-y-4">
                  <h3 className="text-sm font-black">🔒 2. Majburiy Obuna Kanallari</h3>
                  <p className="text-xs text-white/60">
                    Foydalanuvchilar botdan va auksiondan foydalanishdan oldin ushbu kanallarga obuna bo'lishi shart.
                  </p>
                  <form onSubmit={async e => {
                    e.preventDefault();
                    await adminApi("/api/admin/channels", {
                      method: "POST",
                      body: {
                        title: newCh.title,
                        chat_id: newCh.chat_id,
                        invite_link: newCh.invite_link,
                        is_active: true
                      }
                    });
                    setNewCh({ title: "", chat_id: "", invite_link: "" });
                    load();
                  }} className="grid grid-cols-1 md:grid-cols-4 gap-2">
                    <input
                      required
                      placeholder="Kanal nomi (Masalan: Rasmiy Kanal)"
                      value={newCh.title}
                      onChange={e => setNewCh({ ...newCh, title: e.target.value })}
                      className="bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs"
                    />
                    <input
                      required
                      placeholder="@kanal_username yoki -100..."
                      value={newCh.chat_id}
                      onChange={e => setNewCh({ ...newCh, chat_id: e.target.value })}
                      className="bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs"
                    />
                    <input
                      required
                      placeholder="Havola (https://paxta.online/...)"
                      value={newCh.invite_link}
                      onChange={e => setNewCh({ ...newCh, invite_link: e.target.value })}
                      className="bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-xs"
                    />
                    <button className="bg-purple-600 px-4 py-2 rounded-xl text-xs font-bold">
                      + Kanal Qo'shish
                    </button>
                  </form>

                  <div className="space-y-2">
                    {channels.map(ch => (
                      <div key={ch.id} className="flex flex-wrap justify-between items-center bg-white/5 px-4 py-3 rounded-xl border border-white/10 gap-2">
                        <div>
                          <b className="text-xs">{ch.title}</b>
                          <p className="text-[11px] text-white/60">Chat ID: {ch.chat_id} • Havola: {ch.invite_link}</p>
                        </div>
                        <div className="flex gap-2">
                          <button
                            onClick={async () => {
                              await adminApi(`/api/admin/channels/${ch.id}`, {
                                method: "PATCH",
                                body: { is_active: !ch.is_active }
                              });
                              load();
                            }}
                            className={`px-3 py-1 rounded-lg text-xs font-bold border ${ch.is_active ? "bg-emerald-500/20 border-emerald-400 text-emerald-200" : "bg-white/10 border-white/20 text-white/50"}`}
                          >
                            {ch.is_active ? "Faol" : "O'chirilgan"}
                          </button>
                          <button
                            onClick={async () => {
                              await adminApi(`/api/admin/channels/${ch.id}`, { method: "DELETE" });
                              load();
                            }}
                            className="px-3 py-1 rounded-lg bg-rose-500/20 border border-rose-400 text-rose-200 text-xs font-bold"
                          >
                            🗑 O'chirish
                          </button>
                        </div>
                      </div>
                    ))}
                    {channels.length === 0 && (
                      <p className="text-xs text-white/40 text-center py-4">Hozircha majburiy obuna kanallari qo'shilmagan</p>
                    )}
                  </div>
                </div>
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
                await adminApi("/api/admin/settings", {
                  method: "PATCH",
                  body: {
                    commission_percent: Number(settings.commission_percent),
                    min_withdraw: Number(settings.min_withdraw),
                    min_bid_step: Number(settings.min_bid_step),
                    default_auction_duration_sec: Math.max(60, Number(settings.default_auction_duration_sec)),
                    default_max_extension_sec: Math.max(0, Number(settings.default_max_extension_sec))
                  }
                });
                alert("Sozlamalar saqlandi!");
              }} className="glass p-5 rounded-2xl max-w-md space-y-3">
                <div>
                  <label className="text-xs text-white/60">Komissiya (%)</label>
                  <input type="number" value={settings.commission_percent} onChange={e => setSettings({ ...settings, commission_percent: e.target.value })} className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-sm mt-1" />
                </div>
                <div>
                  <label className="text-xs text-white/60">Minimal Yechib Olish (⭐)</label>
                  <input type="number" value={settings.min_withdraw} onChange={e => setSettings({ ...settings, min_withdraw: e.target.value })} className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-sm mt-1" />
                </div>
                <div>
                  <label className="text-xs text-white/60">Standart Auksion Davomiyligi (soniyada, 3600 = 60 daqiqa)</label>
                  <input type="number" value={settings.default_auction_duration_sec} onChange={e => setSettings({ ...settings, default_auction_duration_sec: e.target.value })} className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-sm mt-1" />
                </div>
                <div>
                  <label className="text-xs text-white/60">Standart Maksimal Cho'zilish Vaqti (soniyada, 300 = 5 daqiqa)</label>
                  <input type="number" value={settings.default_max_extension_sec} onChange={e => setSettings({ ...settings, default_max_extension_sec: e.target.value })} className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-sm mt-1" />
                </div>
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
# 10. FASTAPI ILOVASI, AVTOMATIK MIGRATSIYA VA BOT POLLING SUPERVISION
# ====================================================================================

scheduler = AsyncIOScheduler()
bot_polling_task: Optional[asyncio.Task] = None


async def scheduled_finish_auctions() -> None:
    async with AsyncSessionLocal() as db:
        await finish_expired_auctions(db)


async def ensure_schema_columns() -> None:
    """Mavjud bazada yangi ustunlar (max_extension_sec, extended_sec) bo'lmasa qo'shib qo'yadi."""
    for col_sql in (
        "ALTER TABLE auctions ADD COLUMN max_extension_sec INTEGER NOT NULL DEFAULT 300",
        "ALTER TABLE auctions ADD COLUMN extended_sec INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            async with engine.begin() as conn:
                await conn.execute(sql_text(col_sql))
        except Exception:
            pass


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
            default=DefaultBotProperties(parse_mode=None),
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
    global bot_polling_task, engine, AsyncSessionLocal, DATABASE_URL
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as db_exc:
        logger.warning(
            "⚠️ Asosiy DATABASE_URL ga ulanib bo'lmadi (%s). Avtomatik SQLite bazasiga o'tilmoqda...",
            db_exc,
        )
        DATABASE_URL = SQLITE_FALLBACK_URL
        engine, AsyncSessionLocal = _build_engine_and_session(DATABASE_URL)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    await ensure_schema_columns()

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


app = FastAPI(title="PaxtaGram Stars Auction v4.2", version="4.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def auto_detect_public_url_middleware(request: Request, call_next):
    global RUNTIME_PUBLIC_URL
    if not _is_valid_public_url(RUNTIME_PUBLIC_URL):
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
        if host and _is_valid_public_url(host):
            RUNTIME_PUBLIC_URL = f"{proto}://{host}".rstrip("/")
            logger.info("🌐 Haqiqiy server manzili avtomatik aniqlandi: %s", RUNTIME_PUBLIC_URL)
    return await call_next(request)


@app.get("/health", tags=["health"])
@app.get("/api/health", tags=["health"])
async def health_check():
    return {
        "ok": True,
        "version": "4.2.0",
        "demo_mode": DEMO_MODE,
        "public_url": get_public_url(),
    }


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


@app.get("/api/subscription-status")
async def api_subscription_status(
    user: User = Depends(get_current_webapp_user),
    db: AsyncSession = Depends(get_db),
):
    unsub = await get_unsubscribed_channels(db, user.telegram_id)
    return {
        "ok": len(unsub) == 0,
        "unsubscribed": [
            {
                "id": c.id,
                "title": c.title,
                "chat_id": c.chat_id,
                "invite_link": c.invite_link,
            }
            for c in unsub
        ],
    }


@app.post("/api/balance/deposit-stars")
async def api_create_stars_deposit(
    payload: StarsDepositCreateRequest,
    user: User = Depends(get_current_webapp_user),
):
    if DEMO_MODE:
        raise HTTPException(
            status_code=400,
            detail="Demo rejimida Stars Invoice ishlamaydi (BOT_TOKEN ni sozlang).",
        )
    dep_payload = build_safe_deposit_payload(user.telegram_id, payload.amount)
    inv_link = await create_stars_invoice_link(
        title=f"Balansni to'ldirish (+{payload.amount} ⭐)",
        description=f"PaxtaGram Stars Auction balansiga +{payload.amount} Stars depozit qilish",
        payload=dep_payload,
        amount_stars=payload.amount,
    )
    if inv_link:
        return {"ok": True, "invoice_link": inv_link, "payload": dep_payload}

    # Agar createInvoiceLink ishlamasa, foydalanuvchining bot chatiga sendInvoice yuboramiz:
    sent = await send_stars_invoice_to_chat(
        telegram_id=user.telegram_id,
        title=f"➕ Balansni to'ldirish (+{payload.amount} ⭐)",
        description=f"PaxtaGram Stars Auction balansiga +{payload.amount} Stars depozit qilish",
        payload=dep_payload,
        amount_stars=payload.amount,
    )
    if sent:
        return {"ok": True, "sent_to_chat": True, "payload": dep_payload}

    raise HTTPException(
        status_code=400,
        detail="Stars Invoice yaratib bo'lmadi. Bot chatidan '➕ Stars depozit' tugmasini bosing.",
    )


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

    unsub = await get_unsubscribed_channels(db, user.telegram_id)
    if unsub:
        ch_names = ", ".join(c.title for c in unsub)
        raise HTTPException(
            status_code=403,
            detail=f"Stavka qilishdan oldin majburiy kanallarga obuna bo'ling: {ch_names}",
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
    if not await is_user_admin_in_db_or_whitelist(db, payload.telegram_id):
        raise HTTPException(
            status_code=403,
            detail=f"Kiritilgan ID ({payload.telegram_id}) admin ro'yxatida yo'q. Render da ADMIN_TELEGRAM_IDS ga {payload.telegram_id} ni qo'shing.",
        )

    entered_pass = (payload.password or "").strip()
    if not entered_pass or not hmac.compare_digest(entered_pass, ADMIN_PASSWORD):
        raise HTTPException(
            status_code=401,
            detail="Admin paroli noto'g'ri! (Yoki bot ichida /admin buyrug'ini yozib shaxsiy avto-kirish tugmasidan foydalaning).",
        )

    u = (
        await db.execute(select(User).where(User.telegram_id == payload.telegram_id))
    ).scalar_one_or_none()
    if not u:
        u = User(
            telegram_id=payload.telegram_id,
            first_name=f"Admin #{payload.telegram_id}",
            is_admin=True,
            balance_stars=0,
        )
        db.add(u)
        await db.commit()
    elif not u.is_admin:
        u.is_admin = True
        await db.commit()

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


@app.get("/api/admin/channels", response_model=List[MandatoryChannelOut])
async def api_admin_list_channels(
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(MandatoryChannel).order_by(MandatoryChannel.id.asc())
    return list((await db.execute(stmt)).scalars().all())


@app.post("/api/admin/channels", response_model=MandatoryChannelOut)
async def api_admin_create_channel(
    payload: AdminMandatoryChannelCreateRequest,
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    ch = MandatoryChannel(
        title=payload.title.strip(),
        chat_id=payload.chat_id.strip(),
        invite_link=payload.invite_link.strip(),
        is_active=payload.is_active,
    )
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    return ch


@app.patch("/api/admin/channels/{channel_id}", response_model=MandatoryChannelOut)
async def api_admin_patch_channel(
    channel_id: int,
    payload: AdminMandatoryChannelPatchRequest,
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    ch = (
        await db.execute(select(MandatoryChannel).where(MandatoryChannel.id == channel_id))
    ).scalar_one_or_none()
    if not ch:
        raise HTTPException(status_code=404, detail="Kanal topilmadi")
    if payload.title is not None:
        ch.title = payload.title.strip()
    if payload.chat_id is not None:
        ch.chat_id = payload.chat_id.strip()
    if payload.invite_link is not None:
        ch.invite_link = payload.invite_link.strip()
    if payload.is_active is not None:
        ch.is_active = payload.is_active
    await db.commit()
    await db.refresh(ch)
    return ch


@app.delete("/api/admin/channels/{channel_id}")
async def api_admin_delete_channel(
    channel_id: int,
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(delete(MandatoryChannel).where(MandatoryChannel.id == channel_id))
    await db.commit()
    return {"ok": True}


@app.post("/api/admin/channels/test")
async def api_admin_test_channel(
    payload: AdminChannelTestRequest,
    admin_id: int = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    settings = await get_app_settings(db)
    if payload.channel_type == "auction":
        ch_id = (settings.get("auction_channel_id") or "").strip()
        if not ch_id:
            raise HTTPException(status_code=400, detail="Avval Auksion Jonli Kanali ID/username ni kiriting.")
        ok = await send_channel_post(
            ch_id,
            "✅ Test xabar: Auksion Jonli Kanali muvaffaqiyatli ulandi!\n"
            "Endi barcha stavkalar va qolgan vaqt shu kanalga yuboriladi.",
            button_text="🎯 Auksionda qatnashish",
            button_url=get_bot_auction_deeplink(),
        )
    else:
        ch_id = (settings.get("finance_channel_id") or "").strip()
        if not ch_id:
            raise HTTPException(status_code=400, detail="Avval Depozit va Yechib Olishlar Kanali ID/username ni kiriting.")
        ok = await send_channel_post(
            ch_id,
            "✅ Test xabar: Depozit va Yechib Olishlar Kanali muvaffaqiyatli ulandi!\n"
            "Endi barcha Stars/NFT depozitlar va yechib olishlar shu kanalga yuboriladi.",
            button_text="🌟 Botga kirish",
            button_url=f"https://paxta.online/{BOT_USERNAME}",
        )

    if not ok:
        raise HTTPException(
            status_code=400,
            detail=f"Kanalga ({ch_id}) xabar yuborib bo'lmadi. Bot kanalga Admin qilib qo'shilganini tekshiring!",
        )
    return {"ok": True, "message": f"✅ {ch_id} kanaliga test xabar muvaffaqiyatli yuborildi!"}


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
        max_extension_sec=payload.max_extension_sec,
        extended_sec=0,
        started_at=now if payload.start_immediately else None,
        ends_at=(now + timedelta(seconds=payload.duration_sec))
        if payload.start_immediately
        else None,
        status=AuctionStatus.ACTIVE.value if payload.start_immediately else AuctionStatus.PENDING.value,
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)

    if a.status == AuctionStatus.ACTIVE.value:
        asyncio.create_task(
            broadcast_to_auction_channel(
                f"🚀 Yangi Auksion Boshlandi!\n\n"
                f"🎯 Auksion #{a.id}: {a.title}\n"
                f"💎 Boshlang'ich narx: {a.start_price} ⭐ (Qadam: +{a.min_step} ⭐)\n"
                f"⏱ Davomiyligi: {format_hms(a.duration_sec)}\n"
                f"⏳ Maksimal cho'zilish chegarasi: {a.max_extension_sec // 60} daqiqa"
            )
        )

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

    async with get_auction_lock(auction_id):
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
        if payload.max_extension_sec is not None:
            a.max_extension_sec = payload.max_extension_sec
        if payload.remaining_minutes is not None:
            now = datetime.now(timezone.utc)
            new_sec = max(60, int(payload.remaining_minutes) * 60)
            a.duration_sec = new_sec
            a.ends_at = now + timedelta(seconds=new_sec)
        elif payload.duration_sec is not None:
            a.duration_sec = payload.duration_sec
            if a.started_at:
                a.ends_at = ensure_utc(a.started_at) + timedelta(seconds=payload.duration_sec + (a.extended_sec or 0))
        await db.commit()
        await db.refresh(a)
        serialized = await serialize_auction(db, a)

    await ws_manager.broadcast(a.id, {"event": "snapshot", "auction": serialized})
    return serialized


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
    if init_data and init_data != "demo":
        try:
            verify_telegram_init_data(init_data)
        except HTTPException:
            pass

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
