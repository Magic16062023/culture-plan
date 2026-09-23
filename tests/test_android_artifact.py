"""Check the downloadable Android client opens the permanent published address."""

import unittest
import zipfile
from pathlib import Path


APK = Path(__file__).resolve().parents[1] / "dist" / "culture-plan-android-fixed.apk"


class AndroidArtifactTest(unittest.TestCase):
    def test_signed_apk_contains_permanent_site_address(self):
        with zipfile.ZipFile(APK) as package:
            self.assertIsNone(package.testzip())
            self.assertIn("META-INF/ANDROIDD.RSA", package.namelist())
            dex = package.read("classes.dex")
        self.assertIn(b"https://vercel-preview-merqurys-5851.vercel.app", dex)
        self.assertNotIn(b"https://vercel-preview-nwwon7ypm-merqurys-5851.vercel.app", dex)


if __name__ == "__main__":
    unittest.main()
