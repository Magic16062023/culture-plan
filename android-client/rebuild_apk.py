"""Rebuild the Java-only APK from an existing package when SDK Build Tools are incomplete.

The original package provides its compiled resources and manifest. Compile the
updated Java classes, replace classes.dex, align and sign with the same debug key.
"""

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SDK = Path(os.environ["ANDROID_HOME"])
JAVA = Path(os.environ["JAVA_HOME"]) / "bin" / "java.exe"
JAVAC = JAVA.with_name("javac.exe")
TOOLS = SDK / "build-tools" / "35.0.0"
ANDROID_JAR = SDK / "platforms" / "android-35" / "android.jar"
ORIGINAL = ROOT / "dist" / "culture-plan-android-1.0.0.apk"
DESTINATION = ROOT / "dist" / "culture-plan-android-fixed.apk"
SOURCE = ROOT / "android-client" / "app" / "src" / "main" / "java" / "ru" / "cultureplan" / "app" / "MainActivity.java"


def run(*args):
    subprocess.run(list(map(str, args)), check=True)


def main():
    r8 = Path(sys.argv[1]).resolve()
    if not (r8.is_file() and ORIGINAL.is_file() and SOURCE.is_file()):
        raise FileNotFoundError("Нужны R8, исходный APK и MainActivity.java")
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
        unsigned = build / "unsigned.apk"
        with zipfile.ZipFile(ORIGINAL) as old, zipfile.ZipFile(unsigned, "w") as updated:
            for entry in old.infolist():
                if entry.filename == "classes.dex" or entry.filename.startswith("META-INF/"):
                    continue
                updated.writestr(entry, old.read(entry.filename))
            updated.write(dex / "classes.dex", "classes.dex", compress_type=zipfile.ZIP_DEFLATED)
        aligned = build / "aligned.apk"
        run(TOOLS / "zipalign.exe", "-f", "4", unsigned, aligned)
        run(TOOLS / "apksigner.bat", "sign", "--ks", Path.home() / ".android" / "debug.keystore",
            "--ks-pass", "pass:android", "--key-pass", "pass:android", "--out", DESTINATION, aligned)
        run(TOOLS / "apksigner.bat", "verify", "--print-certs", DESTINATION)
    print(f"Готовый APK: {DESTINATION}")


if __name__ == "__main__":
    main()
