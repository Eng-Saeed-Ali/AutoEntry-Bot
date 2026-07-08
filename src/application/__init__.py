"""🎯 Application Layer — Use Cases & DI Composer.

The Application layer sits between the Presentation and Domain layers
in the Hexagonal Architecture.  It orchestrates domain entities and
infrastructure ports but contains **no core business rules** (those
belong to the Domain layer).

Responsibilities:
    - Defining use cases (``ProcessInventoryUseCase``,
      ``VerifyTelegramUserUseCase``) that orchestrate the full
      workflow by calling domain entities and outbound ports.
    - The ``App`` composer (``composer.py``) that wires all ports
      to their infrastructure adapter implementations via **explicit
      manual Constructor Injection** — no magic DI framework.
    - Returning only ``ProcessResultDTO`` and ``AuthContextDTO``
      to the Presentation layer — never leaking domain entities
      or infrastructure internals.

Import Discipline (Hexagonal Iron Law):
    This layer MAY import from:
        - ``src.domain.*``  (ports, entities, VOs, DTOs, exceptions)
    This layer MUST NOT import from:
        - ``src.infrastructure.*``  (adapters live below)
        - ``src.presentation.*``  (delivery mechanism lives above)
"""
