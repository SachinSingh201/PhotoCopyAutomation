# AI Agent Rules for WhatsApp Document Printing System

Every AI agent working on this codebase MUST strictly adhere to the following principles:

1. **Read architecture before modifying code**: Always consult `docs/architecture.md` and `docs/state-machine.md`.
2. **Do not rewrite working modules unnecessarily**: Preserve existing behavior unless a change is explicitly required.
3. **Do not invent APIs**: Stick strictly to defined API schemas and contracts.
4. **Database changes require migrations**: Maintain SQLAlchemy declarative models and Alembic migrations.
5. **Business logic belongs in services**: Keep FastAPI route handlers and WhatsApp transport handlers thin.
6. **WhatsApp handlers must remain thin**: WhatsApp is purely an I/O transport and presentation layer.
7. **LLM cannot control business state**: The LLM is an NLP parser only. It NEVER transitions order states, deletes files, or controls printers.
8. **LLM cannot calculate prices**: Pricing is calculated deterministically by `backend/services/pricing/`.
9. **LLM cannot control printers**: Only the backend queue and authorized Print Agent interact with printers.
10. **Never trust client-side payment status**: All payment transitions must be server-side verified via cryptographic signatures or webhooks.
11. **Never expose private files publicly**: Store uploaded files in isolated private storage accessible only through short-lived backend authorization.
12. **Every important operation must be idempotent**: Handle webhook replays, network retries, and duplicate requests safely.
13. **Every feature needs tests**: Include unit and integration tests for edge cases and happy paths.
14. **Every failure path needs handling**: Gracefully handle corruption, timeouts, agent disconnects, and ambiguities.
15. **Never commit secrets**: Use environment variables and `.env`.
16. **Do not delete files without state checks**: Deletions only occur during proper lifecycle cleanup stages.
17. **Never create duplicate print jobs**: One order = One print job = Multiple print attempts.
18. **Never mark printing successful without valid execution confirmation**: Physical print spooling/completion must be reported by the agent.
19. **Preserve existing working behavior**: Ensure backwards compatibility across versions.
20. **Document architectural changes**: Update relevant docs in `docs/` whenever adding or changing system contracts.
21. **Multi-file bundling in drafts**: While an order is in a draft state (`CREATED` through `AWAITING_CONFIRMATION`), uploaded files append to the current draft order up to `settings.MAX_FILES_PER_ORDER` (10 files). When this limit is reached, reject further uploads gracefully with clear options to confirm or cancel.
22. **Order isolation and instant re-ordering**: Once an order is confirmed (`PAYMENT_PENDING`), paid, queued, or printed, its file set is permanently locked. Any subsequent document sent by the customer MUST automatically create a separate, fresh order instantaneously with its own unique pickup token and order code.
23. **Order cancellation safety**: Customers can cancel an active draft or pending order at any time before physical printing begins by replying `CANCEL`. Cancellation must transition the order to `OrderState.CANCELLED`, log audit metadata, and allow immediate start of a new order.
24. **Dual WhatsApp engine parity**: All WhatsApp webhook ingestion and outbound message dispatching must maintain full functional parity across both Meta WhatsApp Cloud API and OpenWA (`whatsapp-web.js`), normalizing event structures in the transport adapter without polluting domain logic.
25. **Multi-channel payment and counter QR precedence**: Custom counter standee QR (`storage/private/shop_qr.png`) uploaded by the merchant takes priority for display, supplemented by dynamic UPI URIs and Razorpay payment links. Payment verification must remain deterministic and strictly server-side verified.
