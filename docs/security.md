# Security & Storage Architecture

## 1. Storage Isolation
- Uploaded files are stored under `storage/private/orders/{order_id}/{file_uuid}.pdf`.
- Original filenames from customers are sanitized and treated only as display metadata to prevent path traversal (`../../etc/passwd`).
- No public web directories serve uploaded documents directly.

## 2. Webhook Security
- **WhatsApp Webhook**: Verified using shared secret `hub.verify_token` on challenge handshake, and SHA256 HMAC payload signature verification (`X-Hub-Signature-256`).
- **Payment Webhook**: Verified using HMAC signature validation (`X-Razorpay-Signature` or provider equivalent).

## 3. Print Agent Security
- The local Print Agent communicates over HTTPS using dedicated bearer tokens (`PRINT_AGENT_TOKEN`).
- File downloads for printing are granted via short-lived temporary download URLs or authenticated streaming endpoints tied to valid claimed `print_job_id`s.

## 4. Audit Logging
- Every state transition, payment verification, print attempt, and file deletion records an entry in the `audit_logs` table with timestamp, actor type, actor ID, and metadata.
