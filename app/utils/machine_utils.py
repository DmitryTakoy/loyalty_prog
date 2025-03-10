from datetime import datetime
from app.models.auth import UserMachine
from app import db

def upsert_user_machines(user_db_id, machines_list):
    """
    user_db_id — это user.id из локальной БД
    machines_list — список словарей [{serialNumber, humanName}, ...]

    По каждому serialNumber делаем upsert:
      - Если такой запись есть, обновим humanName + updated_at.
      - Если нет, создадим новую.
      - Если машина есть в БД, но отсутствует в machines_list, пометим её как удаленную.
    """
    now = datetime.utcnow()

    # Соберём все serialNumber из machines_list
    incoming_serials = [m["serialNumber"] for m in machines_list]

    # Получим все машины пользователя
    all_user_machines = UserMachine.query.filter(
        UserMachine.user_id == user_db_id
    ).all()

    # Чтобы ускорить поиск, вытащим все UserMachine, у которых serialNumber в incoming_serials
    existing_machines = [m for m in all_user_machines if m.serialNumber in incoming_serials]
    existing_map = {um.serialNumber: um for um in existing_machines}

    # Обновляем существующие и создаем новые машины
    for m in machines_list:
        serial = m["serialNumber"]
        human = m.get("humanName", "Unknown")

        if serial in existing_map:
            # обновим
            existing_map[serial].humanName = human
            existing_map[serial].updated_at = now
            existing_map[serial].is_deleted = False  # Убедимся, что машина помечена как активная
        else:
            # создаём
            new_um = UserMachine(
                user_id = user_db_id,
                serialNumber = serial,
                humanName = human,
                updated_at = now,
                is_deleted = False
            )
            db.session.add(new_um)

    # Помечаем машины, которых нет в incoming_serials, как удаленные
    for machine in all_user_machines:
        if machine.serialNumber not in incoming_serials:
            machine.is_deleted = True
            machine.updated_at = now

    db.session.commit()