"""AutoEntry Bot — Application Entrypoint.

This module is the **startup conductor**.  It loads configuration,
initialises structured logging, creates the Telegram bot and aiogram
Dispatcher, calls the Application Composer to wire all dependencies,
and starts long-polling for updates.

Lifecycle
---------
1. ``setup_logging()``        — structlog config (idempotent).
2. ``Settings()``              — reads env vars / .env (fails fast).
3. ``Bot(token=...)``         — aiogram Bot instance.
4. ``Dispatcher()``           — aiogram Dispatcher instance.
5. ``await build_application_state(bot)`` — composer wires deps.
6. Register middleware + router + inject DI keys into dispatcher.
7. ``await dp.start_polling(bot)`` — event loop begins.

Shutdown
--------
- On KeyboardInterrupt or signal, ``shutdown_application_state(deps)``
  is called to dispose the async engine and any other resources.
"""

from __future__ import annotations

import asyncio
import sys

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from src.config.settings import settings
from src.infrastructure.logging.setup import setup_logging

# -- Application Composer (Phase 5 wiring) -------------------------------
from src.application.composer import build_application_state, shutdown_application_state

# -- Presentation layer (Telegram) ---------------------------------------
from src.presentation.telegram import telegram_router
from src.presentation.telegram.handlers import DATA_KEY_USE_CASE
from src.presentation.telegram.middleware import TenantAuthMiddleware

# ---------------------------------------------------------------------------
# Logger (obtained AFTER setup_logging)
# ---------------------------------------------------------------------------
logger = structlog.get_logger(__name__)


# ============================================================================
# Startup helpers
# ============================================================================


def _create_bot() -> Bot:
    """Create and return the aiogram Bot from Settings.

    Returns
    -------
    Bot
        Pre-configured with the bot token (SecretStr unwrapped).
    """
    token = settings.bot_token.get_secret_value()
    return Bot(token=token, default=DefaultBotProperties(parse_mode="Markdown"))


# ============================================================================
# Main entrypoint
# ============================================================================


async def main() -> None:
    """Application entrypoint: configure, wire, register, and start polling.

    This is the only function that runs at startup.  It orchestrates
    the entire dependency graph and hands control to aiogram's
    long-polling loop.
    """
    # ------------------------------------------------------------------
    # Step 1: Structured Logging
    # ------------------------------------------------------------------
    setup_logging()
    logger.info("autoentry_bot.starting", log_level=settings.log_level)

    # ------------------------------------------------------------------
    # Step 2: Create Bot & Dispatcher
    # ------------------------------------------------------------------
    bot = _create_bot()
    dp = Dispatcher()
    logger.info("aiogram_bot_dispatcher.created")

    # ------------------------------------------------------------------
    # Step 3: Compose Dependencies
    # ------------------------------------------------------------------
    try:
        deps = await build_application_state(bot)
    except Exception:
        logger.exception("application_state.build_failed")
        await bot.session.close()
        sys.exit(1)

    engine_created = "db_engine" in deps
    use_case_wired = "process_inventory_use_case" in deps
    logger.info(
        "application_state.built",
        engine_created=engine_created,
        use_case_wired=use_case_wired,
    )

    # ------------------------------------------------------------------
    # Step 4: Register Middleware
    # ------------------------------------------------------------------
    auth_port = deps["auth_port"]
    dp.update.middleware(TenantAuthMiddleware(auth_port=auth_port))
    logger.info("middleware.registered", middleware="TenantAuthMiddleware")

    # ------------------------------------------------------------------
    # Step 5: Inject Use Case into Dispatcher Workflow Data
    # ------------------------------------------------------------------
    use_case = deps["process_inventory_use_case"]
    dp[DATA_KEY_USE_CASE] = use_case
    logger.info("dispatcher.data.injected", key=DATA_KEY_USE_CASE)

    # ------------------------------------------------------------------
    # Step 6: Include the Telegram Router
    # ------------------------------------------------------------------
    dp.include_router(telegram_router)
    logger.info("router.included", router_name=telegram_router.name)

    # ------------------------------------------------------------------
    # Step 7: Start Polling
    # ------------------------------------------------------------------
    logger.info("dispatcher.start_polling.begin")
    try:
        await dp.start_polling(bot)
    except asyncio.CancelledError:
        logger.info("dispatcher.polling.cancelled")
    except Exception:
        logger.exception("dispatcher.polling.crashed")
    finally:
        # ------------------------------------------------------------------
        # Step 8: Graceful Shutdown
        # ------------------------------------------------------------------
        logger.info("autoentry_bot.shutting_down")
        await shutdown_application_state(deps)
        await bot.session.close()
        logger.info("autoentry_bot.stopped")


# ============================================================================
# Runner
# ============================================================================

if __name__ == "__main__":
    asyncio.run(main())