"""Minimal PDF writer (standard library only) for report cards.

Supports text in Helvetica / Helvetica-Bold, lines, filled rectangles and
multiple A4 pages — enough for a clean, printable result document.
"""


def _esc(text):
    s = str(text).encode("cp1252", "replace").decode("cp1252")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class PDF:
    W, H = 595.28, 841.89  # A4 in points

    def __init__(self, title="Document"):
        self.title = title
        self.pages = []
        self.ops = None
        self.new_page()

    def new_page(self):
        self.ops = []
        self.pages.append(self.ops)

    def text(self, x, y, s, size=10, bold=False, color=(0, 0, 0)):
        r, g, b = color
        self.ops.append(f"BT {r:.3f} {g:.3f} {b:.3f} rg /{'F2' if bold else 'F1'} {size} Tf "
                        f"{x:.2f} {self.H - y:.2f} Td ({_esc(s)}) Tj ET")

    def text_right(self, x, y, s, size=10, bold=False, color=(0, 0, 0)):
        self.text(x - self.width(s, size, bold), y, s, size, bold, color)

    @staticmethod
    def width(s, size, bold=False):
        # Average Helvetica glyph width is ~0.5em (bold ~0.55em); good enough for right alignment.
        return len(str(s)) * size * (0.55 if bold else 0.5)

    def line(self, x1, y1, x2, y2, width=0.6, color=(0.6, 0.6, 0.6)):
        r, g, b = color
        self.ops.append(f"{r:.3f} {g:.3f} {b:.3f} RG {width} w {x1:.2f} {self.H - y1:.2f} m "
                        f"{x2:.2f} {self.H - y2:.2f} l S")

    def rect(self, x, y, w, h, fill=(0.93, 0.95, 0.98)):
        r, g, b = fill
        self.ops.append(f"{r:.3f} {g:.3f} {b:.3f} rg {x:.2f} {self.H - y - h:.2f} {w:.2f} {h:.2f} re f")

    def output(self):
        objs = []

        def add(body):
            objs.append(body)
            return len(objs)

        catalog = add(None)
        pages = add(None)
        f1 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        kids = []
        for ops in self.pages:
            stream = "\n".join(ops).encode("cp1252", "replace")
            content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
            kids.append(add(f"<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 {self.W} {self.H}] "
                            f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> >> /Contents {content} 0 R >>"))
        objs[catalog - 1] = f"<< /Type /Catalog /Pages {pages} 0 R >>"
        objs[pages - 1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
        info = add(f"<< /Title ({_esc(self.title)}) /Producer (PW Batch Online Examination System) >>")

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, 1):
            offsets.append(len(out))
            data = body if isinstance(body, bytes) else body.encode("cp1252", "replace")
            out += b"%d 0 obj\n" % i + data + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        out += (b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                % (len(objs) + 1, catalog, info, xref))
        return bytes(out)
