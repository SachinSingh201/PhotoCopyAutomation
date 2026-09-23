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
