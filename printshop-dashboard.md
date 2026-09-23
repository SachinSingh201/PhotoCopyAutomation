# PrintShop Admin Dashboard — Frontend Spec & Backend Integration

This documents the dashboard mockup (`printshop-dashboard-v2.html`) page by page, and lists what each page needs from the backend to go from static mockup to a working system. No page in the mockup should ship with invented data — every value listed under "Backend integration" below must come from a real API response.

---

## 1. Global shell (sidebar, topbar)

**UI:** Sidebar navigation (Dashboard, Queue, Orders, History, Printers, Schedule, Reports, Settings), system-status strip, global search, notification bell, admin menu.

**Backend integration:**
- `GET /api/system/status` — agent online/offline, printer count connected, jobs needing review. Powers the sidebar status strip and should be polled every 5–10 seconds.
- `GET /api/search?q=` — must search across order ID, pickup token, phone, filename, and customer name in one call.
- `GET /api/notifications` — feeds the bell badge and alert panel; mark-as-read should be a real `PATCH`, not local state.
- Admin identity (name, avatar) comes from the authenticated session, not hardcoded.

---

## 2. Dashboard

**UI:** Five KPI cards (orders today, waiting, printing, ready for pickup, revenue), the Print Service control card, the Currently Printing card, a queue preview table, and a Needs Attention panel.

**Backend integration:**
- `GET /api/dashboard/summary` — returns the KPI numbers. The "↑ 8.4% vs yesterday" comparison must only render if the backend actually returns a comparison value; otherwise the card shows the count alone with no invented trend.
- `GET /api/print-service/status` — returns `RUNNING / PAUSED / CLOSED`, agent status, printer status, current schedule window, and next scheduled change.
- `POST /api/print-service/stop` and `POST /api/print-service/start` — the Stop/Start buttons must call these, then re-fetch `/api/print-service/status` to confirm the change before updating the pill. Never flip the UI to "Paused" optimistically.
- `GET /api/print-jobs/current` — token, order ID, filename, page count, options (color/duplex), start time, and estimated completion. If the printer reports real-time progress, show a percentage; if not, keep the "Estimated completion" text as-is rather than fabricating a percentage.
- `GET /api/queue?limit=5` — the dashboard's queue preview is the same data as the full Queue page, just truncated.
- `GET /api/alerts?status=open` — backs the Needs Attention panel (manual print required, failed jobs, delayed agent heartbeat, payment anomalies).

---

## 3. Queue

**UI:** Full waiting-queue table — token, order, customer, files, pages, payment status, job status, queue position, ETA, mode (auto/manual), and a row action menu.

**Backend integration:**
- `GET /api/queue` — full list, ideally paginated. Queue position and ETA must be calculated by the backend (schedule-aware), never estimated on the frontend.
- Row actions (reorder, hold, release, cancel, force manual) each need their own endpoint, e.g. `POST /api/queue/{token}/hold`, `POST /api/queue/{token}/cancel`. Every action re-fetches the row's state afterward rather than assuming success.
- Filtering/search on this page should hit the backend with query params rather than filtering a client-side cache, so large queues stay accurate.

---

## 4. Printers

**UI:** A "scanning for printers" indicator, a Connected section (device cards with model, connection type, tray/duplex info, a default-for-automatic-jobs toggle, Test Page and Disconnect actions), and a Discovered/Not Connected section with Connect actions — including a disabled state for a detected device missing a driver.

**Backend integration:**
- `GET /api/printers` — returns every printer the print agent can currently see, each with a state: `connected`, `available`, `driver_missing`, `offline`. The scanning indicator should only spin while a real discovery call (`POST /api/printers/scan`) is in flight, not on a fixed timer.
- `POST /api/printers/{id}/connect` and `POST /api/printers/{id}/disconnect` — after either call, re-fetch `/api/printers` rather than moving the card between sections locally.
- `PATCH /api/printers/{id}` — used for the "default for automatic jobs" toggle. Only one printer should be default at a time; the backend should enforce this and the frontend should reflect whatever it returns, not assume the toggle it clicked "won."
- `POST /api/printers/{id}/test-page` — fires a real test print and should return success/failure for a toast, not assume completion.
- A driver-missing printer's "Install driver" action should link to setup instructions from the agent, not attempt a print job.
- This page must never let an operator send jobs to a printer the backend hasn't confirmed as `connected` — physical print execution stays a backend responsibility per the system's integration rules.

---

## 5. Orders, History, Schedule, Reports, Settings

These are placeholders in the current mockup. When built out:

- **Orders** — `GET /api/orders` with full detail view (files, payment, customer, timeline) at `GET /api/orders/{id}`.
- **History** — `GET /api/orders?status=completed,cancelled` with search/filter params sent to the backend.
- **Schedule** — `GET /api/schedule` and `PATCH /api/schedule` for hours, holidays, and special closures. ETA calculations elsewhere in the app depend on this being accurate.
- **Reports** — `GET /api/reports/*` for revenue, volume, and printer usage. No chart should render with placeholder numbers if the endpoint isn't ready — show an empty state instead.
- **Settings** — shop profile, pricing rules, and admin accounts, each backed by their own authenticated endpoints.

---

## 6. Cross-cutting integration rules

- **No fake data anywhere.** If a backend endpoint doesn't exist yet, the frontend should show a clearly labeled empty/error state rather than invented numbers.
- **Confirm before reflecting state.** For every control tied to a destructive or critical action (Stop Printing, Cancel, Retry, Disconnect Printer, Schedule Changes), the flow is always: send the request → wait for the backend's response → re-fetch authoritative state → update the UI. Never optimistically show success.
- **Polling cadence.** Start with 5–10 second polling on status-sensitive views (Dashboard, Queue, Printers); move to WebSocket/SSE later without changing this contract.
- **Ownership boundaries.** Pricing, payment verification, order state, print eligibility, schedule calculation, queue ordering, file authorization, and physical print execution all stay backend responsibilities. The frontend only displays state and requests actions.
