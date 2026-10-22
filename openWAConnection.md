# OpenWA Integration Guide: Local Development vs. Production Meta Cloud API

> **Notice**: This document outlines the architecture and operational guide for using **[OpenWA](https://github.com/rmyndharis/OpenWA)** with the default engine **`whatsapp-web.js`** as a zero-cost, self-hosted WhatsApp gateway during local development, while preserving a seamless toggle to the official **Meta WhatsApp Business Cloud API** for production.
> 
> *The integration has been implemented with automated payload detection, companion QR code authentication, session management, and Docker Compose orchestration.*

---

## 1. High-Level Strategy: Pluggable Transport Architecture

Per system architecture rules ([AGENTS.md](file:///c:/Users/anshu/Desktop/PhotocopyAutomation/AGENTS.md) - Rule 5 & 6), WhatsApp is strictly an **I/O transport and presentation layer**. Business rules, document validation, deterministic pricing, order state transitions, and printer queues remain completely independent of WhatsApp.

To support OpenWA in development and Meta Cloud API in production, the system uses a **Dual-Adapter Transport Pattern**:

```
                                  ┌───────────────────────────────┐
                                  │    Core Application Core      │
                                  │   (State Machine, Pricing,    │
                                  │      Queue, Print Agent)      │
                                  └───────────────▲───────────────┘
                                                  │
                                  ┌───────────────┴───────────────┐
                                  │   WhatsApp Service Interface  │
                                  │  - parse_incoming_webhook()   │
                                  │  - send_text_message()        │
                                  │  - download_media()           │
                                  └───────▲───────────────▲───────┘
                                          │               │
               ┌──────────────────────────┴────┐     ┌────┴───────────────────────────┐
               │    OpenWA Adapter (DEV)       │     │     Meta Cloud Adapter (PROD)  │
               ├───────────────────────────────┤     ├────────────────────────────────┤
               │ • Self-hosted local Docker    │     │ • Official Meta Graph API      │
               │ • Linked via QR code scan     │     │ • Verified Business Account    │
               │ • Direct REST endpoints       │     │ • Webhook signature HMAC check │
               │ • Free, rapid local iteration │     │ • Enterprise SLA & compliance  │
               └───────────────────────────────┘     └────────────────────────────────┘
```

---

## 2. Why Use OpenWA for Development?

| Requirement | Meta Cloud API (Production) | OpenWA Gateway (Development) |
| :--- | :--- | :--- |
| **Cost** | Per-conversation charges | 100% Free & Open Source |
| **Setup Time** | Meta Business Verification, Webhook HTTPS domain, App approval | 2 minutes via local Docker container |
| **Phone Number** | Dedicated clean number required by Meta | Any test number scanned via WhatsApp Web QR |
| **Internet / Tunnel** | Requires public URL (e.g., Ngrok) with SSL | Direct `localhost` or local container networking |
| **API Keys** | System User Access Token via Meta Developer Portal | Auto-generated local Admin API Key (`.api-key`) |

---

## 3. Running OpenWA Locally on Your Laptop

### Step 3.1: Start OpenWA via Docker
Run the prebuilt OpenWA image directly on your machine:

```powershell
docker run -d `
  --stop-timeout 75 `
  --name openwa-api `
  -p 2785:2785 `
  -v "${PWD}/openwa-data:/app/data" `
  rmyndharis/openwa:latest
```

* Ports:
  * `2785`: OpenWA REST API and Management Dashboard
* Volume:
  * `./openwa-data`: Persists WhatsApp session tokens, credentials, and SQLite database.

### Step 3.2: Retrieve the Auto-Generated API Key
On the first container boot, OpenWA creates a secure admin key:

```powershell
docker exec openwa-api cat /app/data/.api-key
```
*(Copy the string starting with `owa_k1_...`).*

### Step 3.3: Link WhatsApp Session
1. Navigate to `http://localhost:2785` in your browser.
2. Enter your API key.
3. Click **Add Session** (e.g., Session Name: `printshop_dev`).
4. Select the engine:
   * **`whatsapp-web.js` (Recommended for Dev)**: Runs headless Chromium, acts identical to WhatsApp Web.
   * **`baileys`**: Direct WebSocket, low RAM footprint.
5. Open WhatsApp on your test phone -> **Settings** -> **Linked Devices** -> **Link a Device** -> Scan the QR on the OpenWA dashboard.

---

## 4. Webhook & Event Mapping (OpenWA vs. Meta)

When a customer sends a message or PDF, both gateways emit HTTP webhooks to our backend endpoint (`POST /webhooks/whatsapp`). The adapter standardizes these payloads into our internal schema.

### 4.1 Inbound Text Message

#### Meta Cloud API Format (Prod)
```json
{
  "entry": [{
    "changes": [{
      "value": {
        "messages": [{
          "from": "919876543210",
          "id": "wamid.HBgLM...",
          "timestamp": "1713000000",
          "type": "text",
          "text": { "body": "2 copies color double sided" }
        }]
      }
    }]
  }]
}
```

#### OpenWA Format (Dev)
```json
{
  "event": "message.create",
  "session": "printshop_dev",
  "data": {
    "id": "true_919876543210@c.us_3EB0...",
    "from": "919876543210@c.us",
    "sender": "919876543210",
    "type": "chat",
    "body": "2 copies color double sided",
    "timestamp": 1713000000
  }
}
```

### 4.2 Inbound Document / PDF Upload

#### Meta Cloud API Format (Prod)
Meta gives a `media_id`. The backend must make a secondary GET request to Meta's Graph API to get the temporary download URL, then stream the file:
```json
{
  "type": "document",
  "document": {
    "id": "1234567890",
    "filename": "assignment.pdf",
    "mime_type": "application/pdf"
  }
}
```

#### OpenWA Format (Dev)
OpenWA provides the media information directly, and exposes an immediate download endpoint:
```json
{
  "event": "message.create",
  "session": "printshop_dev",
  "data": {
    "id": "message_id_123",
    "type": "document",
    "hasMedia": true,
    "mimetype": "application/pdf",
    "filename": "assignment.pdf",
    "mediaUrl": "http://localhost:2785/sessions/printshop_dev/messages/message_id_123/media"
  }
}
```
*In Dev mode, the backend downloads the PDF straight from OpenWA's local port without needing Meta Graph access tokens.*

---

## 5. Outbound Message Dispatch Mapping

When the backend replies to customers (order quotes, payment links, pickup tokens, readiness alerts):

### Sending Plain Text / Markdown Quotes
* **Meta Cloud API**:
  * `POST https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages`
  * Authorization: `Bearer {WHATSAPP_TOKEN}`
* **OpenWA Gateway**:
  * `POST http://localhost:2785/sessions/{SESSION_ID}/messages/send-text`
  * Authorization: `Bearer {OPENWA_API_KEY}`
  * Payload:
    ```json
    {
      "chatId": "919876543210@c.us",
      "text": "📄 *Order Confirmed*\nTotal: ₹45.00\nPickup Token: #7821"
    }
    ```

### Sending Payment Link / UPI QR
* **OpenWA**:
  * Can dispatch text containing the payment link (`https://rzp.io/l/...`), or call `send-image` to send a rendered UPI QR code image directly to the user chat.

---

## 6. Future Configuration Specification (`.env`)

When ready to test OpenWA, environment variables can be set as follows:

```env
# -------------------------------------------------------------
# WHATSAPP PROVIDER SELECTION: "openwa" (dev) | "meta" (prod) | "mock" (test)
# -------------------------------------------------------------
WHATSAPP_PROVIDER=openwa

# --- OpenWA Settings (Active when WHATSAPP_PROVIDER=openwa) ---
OPENWA_BASE_URL=http://localhost:2785
OPENWA_API_KEY=owa_k1_your_local_generated_key_here
OPENWA_SESSION_ID=printshop_dev

# --- Meta Cloud API Settings (Active when WHATSAPP_PROVIDER=meta) ---
WHATSAPP_PHONE_NUMBER_ID=your_meta_phone_number_id
WHATSAPP_TOKEN=your_meta_permanent_access_token
WHATSAPP_VERIFY_TOKEN=your_custom_webhook_verify_token
WHATSAPP_API_VERSION=v19.0
```

---

## 7. Migration Checklist: Moving from Dev (OpenWA) to Prod (Meta Cloud)

When local testing is complete and the system is ready to launch publicly with Meta Verification:

1. **Keep the Core Intact**: No modifications needed in `backend/services/orders/`, `backend/services/pricing/`, or `print_agent/`.
2. **Setup Meta Business Manager**:
   - Register a dedicated phone number on Meta WhatsApp Business Platform.
   - Verify business legal entity and tax info in Meta Business Manager.
3. **Configure Public Webhook**:
   - Point Meta Webhook URL to `https://your-production-domain.com/webhooks/whatsapp`.
   - Complete the `hub.challenge` handshake via `WHATSAPP_VERIFY_TOKEN`.
4. **Switch `.env` Flag**:
   - Change `WHATSAPP_PROVIDER=openwa` to `WHATSAPP_PROVIDER=meta`.
   - Update `WHATSAPP_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID`.
5. **Decommission OpenWA Container**:
   - Stop the local container (`docker stop openwa-api`).

---

## 8. Development Safety & Best Practices

1. **Use a Dedicated Test SIM**: Never scan OpenWA with your primary personal phone number. While developing and testing, use a separate test SIM or burner number.
2. **Prevent Spam Flags**: Test conversations only between your own test devices. Avoid sending unsolicited automated test messages to outside numbers.
3. **Preserve Database Cleanliness**: Local development files saved in `storage/private` can be purged safely between test runs as long as the database is reset or migrated cleanly.
