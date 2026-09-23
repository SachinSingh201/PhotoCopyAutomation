import asyncio
import logging
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

    async def send_heartbeat(self, client: httpx.AsyncClient):
        try:
            res = await client.post(
                f"{self.base_url}/agent/heartbeat",
                headers=self.headers,
                json={
                    "agent_id": self.agent_id,
                    "status": "ONLINE",
                    "printer_status": "READY",
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

    async def poll_and_process_job(self, client: httpx.AsyncClient):
        try:
            res = await client.post(
                f"{self.base_url}/agent/claim-job",
                headers=self.headers,
                json={"agent_id": self.agent_id},
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

            logger.info(f"==> Claimed Job {job_id} for Order {order_code} (Token: {token}) with {len(files)} files")

            # Execute printing for each file
            all_succeeded = True
            for f in files:
                success = await printer_driver.print_file(
                    file_path=f["storage_path"],
                    page_count=f["page_count"],
                    color_mode=f["color_mode"],
                    default_sides=f["default_sides"],
                    rules_json=f["rules_json"],
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
                    "error_code": None if all_succeeded else "PRINT_EXECUTION_ERROR",
                },
                timeout=5.0,
            )
            logger.info(f"<== Job {job_id} marked as {status}")

        except Exception as e:
            logger.error(f"Error in job claiming/processing: {str(e)}")

    async def start(self):
        self.running = True
        logger.info(f"Print Agent {self.agent_id} starting...")
        async with httpx.AsyncClient() as client:
            while self.running:
                await self.send_heartbeat(client)
                await self.poll_and_process_job(client)
                await asyncio.sleep(agent_settings.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    daemon = PrintAgentDaemon()
    try:
        asyncio.run(daemon.start())
    except KeyboardInterrupt:
        logger.info("Print Agent stopped by user.")
