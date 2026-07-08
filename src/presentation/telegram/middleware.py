"""🔐 Tenant Auth Middleware — aiogram Outer Middleware for the AutoEntry Bot.

This module intercepts EVERY incoming Telegram update BEFORE it reaches
any handler.  It extracts the caller's Telegram user ID, resolves their
tenant scope via ``AuthVerificationPort``, and injects an
``AuthContextDTO`` into the aiogram ``data`` dictionary under the
well-known key ``"auth_context"``.

====================================================================
   HEXAGONAL IMPORT DISCIPLINE (ENFORCED)
====================================================================

**Imports from:**
    - ``aiogram``           — BaseMiddleware, types (Update, Message, etc.)
    - ``src.domain.ports``  — ``AuthVerificationPort`` (inbound port ABC)
    - ``src.domain.schemas``— ``AuthContextDTO`` (the DTO injected into
                              ``data["auth_context"]``)
    - ``src.domain.exceptions`` — ``UnauthorizedUserError``,
                                  ``TenantNotFoundError`` (catch + drop)
    - ``src.domain.value_objects`` — ``TelegramUserId`` (wrapping raw int)

**Never imports from:**
    - ``src.infrastructure``  — middleware depends on the port ABC, not
                                on any concrete adapter.
    - ``src.config``          — settings are not needed; the middleware
                                receives its dependency via constructor.

====================================================================
   FLOW
====================================================================

For every update::

    Update arrives
        │
        ▼
    ┌─────────────────────────────────────────┐
    │ TenantAuthMiddleware.__call__            │
    │                                          │
    │ 1. Extract telegram_user_id from update  │
    │ 2. Call auth_port.verify(user_id)        │
    │ 3. Success → data["auth_context"] = ctx  │
    │    → await handler(event, data)           │
    │ 4. UnauthorizedUserError → drop          │
    │    (optionally reply "Access Denied")    │
    │ 5. Unexpected error → log + drop         │
    └─────────────────────────────────────────┘
        │
        ▼
    Handler receives data["auth_context"]
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, Update

from src.domain.exceptions import TenantNotFoundError, UnauthorizedUserError
from src.domain.ports import AuthVerificationPort
from src.domain.schemas import AuthContextDTO
from src.domain.value_objects import TelegramUserId

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Well-known data key (mirrors handlers.py::DATA_KEY_AUTH)
# ---------------------------------------------------------------------------

DATA_KEY_AUTH = "auth_context"
"""Key used to store/retrieve ``AuthContextDTO`` in aiogram ``data``.

