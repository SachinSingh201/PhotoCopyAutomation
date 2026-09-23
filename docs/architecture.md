# Architecture & System Design

## 1. System Overview

```text
Customer
   |
   v
WhatsApp
   |
   v
WhatsApp Business / Cloud API
   |
   v
FastAPI Backend
   |
   +--------------------+---------------------+
   |                    |                     |
   v                    v                     v
Conversation Service   File Service       Scheduling Service (Operating Hours, Exceptions, ETA)
   |                    |                     |
   v                    v                     |
LLM NLP Service       Private Storage         |
   |                                          |
   v                                          |
Structured Intent                             |
   |                                          |
   v                                          |
Order / Configuration Service                 |
   |                                          |
   +----------+-----------+-------------+     |
   |          |           |             |     |
   v          v           v             v     v
Pricing    Payment      Durable Queue  Notifications
   |          |           |
   |          v           v
   |       Verified     Print Job
   |          |           |
   +----------+-----------+
              |
              v
       Local Print Agent
              |
              v
       OS Print Spooler
              |
              v
        Physical Printer

Shop Owner / Admin
   |
   v
Admin Dashboard Frontend (SPA at /admin)
   |
   v
Admin REST API (/api/v1/admin/...)
   +-- Real-time Dashboard Metrics
   +-- Print Queue Controls (Hold / Resume / Retry / Manual Print)
   +-- Order Explorer & Inspector
   +-- Weekly Schedules & Holiday Exception Manager
   +-- Operational Controls (Force Open, Pause, Emergency Stop, Return to Schedule)
   +-- Pricing Engine Rate Configuration
   +-- Audit Log Stream
```

## 2. Core Responsibilities & Boundaries

| Component | Responsibility | Forbidden Actions |
|---|---|---|
| **WhatsApp Layer** | Inbound/Outbound webhook transport, button dispatch | State mutation, pricing, direct DB access |
| **LLM NLP Service** | Parsing natural language into structured JSON | Price calculation, order state changes, printer commands |
| **File Service** | Validation, page count extraction, private storage | Exposing public file URLs |
| **Pricing Engine** | Pure mathematical deterministic calculations | Using LLM for pricing |
| **Scheduling Service** | Operating windows, holiday overrides, schedule-aware ETA | Manual printer operations |
| **State Machine** | Atomic database state transitions & audit events | Skipping transition rules |
| **Queue Manager** | Priority scheduling, job claiming, concurrency locks, hold/resume | Allowing unpaid orders into queue |
| **Print Agent** | Physical printer integration, heartbeat, error reporting | Altering order settings or pricing |
| **Admin Dashboard UI** | Operational monitoring, manual printing fallback, schedule management | Bypassing authorization & audit logs |
