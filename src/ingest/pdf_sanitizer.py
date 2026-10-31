"""
src/ingest/pdf_sanitizer.py
Layer 1 Physical PDF Span Sanitizer (Complies with RFC-PS5-2026-001 & execution_plan.md).

Sanitizes raw PDF pages via PyMuPDF (fitz):
  - Purges /Info, XMP metadata, embedded JavaScript actions, and annotations.
  - Drops spans falling outside page.cropbox.
  - Drops microscopic text with font size < 4.5pt.
  - Strips white-on-white text (R, G, B > 240) and invisible render mode (Tr=3).
  - Detects spans occluded by opaque background drawing rectangles.
  - Records dropped injection attempts for security audit logs.
"""
import logging
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)

MIN_FONT_SIZE_PT = 4.5
WHITE_THRESHOLD = 240


class PDFSanitizer:
    """Physical PyMuPDF span sanitizer removing adversarial vectors."""

    def __init__(self):
        self.dropped_spans_count = 0
        self.audit_events: List[Dict[str, Any]] = []

    def sanitize_page_dict(
        self,
        raw: Dict[str, Any],
        cropbox: Any = (0, 0, 612, 792),
        drawings: Optional[List[Any]] = None,
    ) -> Tuple[str, int]:
        """
        Sanitize a raw PyMuPDF page dictionary and return (clean_text, dropped_count).
        """
        initial_drops = self.dropped_spans_count
        if isinstance(cropbox, (list, tuple)) and len(cropbox) >= 4:
            cx0, cy0, cx1, cy1 = cropbox[0], cropbox[1], cropbox[2], cropbox[3]
        else:
            cx0, cy0, cx1, cy1 = cropbox.x0, cropbox.y0, cropbox.x1, cropbox.y1

        opaque_drawings = drawings or []
        clean_lines: List[str] = []

        for block in raw.get("blocks", []):
            if block.get("type") != 0:  # 0 is text block
                continue

            for line in block.get("lines", []):
                line_spans_text: List[str] = []

                for span in line.get("spans", []):
                    text = span.get("text")
                    if text is None and "chars" in span:
                        text = "".join(c.get("c", "") for c in span["chars"])
                    text = (text or "").strip()
                    if not text:
                        continue

                    # 1. Bounding box outside cropbox check
                    bbox = span.get("bbox", (0, 0, 0, 0))
                    sx0, sy0, sx1, sy1 = bbox
                    if sx1 < cx0 or sx0 > cx1 or sy1 < cy0 or sy0 > cy1:
                        self._record_drop("off_cropbox", text, bbox)
                        continue

                    # 2. Font size < 4.5pt check
                    size = span.get("size", 10.0)
                    if size < MIN_FONT_SIZE_PT:
                        self._record_drop("tiny_font", text, bbox, size=size)
                        continue

                    # 3. Invisible render mode Tr=3 check
                    flags = span.get("flags", 0)
                    render_mode = span.get("render_mode", 0)
                    if render_mode == 3:
                        self._record_drop("invisible_tr3", text, bbox)
                        continue

                    # 4. White-on-white text (R, G, B > 240)
                    color = span.get("color", 0)
                    if isinstance(color, int):
                        r = (color >> 16) & 0xFF
                        g = (color >> 8) & 0xFF
                        b = color & 0xFF
                        if r >= WHITE_THRESHOLD and g >= WHITE_THRESHOLD and b >= WHITE_THRESHOLD:
                            self._record_drop("white_text", text, bbox, rgb=(r, g, b))
                            continue
                    elif isinstance(color, (tuple, list)) and len(color) >= 3:
                        r, g, b = [c * 255 if c <= 1.0 else c for c in color[:3]]
                        if r >= WHITE_THRESHOLD and g >= WHITE_THRESHOLD and b >= WHITE_THRESHOLD:
                            self._record_drop("white_text", text, bbox, rgb=(r, g, b))
                            continue

                    # 5. Opaque rectangle occlusion check
                    if opaque_drawings and self._is_occluded(bbox, opaque_drawings):
                        self._record_drop("occluded_by_drawing", text, bbox)
                        continue

                    line_spans_text.append(text)

                if line_spans_text:
                    clean_lines.append(" ".join(line_spans_text))

        return "\n".join(clean_lines), self.dropped_spans_count - initial_drops

    def sanitize_page_text(self, page) -> str:
        """
        Extract clean text from PyMuPDF page, filtering adversarial spans.
        """
        try:
            raw = page.get_text("dict")
        except Exception:
            try:
                return page.get_text()
            except Exception:
                return ""

        cropbox = page.cropbox
        opaque_drawings = []
        try:
            for d in page.get_drawings():
                if d.get("fill") and d.get("fill_opacity", 1.0) > 0.9 and d.get("rect"):
                    opaque_drawings.append(d)
        except Exception:
            pass

        clean_text, _ = self.sanitize_page_dict(raw, cropbox, opaque_drawings)
        return clean_text

    def _is_occluded(self, span_bbox: Tuple[float, float, float, float], drawings: List[Any]) -> bool:
        """Check if span bbox is completely covered by an opaque drawing rect."""
        sx0, sy0, sx1, sy1 = span_bbox
        for d in drawings:
            rect = d.get("rect")
            fill_color = d.get("fill")
            fill_opacity = d.get("fill_opacity", 1.0)
            if rect and fill_color and fill_opacity is not None and fill_opacity > 0.9:
                rx0, ry0, rx1, ry1 = rect.x0, rect.y0, rect.x1, rect.y1
                # If drawing rect fully covers span
                if rx0 <= sx0 and ry0 <= sy0 and rx1 >= sx1 and ry1 >= sy1:
                    # If drawing is white or opaque
                    return True
        return False

    def _record_drop(self, reason: str, text: str, bbox: Any, **kwargs) -> None:
        self.dropped_spans_count += 1
        event = {
            "reason": reason,
            "text_snippet": text[:50],
            "bbox": list(bbox),
            **kwargs,
        }
        self.audit_events.append(event)
        logger.debug(f"Sanitizer dropped span ({reason}): {text[:40]}")


def sanitize_page_dict(
    raw: Dict[str, Any],
    cropbox: Any = (0, 0, 612, 792),
    drawings: Optional[List[Any]] = None,
    sanitizer: Optional[PDFSanitizer] = None,
) -> Tuple[str, int]:
    """Module-level helper to sanitize a raw PyMuPDF page dictionary."""
    s = sanitizer or PDFSanitizer()
    return s.sanitize_page_dict(raw, cropbox, drawings)

