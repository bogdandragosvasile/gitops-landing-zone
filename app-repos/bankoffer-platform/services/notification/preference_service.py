"""Customer notification preference service.

Manages per-customer channel preferences, frequency caps, quiet hours,
and opt-out state.  Backed by PostgreSQL via SQLAlchemy (async).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Sequence

from pydantic import BaseModel
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Time,
    select,
    func,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "PREFERENCE_DB_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/notifications",
)

# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


class CustomerPreference(Base):
    """Stores each customer's notification preferences."""

    __tablename__ = "customer_preferences"

    customer_id: str = Column(String(64), primary_key=True)
    preferred_channel: str = Column(String(16), nullable=False, default="push")
    max_per_day: int = Column(Integer, nullable=False, default=5)
    max_per_week: int = Column(Integer, nullable=False, default=15)
    quiet_hours_start: time | None = Column(Time, nullable=True)  # e.g. 22:00
    quiet_hours_end: time | None = Column(Time, nullable=True)  # e.g. 08:00
    timezone: str = Column(String(48), nullable=False, default="UTC")
    opted_out: bool = Column(Boolean, nullable=False, default=False)
    updated_at: datetime = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
    )


class NotificationLog(Base):
    """Append-only log of sent notifications — used for frequency-cap checks."""

    __tablename__ = "notification_log"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    customer_id: str = Column(String(64), nullable=False, index=True)
    channel: str = Column(String(16), nullable=False)
    sent_at: datetime = Column(
        DateTime(timezone=True), nullable=False, default=func.now()
    )


# ---------------------------------------------------------------------------
# Pydantic DTOs (for API serialisation)
# ---------------------------------------------------------------------------


class PreferenceUpdate(BaseModel):
    preferred_channel: str | None = None
    max_per_day: int | None = None
    max_per_week: int | None = None
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    timezone: str | None = None
    opted_out: bool | None = None


class PreferenceResponse(BaseModel):
    customer_id: str
    preferred_channel: str
    max_per_day: int
    max_per_week: int
    quiet_hours_start: time | None
    quiet_hours_end: time | None
    timezone: str
    opted_out: bool


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass
class PreferenceService:
    """High-level service that wraps the preference store.

    All public methods are ``async`` so the notification router can call
    them without blocking the event loop.
    """

    database_url: str = DATABASE_URL
    _session_factory: async_sessionmaker[AsyncSession] | None = field(
        default=None, init=False, repr=False
    )

    async def _get_session(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            engine = create_async_engine(self.database_url, echo=False)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            self._session_factory = async_sessionmaker(
                engine, expire_on_commit=False
            )
        return self._session_factory

    # -- queries used by router ------------------------------------------

    async def is_opted_out(self, customer_id: str) -> bool:
        factory = await self._get_session()
        async with factory() as session:
            pref = await session.get(CustomerPreference, customer_id)
            return pref.opted_out if pref else False

    async def is_quiet_hours(self, customer_id: str) -> bool:
        """Return *True* if the current time falls within the customer's
        configured quiet-hours window."""
        factory = await self._get_session()
        async with factory() as session:
            pref = await session.get(CustomerPreference, customer_id)
            if not pref or not pref.quiet_hours_start or not pref.quiet_hours_end:
                return False

            try:
                from zoneinfo import ZoneInfo

                tz = ZoneInfo(pref.timezone)
            except Exception:
                tz = timezone.utc

            now = datetime.now(tz).time()
            start, end = pref.quiet_hours_start, pref.quiet_hours_end

            if start <= end:
                return start <= now <= end
            # Wraps midnight (e.g. 22:00 -> 08:00)
            return now >= start or now <= end

    async def resolve_channel(
        self, customer_id: str, requested_channel: str
    ) -> str:
        """Return the effective channel, preferring the customer's stored
        preference over the event's requested channel."""
        factory = await self._get_session()
        async with factory() as session:
            pref = await session.get(CustomerPreference, customer_id)
            if pref and pref.preferred_channel:
                return pref.preferred_channel
            return requested_channel

    async def check_frequency_cap(
        self, customer_id: str, channel: str
    ) -> bool:
        """Return *True* if sending another notification would NOT exceed
        the customer's daily and weekly caps."""
        factory = await self._get_session()
        async with factory() as session:
            pref = await session.get(CustomerPreference, customer_id)
            max_day = pref.max_per_day if pref else 5
            max_week = pref.max_per_week if pref else 15

            now = datetime.now(timezone.utc)
            day_ago = now - timedelta(days=1)
            week_ago = now - timedelta(weeks=1)

            day_count_q = (
                select(func.count())
                .select_from(NotificationLog)
                .where(
                    NotificationLog.customer_id == customer_id,
                    NotificationLog.channel == channel,
                    NotificationLog.sent_at >= day_ago,
                )
            )
            week_count_q = (
                select(func.count())
                .select_from(NotificationLog)
                .where(
                    NotificationLog.customer_id == customer_id,
                    NotificationLog.channel == channel,
                    NotificationLog.sent_at >= week_ago,
                )
            )

            day_count = (await session.execute(day_count_q)).scalar_one()
            week_count = (await session.execute(week_count_q)).scalar_one()

            return day_count < max_day and week_count < max_week

    async def record_notification(
        self, customer_id: str, channel: str
    ) -> None:
        """Append a log entry after a notification is successfully sent."""
        factory = await self._get_session()
        async with factory() as session:
            session.add(
                NotificationLog(customer_id=customer_id, channel=channel)
            )
            await session.commit()

    # -- CRUD for preferences (used by API) ------------------------------

    async def get_preference(
        self, customer_id: str
    ) -> PreferenceResponse | None:
        factory = await self._get_session()
        async with factory() as session:
            pref = await session.get(CustomerPreference, customer_id)
            if not pref:
                return None
            return PreferenceResponse(
                customer_id=pref.customer_id,
                preferred_channel=pref.preferred_channel,
                max_per_day=pref.max_per_day,
                max_per_week=pref.max_per_week,
                quiet_hours_start=pref.quiet_hours_start,
                quiet_hours_end=pref.quiet_hours_end,
                timezone=pref.timezone,
                opted_out=pref.opted_out,
            )

    async def upsert_preference(
        self, customer_id: str, update: PreferenceUpdate
    ) -> PreferenceResponse:
        factory = await self._get_session()
        async with factory() as session:
            pref = await session.get(CustomerPreference, customer_id)
            if pref is None:
                pref = CustomerPreference(customer_id=customer_id)
                session.add(pref)

            for attr, value in update.model_dump(exclude_unset=True).items():
                setattr(pref, attr, value)

            await session.commit()
            await session.refresh(pref)

            return PreferenceResponse(
                customer_id=pref.customer_id,
                preferred_channel=pref.preferred_channel,
                max_per_day=pref.max_per_day,
                max_per_week=pref.max_per_week,
                quiet_hours_start=pref.quiet_hours_start,
                quiet_hours_end=pref.quiet_hours_end,
                timezone=pref.timezone,
                opted_out=pref.opted_out,
            )

    async def opt_out(self, customer_id: str) -> None:
        await self.upsert_preference(
            customer_id, PreferenceUpdate(opted_out=True)
        )

    async def opt_in(self, customer_id: str) -> None:
        await self.upsert_preference(
            customer_id, PreferenceUpdate(opted_out=False)
        )
