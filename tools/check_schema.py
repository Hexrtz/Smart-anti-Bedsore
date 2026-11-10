#!/usr/bin/env python3
"""ตรวจข้อความ JSON กับ Schema ใน shared/schema/

ใช้:
  python tools/check_schema.py                 # ตรวจตัวอย่างทั้งหมดใน shared/schema/examples/
  python tools/check_schema.py '<json string>' # ตรวจข้อความเดียว
Gateway ใช้ฟังก์ชัน validate() นี้ได้เลย
"""
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "shared" / "schema"
VALIDATORS = {p.stem: Draft202012Validator(json.loads(p.read_text(encoding="utf-8")))
              for p in SCHEMA_DIR.glob("*.json")}


def validate(msg: dict) -> list[str]:
    """คืนรายการ error (ว่าง = ผ่าน)"""
    v = VALIDATORS.get(msg.get("type"))
    if v is None:
        return [f"unknown type: {msg.get('type')!r}"]
    return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in v.iter_errors(msg)]


def main():
    if len(sys.argv) > 1:
        errs = validate(json.loads(sys.argv[1]))
        print("OK" if not errs else "\n".join(errs))
        sys.exit(1 if errs else 0)
    failed = 0
    for p in sorted((SCHEMA_DIR / "examples").glob("*.json")):
        msg = json.loads(p.read_text(encoding="utf-8"))
        errs = validate(msg)
        size = len(json.dumps(msg, separators=(",", ":")).encode())
        if size > 512:
            errs.append(f"packet {size} bytes > 512")
        print(f"{'PASS' if not errs else 'FAIL'}  {p.name} ({size} B)")
        for e in errs:
            print("      ", e)
        failed += bool(errs)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
