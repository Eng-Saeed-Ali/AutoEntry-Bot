"""📱 Telegram Handlers — aiogram 3.x Router for the AutoEntry Bot.

This module implements the I/O boundary between Telegram's API and
the Application layer.  Every handler receives raw Telegram Message
objects, translates them into domain-compatible arguments, delegates
ALL business logic to the use case, and translates the DTO result
back into a Telegram reply.

====================================================================
   HEXAGONAL IMPORT DISCIPLINE (ENFORCED)
====================================================================

**Imports from:**
    - ``aiogram``           — Bot framework types (Router, F, Message,
                              Bot, ContentType, etc.)
    - ``src.application``   — ``ProcessInventoryUseCase`` (the use case
                              the handler delegates to)
    - ``src.domain``        — ports (for type hints), DTOs (for
                              extracting result fields), exceptions
                              (for user-friendly error messages)

**Never imports from:**
    - ``src.infrastructure``  — handlers receive a pre-wired use case;
                                they never touch adapters directly.
    - ``src.config``          — bot token and settings are injected at
                                startup by the bot factory.

====================================================================
   DEPENDENCY PROVIDER PATTERN
====================================================================

The ``ProcessInventoryUseCase`` (and future use cases such as
``VerifyTelegramUserUseCase``) are stored in the aiogram ``data``
dictionary under well-known keys:

    - ``"process_inventory_use_case"`` → ``ProcessInventoryUseCase``
    - ``"auth_context"``               → ``AuthContextDTO`` (populated
                                         by auth middleware)

These are injected at bot startup by the Application Composer
(``src.application.composer.App`` — Task 5.1) and/or by an auth
middleware that runs before every handler.

If the use case key is missing, the handler returns a "⚠ Bot is not
fully configured — please contact the administrator." message rather
than crashing.  This is a safe degrade for early development phases.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import ContentType, Message

from src.application.use_cases.process_inventory import ProcessInventoryUseCase
from src.domain.exceptions import (
    DomainError,
    InvalidSheetSchemaError,
    SheetEmptyError,
    UnauthorizedUserError,
)
from src.domain.schemas import AuthContextDTO, ProcessResultDTO
from src.domain.value_objects import TenantId

if TYPE_CHECKING:
    from aiogram.types import File

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = Router(name="autoentry_main")
"""Main aiogram Router for the AutoEntry Bot.

