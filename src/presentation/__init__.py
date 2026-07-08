"""📱 Presentation Layer — Primary Adapters (Delivery Mechanisms).

This layer contains all **inbound (driving) adapters** that accept
requests from the outside world and invoke the Application layer
(via Use Cases).  It is the outermost ring of the Hexagonal
Architecture.

====================================================================
   IMPORT DISCIPLINE (ENFORCED)
====================================================================

**May import from:**
    - ``src.application``  (use cases, composer, DTOs)
    - ``src.domain``       (ports, exceptions, value objects for
                            type annotations and error handling)

**Must NEVER import from:**
    - ``src.infrastructure``  (adapters are wiring-time concern;
                               the presentation layer receives
                               already-wired use cases from the
                               Application Composer, not raw
                               infrastructure adapters)

**Must NEVER import from:**
    - ``src.config``          (settings are injected via the
                               Composer; presentation receives
                               configured Bot token at startup)

====================================================================
   CURRENT ADAPTERS
====================================================================

* **Telegram Bot** (``src.presentation.telegram``):
    aiogram 3.x handlers, middleware, and bot factory.  The primary
    delivery mechanism for MVP.

====================================================================
   FUTURE ADAPTERS (SWAPPABLE — Domain Unchanged)

* **WhatsApp** — Cloud API webhook handler
* **Discord** — Gateway event handler
* **REST API** — FastAPI router (coexists with Telegram)
* **CLI** — Click-based command-line tool for local testing

Each new adapter calls the SAME ``ProcessInventoryUseCase.process()``
entry point — the Domain and Application layers never change when
a new delivery mechanism is added.  This is the core Hexagonal
Archiecture guarantee.
"""

from __future__ import annotations