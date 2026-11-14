"""โหลดค่าคงที่จาก shared/config.yaml และค่าลับจาก .env

ใช้ร่วมกันทุกไฟล์ใน gateway/ จะได้ไม่มีตัวเลขเขียนตายในโค้ด
"""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = yaml.safe_load((ROOT / "shared" / "config.yaml").read_text(encoding="utf-8"))


def _load_env(path: Path) -> None:
    """อ่านไฟล์ .env แบบง่าย (KEY=VALUE ต่อบรรทัด) ใส่ใน os.environ ถ้ายังไม่มีค่า"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(ROOT / ".env")

BED_ID = os.environ.get("BED_ID", CONFIG["bed_id"])
FIREBASE_DB_URL = os.environ.get("FIREBASE_DB_URL", "")
FIREBASE_SA_PATH = os.environ.get("FIREBASE_SA_PATH", "/etc/smartbed/sa.json")

NET = CONFIG["network"]
RATES = CONFIG["rates"]
FSR_ORDER = CONFIG["fsr_order"]
