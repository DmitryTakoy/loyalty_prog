from flask import Blueprint, jsonify, request, current_app
from app.models.promotion import Promotion, PromotionMachine
from app.models.transaction import Transaction
from datetime import datetime

# Create a blueprint specifically for vending machines without a prefix
vending_bp = Blueprint('vending', __name__)

@vending_bp.route('/test-vending', methods=['GET'])
def test_vending_route():
    current_app.logger.info('Vending test route called')
    return jsonify({'message': 'Vending API is working without /api prefix'}), 200

@vending_bp.route('/request', methods=['POST'])
def handle_request():
    data = request.get_json()
    customer_id = data.get('customer')  # уникальный ID акции/QR
    machine = data.get('machine')
    product = data.get('product')
    unixtime_hex = data.get('unixtime', None)
    
    # Log incoming request details
    current_app.logger.info(f"Received request from machine: {machine}, customer_id: {customer_id}, product: {product}, unixtime: {unixtime_hex}")
    current_app.logger.info(f"Full request data: {data}")

    if not customer_id or not machine:
        current_app.logger.warning(f"Missing customer or machine in request: {data}")
        return jsonify({"error": "Missing customer or machine"}), 400

    # 1) Ищем акцию
    promo = Promotion.query.filter_by(customer_id=customer_id).first()
    if not promo:
        # Нет такой акции
        current_app.logger.info(f"No promotion found for customer_id: {customer_id}")
        return jsonify({}), 200

    # 2) Проверяем, что promo.active, не истекла, не исчерпана и т.д.
    now = datetime.utcnow()
    if not promo.is_active:
        current_app.logger.info(f"Promotion {promo.id} is not active")
        return jsonify({}), 200
    if promo.is_used:
        # Одноразовая уже использована
        current_app.logger.info(f"One-time promotion {promo.id} already used")
        return jsonify({}), 200
    if promo.expiration_date and promo.expiration_date < now:
        # истекла
        current_app.logger.info(f"Promotion {promo.id} expired on {promo.expiration_date}")
        return jsonify({}), 200
    if promo.remaining_uses is not None and promo.remaining_uses <= 0:
        # уже исчерпана
        current_app.logger.info(f"Promotion {promo.id} has no remaining uses")
        return jsonify({}), 200

    # 3) Проверяем machine: есть ли такая запись в PromotionMachine
    pm = PromotionMachine.query.filter_by(
        promotion_id=promo.id,
        serialNumber=machine
    ).first()
    if not pm:
        # Акция не распространяется на этот автомат
        current_app.logger.info(f"Promotion {promo.id} not available for machine {machine}")
        return jsonify({}), 200

    # 4) Вычисляем discount
    if promo.discount_type == 'percentage':
        current_app.logger.info(f"Returning percentage discount {promo.discount_value} for promotion {promo.id}, machine {machine}")
        return jsonify({"discount": promo.discount_value}), 200
    elif promo.discount_type == 'free_drinks':
        current_app.logger.info(f"Returning free drink (100% discount) for promotion {promo.id}, machine {machine}")
        return jsonify({"discount": 100}), 200
    else:
        current_app.logger.info(f"Unknown discount type {promo.discount_type} for promotion {promo.id}")
        return jsonify({}), 200

@vending_bp.route('/completion', methods=['POST'])
def handle_completion():
    data = request.get_json()
    customer_id = data.get('customer')
    machine = data.get('machine')
    success = data.get('success', False)
    
    # Log completion request
    current_app.logger.info(f"Received completion request from machine: {machine}, customer_id: {customer_id}, success: {success}")
    current_app.logger.info(f"Full completion data: {data}")

    if not customer_id or not machine:
        current_app.logger.warning(f"Missing customer or machine in completion request: {data}")
        return jsonify({"error": "Missing customer or machine"}), 400

    promo = Promotion.query.filter_by(customer_id=customer_id).first()
    if not promo:
        current_app.logger.info(f"No promotion found for customer_id: {customer_id} in completion request")
        return jsonify({"message": "No such promotion"}), 200

    # Проверяем machine
    pm = PromotionMachine.query.filter_by(
        promotion_id=promo.id,
        serialNumber=machine
    ).first()
    if not pm:
        # Акция не распространяется на этот автомат - но раз уж success, ничего не делаем
        current_app.logger.info(f"Promotion {promo.id} not valid for machine {machine} in completion request")
        return jsonify({"message": "Promotion not valid for this machine"}), 200

    if success:
        # Обновляем акцию
        if promo.discount_type == 'percentage':
            if promo.is_single_use:
                promo.is_used = True
        elif promo.discount_type == 'free_drinks':
            if promo.remaining_uses is not None and promo.remaining_uses > 0:
                promo.remaining_uses -= 1
                if promo.remaining_uses <= 0:
                    promo.is_used = True
        promo.activation_count += 1
    else:
        # Если неуспешная выдача, можно ничего не делать или логгировать
        pass

    # Логируем транзакцию
    trans = Transaction(
        promotion_id=promo.id,
        machine_id=machine,
        product_id=data.get('product'),
        price=data.get('price'),
        discounted_price=data.get('price'),
    )
    # Если нужна timestamp
    unixtime_hex = data.get('unixtime')
    if unixtime_hex:
        try:
            ts = int(unixtime_hex, 16)
            trans.timestamp = datetime.utcfromtimestamp(ts)
        except:
            pass
    
    db = current_app.extensions['sqlalchemy'].db
    db.session.add(trans)
    db.session.commit()
    
    current_app.logger.info(f"Completed transaction for promotion {promo.id}, machine {machine}")
    return jsonify({"message": "Transaction completed"}), 200 