# API Contracts and Endpoints

## 1. Webhooks
- `GET /webhooks/whatsapp`: Verification endpoint for WhatsApp Cloud API setup (`hub.verify_token`, `hub.challenge`).
- `POST /webhooks/whatsapp`: Ingestion of user messages, document attachments, and interactive button clicks.
- `POST /webhooks/payment`: Secure webhook for payment gateway callback with signature verification.

## 2. Orders
- `POST /orders`: Create a new print order session.
- `GET /orders/{id}`: Retrieve current order summary, status, and price snapshot.
- `POST /orders/{id}/files`: Upload or register a document for an order.
- `DELETE /orders/{id}/files/{file_id}`: Remove a file before payment.
- `POST /orders/{id}/interpret`: Submit natural language print instructions for LLM parsing.
- `POST /orders/{id}/configuration`: Set or update per-file print configuration.
- `POST /orders/{id}/confirm`: Confirm order configuration and lock in price snapshot.

## 3. Payments
- `POST /payments/create`: Initiate payment provider order for a confirmed order.
- `POST /payments/verify`: Server-side verification of payment proof or direct gateway verification. Transitions order to `PAID` and either `QUEUED` (if open) or `WAITING_FOR_PRINT_WINDOW` (if closed).

## 4. Queue & Print Agent
- `GET /queue`: Get current active queue items and estimated completion times.
- `POST /agent/heartbeat`: Agent reports online status, printer readiness, and queue state.
- `POST /agent/claim-job`: Print agent claims next pending eligible print job.
- `POST /agent/jobs/{id}/status`: Agent reports printing SUCCESS, FAILED, or UNKNOWN.

## 5. Admin Endpoints (`/api/v1/admin` and `/admin`)
- `GET /admin`: Interactive Admin Dashboard single-page web application.
- `GET /admin/dashboard`: Real-time aggregated statistics (orders, queue, printing, revenue, printer & agent status).
- `GET /admin/queue`: List all active, held, and failed queue jobs with token, pages, payment, and ETA.
- `GET /admin/queue/{id}`: Detail of a specific print job.
- `POST /admin/queue/{id}/hold`: Place a queued print job on administrative hold.
- `POST /admin/queue/{id}/resume`: Resume a held print job in queue.
- `POST /admin/queue/{id}/retry`: Re-enqueue a failed job.
- `POST /admin/queue/{id}/manual-print`: Execute manual print fallback for an order.
- `GET /admin/orders`: List all orders with status/search filtering.
- `GET /admin/orders/{id}`: Full detail of order, configurations, and pricing snapshot.
- `POST /admin/orders/{id}/collected`: Mark order collected by counter customer.
- `POST /admin/printing/start`: Force print service open override.
- `POST /admin/printing/stop`: Pause print service dispatch.
- `POST /admin/printing/emergency-stop`: Trigger emergency stop.
- `POST /admin/printing/resume-schedule`: Return print service to automatic operating schedule.
- `GET /admin/printing/status`: Current effective operational state.
- `GET /admin/schedule`: Retrieve weekly operating schedule (Mon-Sun).
- `PUT /admin/schedule`: Update weekly operating hours and open/closed toggles.
- `GET /admin/schedule/exceptions`: List calendar exceptions (holidays, special hours).
- `POST /admin/schedule/exceptions`: Add holiday or special hours exception.
- `DELETE /admin/schedule/exceptions/{id}`: Remove a schedule exception.
- `GET /admin/pricing`: Retrieve current pricing engine rates.
- `PUT /admin/pricing`: Update base charge, B&W, and Color rates.
- `GET /admin/audit-logs`: Audit log event history.
