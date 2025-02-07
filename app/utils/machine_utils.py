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
    """
    now = datetime.utcnow()

    # Соберём все serialNumber из machines_list
    incoming_serials = [m["serialNumber"] for m in machines_list]

    # Чтобы ускорить поиск, вытащим все UserMachine, у которых serialNumber в incoming_serials
    existing_machines = UserMachine.query.filter(
        UserMachine.user_id == user_db_id,
        UserMachine.serialNumber.in_(incoming_serials)
    ).all()

    existing_map = {um.serialNumber: um for um in existing_machines}

    for m in machines_list:
        serial = m["serialNumber"]
        human = m.get("humanName", "Unknown")

        if serial in existing_map:
            # обновим
            existing_map[serial].humanName = human
            existing_map[serial].updated_at = now
        else:
            # создаём
            new_um = UserMachine(
                user_id = user_db_id,
                serialNumber = serial,
                humanName = human,
                updated_at = now
            )
            db.session.add(new_um)

    db.session.commit()