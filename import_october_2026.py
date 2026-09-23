"""Import the verified October 2026 LNR cultural programme."""

import base64
import io
import sqlite3
import uuid
from urllib.request import Request, urlopen

from PIL import Image

from server import DB_PATH, save_event


THEATRE = {
    "institution": "Луганский республиканский театр имени М. Голубовича",
    "contact": "Касса: +7 (857-2) 92-11-83; laymdt@yandex.ru",
    "representative": "Касса театра",
    "direction": "Театральное искусство",
    "source": "https://teatrgolubovicha.ru/afisha",
}
PHILHARMONIC = {
    "institution": "Луганская республиканская академическая филармония",
    "place": "г. Луганск, ул. Ленина, д. 23",
    "contact": "+7 (857-2) 93-23-41; filarmonia64@mail.ru",
    "representative": "Касса филармонии",
    "direction": "Музыкальное искусство",
}
PUPPET = {
    "institution": "Луганский академический театр кукол",
    "place": "г. Луганск, ул. Героя России Зозулина В. Н., д. 7Б",
    "contact": "+7 (857-2) 58-52-44; lg-tk@list.ru",
    "representative": "Касса театра кукол",
    "direction": "Театральное искусство",
}


def theatre(date, time, title, hall, category, description, poster):
    return {
        **THEATRE,
        "date": date,
        "time": time,
        "title": title,
        "place": f"{hall}, г. Луганск, ул. Оборонная, д. 11",
        "category": category,
        "description": description,
        "posterUrl": poster,
        "ticketUrl": "",
    }


def concert(date, time, title, category, description, event_id, session_id, image_stamp):
    source = f"https://quicktickets.ru/lugansk-filarmoniya/e{event_id}"
    return {
        **PHILHARMONIC,
        "date": date,
        "time": time,
        "title": title,
        "category": category,
        "description": description,
        "source": source,
        "ticketUrl": f"https://quicktickets.ru/lugansk-filarmoniya/s{session_id}",
        "posterUrl": f"https://quicktickets.ru/files/o/lugansk-filarmoniya/e/{event_id}/photo.jpg?{image_stamp}",
    }


def puppet(date, time, title, description, official_url, culture_id, poster):
    return {
        **PUPPET,
        "date": date,
        "time": time,
        "title": title,
        "category": "Спектакль",
        "description": description,
        "source": f"https://www.culture.ru/events/{culture_id}",
        "ticketUrl": "",
        "posterUrl": poster,
        "officialUrl": official_url,
    }


