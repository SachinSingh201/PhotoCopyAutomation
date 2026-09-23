# Local Print Agent Protocol & Architecture

## 1. Responsibilities
The Print Agent is a lightweight Python daemon installed on the physical print-shop computer.

```text
Backend Queue <--- (Polling / Claim) --- Print Agent ---> OS Spooler / CUPS / Win32Print ---> Physical Printer
```

1. **Heartbeat**: Sends periodic heartbeats (default every 15s) reporting agent uptime, printer state, paper/toner warnings.
2. **Job Claiming**: Requests the next queued job atomically from `/print-jobs/claim`.
3. **Download**: Securely fetches the files associated with the claimed `print_job_id`.
4. **Execution**: Formats and dispatches pages to the OS print spooler according to the validated rules (BW/Color, Simplex/Duplex).
5. **Status Reporting**: Reports `STARTED`, `COMPLETED`, or `FAILED` back to the backend.

## 2. Duplicate Printing Guard
- If a network error occurs after dispatching to the OS spooler, the agent checks the spooler status before retrying to prevent duplicate physical paper usage.
- Ambiguous physical states trigger `MANUAL_REQUIRED` rather than an automatic re-print.
