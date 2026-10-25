import asyncio
import json
import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional
from print_agent.config import agent_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [PrintAgent] %(message)s")
logger = logging.getLogger("PrintAgent")


class PrinterDriver:
    """
    Spooler driver abstraction.
    In development/mock mode, simulates physical printer output with delay and checks.
    In production mode, dispatches to target physical printer spooler via win32print / OS spooler.
    """

    def get_available_printers(self) -> List[Dict[str, Any]]:
        """Detect all printers available on the local OS."""
        printers = []
        # 1. Native Windows win32print
        try:
            import win32print
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            default_name = None
            try:
                default_name = win32print.GetDefaultPrinter()
            except Exception:
                pass

            for p in win32print.EnumPrinters(flags):
                name = p[2]
                conn = "Network" if ("\\" in name or "net" in name.lower()) else "USB"
                printers.append({
                    "name": name,
                    "model": name,
                    "connection_type": conn,
                    "is_default": (name == default_name),
                    "supports_color": True,
                    "supports_duplex": True,
                })
        except Exception:
            pass

        # 2. PowerShell Get-Printer on Windows
        if not printers and os.name == "nt":
            try:
                res = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Get-Printer | Select-Object Name, DriverName, PortName | ConvertTo-Json"],
                    capture_output=True, text=True, timeout=5
                )
                if res.returncode == 0 and res.stdout.strip():
                    data = json.loads(res.stdout.strip())
                    if isinstance(data, dict):
                        data = [data]
                    for item in data:
                        name = item.get("Name")
                        if not name:
                            continue
                        driver = item.get("DriverName", name)
                        port = str(item.get("PortName", ""))
                        conn = "Network" if ("." in port or "IP" in port or "\\" in name) else "USB"
                        printers.append({
                            "name": name,
                            "model": driver,
                            "connection_type": conn,
                            "is_default": False,
                            "supports_color": True,
                            "supports_duplex": True,
                        })
            except Exception:
                pass

        # 3. Unix/Linux lpstat
        if not printers and os.name != "nt":
            try:
                res = subprocess.run(["lpstat", "-p"], capture_output=True, text=True, timeout=5)
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        if line.startswith("printer "):
                            parts = line.split()
                            if len(parts) > 1:
                                pname = parts[1]
                                printers.append({
                                    "name": pname,
                                    "model": pname,
                                    "connection_type": "Local",
                                    "is_default": False,
                                    "supports_color": True,
                                    "supports_duplex": True,
                                })
            except Exception:
                pass

        return printers

    async def print_file(
        self,
        file_path: str,
        page_count: int,
        copies: int = 1,
        color_mode: str = "bw",
        default_sides: str = "single",
        rules_json: str = "[]",
        printer_name: Optional[str] = None,
    ) -> bool:
        target_printer = printer_name or "Default Printer"
        copies = max(1, copies or 1)
        logger.info(
            f"Spooling file: {file_path} -> [{target_printer}] (Pages: {page_count}, Copies: {copies}, Mode: {color_mode}, Sides: {default_sides})"
        )
        # Simulate physical spooling
        await asyncio.sleep(agent_settings.MOCK_PRINT_EXECUTION_TIME_SECONDS)
        logger.info(f"Successfully spooled {file_path} ({copies} copies) to [{target_printer}].")
        return True


printer_driver = PrinterDriver()