EVENTS = [
    theatre("2026-10-02", "17:00", "Академия смеха", "Малый зал", "Спектакль", "Комедия Коки Митани о цензоре и драматурге.", "https://static.tildacdn.com/tild6462-3230-4630-b134-656535616137/_.jpg"),
    theatre("2026-10-03", "15:00", "Ловушка", "Большой зал", "Спектакль", "Даниэль Корбан заявляет об исчезновении жены, но найденную женщину супругой не признает.", "https://static.tildacdn.com/tild3561-3532-4564-b333-383838663765/photo.jpg"),
    concert("2026-10-03", "13:00", "Открытие 81-го концертного сезона", "Концерт", "Симфонический оркестр, скрипачка Юлия Игонина, дирижер Александр Щуров.", 243, 244, 1788937166),
    puppet("2026-10-03", "11:00", "Маугли", "Постановка по Редьярду Киплингу о взрослении, стойкости и человечности.", "https://teatr-kukol-lg.ru/%d0%bc%d0%b0%d1%83%d0%b3%d0%bb%d0%b8/", "7268754/spektakl-maugli", "https://teatr-kukol-lg.ru/wp-content/uploads/2025/12/%D0%BC%D0%B0%D1%83%D0%B3%D0%BB%D0%B81.jpg"),
    puppet("2026-10-03", "13:00", "Маугли", "Постановка по Редьярду Киплингу о взрослении, стойкости и человечности.", "https://teatr-kukol-lg.ru/%d0%bc%d0%b0%d1%83%d0%b3%d0%bb%d0%b8/", "7268754/spektakl-maugli", "https://teatr-kukol-lg.ru/wp-content/uploads/2025/12/%D0%BC%D0%B0%D1%83%D0%B3%D0%BB%D0%B81.jpg"),
    theatre("2026-10-04", "15:00", "Плутни Скапена", "Большой зал", "Спектакль", "Комедия Мольера о ловком слуге Скапене.", "https://static.tildacdn.com/tild3433-3639-4033-b531-376161636265/_.jpg"),
    concert("2026-10-04", "12:00", "Сказки русского двора", "Концерт", "Детская музыкальная программа по мотивам русских сказок и литературы.", 248, 249, 1789647167),
    concert("2026-10-04", "14:00", "Джон Колтрейн: Столетие легенды", "Джазовый концерт", "Концерт к столетию американского саксофониста Джона Колтрейна.", 247, 248, 1789647033),
    puppet("2026-10-04", "11:00", "Незнайка и его друзья", "Приключения Незнайки и его друзей по произведениям Николая Носова.", "https://teatr-kukol-lg.ru/%d0%bd%d0%b8%d0%ba%d0%be%d0%bb%d0%b0%d0%b9-%d0%bd%d0%be%d1%81%d0%be%d0%b2-%d0%bd%d0%b5%d0%b7%d0%bd%d0%b0%d0%b9%d0%ba%d0%b0-%d0%b8-%d0%b5%d0%b3%d0%be-%d0%b4%d1%80%d1%83%d0%b7%d1%8c%d1%8f/", "7268801/spektakl-neznaika-i-ego-druzya", "https://teatr-kukol-lg.ru/wp-content/uploads/2025/07/%D0%B8%D0%B7%D0%BE%D0%B1%D1%80%D0%B0%D0%B6%D0%B5%D0%BD%D0%B8%D0%B5_2025-07-12_011901685.png"),
    puppet("2026-10-04", "13:00", "Незнайка и его друзья", "Приключения Незнайки и его друзей по произведениям Николая Носова.", "https://teatr-kukol-lg.ru/%d0%bd%d0%b8%d0%ba%d0%be%d0%bb%d0%b0%d0%b9-%d0%bd%d0%be%d1%81%d0%be%d0%b2-%d0%bd%d0%b5%d0%b7%d0%bd%d0%b0%d0%b9%d0%ba%d0%b0-%d0%b8-%d0%b5%d0%b3%d0%be-%d0%b4%d1%80%d1%83%d0%b7%d1%8c%d1%8f/", "7268801/spektakl-neznaika-i-ego-druzya", "https://teatr-kukol-lg.ru/wp-content/uploads/2025/07/%D0%B8%D0%B7%D0%BE%D0%B1%D1%80%D0%B0%D0%B6%D0%B5%D0%BD%D0%B8%D0%B5_2025-07-12_011901685.png"),
    theatre("2026-10-07", "14:00", "Распятая юность", "Большой зал", "Опера", "Музыкальный рассказ о подпольной организации «Молодая гвардия».", "https://static.tildacdn.com/tild3535-3166-4562-a232-306237653266/_.jpg"),
    theatre("2026-10-09", "17:00", "Танго на миллион", "Малый зал", "Спектакль", "Романтический триллер о деньгах, лжи и чувствах.", "https://static.tildacdn.com/tild6638-6532-4039-a235-333332323638/__1.jpg"),
    concert("2026-10-09", "16:00", "Негромкая гениальность", "Концерт", "Произведения Яна Френкеля и Микаэла Таривердиева.", 246, 247, 1789553974),
    theatre("2026-10-10", "15:00", "Любовь на счастье нам дана, иль...", "Большой зал", "Спектакль", "Ученые исследуют, могут ли песни о любви сделать людей счастливее.", "https://static.tildacdn.com/tild3962-6630-4363-b238-616339616263/______7.jpg"),
    concert("2026-10-10", "12:00", "Мир ударных инструментов", "Концерт", "Программа о классических и современных ударных инструментах.", 250, 252, 1789975156),
    concert("2026-10-10", "14:00", "Дорогой к солнцу", "Фолк-рок концерт", "Фолк-рок былина о пути от тихой печали к героическому порыву.", 251, 251, 1789727579),
    theatre("2026-10-11", "15:00", "Бабий бунт", "Большой зал", "Спектакль", "Казачки хутора требуют равноправия и решают уйти за Дон.", "https://static.tildacdn.com/tild6633-3130-4632-b534-663163313166/_.jpg"),
    concert("2026-10-11", "12:00", "Как музыка оживать научилась", "Камерный концерт", "Детская классическая программа, где музыка рассказывает истории.", 249, 250, 1789974951),
    concert("2026-10-11", "14:00", "Две грани бытия", "Вокально-органный концерт", "Анна Мокрова, Юлия Берсан и инструменталисты исполнят Генделя, Форе, Сен-Санса и других композиторов.", 252, 253, 1789975350),
    theatre("2026-10-14", "10:00", "Э-э-эх, какая рыба", "Большой зал", "Музыкальная сказка", "Музыкальная сказка.", "https://static.tildacdn.com/tild3734-3765-4137-b665-643232636530/eekh_kakaya_ryba_ito.jpg"),
    theatre("2026-10-15", "10:00", "Э-э-эх, какая рыба", "Большой зал", "Музыкальная сказка", "Музыкальная сказка.", "https://static.tildacdn.com/tild3734-3765-4137-b665-643232636530/eekh_kakaya_ryba_ito.jpg"),
    theatre("2026-10-17", "11:00", "Э-э-эх, какая рыба", "Большой зал", "Музыкальная сказка", "Музыкальная сказка.", "https://static.tildacdn.com/tild3734-3765-4137-b665-643232636530/eekh_kakaya_ryba_ito.jpg"),
    theatre("2026-10-18", "15:00", "Музыка на все времена. Бархатный сезон", "Малый зал", "Концертная программа", "Концертная программа.", "https://static.tildacdn.com/tild3966-3233-4432-b337-373831623739/noroot.png"),
    theatre("2026-10-24", "15:00", "Музыка на все времена. Бархатный сезон", "Малый зал", "Концертная программа", "Концертная программа.", "https://static.tildacdn.com/tild3966-3233-4432-b337-373831623739/noroot.png"),
    theatre("2026-10-25", "11:00", "Наследство волшебника Бахрама", "Малый зал", "Спектакль-сказка", "Пожилой волшебник ищет ученика, которому сможет передать свои знания.", "https://static.tildacdn.com/tild6139-3265-4863-a466-643936326162/__.jpg"),
]


