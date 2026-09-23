"""Build the Android APK with aapt and D8 when Gradle's SDK is incomplete.

Compile the current manifest, resources and Java sources, then align and sign
with the same debug key as the original package.
"""

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parent.parent
SDK = Path(os.environ["ANDROID_HOME"])
JAVA = Path(os.environ["JAVA_HOME"]) / "bin" / "java.exe"
JAVAC = JAVA.with_name("javac.exe")
TOOLS = SDK / "build-tools" / "35.0.0"
ANDROID_JAR = SDK / "platforms" / "android-35" / "android.jar"
APP = ROOT / "android-client" / "app"
DESTINATION = ROOT / "dist" / "culture-plan-android-fixed.apk"
SOURCE = APP / "src" / "main" / "java" / "ru" / "cultureplan" / "app" / "MainActivity.java"
ANDROID_NS = "http://schemas.android.com/apk/res/android"


def run(*args):
    subprocess.run(list(map(str, args)), check=True)


def main():
    r8 = Path(sys.argv[1]).resolve()
    if not (r8.is_file() and SOURCE.is_file()):
        raise FileNotFoundError("Нужны R8 и MainActivity.java")
    with tempfile.TemporaryDirectory(dir=Path(os.environ.get("TEMP", str(ROOT)))) as temporary:
        build = Path(temporary)
        classes = build / "classes"
        dex = build / "dex"
        classes.mkdir()
        dex.mkdir()
        run(JAVAC, "-encoding", "UTF-8", "-source", "17", "-target", "17",
            "-classpath", ANDROID_JAR, "-d", classes, SOURCE)
        run(JAVA, "-cp", r8, "com.android.tools.r8.D8", "--min-api", "24", "--lib", ANDROID_JAR,
            "--output", dex, *classes.rglob("*.class"))
        if not (dex / "classes.dex").exists():
            raise RuntimeError("Не удалось создать classes.dex")
        ElementTree.register_namespace("android", ANDROID_NS)
        manifest = ElementTree.parse(APP / "src" / "main" / "AndroidManifest.xml")
        root = manifest.getroot()
        root.set("package", "ru.cultureplan.app")
        root.set(f"{{{ANDROID_NS}}}versionCode", "2")
        root.set(f"{{{ANDROID_NS}}}versionName", "1.1.0")
        sdk = ElementTree.Element("uses-sdk", {
            f"{{{ANDROID_NS}}}minSdkVersion": "24",
            f"{{{ANDROID_NS}}}targetSdkVersion": "35",
        })
        root.insert(0, sdk)
        manifest_path = build / "AndroidManifest.xml"
        manifest.write(manifest_path, encoding="utf-8", xml_declaration=True)
        resources = build / "resources.apk"
        run(TOOLS / "aapt.exe", "package", "-f", "-M", manifest_path,
            "-S", APP / "src" / "main" / "res", "-I", ANDROID_JAR, "-F", resources)
        unsigned = build / "unsigned.apk"
        with zipfile.ZipFile(resources) as compiled, zipfile.ZipFile(unsigned, "w") as updated:
            for entry in compiled.infolist():
                updated.writestr(entry, compiled.read(entry.filename))
            updated.write(dex / "classes.dex", "classes.dex", compress_type=zipfile.ZIP_DEFLATED)
        aligned = build / "aligned.apk"
        run(TOOLS / "zipalign.exe", "-f", "4", unsigned, aligned)
        run(TOOLS / "apksigner.bat", "sign", "--ks", Path.home() / ".android" / "debug.keystore",
            "--ks-pass", "pass:android", "--key-pass", "pass:android", "--out", DESTINATION, aligned)
        run(TOOLS / "apksigner.bat", "verify", "--print-certs", DESTINATION)
    print(f"Готовый APK: {DESTINATION}")


if __name__ == "__main__":
    main()
