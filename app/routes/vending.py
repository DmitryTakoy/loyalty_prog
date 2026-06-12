from flask import Blueprint, jsonify, request, current_app
from app.models.promotion import Promotion, PromotionMachine
from app.models.transaction import Transaction
from datetime import datetime, timedelta
from app import db

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
        
    # Для безлимитных акций не проверяем is_used
    if promo.is_single_use and promo.is_used:
        # Одноразовая уже использована
        current_app.logger.info(f"One-time promotion {promo.id} already used")
        return jsonify({}), 200
        
    if promo.expiration_date and promo.expiration_date < now:
        # Проверяем, можно ли продлить акцию
        if promo.is_renewable:
            # Продлеваем на месяц
            promo.expiration_date = now + timedelta(days=30)
            current_app.logger.info(f"Renewed promotion {promo.id} until {promo.expiration_date}")
        else:
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
    elif promo.discount_type == 'free_drinks' or promo.discount_type == 'free_drink':
        current_app.logger.info(f"Returning free drink (100% discount) for promotion {promo.id}, machine {machine}")
        return jsonify({"discount": 100}), 200
    else:
        current_app.logger.info(f"Unknown discount type {promo.discount_type} for promotion {promo.id}")
        return jsonify({}), 200

@vending_bp.route('/completion', methods=['POST'])
def handle_completion():
    try:
        data = request.get_json()
        if not data:
            current_app.logger.warning("No JSON data in completion request")
            return jsonify({"error": "Invalid JSON data"}), 400
            
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

        # Время события: unixtime от автомата (hex), иначе текущее время
        event_time = datetime.utcnow()
        unixtime_hex = data.get('unixtime')
        if unixtime_hex:
            try:
                event_time = datetime.utcfromtimestamp(int(unixtime_hex, 16))
            except Exception as e:
                current_app.logger.warning(f"Failed to parse unixtime: {unixtime_hex}, error: {str(e)}")

        # Применилась ли скидка фактически (для last_used_at и лога транзакции)
        discount_applied = False

        if success:
            # Вендинговый автомат присылает /completion для КАЖДОЙ успешной
            # продажи по отсканированному QR, даже если /request вернул пустой
            # ответ (акция уже исчерпана/неактивна/просрочена или автомат вне
            # списка). В таких случаях реальная скидка не применялась, поэтому
            # засчитывать это как активацию нельзя - иначе activation_count
            # выходит за пределы drinks_limit. См. кейс с
            # "Арлепта 100% ежемесячные ..." где USED=11/12 при лимите 10.
            now = datetime.utcnow()
            is_expired = (
                promo.expiration_date is not None
                and promo.expiration_date < now
                and not promo.is_renewable
            )
            is_exhausted = (
                promo.remaining_uses is not None and promo.remaining_uses <= 0
            )
            already_used = promo.is_single_use and promo.is_used

            if not promo.is_active or is_expired or is_exhausted or already_used:
                current_app.logger.info(
                    f"Completion for promo {promo.id} arrived but discount "
                    f"was not applied (is_active={promo.is_active}, "
                    f"expired={is_expired}, exhausted={is_exhausted}, "
                    f"already_used={already_used}). "
                    f"Skipping activation_count/remaining_uses update."
                )
            else:
                # Обновляем акцию
                if promo.discount_type == 'percentage':
                    if promo.is_single_use:
                        promo.is_used = True
                        promo.is_active = False  # Деактивируем одноразовую акцию после использования
                    # Для многоразовых акций percentage также обновляем статус
                    elif promo.remaining_uses is not None and promo.remaining_uses > 0:
                        promo.remaining_uses -= 1
                        if promo.remaining_uses <= 0:
                            promo.is_used = True
                            promo.is_active = False
                    # Для безлимитных акций просто увеличиваем счетчик активаций
                    elif promo.remaining_uses is None and not promo.is_single_use:
                        # Не помечаем как used, только увеличиваем счетчик
                        pass
                elif promo.discount_type == 'free_drinks' or promo.discount_type == 'free_drink':
                    if promo.remaining_uses is not None and promo.remaining_uses > 0:
                        promo.remaining_uses -= 1
                        if promo.remaining_uses <= 0:
                            promo.is_used = True
                            promo.is_active = False
                    # Если это одноразовая акция free_drink, то помечаем как использованную
                    elif promo.is_single_use:
                        promo.is_used = True
                        promo.is_active = False
                    # Если remaining_uses is None и это одноразовая акция, помечаем как использованную
                    elif promo.remaining_uses is None and promo.is_single_use:
                        promo.is_used = True
                        promo.is_active = False
                    # Если это многоразовая акция free_drink без ограничения на количество использований,
                    # просто увеличиваем счетчик активаций
                    elif promo.remaining_uses is None and not promo.is_single_use:
                        # Не помечаем как used, только увеличиваем счетчик
                        pass
                promo.activation_count += 1
                promo.last_used_at = event_time
                discount_applied = True
        else:
            # Если неуспешная выдача, можно ничего не делать или логгировать
            pass

        # Логируем транзакцию
        trans = Transaction(
            user_id=promo.user_id,
            machine_id=machine,
            product_id=data.get('product'),
            price=data.get('price'),
            discounted_price=data.get('price'),
            promotion_id=promo.id,
            success=discount_applied,
            timestamp=event_time,
        )
        db.session.add(trans)
        db.session.commit()
        
        current_app.logger.info(f"Completed transaction for promotion {promo.id}, machine {machine}")
        return jsonify({"message": "Transaction completed"}), 200
    except Exception as e:
        current_app.logger.error(f"Error processing completion request: {str(e)}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500 