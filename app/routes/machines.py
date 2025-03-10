from flask import request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from loyaltypro.services.machines import verify_vending_machines
from loyaltypro.models.user import User
from loyaltypro.models.user_machine import UserMachine
from loyaltypro.extensions import db
from datetime import datetime

machines_bp = Blueprint('machines', __name__)

@machines_bp.before_request
def log_request_info():
    current_app.logger.info("=== Machines Request ===")
    current_app.logger.info(f"Headers: {dict(request.headers)}")
    current_app.logger.info("=====================")

@machines_bp.route('/machines', methods=['GET'])
@jwt_required()
def get_machines():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    # 1) Всегда идем в SmartVend
    result = verify_vending_machines(user.api_key, user.user_id)
    if result.get('success'):
        machines_list = result['machines']
        upsert_user_machines(user.id, machines_list)
    else:
        current_app.logger.error(f"Failed to update from SmartVend: {result.get('error')}")
        return jsonify({'error': result.get('error')}), 400

    # 2) Снова берем локальные машины
    local_machines = UserMachine.query.filter_by(user_id=user.id).all()

    # 3) Получаем количество активных акций для каждой машины
    active_promos_count = {}
    for machine in local_machines:
        count = db.session.query(PromotionMachine)\
            .join(Promotion)\
            .filter(
                PromotionMachine.serialNumber == machine.serialNumber,
                Promotion.user_id == user.id,
                Promotion.is_active == True,
                (Promotion.expiration_date.is_(None) | (Promotion.expiration_date > datetime.utcnow()))
            ).count()
        active_promos_count[machine.serialNumber] = count

    # 4) Получаем последние транзакции для каждой машины
    last_transactions = {}
    for machine in local_machines:
        last_transaction = Transaction.query\
            .filter(
                Transaction.machine_id == machine.serialNumber,
                Transaction.user_id == user.id,  # Только транзакции текущего пользователя
                Transaction.discounted_price < Transaction.price  # Только транзакции со скидкой
            )\
            .order_by(Transaction.timestamp.desc())\
            .first()
        
        if last_transaction:
            last_transactions[machine.serialNumber] = last_transaction.timestamp

    # 5) Формируем JSON с добавлением количества активных акций и последних транзакций
    response_data = []
    for um in local_machines:
        last_transaction_time = last_transactions.get(um.serialNumber)
        response_data.append({
            "serialNumber": um.serialNumber,
            "humanName": um.humanName,
            "isDeleted": um.is_deleted,
            "activePromotions": active_promos_count.get(um.serialNumber, 0),
            "lastQrUsed": last_transaction_time.isoformat() if last_transaction_time else None
        })

    return jsonify(response_data), 200 