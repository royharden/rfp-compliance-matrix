"""Generate explicitly fictional development inputs, without importing the app.

This script was source-inspected only during construction. Its output is not a
public RFP, licensed external corpus, independent gold, or executed demo receipt.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _pdf(lines: list[str]) -> bytes:
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    commands = "BT /F1 12 Tf 48 750 Td 18 TL " + " ".join(f"({line}) Tj T*" for line in escaped) + " ET"
    stream = commands.encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n% Authored fictional development fixture\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate authored synthetic development inputs; does not run the application")
    parser.add_argument("--out", required=True, help="New directory; existing paths are never overwritten")
    args = parser.parse_args()
    output = Path(args.out).expanduser()
    if output.exists() or output.is_symlink():
        parser.error("Output path must not exist; preserve previous artifacts")
    output.mkdir(parents=True, exist_ok=False)
    lines = ["FICTIONAL DEVELOPMENT RFP - authored example", "1. Technical requirements", "The supplier shall provide daily encrypted backups.", "The supplier must hold ISO 27001 certification.", "2. Submission", "The supplier must submit a project plan by Friday."]
    (output / "fictional-rfp.pdf").write_bytes(_pdf(lines))
    terms = "Project-authored fictional development example; no real vendor claim."
    first = [{"record_id": "backups", "text": "The supplier shall provide daily encrypted backups.", "terms": terms, "fictional": True}]
    second = first + [{"record_id": "certification-denial", "text": "This fictional supplier does not hold ISO 27001 certification.", "terms": terms, "fictional": True}]
    for name, records in (("vendor-v1.jsonl", first), ("vendor-v2.jsonl", second)):
        (output / name).write_text("".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records), encoding="utf-8")
    (output / "PROVENANCE.txt").write_text("Authored synthetic development inputs. Not a public-PDF corpus or human gold.\nGenerated outputs do not establish parser correctness, quality metrics, or a recorded runtime journey.\n", encoding="utf-8")
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
