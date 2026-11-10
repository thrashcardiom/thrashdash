"""Cross-check the JavaScript QR encoder (web/js/qr.js) against the independent Python `qrcode` package.

Skipped when `qrcode` (pip install -r requirements-dev.txt) or Node.js is not available.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

try:
    import qrcode
    from qrcode import constants
    from qrcode.util import MODE_8BIT_BYTE, QRData
except ImportError:  # pragma: no cover
    qrcode = None

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")

# Lengths chosen to land in every version 1-10 and to cover each block layout (1, 2, 4 blocks; unequal groups).
SAMPLES = [
    "a", "hello", "http://192.168.1.20:8765/", "http://192.168.100.200:8765/?d=u-0123456789ab",
    "x" * 14, "y" * 15, "z" * 26, "q" * 42, "w" * 62, "e" * 84, "r" * 106, "t" * 122, "u" * 152, "i" * 180, "o" * 213,
    "Héllo € über/你好",  # non-ASCII: must be encoded as UTF-8 bytes
]


def js_matrices(cases):
    script = (
        "import { encodeQr } from './web/js/qr.js';"
        "const cases = JSON.parse(process.argv[1]);"
        "console.log(JSON.stringify(cases.map(([t, m]) => encodeQr(t, m === null ? {} : { mask: m }).map(r => r.map(d => d ? 1 : 0).join('')))));"
    )
    out = subprocess.run([NODE, "--input-type=module", "-e", script, json.dumps(cases)], cwd=ROOT, capture_output=True, text=True, check=True, timeout=60)
    return json.loads(out.stdout)


def reference(text, mask, size):
    version = (size - 17) // 4
    qr = qrcode.QRCode(version=version, error_correction=constants.ERROR_CORRECT_M, box_size=1, border=0, mask_pattern=mask)
    qr.add_data(QRData(text.encode("utf-8"), mode=MODE_8BIT_BYTE))
    qr.make(fit=False)
    return ["".join("1" if d else "0" for d in row) for row in qr.modules]


@unittest.skipIf(qrcode is None or NODE is None, "needs the `qrcode` package and Node.js")
class QrCrossCheck(unittest.TestCase):
    def test_every_mask_matches_the_reference_for_every_sample(self):
        cases = [[text, mask] for text in SAMPLES for mask in range(8)]
        ours = js_matrices(cases)
        versions = set()
        for (text, mask), matrix in zip(cases, ours):
            versions.add((len(matrix) - 17) // 4)
            self.assertEqual(matrix, reference(text, mask, len(matrix)), f"{text[:30]!r} mask {mask}")
        self.assertEqual(versions, set(range(1, 11)), "the samples should exercise every supported version")

    def test_the_automatic_mask_choice_is_a_valid_mask(self):
        ours = js_matrices([[t, None] for t in SAMPLES])
        for text, matrix in zip(SAMPLES, ours):
            # the chosen mask is whichever one makes the matrix equal to the reference for some mask
            self.assertTrue(any(matrix == reference(text, m, len(matrix)) for m in range(8)), text[:30])

    def test_too_long_text_is_refused(self):
        script = "import { encodeQr } from './web/js/qr.js'; try { encodeQr('x'.repeat(214)); console.log('no error'); } catch (e) { console.log(e.name); }"
        out = subprocess.run([NODE, "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout.strip(), "RangeError")


if __name__ == "__main__":
    unittest.main()
