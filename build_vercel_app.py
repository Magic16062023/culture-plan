"""Package the full application without bundling the private local database."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT = Path(os.environ.get("VERCEL_APP_OUTPUT", str(ROOT / "vercel-app"))).resolve()


def main():
    if OUTPUT == ROOT or ROOT not in OUTPUT.parents and OUTPUT == ROOT.parent:
        raise ValueError("Выберите отдельный каталог для сборки")
    env = os.environ.copy()
    env["VERCEL_PREVIEW_OUTPUT"] = str(OUTPUT)
    env["VERCEL_PRESERVE_LINK"] = "1"
    subprocess.run([sys.executable, str(ROOT / "build_vercel_preview.py")], env=env, check=True)
    (OUTPUT / "events.json").replace(OUTPUT / "public-events-seed.json")
    for name in ("index.html", "places.json", "server.py", "postgres_db.py", "app.py", "requirements.txt", "vercel.json"):
        shutil.copyfile(ROOT / name, OUTPUT / name)
    qr = ROOT / "dist" / "culture-plan-full-page-qr.png"
    if qr.exists():
        shutil.copyfile(qr, OUTPUT / qr.name)
    print(f"Полное приложение подготовлено в {OUTPUT}. Приватная база не копировалась.")


if __name__ == "__main__":
    main()
