# 🖨️ WhatsApp Document Printing & Photocopy Automation System

[![FastAPI](https://img.shields.io/badge/FastAPI-0.110.0-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com/)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0+-D71F00?style=flat-square)](https://www.sqlalchemy.org/)
[![Alembic](https://img.shields.io/badge/Alembic-Migrations-blue?style=flat-square)](https://alembic.sqlalchemy.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)

An enterprise-grade, fully autonomous document printing and photocopy management system. Customers can send documents and natural-language print instructions directly via **WhatsApp**, receive deterministic pricing and live queue ETAs, complete payments securely, and have their documents automatically dispatched to shop printers via a secure **Local Print Agent** daemon — monitored in real time from a modern **Admin Operational Command Center**.

---

## 🏛️ Core Architectural Principle

> **"AI understands. Backend decides. Print Agent executes."**

- **WhatsApp Cloud API**: Pure I/O transport layer for customer document uploads, interactive buttons, and notifications.
- **FastAPI Core**: Orchestrates deterministic order state machine, price calculation, queue management, and payment verification.
- **NLP / LLM Intent Engine**: Parses unstructured customer requirements into structured, strictly validated schemas without touching database state.
- **Database (SQLAlchemy + Alembic)**: Authoritative, ACID-compliant source of truth for orders, files, payments, audit trails, and shop schedules.
- **Local Print Agent**: Secure daemon running on the physical print shop computer that polls authorized jobs and drives hardware printers.
- **Admin Dashboard**: Real-time web-based command center for telemetry, order management, printer fleet monitoring, schedule control, and emergency st## ✨ Features

- 📱 **Dual WhatsApp Transport Support**: Native integration with both **Meta WhatsApp Cloud API** and **OpenWA** (`whatsapp-web.js` self-hosted bridge) with automatic engine detection.
- 📄 **Multi-File Document Bundling**: Upload up to **10 PDF files per order** (up to 50 MB each) with automated page extraction, file validation, and cumulative cost calculation.
- ⚡ **Instant Concurrent Reordering**: Customers can place multiple orders simultaneously. As soon as an order is confirmed or paid, sending another PDF creates a fresh separate order immediately with its own unique pickup token.
- ❌ **Instant Order Cancellation**: Customers can reply `CANCEL` anytime before physical printing to abort an order and discard draft files.
- 💳 **Smart UPI & Razorpay Payments**: Dynamic UPI QR codes, custom shop counter standee upload via Admin Dashboard, and online payment links with instant `PAID` / `VERIFY` auto-confirmation.
- 🧠 **Smart NLP Interpretation**: Natural phrasing like *"print 3 copies of pages 1 to 10 in b&w both sides"* converted to deterministic order items.
- 💰 **Deterministic Pricing Engine**: Flat base fees, page-range normalization, B&W / Color rates, and Simplex / Duplex configurations.
- 🕒 **Store Scheduling & Smart ETAs**: Working hours enforcement, holiday exceptions, after-hours queuing, and dynamic queue wait-time estimation.
- 🖨️ **Autonomous Local Print Agent**: Long-polling daemon with automated printer discovery, spooling, speed benchmarking, and completion verification.
- 📊 **Modern Admin Command Center**: Real-time KPI metrics, order table with filters/search, manual payment re-verification, custom QR upload, and emergency killswitch.
- 🔒 **Ironclad Security**: Private document storage, short-lived signed tokens, webhook HMAC signature verification, and admin secret protection.

---

## 💬 WhatsApp Customer Commands

Customers interacting with the WhatsApp bot can use the following interactive commands at any time:

| Command | Description | Example |
| :--- | :--- | :--- |
| `[Send PDF]` | Uploads a PDF file. Adds to existing draft or starts a fresh order. | *Uploads document.pdf* |
| `[Instructions]` | Natural language print options (duplex, copies, color, page ranges). | *"Double sided, 2 copies"* |
| `CONFIRM` / `PAY` | Locks in order file set and cost estimation; displays payment QR & link. | `CONFIRM` |
| `CANCEL` / `ABORT` | Cancels draft or pending order and releases any reserved state. | `CANCEL` |
| `PAID` / `VERIFY` | Re-verifies payment status and auto-dispatches to print queue if paid. | `PAID` |
| `STATUS` / `MY ORDERS` | Lists all active print orders with pickup tokens, page counts, and statuses. | `STATUS` |
| `LIMITS` / `HELP` | Shows system limits (10 files max, 50MB, order hold duration). | `LIMITS` |

---

## ⚙️ System Limits & Constraints

| Parameter | Setting | Description |
| :--- | :--- | :--- |
| **Max Files per Order** | `10 files` | Configured via `MAX_FILES_PER_ORDER`. Additional files are rejected gracefully. |
| **Max File Size** | `50 MB` | Configured via `MAX_FILE_SIZE_BYTES`. Corrupt or oversized files are rejected. |
| **Concurrent Orders** | `Unlimited` | Customers can run multiple orders simultaneously with distinct pickup tokens. |
| **Reorder Timing** | `Instantaneous` | Sending a PDF after confirming/paying automatically starts a new separate order. |
| **Unpaid Order Hold** | `20 minutes` | Unpaid orders are held before automatic expiration and cleanup. |

---

## 🚀 Quickstart Guide

For step-by-step Meta WhatsApp Cloud API or OpenWA connection, webhook configuration, and production deployment, refer to the **[Complete Setup Guide (SETUP.md)](SETUP.md)**.

### 1. Clone & Setup Virtual Environment

```bash
git clone https://github.com/SachinSingh201/PhotoCopyAutomation.git
cd PhotoCopyAutomation

# Create & activate virtual environment
python -m venv .venv

# Windows:
.venv\Scripts\activate
# Linux / macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

Key environment configurations:
- `WHATSAPP_ENGINE`: Choose `openwa` or `meta`.
- `SHOP_UPI_ID`: Your UPI ID (e.g., `shopname@okicici`).
- `SHOP_NAME`: Business name displayed on UPI payment prompts.
- `ADMIN_SECRET`: Secret token for accessing the Admin Command Center.

### 3. Run Database Migrations

```bash
alembic upgrade head
```

### 4. Start the Application Server

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

- **Admin Command Center**: [http://127.0.0.1:8000/admin](http://127.0.0.1:8000/admin)
- **API Documentation (Swagger)**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **WhatsApp Webhook Endpoint**: `http://127.0.0.1:8000/webhooks/whatsapp`

### 5. Launch OpenWA Bridge (Optional, for whatsapp-web.js engine)

```bash
cd openwa-bridge
npm install
node index.js
```
Scan the QR code displayed in the terminal with WhatsApp to link your number.

### 6. Launch the Local Print Agent

On the computer connected to your physical printers:

```bash
python print_agent/agent.py
```

---

## 🧪 Running Tests

Run the full automated test suite (98 unit and integration tests):

```bash
pytest tests/ -v
```

---

## 📂 Repository Structure

```
├── .env.example                # Template configuration with all settings
├── .gitignore                  # Git exclusion rules (secrets, venv, sqlite)
├── AGENTS.md                   # AI Agent development rules and constraints
├── SETUP.md                    # In-depth setup, WhatsApp API & database guide
├── alembic.ini                 # Database migration configuration
├── requirements.txt            # Python dependencies
│
├── backend/                    # Core FastAPI Backend
│   ├── api/routes/             # WhatsApp, Admin, Agent, Health route handlers
│   ├── core/                   # Config, database, security, and logging
│   ├── models/                 # SQLAlchemy database models (Order, File, Printer, Schedule)
│   ├── schemas/                # Pydantic schemas and DTOs
│   ├── services/               # State machine, pricing, NLP parser, storage, and scheduling
│   └── static/                 # Admin Dashboard frontend (HTML, Vanilla CSS, JS)
│
├── openwa-bridge/              # OpenWA / whatsapp-web.js local gateway
├── print_agent/                # Local hardware print agent daemon
├── storage/                    # Private isolated document & QR storage
├── docs/                       # Architecture, security, state machine, and API specs
└── tests/                      # Unit and integration test suites
```

---

## 📖 Detailed Documentation

- 📘 [Setup & Integration Guide (SETUP.md)](SETUP.md)
- 📐 [Architecture Specification](docs/architecture.md)
- 🔄 [Order State Machine](docs/state-machine.md)
- 🌐 [API Reference](docs/api.md)
- 🤖 [LLM NLP Contract](docs/llm-contract.md)
- 🛡️ [Security Guidelines](docs/security.md)
- 🖨️ [Print Agent Specification](docs/print-agent.md)

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.