"""Generate clearly synthetic PDFs for local PDF.js viewer testing."""

from pathlib import Path


FIXTURE_DIR = Path(__file__).parent


def pdf_literal(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return escaped.encode("ascii")


def create_pdf(path: Path, pages: list[list[str]]) -> None:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>" if len(pages) == 2
        else b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    ]

    for page_index, lines in enumerate(pages):
        page_object_id = 3 + page_index * 2
        content_object_id = page_object_id + 1
        commands = [b"BT", b"/F1 18 Tf", b"72 720 Td"]
        for line_index, line in enumerate(lines):
            if line_index:
                commands.append(b"0 -36 Td")
            commands.extend((b"(", pdf_literal(line), b") Tj"))
        commands.append(b"ET")
        stream = b" ".join(commands)
        objects.extend(
            (
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 {3 + len(pages) * 2} 0 R >> >> "
                    f"/Contents {content_object_id} 0 R >>"
                ).encode("ascii"),
                b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
                + stream + b"\nendstream",
            )
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")

    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(output)


create_pdf(
    FIXTURE_DIR / "synthetic-viewer-fixture.pdf",
    [
        [
            "SYNTHETIC TEST DOCUMENT - NOT A REAL FILING",
            "Revenue increased by 12% in the synthetic test filing.",
        ],
        [
            "SYNTHETIC TEST DOCUMENT - PAGE TWO",
            "Expenses totaled 37 million synthetic units.",
        ],
    ],
)
create_pdf(
    FIXTURE_DIR / "synthetic-second-viewer-fixture.pdf",
    [["SYNTHETIC SECOND DOCUMENT - NOT A REAL FILING", "Different PDF loaded successfully."]],
)
print(f"Generated clearly synthetic PDF viewer fixtures in {FIXTURE_DIR}")
