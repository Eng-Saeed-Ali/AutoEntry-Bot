# 🏗️ ARCHITECTURE.md — AutoEntry Bot

> **Enterprise Workflow Automation Bot for Inventory & Sales Data Processing**
> Hexagonal Architecture | Multi-Tenant | Async-First | White-Label Ready

---

## Table of Contents
1. [System Overview](#1-system-overview)
2. [Hexagonal Architecture Diagram](#2-hexagonal-architecture-diagram)
3. [Directory Structure](#3-directory-structure)
4. [Data Flow Execution](#4-data-flow-execution)
5. [Micro-Task Breakdown](#5-micro-task-breakdown)
6. [Tech Stack Summary](#6-tech-stack-summary)

---

## 1. System Overview

### Business Value
Store managers upload daily/weekly inventory count Excel sheets to a Telegram bot. The bot instantly:
1. Validates the sheet against the expected schema.
2. Compares `System_Qty` (ERP expected stock) vs `Actual_Qty` (physically counted stock).
3. Writes the inventory snapshot to the database.
4. Returns a concise Markdown summary + an attached discrepancy Excel report.

The product is designed as a **white-label, multi-tenant SaaS** — multiple businesses (tenants) use the same bot, completely isolated by `store_id`/`tenant_id`. Authentication is via a strict Telegram user whitelist.

### Core Principles
- **Hexagonal (Ports & Adapters):** Domain logic is 100% pure Python. Telegram, PostgreSQL, Excel parsing are all replaceable adapters.
- **Async-First:** Everything from Telegram handlers to DB queries uses `async/await`. No blocking I/O.
- **Zero-Cost Operations:** Minimal Alpine Docker images, fast `polars` processing, connection pooling.
- **Strict Separation of Concerns:** Each layer knows nothing about the layer above it.

---

## 2. Hexagonal Architecture Diagram

```mermaid
graph TB
    subgraph "DELIVERY MECHANISM (Primary Adapters)"
        TG[Telegram Bot<br/>aiogram 3.x]
        FUTURE1[WhatsApp Adapter<br/>(Future)]
        FUTURE2[Discord Adapter<br/>(Future)]
        FUTURE3[REST API<br/>(Future)]
    end

    subgraph "CORE DOMAIN (Pure Python — No Frameworks)"
        direction TB
        IN[📥 Inbound Ports<br/>FileProcessingPort<br/>AuthVerificationPort]
        DOMAIN_DOMAIN[🏭 Domain Model<br/>Tenant<br/>InventoryItem<br/>DiscrepancyReport<br/>ExcelTemplateSchema]
        DOMAIN_SERVICE[⚙️ Domain Services<br/>InventoryReconciliationService<br/>ReportGenerationService<br/>SchemaValidationService]
        OUT[📤 Outbound Ports<br/>Trait/ABC<br/>FileParserPort<br/>InventoryRepositoryPort<br/>TenantRepositoryPort<br/>ReportExporterPort<br/>NotificationPort]
    end

    subgraph "INFRASTRUCTURE (Secondary Adapters)"
        EXCEL[ExcelParser<br/>openpyxl + polars<br/>Implements FileParserPort]
        DB[PostgreSQL Adapter<br/>SQLAlchemy 2.0 Async<br/>Implements InventoryRepositoryPort<br/>Implements TenantRepositoryPort]
        EXPORT[ExcelExporter<br/>openpyxl<br/>Implements ReportExporterPort]
        NOTIFY[TelegramNotifier<br/>Implements NotificationPort]
        AUTH[WhitelistAuth<br/>Implements AuthVerificationPort provisionally]
    end

    TG -->|"calls"| IN
    FUTURE1 -.->|"swappable"| IN
    FUTURE2 -.->|"swappable"| IN
    FUTURE3 -.->|"swappable"| IN

    IN --> DOMAIN_SERVICE
    DOMAIN_SERVICE --> DOMAIN_DOMAIN
    DOMAIN_SERVICE --> OUT

    OUT -.->|"implemented by"| EXCEL
    OUT -.->|"implemented by"| DB
    OUT -.->|"implemented by"| EXPORT
    OUT -.->|"implemented by"| NOTIFY

    IN -.->|"implemented provisionally"| AUTH

    subgraph "CONFIGURATION & WIRING"
        APP[Application Composer<br/>Wires ports to adapters<br/>Manual Constructor DI]
        CONFIG[Settings<br/>pydantic-settings<br/>env vars / .env]
    end

    APP --> IN
    APP --> OUT
    CONFIG --> APP
```

### Layer Dependency Rule (The Iron Law)
```
Presentation (Telegram) → Application (Composer/Use Cases) → Domain (Pure Logic) ← Infrastructure (Adapters)
```
- **Outer layers** depend on **inner layers** (never the reverse).
- **Domain** depends on **nothing** except Python stdlib + `pydantic` (for data structures).
- **Ports** are `Protocol` classes or `ABC` interfaces defined in the Domain layer.
- **Adapters** implement ports and live in Infrastructure.

---

## 3. Directory Structure

```
AutoEntry_bot/
├── .env.example                    # Template for environment variables
├── .gitignore
├── docker-compose.yml              # PostgreSQL + Bot + (optional Redis)
├── Dockerfile                      # Multi-stage Alpine Python build
├── Makefile                        # Convenience: make dev, make test, make migrate
├── pyproject.toml                  # PEP 621: dependencies, tool configs
├── alembic.ini                     # Alembic config (points to migrations/)
├── ARCHITECTURE.md                 # This file
│
├── migrations/                     # Alembic auto-generated DB migrations
│   ├── env.py                      # Async alembic env configuration
│   ├── script.py.mako              # Migration template
│   └── versions/                   # Versioned migration scripts
│       └── 001_initial_schema.py
│
├── src/
│   ├── __init__.py
│   │
│   ├── config/                     # ⚙️ Centralized Configuration
│   │   ├── __init__.py
│   │   └── settings.py             # pydantic-settings: DB URL, Bot Token, etc.
│   │
│   ├── domain/                     # 🧠 CORE DOMAIN (Pure Python — NO frameworks)
│   │   ├── __init__.py
│   │   ├── models.py               # Domain entities: Tenant, InventoryItem, DiscrepancyReport
│   │   ├── value_objects.py        # SKU, Quantity, StoreId, TelegramUserId, etc.
│   │   ├── schemas.py              # pydantic DTOs: ExcelRowSchema, ReportSchema
│   │   ├── services.py             # Pure domain services: InventoryReconciliationService
│   │   ├── exceptions.py           # Domain-specific exceptions
│   │   └── ports.py                # 📥📤 ALL Port interfaces (ABC/Protocol)
│   │       # Contains:
│   │       #   - Inbound: FileProcessingPort, AuthVerificationPort
│   │       #   - Outbound: FileParserPort, InventoryRepositoryPort,
│   │       #               TenantRepositoryPort, ReportExporterPort,
│   │       #               NotificationPort
│   │
│   ├── application/                # 🎯 Application Layer (Use Cases + Composer)
│   │   ├── __init__.py
│   │   ├── composer.py             # App: wires ports → adapters (Manual DI)
│   │   ├── use_cases/
│   │   │   ├── __init__.py
│   │   │   ├── process_inventory.py     # Orchestrates: parse→validate→reconcile→persist→report
│   │   │   └── verify_telegram_user.py  # Whitelist check + tenant resolution
│   │   └── dto.py                  # Application-level DTOs (input/output boundaries)
│   │
│   ├── infrastructure/            # 🔌 SECONDARY ADAPTERS (Implements Outbound Ports)
│   │   ├── __init__.py
│   │   ├── persistence/
│   │   │   ├── __init__.py
│   │   │   ├── models.py           # SQLAlchemy 2.0 ORM models (maps to domain)
│   │   │   ├── repository.py       # Implements InventoryRepositoryPort + TenantRepositoryPort
│   │   │   └── unit_of_work.py     # Async Unit of Work pattern (optional, for transactions)
│   │   ├── excel_parser/
│   │   │   ├── __init__.py
│   │   │   ├── parser.py           # Implements FileParserPort (openpyxl extract → polars transform)
│   │   │   └── validator.py        # pandera DataFrame schema validation
│   │   ├── excel_exporter/
│   │   │   ├── __init__.py
│   │   │   └── exporter.py         # Implements ReportExporterPort (generates discrepancy .xlsx)
│   │   ├── auth/
│   │   │   ├── __init__.py
│   │   │   └── whitelist.py        # Implements AuthVerificationPort (TG user_id → tenant_id lookup)
│   │   └── logging/
│   │       ├── __init__.py
│   │       └── setup.py            # structlog configuration (JSON to stdout)
│   │
│   └── presentation/              # 📱 PRIMARY ADAPTERS (Delivery Mechanism)
│       ├── __init__.py
│       └── telegram/
│           ├── __init__.py
│           ├── bot.py              # aiogram Dispatcher + Bot instance creation
│           ├── handlers/
│           │   ├── __init__.py
│           │   ├── start.py        # /start command handler (registration flow)
│           │   └── file_upload.py  # Document message handler (Excel processing)
│           ├── middleware/
│           │   ├── __init__.py
│           │   └── auth.py         # aiogram middleware: rejects unauthorized users
│           └── keyboards.py        # Optional inline keyboards for future use
│
├── tests/                          # 🧪 Test Suite (Mirrors src/ structure)
│   ├── __init__.py
│   ├── conftest.py                 # pytest fixtures: test DB, mock adapters
│   ├── domain/
│   │   ├── test_services.py        # Pure unit tests (no I/O, no mocks needed)
│   │   └── test_models.py          # Entity & value object tests
│   ├── application/
│   │   └── test_use_cases.py       # Use case tests with fake adapter implementations
│   ├── infrastructure/
│   │   ├── test_excel_parser.py
│   │   ├── test_repository.py
│   │   └── test_excel_exporter.py
│   └── integration/
│       └── test_full_flow.py       # End-to-end: real DB + real parser (Docker compose)
│
└── fixtures/                       # Test fixtures: sample Excel files (valid + invalid)
    ├── valid_inventory.xlsx
    ├── missing_columns.xlsx
    └── multi_tenant_sample.xlsx
```

### Import Discipline (Enforced by Layer)
| Layer | May Import From | Must NOT Import From |
|--------|----------------|----------------------|
| **`domain/`** | `pydantic`, Python stdlib | `sqlalchemy`, `aiogram`, `openpyxl`, `polars`, `structlog` |
| **`application/`** | `domain/` | `infrastructure/`, `presentation/` |
| **`infrastructure/`** | `domain/` (ports only) | `presentation/` |
| **`presentation/`** | `application/` (composer), `domain/` (ports only) | `infrastructure/` directly |

> **Linter Enforcement Plan:** Use `ruff` with per-directory `__init__.py` docstring markers. A custom `import-linter` contract will validate these rules in CI.

---

## 4. Data Flow Execution

### Step-by-Step: From Telegram Excel Upload to Bot Reply

```
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 1: INBOUND REQUEST                                                │
│                                                                         │
│ Store manager sends Excel file to Telegram bot.                        │
│ aiogram middleware intercepts the message.                             │
│                                                                         │
│  ┌──────────────┐                                                      │
│  │ Auth         │  Checks: Is telegram_user_id in whitelist table?    │
│  │ Middleware   │  NO  → Silent ignore or "Access Denied" reply       │
│  │              │  YES → Attach {tenant_id, store_id} to handler ctx   │
│  └──────────────┘                                                      │
│                                                                         │
│ Handler receives: (file_bytes: bytes, filename: str, tenant_id: str)   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 2: APPLICATION ORCHESTRATION                                      │
│                                                                         │
│ `ProcessInventoryUseCase.execute(                                       │
│     file_bytes=...,                                                     │
│     filename=...,                                                       │
│     tenant_id=...                                                       │
│ )`                                                                       │
│ Called from presentation layer. The use case receives ALL adapter      │
│ ports via constructor injection.                                         │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 3: FILE PARSING (Excel → polars DataFrame)                        │
│                                                                         │
│ Adapter: `ExcelParser.parse(file_bytes)`                                │
│  a. `openpyxl` loads workbook into memory (handles merged cells).      │
│  b. First sheet extracted into list[dict] (raw rows).                  │
│  c. `polars.DataFrame(raw_rows)` constructs DataFrame.                 │
│  d. `pandera` schema check: columns [SKU, Item_Name, System_Qty,       │
│     Actual_Qty] exist & have correct dtypes (str, str, int, int).      │
│  e. Returns `ParsedInventorySheet(tenant_id, df, parse_timestamp)`     │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 4: DOMAIN RECONCILIATION (Pure Logic — No I/O)                    │
│                                                                         │
│ `InventoryReconciliationService.reconcile(parsed_df)`                   │
│                                                                         │
│ For each row:                                                           │
│   diff = Actual_Qty - System_Qty                                        │
│                                                                         │
│   if diff == 0:     → MATCHED                                          │
│   if diff < 0:      → SHORTAGE (missing stock! ⚠️)                    │
│   if diff > 0:      → SURPLUS (overstock anomaly ⚠️)                  │
│   if System_Qty == 0 and Actual_Qty > 0: → UNTRACKED_ITEM             │
│   if System_Qty > 0 and Actual_Qty == 0: → MISSING_ENTIRELY           │
│                                                                         │
│ Returns: `ReconciliationResult(                                         │
│     matched_items: list[InventoryItem],                                 │
│     discrepancies: list[DiscrepancyItem],                               │
│     summary: ReconciliationSummary                                     │
│ )`                                                                       │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 5: PERSISTENCE (Write to PostgreSQL)                              │
│                                                                         │
│ `InventoryRepository.save_snapshot(                                     │
│     tenant_id,                                                          │
│     reconciliation_result,                                              │
│     parse_timestamp                                                     │
│ )`                                                                       │
│                                                                         │
│ SQLAlchemy 2.0 Async:                                                   │
│  a. BEGIN transaction                                                  │
│  b. INSERT INTO inventory_snapshots (meta row)                         │
│  c. BULK INSERT INTO inventory_items (all matched + discrepant rows)   │
│  d. BULK INSERT INTO discrepancy_items (only anomalies)                │
│  e. COMMIT                                                             │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 6: REPORT GENERATION                                              │
│                                                                         │
│ `ReportExporterPort.export(result)`                                     │
│  a. Build a Markdown summary string:                                    │
│     "📊 *Inventory Reconciliation Report*                               │
│      🏪 Store: Store_42 | 📅 2026-07-07                                │
│                                                                          │
│      ✅ Matched: 127 items                                              │
│      ⚠️ Shortages: 3 items (SKU-001, SKU-045, SKU-112)                 │
│      🔄 Surplus: 1 item (SKU-089)                                       │
│      ❓ Untracked: 2 new items found                                    │
│                                                                          │
│      ⚠️ *Action Required:* 3 shortages need investigation."           │
│                                                                         │
│  b. Generate discrepancy Excel via openpyxl:                            │
│     Columns: SKU, Item_Name, System_Qty, Actual_Qty, Diff, Status      │
│     Rows: Only discrepancies. Freeze header row. Auto-fit columns.     │
│     Returns `ReportResult(markdown_text, excel_bytes, filename)`        │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 7: TELEGRAM RESPONSE (Notification Port)                          │
│                                                                         │
│ `NotificationPort.send_report(chat_id, report_result)`                  │
│ Adapter: `TelegramNotifier` (thin wrapper around aiogram Bot)          │
│  a. Send markdown message: `await bot.send_message(..., parse_mode)`   │
│  b. Send Excel attachment: `await bot.send_document(..., BufferedIO)`   │
│  c. If both succeed → log success via structlog                        │
│  d. If fails → log error, optionally send fallback error message       │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                           ┌──────────────────┐
                           │   🎉 COMPLETE    │
                           │ Manager receives │
                           │ summary + report │
                           └──────────────────┘
```

### Async Concurrency Notes
- Steps 3 (parse) and 4 (reconcile) are **CPU-bound** but run in minutes (<500ms for typical sheets). Using `polars` (Rust-backed, multithreaded) keeps this fast enough that no background task queue is needed for MVP.
- DB writes use `asyncpg` (SQLAlchemy's async driver) — never blocks the event loop.
- The entire request lifecycle runs in a single `aiogram` handler coroutine. If processing time exceeds Telegram's ~10s ack window, we add `arq` later via a new adapter **without changing domain code**.

---

## 5. Micro-Task Breakdown

Each micro-task is **self-contained, sequential, and independently testable**. Estimated effort per task: 15-60 minutes. Every task produces one or more deliverable files.

### Phase 0: Project Scaffolding & Configuration
| # | Task | Deliverables | Description |
|---|------|-------------|-------------|
| 0.1 | **Initialize project skeleton** | `pyproject.toml`, `.gitignore`, `.env.example`, `Makefile` | Create PEP 621-compliant `pyproject.toml` with all dependencies pinned. Set up `.env.example` with `BOT_TOKEN`, `DATABASE_URL`, `LOG_LEVEL`. Create `Makefile` with targets: `dev`, `lint`, `test`, `migrate-up`, `migrate-down`. |
| 0.2 | **Create config module** | `src/config/settings.py`, `src/config/__init__.py` | Implement `Settings` class using `pydantic-settings`. Loads from `.env` + environment variables. Fields: `bot_token`, `database_url`, `log_level`, `telegram_allowed_updates`. |
| 0.3 | **Set up structlog** | `src/infrastructure/logging/setup.py`, `src/infrastructure/logging/__init__.py` | Configure `structlog` with JSON renderer for stdout, key-value context binding, and async-compatible processor chain. Provide a `get_logger()` factory. |
| 0.4 | **Docker scaffolding** | `Dockerfile`, `docker-compose.yml` | Multi-stage Alpine `Dockerfile`: builder stage installs deps, runtime stage copies venv + app code. `docker-compose.yml` defines `postgres` (postgres:16-alpine) + `bot` services with healthchecks and named volumes. |

### Phase 1: Core Domain (Pure Python — Zero Framework Dependencies)
| # | Task | Deliverables | Description |
|---|------|-------------|-------------|
| 1.1 | **Define domain value objects** | `src/domain/value_objects.py` | Create `SKU`, `Quantity`, `StoreId`, `TenantId`, `TelegramUserId`, `ItemName` as `pydantic` constrained types (e.g., `SKU = Annotated[str, StringConstraints(min_length=3, max_length=50)]`). Immutable, self-validating. |
| 1.2 | **Define domain entities** | `src/domain/models.py` | Create dataclasses: `Tenant(id, name, created_at)`, `InventoryItem(sku, item_name, system_qty, actual_qty, tenant_id, snapshot_id)`, `DiscrepancyItem(inventory_item, diff, status: DiscrepancyStatus enum)`, `InventorySnapshot(id, tenant_id, store_id, parsed_at)`, `TelegramUser(user_id, tenant_id, is_active, role)`. |
| 1.3 | **Define domain exceptions** | `src/domain/exceptions.py` | `DomainError` base, `InvalidSheetSchemaError`, `SheetEmptyError`, `TenantNotFoundError`, `UnauthorizedUserError`, `ReconciliationError`. Each carries contextual fields. |
| 1.4 | **Define ALL port interfaces** | `src/domain/ports.py` | Every port as a `Protocol` or `ABC`: `FileParserPort.parse(bytes)` → `ParsedSheetDTO`, `InventoryRepositoryPort.save_snapshot(...)`, `TenantRepositoryPort.get_by_telegram_id(...)`, `ReportExporterPort.export(...)` → `ReportResultDTO`, `NotificationPort.send_report(...)`, `AuthVerificationPort.verify(...)` → `AuthContextDTO`. All methods are `async def`. |
| 1.5 | **Define domain schemas (DTOs)** | `src/domain/schemas.py` | `pydantic` v2 models: `ExcelRowSchema` (SKU, Item_Name, System_Qty, Actual_Qty), `ParsedSheetDTO`, `ReconciliationResultDTO`, `ReconciliationSummaryDTO`, `ReportResultDTO`, `AuthContextDTO`. These are the *lingua franca* crossing port boundaries. |
| 1.6 | **Implement InventoryReconciliationService** | `src/domain/services.py` | Pure function (or stateless class). Input: `ParsedSheetDTO` (with DataFrame). Output: `ReconciliationResultDTO`. Walks each row, computes diff, classifies into Matched/Shortage/Surplus/Untracked/Missing. **No I/O, no framework imports.** |

### Phase 2: Infrastructure — Secondary Adapters
| # | Task | Deliverables | Description |
|---|------|-------------|-------------|
| 2.1 | **Implement SQLAlchemy ORM models** | `src/infrastructure/persistence/models.py` | Declarative `Base`, tables: `tenants`, `telegram_users`, `inventory_snapshots`, `inventory_items`, `discrepancy_items`. Columns include `tenant_id` on every data table. Use `UUID` PKs, `DateTime(timezone=True)`, proper indexes on `(tenant_id, sku)` and `(telegram_user_id)`. |
| 2.2 | **Configure Alembic + generate 001 migration** | `alembic.ini`, `migrations/env.py`, `migrations/versions/001_initial_schema.py` | Async `env.py` using `sqlalchemy.ext.asyncio.create_async_engine`. Auto-generate initial migration from ORM models. Verify with `make migrate-up` against a local Docker PostgreSQL. |
| 2.3 | **Implement TenantRepository + InventoryRepository** | `src/infrastructure/persistence/repository.py` | Async methods implementing `TenantRepositoryPort` and `InventoryRepositoryPort`. Uses `AsyncSession` (injected). `get_by_telegram_id()` joins `telegram_users` + `tenants`. `save_snapshot()` does bulk INSERT via `insert()` (SQLAlchemy Core) for performance. |
| 2.4 | **Implement ExcelParser (openpyxl + polars + pandera)** | `src/infrastructure/excel_parser/parser.py`, `src/infrastructure/excel_parser/validator.py` | `ExcelParser` implements `FileParserPort`. Stage 1: `openpyxl.load_workbook(BytesIO(bytes))`, extract rows → `list[dict]`. Stage 2: `polars.DataFrame(rows)`. Stage 3: `pandera.DataFrameSchema` validates columns + types. Returns `ParsedSheetDTO`. |
| 2.5 | **Implement ExcelExporter (discrepancy report)** | `src/infrastructure/excel_exporter/exporter.py` | `ExcelExporter` implements `ReportExporterPort`. Takes `ReconciliationResultDTO`, builds an `openpyxl.Workbook` with one sheet: SKU, Item_Name, System_Qty, Actual_Qty, Difference, Status. Auto-fits column widths. Freezes header row. Returns `ReportResultDTO(markdown_text, excel_bytes, suggested_filename)`. |
| 2.6 | **Implement TelegramNotifier** | `src/presentation/telegram/notifier.py` (lives in presentation since it uses aiogram) | Implements `NotificationPort`. Thin wrapper: receives `aiogram.Bot` instance via DI. Sends Markdown text via `bot.send_message()` + Excel attachment via `bot.send_document(BufferedIOBase)`. Logs success/failure via structlog. |
| 2.7 | **Implement WhitelistAuth** | `src/infrastructure/auth/whitelist.py` | Implements `AuthVerificationPort`. Queries `TenantRepositoryPort` by `telegram_user_id`. Returns `AuthContextDTO(tenant_id, store_id, user_role)` or raises `UnauthorizedUserError`. |

### Phase 3: Application Layer — Use Cases & DI Composer
| # | Task | Deliverables | Description |
|---|------|-------------|-------------|
| 3.1 | **Implement VerifyTelegramUser use case** | `src/application/use_cases/verify_telegram_user.py` | Thin orchestrator: receives `telegram_user_id`, calls `auth_port.verify()`, returns `AuthContextDTO` or raises. Adds structlog context binding (tenant_id, user_id). |
| 3.2 | **Implement ProcessInventory use case** | `src/application/use_cases/process_inventory.py` | Full orchestration: (1) parse via `file_parser_port`, (2) reconcile via `reconciliation_service`, (3) persist via `inventory_repo_port`, (4) export via `report_exporter_port`, (5) notify via `notification_port`. Catches domain exceptions, logs each step via structlog. Returns `ProcessResultDTO(summary, duration_ms)`. |
| 3.3 | **Define Application DTOs** | `src/application/dto.py` | `ProcessInventoryInput(file_bytes, filename, tenant_id, chat_id)`, `ProcessResultDTO`, `VerifyUserInput(telegram_user_id)`. Simple pydantic models for the application boundary. |
| 3.4 | **Implement Application Composer (DI Wiring)** | `src/application/composer.py` | `App` class: a single compositor that (1) loads `Settings`, (2) creates `async_engine` + `async_session_factory`, (3) instantiates all adapters with their dependencies via **manual constructor injection**, (4) instantiates use cases with their required ports, (5) exposes `process_inventory_use_case` and `verify_user_use_case`. Provides `async def startup()` and `async def shutdown()` for DB connection lifecycle. |

### Phase 4: Presentation — Telegram Bot (Primary Adapter)
| # | Task | Deliverables | Description |
|---|------|-------------|-------------|
| 4.1 | **Implement aiogram Bot + Dispatcher factory** | `src/presentation/telegram/bot.py` | Function `create_bot(token)` → `Bot`. Function `create_dispatcher()` → `Dispatcher`. Registers routers. Provides `start_polling(app: App)` entrypoint that calls `app.startup()` before polling. |
| 4.2 | **Implement Auth Middleware** | `src/presentation/telegram/middleware/auth.py` | `aiogram` outer middleware. For every incoming update: extracts `telegram_user_id`, calls `app.verify_user_use_case`, attaches `auth_context` to `data["auth"]`. On `UnauthorizedUserError`: silently ignores (no reply). On unexpected error: logs and ignores. |
| 4.3 | **Implement /start handler** | `src/presentation/telegram/handlers/start.py` | Simple welcome: parses optional deep-link token (future registration), replies with Markdown message explaining how to use the bot. Pre-authorized users only (middleware already filtered). |
| 4.4 | **Implement File Upload Handler** | `src/presentation/telegram/handlers/file_upload.py` | Handles `Message` containing `Document`. Checks MIME type (`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`). Downloads file bytes via `bot.download()`. Calls `app.process_inventory_use_case.execute(...)`. On success: sends a brief "✅ Report generated!" text (the use case already sent the detailed report via NotificationPort). On `InvalidSheetSchemaError`: sends user-friendly error with expected columns. On other errors: sends generic "Processing failed" message. All wrapped in try/except with structlog. |
| 4.5 | **Wire everything: main entrypoint** | `src/__main__.py` (or `main.py` at root) | `async def main()`: loads settings, builds `App`, creates bot+dp, registers middleware+handlers, calls `start_polling()`. Graceful shutdown via signal handlers calling `app.shutdown()`. |

### Phase 5: Testing & Quality Assurance
| # | Task | Deliverables | Description |
|---|------|-------------|-------------|
| 5.1 | **Domain unit tests** | `tests/domain/test_services.py`, `tests/domain/test_models.py` | Pure pytest — no fixtures needed beyond in-memory DTOs. Test reconciliation: shortages detected, surpluses flagged, edge cases (zero qty, negative diff). 100% coverage target on domain layer. |
| 5.2 | **Infrastructure adapter tests** | `tests/infrastructure/test_excel_parser.py`, `tests/infrastructure/test_repository.py`, `tests/infrastructure/test_excel_exporter.py` | Parser: test with fixture Excel files (valid, missing columns, multi-sheet). Repository: uses `pytest-asyncio` + test PostgreSQL (docker compose profile `test`). Exporter: verify generated .xlsx bytes contain correct rows. |
| 5.3 | **Application use case tests** | `tests/application/test_use_cases.py` | Mock/fake adapters. Test full orchestration: happy path, empty sheet, unauthorized user, DB write failure. Verify correct port methods are called with correct arguments. |
| 5.4 | **Integration / End-to-End test** | `tests/integration/test_full_flow.py` | Docker compose spins up real PostgreSQL + bot container. Test: insert whitelisted user into DB, simulate Telegram `Document` message via raw handler call, verify DB rows + generated report format. |

### Phase 6: DevOps & Polish
| # | Task | Deliverables | Description |
|---|------|-------------|-------------|
| 6.1 | **CI pipeline outline** | `.github/workflows/ci.yml` (or GitLab CI) | Lint (`ruff`), type-check (`mypy` — strict mode on domain), test (`pytest` with coverage report), build Docker image. Run on every push to main. |
| 6.2 | **Import-linter contract** | `.importlinter` or `pyproject.toml [tool.importlinter]` | Enforce layer dependency rules: `domain` imports nothing from `infrastructure`/`presentation`, `application` imports nothing from `infrastructure`/`presentation`. CI step that fails on violations. |
| 6.3 | **README.md** | `README.md` | Quickstart: `cp .env.example .env`, fill in values, `docker compose up -d`, `make migrate-up`, interact with bot. |

---

## 6. Tech Stack Summary

| Layer | Technology | Justification |
|-------|-----------|---------------|
| **Bot Framework** | `aiogram` 3.x | Mature, async-native, excellent Telegram Bot API coverage |
| **Excel Read** | `openpyxl` (extract) + `polars` (transform) | Handles real-world messy sheets; polars is 5-10x faster than pandas |
| **Data Validation** | `pandera` | DataFrame schema enforcement before domain processing |
| **ORM** | `SQLAlchemy` 2.0 Async (with `asyncpg` driver) | Industry standard, mature async support, bulk operations |
| **Migrations** | `alembic` (async mode) | Auto-generates from SQLAlchemy models, version-controlled schema |
| **Config** | `pydantic-settings` | Type-safe .env loading, validates at startup |
| **Data DTOs** | `pydantic` v2 | Fast validation, auto-generated JSON schema |
| **Logging** | `structlog` | Structured JSON, async-compatible, container-friendly |
| **DI** | Manual constructor injection | Zero magic, debuggable, no framework dependency |
| **Containerization** | Docker (Alpine), Docker Compose | Minimal footprint, reproducible, CI-friendly |
| **Testing** | `pytest` + `pytest-asyncio` + `coverage` | Standard, well-supported |
| **Linting** | `ruff` (format + lint) | One tool, blazing fast, replaces flake8+isort+black |
| **Type Checking** | `mypy` (strict on domain) | Catches interface mismatches early |

### Dependency List (`pyproject.toml`)
```toml
[project]
name = "autoentry-bot"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "aiogram>=3.7",
    "polars>=1.0",
    "openpyxl>=3.1",
    "pandera>=0.20",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
    "alembic>=1.13",
    "structlog>=24.1",
    "python-dotenv>=1.0",    # (used by pydantic-settings)
]

[project.optional-dependencies]
dev = [
    "pytest>=8.2",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "ruff>=0.4",
    "mypy>=1.10",
    "import-linter>=2.0",
]
```

---

## 🎯 Summary of Architectural Guarantees

1. **Swap Telegram for WhatsApp:** Write a new `presentation/whatsapp/` adapter that calls the SAME `ProcessInventoryUseCase`. Domain code unchanged.
2. **Swap PostgreSQL for SQLite:** Write `infrastructure/persistence/sqlite_repository.py` implementing the SAME `InventoryRepositoryPort`. Domain code unchanged.
3. **Add REST API alongside Telegram:** Add `presentation/rest/` with FastAPI. Both coexist, sharing one `App` instance.
4. **Multi-Tenant isolation:** `tenant_id` guards every query. No cross-tenant data leakage possible at the repository level.
5. **Testability:** Domain services tested with zero infrastructure. Use cases tested with fake adapters (5 lines of code each). Full flow tested with Docker Compose.

---

> **Ready for implementation.** Start from Micro-Task **0.1** and proceed sequentially. Each task is self-contained — you can prompt me: _"Execute Micro-Task X.Y"_ and I will generate the complete, production-ready code for that task.