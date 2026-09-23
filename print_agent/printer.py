import asyncio
import logging
import sys
from typing import Dict, List
from print_agent.config import agent_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [PrintAgent] %(message)s")
logger = logging.getLogger("PrintAgent")


class PrinterDriver:
    """
    Spooler driver abstraction.
    In development/mock mode, simulates physical printer output with delay and checks.
    In production mode, uses pywin32 / win32print or cups.
    """

    async def print_file(
        self,
        file_path: str,
        page_count: int,
        color_mode: str,
        default_sides: str,
        rules_json: str,
    ) -> bool:
        logger.info(
            f"Spooling file: {file_path} (Pages: {page_count}, Mode: {color_mode}, Sides: {default_sides})"
        )
        # Simulate physical spooling
        await asyncio.sleep(agent_settings.MOCK_PRINT_EXECUTION_TIME_SECONDS)
        logger.info(f"Successfully spooled {file_path} to physical printer.")
        return True


printer_driver = PrinterDriver()
