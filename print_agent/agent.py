import asyncio
import logging
from typing import Any, Dict, List, Optional
import httpx
from print_agent.config import agent_settings
from print_agent.printer import printer_driver

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [PrintAgent] %(message)s")
logger = logging.getLogger("PrintAgent")


class PrintAgentDaemon:
    def __init__(self):
        self.base_url = agent_settings.BACKEND_URL.rstrip("/")
        self.headers = {"Authorization": f"Bearer {agent_settings.PRINT_AGENT_TOKEN}"}
        self.agent_id = agent_settings.PRINT_AGENT_ID
        self.running = False
        self.printers: List[Dict[str, Any]] = []
        self.active_printers: Dict[str, bool] = {}

    async def send_heartbeat(self, client: httpx.AsyncClient):
        try:
            printer_status = "BUSY" if any(self.active_printers.values()) else "READY"
            res = await client.post(
                f"{self.base_url}/agent/heartbeat",
                headers=self.headers,
                json={
                    "agent_id": self.agent_id,
                    "status": "ONLINE",
                    "printer_status": printer_status,
                    "printers": self.printers,
                    "metadata": {
                        "printer_count": len(self.printers),
                        "active_jobs": sum(1 for v in self.active_printers.values() if v),
                    },
                },
                timeout=5.0,
            )
            if res.status_code == 200:
                data = res.json()
                logger.debug(f"Heartbeat OK. Pending jobs: {data.get('pending_queue_count')}")
            else:
                logger.warning(f"Heartbeat failed with HTTP {res.status_code}")
        except Exception as e:
            logger.error(f"Failed to connect to backend for heartbeat: {str(e)}")

    async def poll_and_process_job(self, client: httpx.AsyncClient, printer_name: Optional[str] = None):
        """Claims and executes a single pending print job. Ensures whole order is printed on assigned printer."""
        try:
            claim_payload = {"agent_id": self.agent_id}
            if printer_name:
                claim_payload["printer_name"] = printer_name

            res = await client.post(
                f"{self.base_url}/agent/claim-job",
                headers=self.headers,
                json=claim_payload,
                timeout=5.0,
            )
            if res.status_code != 200:
                return

            claim_data = res.json()
            if not claim_data.get("job_found"):
                return

            job_id = claim_data["job_id"]
            order_code = claim_data["order_code"]
            token = claim_data["pickup_token"]
            files = claim_data.get("files", [])
            assigned_printer = claim_data.get("printer_name") or printer_name or "DefaultPrinter"

            logger.info(
                f"==> [{assigned_printer}] Claimed entire Order {order_code} (Job: {job_id}, Token: {token}) with {len(files)} files"
            )

            # Execute printing for the entire order on this assigned printer
            all_succeeded = True
            for f in files:
                success = await printer_driver.print_file(
                    file_path=f["storage_path"],
                    page_count=f["page_count"],
                    color_mode=f["color_mode"],
                    default_sides=f["default_sides"],
                    rules_json=f["rules_json"],
                    printer_name=assigned_printer,
                )
                if not success:
                    all_succeeded = False
                    break

            # Report completion back to backend
            status = "SUCCESS" if all_succeeded else "FAILED"
            await client.post(
                f"{self.base_url}/agent/jobs/{job_id}/status",
                headers=self.headers,
                json={
                    "agent_id": self.agent_id,
                    "status": status,
                    "printer_name": assigned_printer,
                    "error_code": None if all_succeeded else "PRINT_EXECUTION_ERROR",
                },
                timeout=5.0,
            )
            logger.info(f"<== [{assigned_printer}] Order {order_code} (Job {job_id}) completed: {status}")

        except Exception as e:
            logger.error(f"Error in job claiming/processing: {str(e)}")

    async def printer_worker_loop(self, client: httpx.AsyncClient, printer_name: Optional[str] = None):
        """Dedicated worker for a single physical/virtual printer. Ensures 1 order per printer, no order splitting."""
        p_label = printer_name or "DefaultPrinter"
        logger.info(f"Worker for [{p_label}] started.")

        while self.running:
            try:
                # If printer is currently marked busy, wait
                if self.active_printers.get(p_label, False):
                    await asyncio.sleep(agent_settings.POLL_INTERVAL_SECONDS)
                    continue

                claim_payload = {"agent_id": self.agent_id}
                if printer_name:
                    claim_payload["printer_name"] = printer_name

                res = await client.post(
                    f"{self.base_url}/agent/claim-job",
                    headers=self.headers,
                    json=claim_payload,
                    timeout=5.0,
                )
                if res.status_code != 200:
                    await asyncio.sleep(agent_settings.POLL_INTERVAL_SECONDS)
                    continue

                claim_data = res.json()
                if not claim_data.get("job_found"):
                    await asyncio.sleep(agent_settings.POLL_INTERVAL_SECONDS)
                    continue

                job_id = claim_data["job_id"]
                order_code = claim_data["order_code"]
                token = claim_data["pickup_token"]
                files = claim_data.get("files", [])
                assigned_printer = claim_data.get("printer_name") or p_label

                self.active_printers[p_label] = True
                logger.info(
                    f"==> [{assigned_printer}] Claimed entire Order {order_code} (Job: {job_id}, Token: {token}) with {len(files)} files"
                )

                # Execute printing for the entire order on this assigned printer
                all_succeeded = True
                for f in files:
                    success = await printer_driver.print_file(
                        file_path=f["storage_path"],
                        page_count=f["page_count"],
                        color_mode=f["color_mode"],
                        default_sides=f["default_sides"],
                        rules_json=f["rules_json"],
                        printer_name=assigned_printer,
                    )
                    if not success:
                        all_succeeded = False
                        break

                # Report completion back to backend
                status = "SUCCESS" if all_succeeded else "FAILED"
                await client.post(
                    f"{self.base_url}/agent/jobs/{job_id}/status",
                    headers=self.headers,
                    json={
                        "agent_id": self.agent_id,
                        "status": status,
                        "printer_name": assigned_printer,
                        "error_code": None if all_succeeded else "PRINT_EXECUTION_ERROR",
                    },
                    timeout=5.0,
                )
                logger.info(f"<== [{assigned_printer}] Order {order_code} (Job {job_id}) completed: {status}")

            except Exception as e:
                logger.error(f"Error in worker [{p_label}]: {str(e)}")
            finally:
                self.active_printers[p_label] = False

            await asyncio.sleep(agent_settings.POLL_INTERVAL_SECONDS)

    async def heartbeat_loop(self, client: httpx.AsyncClient):
        while self.running:
            await self.send_heartbeat(client)
            await asyncio.sleep(agent_settings.HEARTBEAT_INTERVAL_SECONDS)

    async def start(self):
        self.running = True
        logger.info(f"Print Agent {self.agent_id} starting...")

        # Discover system printers
        self.printers = printer_driver.get_available_printers()
        logger.info(f"Discovered {len(self.printers)} local printers: {[p['name'] for p in self.printers]}")

        async with httpx.AsyncClient() as client:
            worker_tasks = [asyncio.create_task(self.heartbeat_loop(client))]

            if self.printers:
                for p in self.printers:
                    p_name = p["name"]
                    self.active_printers[p_name] = False
                    worker_tasks.append(
                        asyncio.create_task(self.printer_worker_loop(client, printer_name=p_name))
                    )
            else:
                self.active_printers["Default"] = False
                worker_tasks.append(
                    asyncio.create_task(self.printer_worker_loop(client, printer_name=None))
                )

            await asyncio.gather(*worker_tasks)


if __name__ == "__main__":
    daemon = PrintAgentDaemon()
    try:
        asyncio.run(daemon.start())
    except KeyboardInterrupt:
        logger.info("Print Agent stopped by user.")