Must match the constant in ``src.presentation.telegram.handlers``
so handlers can resolve the auth context injected here.
"""

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

HandlerCallable = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]
"""Type alias for the next handler in the aiogram middleware chain."""


class TenantAuthMiddleware(BaseMiddleware):
    """aiogram outer middleware: authenticate every incoming update.

    Resolves the caller's Telegram user ID → tenant scope via the
    injected ``AuthVerificationPort``, and attaches the resulting
    ``AuthContextDTO`` to ``data["auth_context"]`` so downstream
    handlers never deal with raw authentication.

    Constructor Injection
    ---------------------
    ``auth_port : AuthVerificationPort``
        The inbound port ABC that wraps the auth use case.  The
        Application Composer injects the concrete implementation
        (``VerifyTelegramUserUseCase`` or a stub) at wiring time.
    """

    def __init__(self, auth_port: AuthVerificationPort) -> None:
        self._auth_port = auth_port
        logger.debug(
            "TenantAuthMiddleware initialised with auth_port=%s",
            type(auth_port).__name__,
        )

    async def __call__(  # type: ignore[override]
        self,
        handler: HandlerCallable,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Intercept the update, authenticate, and inject auth context.

        Parameters
        ----------
        handler : HandlerCallable
            The next handler (or inner middleware) in the chain.
        event : TelegramObject
            The incoming Telegram update (Message, CallbackQuery, etc.).
        data : dict[str, Any]
            The aiogram context dictionary.  On success,
            ``data["auth_context"]`` is populated with an
            ``AuthContextDTO``.

        Returns
        -------
        Any
            Whatever the downstream handler returns (typically ``None``
            for message handlers).
        """
        # ------------------------------------------------------------------
        # Step 1: Extract telegram_user_id from the event
        # ------------------------------------------------------------------
        user_id_raw = _extract_user_id(event)
        if user_id_raw is None:
            # Events without a user (e.g., channel posts, some service
            # messages) — pass through unauthenticated.  Handlers that
            # require auth will check data["auth_context"] themselves.
            logger.debug("No user_id extractable from event type=%s — passing through.", type(event).__name__)
            return await handler(event, data)

        # ------------------------------------------------------------------
        # Step 2: Wrap into domain VO and verify
        # ------------------------------------------------------------------
        telegram_user_id = TelegramUserId(user_id_raw)

        try:
            auth_context: AuthContextDTO = await self._auth_port.verify(telegram_user_id)
        except UnauthorizedUserError:
            logger.info(
                "UnauthorizedUserError for telegram_user_id=%s — dropping update.",
                user_id_raw,
            )
            await _maybe_reply_unauthorized(event)
            return  # drop the update
        except TenantNotFoundError:
            logger.warning(
                "TenantNotFoundError for telegram_user_id=%s — whitelist entry "
                "points to non-existent tenant. Treating as unauthorized.",
                user_id_raw,
            )
            await _maybe_reply_unauthorized(event)
            return  # drop the update
        except Exception:
            logger.exception(
                "Unexpected error during auth verification for telegram_user_id=%s.",
                user_id_raw,
            )
            return  # drop — do NOT crash the dispatcher

        # ------------------------------------------------------------------
        # Step 3: Inject auth context and continue the chain
        # ------------------------------------------------------------------
        data[DATA_KEY_AUTH] = auth_context
        logger.debug(
            "Auth context injected for telegram_user_id=%s tenant_id=%s role=%s.",
            user_id_raw,
            auth_context.tenant_id,
            auth_context.user_role,
        )
        return await handler(event, data)


# ============================================================================
# Private helpers
# ============================================================================


def _extract_user_id(event: TelegramObject) -> int | None:
    """Best-effort extraction of the Telegram user ID from an update.

    Covers the common aiogram event types.  Returns ``None`` for
    events that carry no user context (e.g., channel posts).

    Parameters
    ----------
    event : TelegramObject
        The raw event (Message, CallbackQuery, InlineQuery, etc.).

    Returns
    -------
    int | None
        The Telegram user ID, or ``None`` if unreachable.
    """
    # aiogram wraps everything in an Update; try to reach through it
    inner = event
    if isinstance(inner, Update):
        inner = inner.event  # type: ignore[assignment]

    # Message is the most common case
    if isinstance(inner, Message):
        return inner.from_user.id if inner.from_user else None

    # CallbackQuery (inline button presses) — common in future
    if hasattr(inner, "from_user") and inner.from_user is not None:
        return inner.from_user.id  # type: ignore[union-attr]

    # Fallback: scan for a 'from_user' attribute on the raw object
    fu = getattr(inner, "from_user", None)
    if fu is not None:
        return fu.id  # type: ignore[union-attr]

    return None


async def _maybe_reply_unauthorized(event: TelegramObject) -> None:
    """Send an "Access Denied" reply if the event carries a message.

    Only sends a reply for Message-type events.  Callback queries,
    inline queries, and other non-message updates are silently
    dropped — spamming "Access Denied" on every inline button press
    would be poor UX.

    Parameters
    ----------
    event : TelegramObject
        The raw event, possibly wrapping a ``Message``.
    """
    inner = event
    if isinstance(inner, Update):
        inner = inner.event  # type: ignore[assignment]

    if isinstance(inner, Message):
        try:
            await inner.answer(
                "🚫 *Access Denied.*\n\n"
                "Your Telegram account is not whitelisted for this bot.  "
                "Please contact your system administrator if you believe "
                "this is an error.",
                parse_mode="Markdown",
            )
        except Exception:
            logger.exception(
                "Failed to send 'Access Denied' reply to chat_id=%s.",
                inner.chat.id,
            )