"""Export a read-only, public events snapshot for static Vercel hosting."""

import base64
import json
import os
import shutil
import sqlite3
from pathlib import Path

from server import DB_PATH


ROOT = Path(__file__).resolve().parent
OUTPUT = Path(os.environ.get("VERCEL_PREVIEW_OUTPUT", str(ROOT / "vercel-preview"))).resolve()
SOURCE = ROOT / "vercel-preview-src" / "index.html"
APK_NAME = "culture-plan-android-fixed.apk"
PUBLIC_FIELDS = ("id", "title", "institution", "date", "time", "place", "city", "category", "description", "ticketUrl", "pushkinCard")


def main():
    if not DB_PATH.exists():
        raise FileNotFoundError("Для экспорта нужна локальная calendar.db")
    if OUTPUT.exists():
        if os.getenv("VERCEL_PRESERVE_LINK") == "1":
            for entry in OUTPUT.iterdir():
                if entry.name in {".vercel", ".env.local", ".gitignore"}:
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
        else:
            shutil.rmtree(OUTPUT)
    poster_dir = OUTPUT / "posters"
    poster_dir.mkdir(parents=True)
    shutil.copyfile(SOURCE, OUTPUT / "index.html")
    shutil.copyfile(ROOT / "dist" / APK_NAME, OUTPUT / APK_NAME)
    public_events = []
    with sqlite3.connect(DB_PATH) as connection:
        for event_id, raw in connection.execute("SELECT id, data FROM events ORDER BY json_extract(data, '$.date'), json_extract(data, '$.time')"):
            event = json.loads(raw)
            if not event["date"].startswith("2026-10-"):
                continue
            public = {field: event.get(field, "") for field in PUBLIC_FIELDS if field != "id"}
            public["pushkinCard"] = event.get("pushkinCard") is True
            public["id"] = event_id
            poster = event.get("poster", "")
            if poster.startswith("data:image/jpeg;base64,"):
                (poster_dir / f"{event_id}.jpg").write_bytes(base64.b64decode(poster.split(",", 1)[1]))
                public["poster"] = f"/posters/{event_id}.jpg"
            else:
                public["poster"] = ""
            public_events.append(public)
    (OUTPUT / "events.json").write_text(json.dumps(public_events, ensure_ascii=False), encoding="utf-8")
    print(f"Экспортировано {len(public_events)} мероприятий и {len(list(poster_dir.iterdir()))} афиш")


if __name__ == "__main__":
    main()
