# Photocopy & Print Automation System — Complete Setup & Integration Guide

A comprehensive, production-ready guide to set up the **Photocopy & Print Automation System**, connect the **Meta WhatsApp Cloud API**, configure the **Database (SQLite / PostgreSQL)**, run **Alembic Migrations**, launch the **Print Agent daemon**, and execute **testing referral code payloads**.

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Prerequisites & Environment Requirements](#2-prerequisites--environment-requirements)
3. [Environment Configuration (.env)](#3-environment-configuration-env)
4. [Database Setup & Migrations](#4-database-setup--migrations)
   - [Development (SQLite)](#41-development-setup-sqlite)
   - [Production (PostgreSQL)](#42-production-setup-postgresql)
   - [Running Alembic Migrations](#43-running-alembic-migrations)
5. [Meta WhatsApp Cloud API Integration Guide](#5-meta-whatsapp-cloud-api-integration-guide)
   - [Step 1: Create a Meta Developer App](#step-1-create-a-meta-developer-app)
   - [Step 2: Obtain Phone Number ID & Access Tokens](#step-2-obtain-phone-number-id--access-tokens)
   - [Step 3: Setup Webhook Tunneling (ngrok / Cloudflare)](#step-3-setup-webhook-tunneling-ngrok--cloudflare)
   - [Step 4: Configure & Verify WhatsApp Webhooks](#step-4-configure--verify-whatsapp-webhooks)
   - [Step 5: Subscribing to Webhook Fields](#step-5-subscribing-to-webhook-fields)
6. [Step-by-Step Execution Guide (All Startup Codes)](#6-step-by-step-execution-guide-all-startup-codes)
   - [Backend API Server](#61-run-fastapi-backend-server)
   - [Admin Operational Command Center](#62-access-admin-dashboard)
   - [Print Agent Local Daemon](#63-run-print-agent-daemon)
7. [Reference Execution Codes & Testing Referral Payloads](#7-reference-execution-codes--testing-referral-payloads)
   - [7.1 WhatsApp Webhook Challenge Verification Test](#71-whatsapp-webhook-challenge-verification-test)
   - [7.2 Inbound WhatsApp Text Message Test](#72-inbound-whatsapp-text-message-test)
   - [7.3 Inbound WhatsApp Document (PDF) Upload Test](#73-inbound-whatsapp-document-pdf-upload-test)
   - [7.4 Inbound WhatsApp Interactive Button Test](#74-inbound-whatsapp-interactive-button-test)
   - [7.5 Admin API Endpoints Reference Commands](#75-admin-api-endpoints-reference-commands)
   - [7.6 Print Agent Polling & Execution Test](#76-print-agent-polling--execution-test)
   - [7.7 Running Automated Test Suite](#77-running-automated-test-suite)
8. [Troubleshooting & Best Practices](#8-troubleshooting--best-practices)

---

## 1. System Architecture Overview

```
                      ┌────────────────────────────────────────┐
                      │        WhatsApp Customer User          │
                      └──────────────────┬─────────────────────┘
                                         │ WhatsApp Messages / PDF
                                         ▼
                      ┌────────────────────────────────────────┐
                      │         Meta Cloud API Server          │
                      └──────────────────┬─────────────────────┘
                                         │ Webhook POST events
                                         ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ FastAPI Backend Server (Port 8000)                                            │
│                                                                               │
│  ┌───────────────────────┐   ┌───────────────────────┐   ┌─────────────────┐ │
│  │ /webhooks/whatsapp    │   │ NLP / Order Processor │   │ Pricing Engine  │ │
│  └──────────┬────────────┘   └───────────┬───────────┘   └────────┬────────┘ │
│             │                            │                        │          │
│             └────────────────────────────┼────────────────────────┘          │
│                                          ▼                                   │
│                             ┌────────────────────────┐                       │
│                             │ State Machine (Orders) │                       │
│                             └────────────┬───────────┘                       │
│                                          │                                   │
│                 ┌────────────────────────┴────────────────────────┐          │
│                 ▼                                                 ▼          │
│  ┌─────────────────────────────┐                  ┌────────────────────────┐ │
│  │ SQLAlchemy + PostgreSQL/    │                  │ Private File Storage   │ │
│  │ SQLite Database             │                  │ (storage/private/)     │ │
│  └─────────────────────────────┘                  └────────────────────────┘ │
│                 ▲                                                 ▲          │
└─────────────────┼─────────────────────────────────────────────────┼──────────┘
                  │                                                 │
       Job Polling / Heartbeats                        Authorized Secure Download
                  │                                                 │
┌─────────────────┴─────────────────────────────────────────────────┴──────────┐
│ Local Print Agent (runs on shop PC connected to physical printers)           │
│                                                                              │
│  ┌───────────────────────┐   ┌───────────────────────┐   ┌─────────────────┐ │
│  │  Long Poller Engine   │──▶│  Spooler / PDF Render │──▶│ Physical Driver │ │
│  └───────────────────────┘   └───────────────────────┘   └─────────────────┘ │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Prerequisites & Environment Requirements

Ensure you have installed:
- **Python**: Version `3.11` or higher (`python --version`)
- **Git**: For version control (`git --version`)
- **PostgreSQL** (Optional for production; SQLite is included out of the box for dev)
- **Poppler / PyMuPDF**: For PDF page rendering and physical printing
- **ngrok** or **Cloudflare Tunnel**: For exposing your local port `8000` to the internet during development

---

## 3. Environment Configuration (.env)

Create a `.env` file in the root folder by copying the provided example:

```bash
cp .env.example .env
```

### Complete `.env` Reference Table

| Variable | Description | Example / Recommended Value |
| :--- | :--- | :--- |
| `APP_NAME` | Name of the application | `WhatsApp-Print-System` |
| `ENVIRONMENT` | Environment stage | `development` or `production` |
| `DEBUG` | Enable verbose logging | `true` |
| `PORT` | Backend server port | `8000` |
| `HOST` | Backend server host | `0.0.0.0` |
| `SECRET_KEY` | Application cryptographic secret | `generate_with_openssl_rand_hex_32` |
| `DATABASE_URL` | Async database connection URL | `sqlite+aiosqlite:///./whatsapp_print.db` (Dev) or `postgresql+asyncpg://user:pass@localhost:5432/printdb` (Prod) |
| `DATABASE_SYNC_URL` | Sync database connection (Alembic) | `sqlite:///./whatsapp_print.db` (Dev) or `postgresql://user:pass@localhost:5432/printdb` (Prod) |
| `STORAGE_ROOT` | Private document storage directory | `./storage/private` |
| `WHATSAPP_PHONE_NUMBER_ID` | Meta WhatsApp Cloud Phone Number ID | Found in Meta App Dashboard |
| `WHATSAPP_TOKEN` | System User Access Token from Meta | Permanent Bearer token from Meta Developer Portal |
| `WHATSAPP_VERIFY_TOKEN` | Webhook verification challenge token | A secure string e.g. `printshop_webhook_secret_verify_2026` |
| `WHATSAPP_API_VERSION` | Graph API version | `v19.0` |
| `LLM_PROVIDER` | NLP Intent Parser provider | `mock` or `gemini` or `openai` |
| `LLM_API_KEY` | API Key for LLM provider | `AIzaSy...` (if using Gemini) |
| `LLM_MODEL` | NLP model identifier | `gemini-1.5-flash` |
| `PAYMENT_PROVIDER` | Payment gateway | `mock` or `razorpay` |
| `PAYMENT_KEY_ID` | Razorpay Key ID | `rzp_test_xxxxxx` |
| `PAYMENT_KEY_SECRET` | Razorpay Key Secret | `secret_xxxxxx` |
| `PAYMENT_WEBHOOK_SECRET` | Razorpay Webhook Signature Secret | `webhook_secret_xxxxxx` |
| `PRICE_FILE_BASE_CHARGE` | Flat base fee per order (INR) | `5.0` |
| `PRICE_BW_SINGLE` | Price per page B&W single-sided | `2.0` |
| `PRICE_BW_DOUBLE` | Price per sheet B&W double-sided | `3.0` |
| `PRICE_COLOR_SINGLE` | Price per page Color single-sided | `10.0` |
| `PRICE_COLOR_DOUBLE` | Price per sheet Color double-sided | `15.0` |
| `CURRENCY` | Currency code | `INR` |
| `PRINT_AGENT_TOKEN` | Shared secret token for Print Agent auth | `print_agent_secret_token_123` |
| `PRINT_AGENT_ID` | Identifier of this print agent | `agent-local-01` |
| `PRINTER_SPEED_PPM` | Print speed estimate in pages/minute | `20` |
| `ADMIN_USERNAME` | Admin login username | `admin` |
| `ADMIN_PASSWORD` | Admin login password | `admin_password_change_me` |
| `ADMIN_SECRET` | Secret token for Admin API header `X-Admin-Secret` | `admin_secret_token_change_me` |

---

## 4. Database Setup & Migrations

### 4.1 Development Setup (SQLite)
By default, the application is configured to run on a local SQLite database (`whatsapp_print.db`). No database server installation is required.

In `.env`:
```ini
DATABASE_URL=sqlite+aiosqlite:///./whatsapp_print.db
DATABASE_SYNC_URL=sqlite:///./whatsapp_print.db
```

### 4.2 Production Setup (PostgreSQL)
For production environments, install PostgreSQL or launch via Docker:

```bash
# Optional: Launch PostgreSQL with Docker
docker run --name printshop-postgres -e POSTGRES_USER=printuser -e POSTGRES_PASSWORD=printpass -e POSTGRES_DB=printdb -p 5432:5432 -d postgres:16-alpine
```

In `.env`:
```ini
DATABASE_URL=postgresql+asyncpg://printuser:printpass@localhost:5432/printdb
DATABASE_SYNC_URL=postgresql://printuser:printpass@localhost:5432/printdb
```

### 4.3 Running Alembic Migrations

To apply all database migrations and bring the schema up to date:

```bash
# Activate your virtual environment first
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# Apply all migrations to the latest revision (head)
alembic upgrade head
```

#### Creating New Migrations (When modifying models)
```bash
alembic revision --autogenerate -m "add new field to orders"
alembic upgrade head
```

#### Checking Migration History / Current Status
```bash
alembic current
alembic history --verbose
```

---

## 5. Meta WhatsApp Cloud API Integration Guide

Follow these steps to connect your WhatsApp Business account to the automation system:

### Step 1: Create a Meta Developer App
1. Navigate to the [Meta for Developers Portal](https://developers.facebook.com/).
2. Log in and click **My Apps** > **Create App**.
3. Select **Other** > **Business** as the App Type.
4. Name your app (e.g., `Shop-Print-Automation`) and link your Meta Business Account.
5. Click **Create App**.

### Step 2: Obtain Phone Number ID & Access Tokens
1. On the App Dashboard left sidebar, find **WhatsApp** and click **Set up**.
2. Go to **WhatsApp** > **API Setup**:
   - Locate the **Phone number ID** (e.g., `109283746501928`). Copy this into `.env` as `WHATSAPP_PHONE_NUMBER_ID`.
   - Locate the **Temporary access token** (valid for 24h) for initial testing.
3. **For Permanent Production Access Token**:
   - Go to **Business Settings** (`business.facebook.com`).
   - Navigate to **Users** > **System Users**.
   - Click **Add System User**, give it an Admin role.
   - Click **Generate New Token**, select your WhatsApp App, and grant `whatsapp_business_messaging` and `whatsapp_business_management` permissions.
   - Copy this permanent token into `.env` as `WHATSAPP_TOKEN`.

### Step 3: Setup Webhook Tunneling (ngrok / Cloudflare)
Meta requires a publicly accessible HTTPS endpoint to deliver incoming message webhooks.

Run **ngrok** to forward port `8000`:
```bash
ngrok http 8000
```
*Note your public HTTPS URL (e.g., `https://a1b2-c3d4.ngrok-free.app`).*

### Step 4: Configure & Verify WhatsApp Webhooks
1. In the Meta App Dashboard, go to **WhatsApp** > **Configuration**.
2. Click **Edit** next to **Webhook**:
   - **Callback URL**: `https://<your-ngrok-domain>/webhooks/whatsapp`
     *(Example: `https://a1b2-c3d4.ngrok-free.app/webhooks/whatsapp`)*
   - **Verify Token**: Enter the exact string set in your `.env` for `WHATSAPP_VERIFY_TOKEN` (e.g., `printshop_webhook_secret_verify_2026`).
3. Click **Verify and Save**.
   - Meta sends a `GET` request to your endpoint with `hub.challenge` and `hub.verify_token`.
   - FastAPI verifies the token and immediately responds with `hub.challenge`, confirming the webhook!

### Step 5: Subscribing to Webhook Fields
1. Under **Webhook fields**, find the `messages` row.
2. Click **Subscribe**.
3. Now, whenever any user sends a message, document, or clicks an interactive button in your WhatsApp chat, Meta forwards the payload to `/webhooks/whatsapp`.

---

## 6. Step-by-Step Execution Guide (All Startup Codes)

### 6.1 Setup Virtual Environment & Dependencies

```bash
# 1. Clone repository & enter directory
cd c:\Users\anshu\Desktop\PhotocopyAutomation

# 2. Create virtual environment
python -m venv .venv

# 3. Activate virtual environment
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Run Database Migrations
alembic upgrade head
```

### 6.2 Run FastAPI Backend Server

```bash
# Start backend server with live reload enabled
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```
- **Backend API & Webhooks**: `http://127.0.0.1:8000`
- **Interactive Swagger Documentation**: `http://127.0.0.1:8000/docs`
- **OpenAPI JSON**: `http://127.0.0.1:8000/openapi.json`

### 6.3 Access Admin Dashboard
Open your browser and navigate to:
```
http://127.0.0.1:8000/admin
```
The dashboard provides a real-time command center:
- Live revenue, order count, page counters, and queue telemetry
- Live order control table with search, filter, and manual status override
- Daily schedule manager (Open/Close times, cutoff times, auto-queue pause)
- Active printer fleet control (Speed, paper status, toggle online/offline)
- Emergency Master Kill Switch & Auto-recovery mode toggle

### 6.4 Run Print Agent Daemon
The Print Agent is the bridge between the cloud backend and the physical shop printers. Run it on the shop's computer:

```bash
# Activate virtual environment in a separate terminal
.venv\Scripts\activate

# Start the Print Agent
python print_agent/agent.py
```
The agent automatically:
- Registers with the backend via `PRINT_AGENT_TOKEN`
- Heartbeats every 10 seconds
- Polls for queued `PAID` print jobs
- Spools files to local printers and confirms execution back to the server

---

## 7. Reference Execution Codes & Testing Referral Payloads

Use these `curl` commands and referral test payloads to verify all integrations end-to-end.

### 7.1 WhatsApp Webhook Challenge Verification Test
Simulate Meta's verification handshake:

```bash
curl -X GET "http://127.0.0.1:8000/webhooks/whatsapp?hub.mode=subscribe&hub.challenge=1158201444&hub.verify_token=mock_verify_token"
```
**Expected Response:** `1158201444` (HTTP 200)

---

### 7.2 Inbound WhatsApp Text Message Test
Simulate a customer texting print requirements (e.g. "2 copies black and white double sided"):

```bash
curl -X POST "http://127.0.0.1:8000/webhooks/whatsapp" \
  -H "Content-Type: application/json" \
  -d '{
    "object": "whatsapp_business_account",
    "entry": [
      {
        "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
        "changes": [
          {
            "value": {
              "messaging_product": "whatsapp",
              "metadata": {
                "display_phone_number": "15550239999",
                "phone_number_id": "109283746501928"
              },
              "contacts": [
                {
                  "profile": { "name": "Rahul Sharma" },
                  "wa_id": "919876543210"
                }
              ],
              "messages": [
                {
                  "from": "919876543210",
                  "id": "wamid.HBgMOTE5ODc2NTQzMjEwFQIAEhgWM0VCMDEyMzQ1Njc4OTBBQkNERUYwMQ==",
                  "timestamp": "1711200000",
                  "text": {
                    "body": "Hi, please print 2 copies black and white, double sided"
                  },
                  "type": "text"
                }
              ]
            },
            "field": "messages"
          }
        ]
      }
    ]
  }'
```
**Expected Response:** `{"status": "received", "event_count": 1}`

---

### 7.3 Inbound WhatsApp Document (PDF) Upload Test
Simulate a customer uploading a PDF document:

```bash
curl -X POST "http://127.0.0.1:8000/webhooks/whatsapp" \
  -H "Content-Type: application/json" \
  -d '{
    "object": "whatsapp_business_account",
    "entry": [
      {
        "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
        "changes": [
          {
            "value": {
              "messaging_product": "whatsapp",
              "metadata": {
                "display_phone_number": "15550239999",
                "phone_number_id": "109283746501928"
              },
              "contacts": [
                {
                  "profile": { "name": "Rahul Sharma" },
                  "wa_id": "919876543210"
                }
              ],
              "messages": [
                {
                  "from": "919876543210",
                  "id": "wamid.HBgMOTE5ODc2NTQzMjEwFQIAEhgWM0VCMDEyMzQ1Njc4OTBBQkNERUYwMg==",
                  "timestamp": "1711200005",
                  "type": "document",
                  "document": {
                    "filename": "College_Project_Report.pdf",
                    "mime_type": "application/pdf",
                    "sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
                    "id": "meta_media_doc_987654"
                  }
                }
              ]
            },
            "field": "messages"
          }
        ]
      }
    ]
  }'
```
**Expected Response:** `{"status": "received", "event_count": 1}`

---

### 7.4 Inbound WhatsApp Interactive Button Test
Simulate customer confirming order by tapping a button:

```bash
curl -X POST "http://127.0.0.1:8000/webhooks/whatsapp" \
  -H "Content-Type: application/json" \
  -d '{
    "object": "whatsapp_business_account",
    "entry": [
      {
        "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
        "changes": [
          {
            "value": {
              "messaging_product": "whatsapp",
              "metadata": {
                "display_phone_number": "15550239999",
                "phone_number_id": "109283746501928"
              },
              "contacts": [
                {
                  "profile": { "name": "Rahul Sharma" },
                  "wa_id": "919876543210"
                }
              ],
              "messages": [
                {
                  "from": "919876543210",
                  "id": "wamid.HBgMOTE5ODc2NTQzMjEwFQIAEhgWM0VCMDEyMzQ1Njc4OTBBQkNERUYwMw==",
                  "timestamp": "1711200010",
                  "type": "interactive",
                  "interactive": {
                    "type": "button_reply",
                    "button_reply": {
                      "id": "btn_confirm_order",
                      "title": "Confirm & Pay"
                    }
                  }
                }
              ]
            },
            "field": "messages"
          }
        ]
      }
    ]
  }'
```

---

### 7.5 Admin API Endpoints Reference Commands

#### Get Dashboard Analytics & System Telemetry:
```bash
curl -X GET "http://127.0.0.1:8000/api/v1/admin/stats" \
  -H "X-Admin-Secret: admin_secret_token_change_me"
```

#### List All Orders (with filters):
```bash
curl -X GET "http://127.0.0.1:8000/api/v1/admin/orders?limit=50&status_filter=PAID" \
  -H "X-Admin-Secret: admin_secret_token_change_me"
```

#### Update Shop Operational Schedule:
```bash
curl -X PUT "http://127.0.0.1:8000/api/v1/admin/schedule" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Secret: admin_secret_token_change_me" \
  -d '{
    "open_time": "08:00",
    "close_time": "21:00",
    "is_open_today": true,
    "allow_after_hours_orders": true,
    "emergency_stop": false
  }'
```

#### Manage Printer Fleet (Update Printer Status):
```bash
curl -X POST "http://127.0.0.1:8000/api/v1/admin/printers" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Secret: admin_secret_token_change_me" \
  -d '{
    "printer_id": "hp-laserjet-m404dn",
    "name": "HP LaserJet Pro M404dn (Counter 1)",
    "is_active": true,
    "speed_ppm": 38,
    "paper_status": "OK"
  }'
```

---

### 7.6 Print Agent Polling & Execution Test

#### Step 1: Register / Heartbeat Agent:
```bash
curl -X POST "http://127.0.0.1:8000/api/v1/agent/heartbeat" \
  -H "Authorization: Bearer print_agent_secret_token_123" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "agent-local-01",
    "status": "IDLE",
    "active_jobs_count": 0
  }'
```

#### Step 2: Poll Next Pending Print Job:
```bash
curl -X GET "http://127.0.0.1:8000/api/v1/agent/jobs/next" \
  -H "Authorization: Bearer print_agent_secret_token_123"
```

#### Step 3: Complete Print Job:
```bash
curl -X POST "http://127.0.0.1:8000/api/v1/agent/jobs/JOB_ID_HERE/complete" \
  -H "Authorization: Bearer print_agent_secret_token_123" \
  -H "Content-Type: application/json" \
  -d '{
    "pages_printed": 12,
    "status": "COMPLETED"
  }'
```

---

### 7.7 Running Automated Test Suite

Run all unit, integration, and security tests to verify application integrity:

```bash
pytest tests/ -v
```

---

## 8. Troubleshooting & Best Practices

1. **Webhook Token Mismatch (HTTP 403)**:
   - Ensure the token entered in Meta matches `WHATSAPP_VERIFY_TOKEN` in your `.env`.
   - Remember to reload the FastAPI server if you changed values in `.env`.
2. **Database Locked in SQLite**:
   - For high concurrent load, switch to PostgreSQL. SQLite in local dev works seamlessly with async connections via `aiosqlite`.
3. **Print Agent Authentication Failure (HTTP 401)**:
   - Verify `PRINT_AGENT_TOKEN` in `print_agent/config.py` matches `PRINT_AGENT_TOKEN` in `.env`.
4. **File Permission Errors**:
   - Verify that `./storage/private` exists and is writable by the user running the FastAPI process.
5. **Alembic Migration Out of Sync**:
   - If resetting the database in development: delete `whatsapp_print.db` and run `alembic upgrade head`.

---
*Photocopy Automation System — Developed for production-grade, autonomous document printing.*