All Telegram update handlers are registered on this single router.
The bot factory (Task 4.2) includes this router in the Dispatcher
at startup.
"""

# ---------------------------------------------------------------------------
# Well-known aiogram data keys for dependency injection
# ---------------------------------------------------------------------------

DATA_KEY_USE_CASE = "process_inventory_use_case"
"""Key used to store/retrieve ``ProcessInventoryUseCase`` in aiogram ``data``."""

DATA_KEY_AUTH = "auth_context"
"""Key used to store/retrieve ``AuthContextDTO`` (set by auth middleware)."""

# ---------------------------------------------------------------------------
# Helper: Resolve the use case from aiogram data
# ---------------------------------------------------------------------------


def _get_use_case(
    data: dict[str, object],
    chat_id: int,
) -> ProcessInventoryUseCase | None:
    """Retrieve the wired ``ProcessInventoryUseCase`` from aiogram data.

    Returns ``None`` if the use case has not been injected yet, which
    triggers a polite "not configured" reply rather than a crash.

    Parameters
    ----------
    data : dict[str, object]
        The aiogram ``data`` dictionary (available in every handler).
    chat_id : int
        Used only for logging which user triggered the lookup.

    Returns
    -------
    ProcessInventoryUseCase | None
        The wired use case, or ``None`` if not yet injected.
    """
    use_case = data.get(DATA_KEY_USE_CASE)
    if use_case is None:
        logger.warning(
            "ProcessInventoryUseCase not found in aiogram data for chat_id=%s — "
            "bot may not be fully wired.",
            chat_id,
        )
        return None
    if not isinstance(use_case, ProcessInventoryUseCase):
        logger.error(
            "Invalid object type for key %r in aiogram data: expected "
            "ProcessInventoryUseCase, got %s.",
            DATA_KEY_USE_CASE,
            type(use_case).__name__,
        )
        return None
    return use_case  # type: ignore[return-value]


def _get_auth_context(data: dict[str, object]) -> AuthContextDTO | None:
    """Retrieve the ``AuthContextDTO`` from aiogram data.

    Populated by the auth middleware (Task 4.3).  Returns ``None``
    if the middleware has not run or the user is unauthenticated.

    Parameters
    ----------
    data : dict[str, object]
        The aiogram ``data`` dictionary.

    Returns
    -------
    AuthContextDTO | None
        The authenticated user's auth context, or ``None``.
    """
    auth = data.get(DATA_KEY_AUTH)
    if isinstance(auth, AuthContextDTO):
        return auth
    return None


# ---------------------------------------------------------------------------
# Error message helpers — map domain exceptions to user-facing replies
# ---------------------------------------------------------------------------

_EXPECTED_COLUMNS = (
    "SKU",
    "Item_Name",
    "System_Qty",
    "Actual_Qty",
)
_EXPECTED_COLUMNS_TEXT = ", ".join(_EXPECTED_COLUMNS)
_MAX_FILE_SIZE_MB = 5


def _format_domain_error(exc: DomainError) -> str:
    """Translate a domain exception into a user-friendly Telegram reply.

    The raw exception messages contain technical details (column names,
    stack traces).  This function maps each known domain error type to
    a concise, actionable message suitable for non-technical users.

    Parameters
    ----------
    exc : DomainError
        The exception caught from the use case.

    Returns
    -------
    str
        Telegram-safe Markdown reply text (no HTML/MarkdownV2).
    """
    if isinstance(exc, InvalidSheetSchemaError):
        missing = exc.missing_columns
        unexpected = exc.unexpected_columns
        parts = ["❌ *Invalid Excel format.*"]
        parts.append("")
        parts.append(f"Expected columns: `{_EXPECTED_COLUMNS_TEXT}`")
        if missing:
            parts.append(f"Missing columns: `{', '.join(missing)}`")
        if unexpected:
            parts.append(f"Unexpected columns: `{', '.join(unexpected)}`")
        parts.append("")
        parts.append("Please correct the column headers and re-send the file.")
        return "\n".join(parts)

    if isinstance(exc, SheetEmptyError):
        filename_info = f" (file: `{exc.filename}`)" if exc.filename else ""
        return (
            f"❌ *Empty Sheet*{filename_info}\n\n"
            "The file you sent contains no data rows — only a header, "
            "or it is completely blank.  Please add your inventory data "
            "and re-send the file."
        )

    if isinstance(exc, UnauthorizedUserError):
        return "🚫 *Access Denied.*\n\nYou are not authorized to use this bot.  Please contact your administrator."

    # Generic DomainError fallback
    return f"❌ *Processing Failed*\n\n`{exc.message}`"


# ---------------------------------------------------------------------------
# /start Command Handler
# ---------------------------------------------------------------------------


@router.message(CommandStart(deep_link=False))
async def start_command(message: Message) -> None:
    """Handle the /start command — welcome the user and explain usage.

    This handler runs the very first time a user interacts with the
    bot (or whenever they type /start).  It provides:

    1. A friendly welcome message.
    2. An explanation of the expected Excel file format.
    3. A note that the bot is multi-tenant (the user must be whitelisted).

    Zero business logic — pure I/O translation from Telegram Update
    → welcome text.

    Parameters
    ----------
    message : Message
        The incoming Telegram message containing the /start command.
    """
    welcome_lines = [
        "👋 *Welcome to the AutoEntry Bot!*",
        "",
        "I automate inventory reconciliation — you send me an Excel count sheet, "
        "and I compare it against your ERP system, find discrepancies, and deliver "
        "a detailed report.",
        "",
        "📋 *How to use:*",
        "1. Prepare your Excel file (`.xlsx`) with exactly these four columns:",
        f"   `{_EXPECTED_COLUMNS_TEXT}`",
        "2. Send the file to this chat as a document attachment.",
        "3. I will process it and reply with a summary + discrepancy report.",
        "",
        "⚠️ *File Size Limit:* `{max_size_mb} MB`",
        "",
        "🔐 *Access Control:* Only whitelisted Telegram users can use this bot. "
        "If you receive an 'Access Denied' message, contact your system administrator.",
    ]
    welcome_text = "\n".join(welcome_lines).format(max_size_mb=_MAX_FILE_SIZE_MB)

    await message.answer(
        welcome_text,
        parse_mode="Markdown",
    )
    logger.info("Sent /start welcome to chat_id=%s", message.chat.id)


# ---------------------------------------------------------------------------
# Document Message Handler — the core file-processing entry point
# ---------------------------------------------------------------------------


@router.message(F.content_type == ContentType.DOCUMENT)
async def document_handler(message: Message, bot: Bot, data: dict[str, object]) -> None:
    """Handle an incoming document (Excel file) upload from a Telegram user.

    This is the **primary inbound adapter** for the inventory processing
    pipeline.  It performs I/O-only tasks:
        1. Validate the file type and size.
        2. Download raw bytes from Telegram.
        3. Delegate ALL business logic to ``ProcessInventoryUseCase.process()``.
        4. Translate the ``ProcessResultDTO`` → user-facing reply.

    The handler NEVER:
        - Parses Excel bytes (that's ``FileParserPort`` behind the use case).
        - Reconciles quantities (that's the Domain layer).
        - Writes to a database (that's the repository adapter).
        - Formats the discrepancy report (that's the exporter adapter).

    Hexagonal purity: this function only knows about Telegram I/O
    types (Message, Bot, File) and domain DTO types (ProcessResultDTO).
    It does NOT import infrastructure code.

    Parameters
    ----------
    message : Message
        The incoming Telegram message with a document attachment.
    bot : Bot
        The aiogram Bot instance (injected by aiogram dispatcher).
    data : dict[str, object]
        The aiogram data dictionary carrying injected dependencies:
            - ``"process_inventory_use_case"`` → ``ProcessInventoryUseCase``
            - ``"auth_context"``               → ``AuthContextDTO``
    """
    chat_id = message.chat.id
    document = message.document

    # Sanity check: document field must exist (guaranteed by F filter, but defensive)
    if document is None:
        logger.warning("Received document message with no document object — chat_id=%s", chat_id)
        await message.answer("⚠️ No document found in your message.  Please attach an Excel file.")
        return

    filename = document.file_name or "unknown.xlsx"
    file_id = document.file_id
    file_size = document.file_size or 0

    logger.info(
        "Document received: filename=%r file_id=%s file_size=%d chat_id=%s",
        filename,
        file_id,
        file_size,
        chat_id,
    )

    # ------------------------------------------------------------------
    # Guard 1: File type validation (must be .xlsx)
    # ------------------------------------------------------------------
    mime_type = document.mime_type or ""
    if mime_type != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        await message.answer(
            "⚠️ *Wrong File Type*\n\n"
            "I only accept `.xlsx` Excel files.  Please save your "
            "sheet in Excel format and re-send it.",
            parse_mode="Markdown",
        )
        logger.info("Rejected non-xlsx file mime_type=%r from chat_id=%s", mime_type, chat_id)
        return

    # ------------------------------------------------------------------
    # Guard 2: File size validation
    # ------------------------------------------------------------------
    max_bytes = _MAX_FILE_SIZE_MB * 1024 * 1024
    if file_size > max_bytes:
        await message.answer(
            f"⚠️ *File Too Large*\n\n"
            f"Your file is `{file_size / 1024 / 1024:.1f} MB`.  "
            f"The maximum allowed size is `{_MAX_FILE_SIZE_MB} MB`.  "
            f"Please split your data into smaller files.",
            parse_mode="Markdown",
        )
        logger.info("Rejected oversized file size=%d from chat_id=%s", file_size, chat_id)
        return

    # ------------------------------------------------------------------
    # Guard 3: Resolve the wired use case
    # ------------------------------------------------------------------
    use_case = _get_use_case(data, chat_id)
    if use_case is None:
        await message.answer(
            "⚠️ *Bot Not Fully Configured*\n\n"
            "The processing engine is not wired yet.  Please contact "
            "the administrator to complete the bot setup.",
            parse_mode="Markdown",
        )
        return

    # ------------------------------------------------------------------
    # Guard 4: Resolve auth context
    # ------------------------------------------------------------------
    auth_ctx = _get_auth_context(data)
    if auth_ctx is None:
        await message.answer(
            "⚠️ *Authentication Required*\n\n"
            "Your identity could not be verified.  The auth middleware "
            "may not be active yet, or your Telegram account is not "
            "whitelisted.",
            parse_mode="Markdown",
        )
        logger.warning("No auth context for chat_id=%s — middleware may not be active.", chat_id)
        return

    tenant_id: TenantId = auth_ctx.tenant_id

    # ------------------------------------------------------------------
    # Step 1: Acknowledge receipt (user gets immediate feedback)
    # ------------------------------------------------------------------
    processing_msg = await message.answer(
        "⏳ *Processing your file...*\n\n"
        f"File: `{filename}`\n"
        f"Size: `{file_size / 1024:.1f} KB`\n\n"
        "I'm extracting the data and reconciling it — this may take "
        "a few seconds.",
        parse_mode="Markdown",
    )
    logger.info("Sent processing acknowledgement to chat_id=%s", chat_id)

    # ------------------------------------------------------------------
    # Step 2: Download file bytes from Telegram's servers
    # ------------------------------------------------------------------
    try:
        telegram_file: File = await bot.get_file(file_id)
        file_bytes: bytes = await bot.download_file(
            telegram_file.file_path,  # type: ignore[arg-type]
        )
        # download_file returns a BytesIO in aiogram 3.x — get raw bytes
        if hasattr(file_bytes, "read"):
            file_bytes = file_bytes.read()  # type: ignore[union-attr]
    except Exception as exc:
        logger.error("Failed to download file %s from Telegram: %s", file_id, exc)
        await processing_msg.edit_text(
            "❌ *Download Failed*\n\n"
            "I could not download your file from Telegram's servers.  "
            "Please try re-sending the file.",
            parse_mode="Markdown",
        )
        return

    # ------------------------------------------------------------------
    # Step 3: Delegate to the Application Layer
    # ------------------------------------------------------------------
    try:
        result: ProcessResultDTO = await use_case.process(
            file_bytes=file_bytes,
            filename=filename,
            tenant_id=tenant_id,
            chat_id=chat_id,
        )
    except DomainError as exc:
        # Domain error caught as exception (defensive — use case should
        # return a failed DTO, but if it propagates, we handle it).
        logger.warning("DomainError propagated from use case: %s", exc)
        await processing_msg.edit_text(
            _format_domain_error(exc),
            parse_mode="Markdown",
        )
        return
    except Exception as exc:
        # Unexpected infrastructure failure (network, etc.)
        logger.error("Unexpected error during processing for chat_id=%s: %s", chat_id, exc)
        await processing_msg.edit_text(
            "❌ *Unexpected Error*\n\n"
            "An internal error occurred while processing your file.  "
            "The administrator has been notified.  Please try again later.",
            parse_mode="Markdown",
        )
        return

    # ------------------------------------------------------------------
    # Step 4: Translate DTO → user-facing reply
    # ------------------------------------------------------------------
    if result.success:
        success_lines = [
            "✅ *Processing Complete!*",
            "",
            result.summary,
            "",
            f"⏱ Duration: `{result.duration_ms / 1000:.2f} seconds`",
        ]
        if result.snapshot_id:
            success_lines.append(f"🔖 Snapshot ID: `{result.snapshot_id}`")
        await processing_msg.edit_text(
            "\n".join(success_lines),
            parse_mode="Markdown",
        )
        logger.info(
            "Successfully processed %s for chat_id=%s [snapshot=%s].",
            filename,
            chat_id,
            result.snapshot_id,
        )
    else:
        # Use case returned a failed DTO with a user-readable summary
        await processing_msg.edit_text(
            result.summary,
            parse_mode="Markdown",
        )
        logger.warning(
            "Processing failed for %s chat_id=%s: %s",
            filename,
            chat_id,
            result.summary,
        )