def poster_data(url):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=30) as response:
        source = response.read()
    with Image.open(io.BytesIO(source)) as opened:
        image = opened.convert("RGB")
        image.thumbnail((600, 800), Image.Resampling.LANCZOS)
        for quality in (68, 58, 48, 40):
            output = io.BytesIO()
            image.save(output, "JPEG", quality=quality, optimize=True)
            encoded = base64.b64encode(output.getvalue()).decode("ascii")
            value = "data:image/jpeg;base64," + encoded
            if len(value) <= 120_000:
                return value
    raise ValueError(f"Не удалось уменьшить афишу: {url}")


def admin_user():
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM users WHERE role = 'admin' AND active = 1 ORDER BY created_at LIMIT 1").fetchone()
    if not row:
        raise RuntimeError("Активный администратор не найден")
    return {"id": row["id"], "role": row["role"], "direction": row["direction"]}


def main():
    user = admin_user()
    posters = {}
    for index, item in enumerate(EVENTS, 1):
        image_url = item["posterUrl"]
        official_url = item.get("officialUrl", "")
        if image_url not in posters:
            posters[image_url] = poster_data(image_url)
        source = item["source"]
        payload = {
            **{key: value for key, value in item.items() if key not in {"posterUrl", "officialUrl", "source"}},
            "description": f"{item['description']} Источник: {official_url or source}",
            "poster": posters[image_url],
        }
        key = f"{payload['date']}|{payload['time']}|{payload['title']}|{payload['institution']}"
        event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "culture-plan:october-2026:" + key))
        save_event(payload, user, event_id)
        print(f"[{index:02}/{len(EVENTS)}] {payload['date']} {payload['time']} {payload['title']}")


if __name__ == "__main__":
    main()
