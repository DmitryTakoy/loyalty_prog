from flask import Blueprint, jsonify, request, render_template, send_file, current_app
from flask import send_file
from app import db
from app.models.transaction import Transaction
from app.models.promotion import Promotion, PromotionMachine
from app.models.mass_generation import MassGeneration
from app.utils.work_utils import create_qr_code_image, save_archive, generate_unique_code
from app.utils.machine_utils import upsert_user_machines
from datetime import datetime, timedelta
import os
import io
import zipfile
import logging
from flask import make_response
from app.utils.auth_utils import (
    generate_jwt_token,
    send_verification_email,
    verify_vending_machines
)
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.utils.auth_utils import verify_vending_machines
from app.models.auth import User, UserMachine
from sqlalchemy import func

main = Blueprint('main', __name__)

# Добавьте тестовый роут
@main.route('/test', methods=['GET'])
def test_route():
    current_app.logger.info('Test route called')
    return jsonify({'message': 'API is working'}), 200

@main.before_request
def skip_jwt_for_options():
    if request.method == 'OPTIONS':
        # Подменить endpoint на что-то, что не требует @jwt_required
        return

# Рендеринг главной страницы
@main.route('/')
def index():
    return render_template('main.html')


@main.route('/mass_generations', methods=['GET'])
@jwt_required()
def get_mass_generations():
    try:
        user_id = get_jwt_identity()
        include_deleted = request.args.get('include_deleted', 'false').lower() == 'true'
        
        # Базовый запрос
        query = MassGeneration.query.filter_by(user_id=user_id)
        
        # По умолчанию не включаем удаленные массовые генерации
        if not include_deleted:
            query = query.filter_by(is_deleted=False)
        
        # Получаем все массовые генерации
        mass_generations = query.all()
        
        return jsonify([{
            'id': mg.id,
            'name': mg.name,
            'discount_type': mg.discount_type,
            'discount_value': mg.discount_value,
            'is_single_use': mg.is_single_use,
            'is_renewable': mg.is_renewable,
            'expiration_date': mg.expiration_date.isoformat() if mg.expiration_date else None,
            'creation_date': mg.creation_date.isoformat(),
            'archive_filename': mg.archive_filename,
            'is_deleted': mg.is_deleted,
            'deleted_at': mg.deleted_at.isoformat() if mg.deleted_at else None,
            'promotions_count': len(mg.promotions)
        } for mg in mass_generations]), 200
    except Exception as e:
        current_app.logger.error(f"Error fetching mass generations: {str(e)}")
        return jsonify({"error": "Failed to fetch mass generations"}), 500

# Пример: в main.py или другом routes-файле
@main.route('/promotion/<int:promotion_id>/renew', methods=['PUT'])
def renew_promotion(promotion_id):
    promo = Promotion.query.get(promotion_id)
    if not promo:
        return jsonify({'error': 'Promotion not found'}), 404
    
    # Логика "продления" зависит от вашего ТЗ. Например, продлим expiration_date на 30 дней
    from datetime import datetime, timedelta
    now = datetime.utcnow()

    # Если акция вообще не имеет expiration_date, зададим новую, если нужно
    # Или если уже была дата, прибавим к ней 30 дней
    if promo.expiration_date is None:
        promo.expiration_date = now + timedelta(days=30)
    else:
        if promo.expiration_date < now:
            # если уже истекла, начинаем с "сейчас + 30 дней"
            promo.expiration_date = now + timedelta(days=30)
        else:
            # если ещё не истекла, прибавим 30 дней
            promo.expiration_date += timedelta(days=30)
    
    # Можно обнулить activation_count, remaining_uses и т.д.:
    # promo.activation_count = 0
    # if promo.discount_type == 'free_drinks':
    #     promo.remaining_uses = promo.drinks_limit
    # elif promo.is_single_use:
    #     promo.remaining_uses = 1
    # else:
    #     promo.remaining_uses = None

    promo.is_used = False  # Можно сбрасывать, если было is_used

    db.session.commit()
    return jsonify({'message': 'Promotion renewed successfully'}), 200

@main.route('/request', methods=['POST'])
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

@main.route('/completion', methods=['POST'])
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

    db.session.add(trans)
    db.session.commit()

    return jsonify({"message": "Transaction completed"}), 200


@main.route('/generate_codes', methods=['POST'])
@jwt_required()
def generate_codes():
    try:
        data = request.json
        print("Received data:", data)  # Добавьте логирование
        
        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404

        # Основные поля
        num_codes = data.get('num_codes', 1)
        discount_type = data.get('discount_type', 'percentage')
        discount_value = data.get('discount_value', 0)
        is_single_use = data.get('is_single_use', True)
        is_renewable = data.get('is_renewable', False)
        expiration_date_str = data.get('expiration_date', None)
        name = data.get('name', 'MassGeneratedPromo')

        # Список машин (передаётся с фронта)
        machines = data.get('machines', [])

        # Парсим дату
        if expiration_date_str:
            expiration_date = datetime.fromisoformat(expiration_date_str)
        else:
            expiration_date = None

        # 1) Создаем запись о массовой генерации
        mass_generation = MassGeneration(
            name=name,
            discount_type=discount_type,
            discount_value=discount_value,
            is_single_use=is_single_use,
            is_renewable=is_renewable,
            expiration_date=expiration_date,
            user_id=user.id,
        )
        db.session.add(mass_generation)
        db.session.flush()  # чтобы получить mass_generation.id до цикла

        codes = []

        # 2) Создаем in-memory zip-файл
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'a', zipfile.ZIP_DEFLATED) as zip_file:
            codes_txt = io.StringIO()

            for _ in range(num_codes):
                code = generate_unique_code()  # Уникальный customer_id
                img = create_qr_code_image(code)

                # Сохраняем PNG во временный буфер
                img_bytes = io.BytesIO()
                img.save(img_bytes, format='PNG')
                img_bytes.seek(0)

                # Добавляем в zip-архив
                img_filename = f"{code}.png"
                zip_file.writestr(img_filename, img_bytes.getvalue())

                # Запоминаем код для последующего списка codes.txt
                codes.append(code)

                # 3) Создаем Promotion (один на каждый сгенерированный код)
                promotion = Promotion(
                    name=name,
                    discount_type=discount_type,
                    discount_value=discount_value,
                    is_single_use=is_single_use,
                    is_renewable=is_renewable,
                    expiration_date=expiration_date,
                    creation_date=datetime.utcnow(),
                    is_active=True,
                    activation_count=0,
                    remaining_uses=None,  # Можно логически рассчитать, если нужно
                    mass_generation_id=mass_generation.id,
                    customer_id=code,
                    user_id=get_jwt_identity()  # берем user_id из JWT
                )
                db.session.add(promotion)
                db.session.flush()  # чтобы получить promotion.id

                # 4) Привязываем каждую машину к созданной акции
                for machine_dict in machines:
                    pm = PromotionMachine(
                        promotion_id=promotion.id,
                        serialNumber=machine_dict.get('serialNumber', ''),
                        humanName=machine_dict.get('humanName', ''),
                        mass_generation_id=mass_generation.id
                    )
                    db.session.add(pm)

            # 5) Добавляем все сгенерированные коды в файл codes.txt внутри архива
            codes_txt.write('\n'.join(codes))
            zip_file.writestr('codes.txt', codes_txt.getvalue())

        # 6) Коммитим добавленные записи (mass_generation, promotions, machines)
        db.session.commit()

        # Сохраняем zip-файл в UPLOAD_FOLDER
        zip_buffer.seek(0)
        archive_filename = f"mass_generation_{mass_generation.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        upload_folder = current_app.config['UPLOAD_FOLDER']
        archive_path = os.path.join(upload_folder, archive_filename)
        
        # Убедимся, что папка существует
        upload_folder = current_app.config['UPLOAD_FOLDER']
        if not os.path.exists(upload_folder):
            os.makedirs(upload_folder)

        with open(archive_path, 'wb') as f:
            f.write(zip_buffer.getvalue())

        # Обновляем запись массовой генерации с именем архива
        mass_generation.archive_filename = archive_filename
        db.session.commit()

        # Возвращаем клиенту zip
        zip_buffer.seek(0)
        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name='codes_and_images.zip'
        )

    except Exception as e:
        print("Generate codes error:", str(e))
        return jsonify({'error': str(e)}), 500


@main.route('/machines', methods=['GET'])
@jwt_required()
def get_machines():
    try:
        user_id = get_jwt_identity()
        current_app.logger.info(f"Getting machines for user {user_id}")
        
        user = User.query.get(user_id)
        if not user:
            current_app.logger.error(f"User {user_id} not found")
            return jsonify({'error': 'User not found'}), 404

        # 1) Всегда идем в SmartVend
        result = verify_vending_machines(user.api_key, user.user_id)
        if result.get('success'):
            machines_list = result['machines']
            
            # Используем функцию upsert_user_machines вместо дублирования кода
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
        current_app.logger.info(f"Active promotions count calculated")

        # 4) Получаем последние транзакции для каждой машины
        try:
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
            current_app.logger.info(f"Last transactions calculated")
        except Exception as e:
            current_app.logger.error(f"Error calculating last transactions: {str(e)}")
            return jsonify({'error': f'Error calculating last transactions: {str(e)}'}), 500

        # 5) Формируем JSON с добавлением количества активных акций и последних транзакций
        try:
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
            current_app.logger.info(f"Response data prepared with {len(response_data)} machines")
            return jsonify(response_data), 200
        except Exception as e:
            current_app.logger.error(f"Error preparing response: {str(e)}")
            return jsonify({'error': f'Error preparing response: {str(e)}'}), 500
    except Exception as e:
        current_app.logger.error(f"Error getting machines: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@main.route('/machines-list', methods=['OPTIONS'])
def machines_list_options():
    # просто вернуть 200, заголовки CORS будут добавлены в after_request
    return '', 200

@main.route('/machines-list', methods=['GET'])
@jwt_required()
def get_machines_list():
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404

        # 1) Всегда идем в SmartVend
        current_app.logger.info(f"Starting machines-list for user {user.email}")
        result = verify_vending_machines(user.api_key, user.user_id)
        if result.get('success'):
            machines_list = result['machines']
            
            # Get current machines before updating
            current_machines = UserMachine.query.filter_by(user_id=user.id).all()
            current_app.logger.info(f"Current machines count: {len(current_machines)}")
            current_machine_serials = {m.serialNumber for m in current_machines}
            
            # Get new machine serials from SmartVend
            new_machine_serials = {m['serialNumber'] for m in machines_list}
            current_app.logger.info(f"New machines count from SmartVend: {len(new_machine_serials)}")
            
            # Find machines that were removed in SmartVend
            removed_machines = current_machine_serials - new_machine_serials
            
            if removed_machines:
                current_app.logger.info(f"Machines removed in SmartVend: {removed_machines}")
                current_app.logger.info(f"Removed machines count: {len(removed_machines)}")
                
                try:
                    # Get all user's promotions that have these machines
                    user_promotions = Promotion.query.filter_by(user_id=user.id, is_deleted=False).all()
                    current_app.logger.info(f"User promotions count: {len(user_promotions)}")
                    
                    # Remove deleted machines from all promotions
                    for promotion in user_promotions:
                        if promotion.machines:
                            current_app.logger.info(f"Processing promotion {promotion.id} with {len(promotion.machines)} machines")
                            # Используем блок no_autoflush, чтобы предотвратить автоматический flush
                            with db.session.no_autoflush:
                                # Вместо прямого присваивания, удаляем связи с удаленными машинами
                                machines_to_remove = [m for m in promotion.machines if m.serialNumber in removed_machines]
                                for machine in machines_to_remove:
                                    # Проверяем, что запись имеет корректный promotion_id
                                    if machine.promotion_id is not None:
                                        db.session.delete(machine)  # Удаляем запись из таблицы promotion_machines
                                    else:
                                        current_app.logger.warning(f"Skipping machine with id {machine.id} due to NULL promotion_id")
                            # Применяем изменения после выхода из блока no_autoflush
                            db.session.flush()
                            current_app.logger.info(f"After filtering: {len(promotion.machines) - len(machines_to_remove)} machines")
                except Exception as e:
                    current_app.logger.error(f"Error processing promotions: {str(e)}")
                    return jsonify({'error': f'Error processing promotions: {str(e)}'}), 500
            
            try:
                upsert_user_machines(user.id, machines_list)
                current_app.logger.info(f"Machines upserted successfully")
            except Exception as e:
                current_app.logger.error(f"Error upserting machines: {str(e)}")
                return jsonify({'error': f'Error upserting machines: {str(e)}'}), 500
        else:
            current_app.logger.error(f"Failed to update from SmartVend: {result.get('error')}")
            return jsonify({'error': result.get('error')}), 400

        # 2) Снова берем локальные машины
        try:
            local_machines = UserMachine.query.filter_by(user_id=user.id).all()
            current_app.logger.info(f"Local machines after update: {len(local_machines)}")
        except Exception as e:
            current_app.logger.error(f"Error getting local machines: {str(e)}")
            return jsonify({'error': f'Error getting local machines: {str(e)}'}), 500

        # 3) Получаем количество активных акций для каждой машины
        try:
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
            current_app.logger.info(f"Active promotions count calculated")
        except Exception as e:
            current_app.logger.error(f"Error calculating active promotions: {str(e)}")
            return jsonify({'error': f'Error calculating active promotions: {str(e)}'}), 500

        # 4) Получаем последние транзакции для каждой машины
        try:
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
            current_app.logger.info(f"Last transactions calculated")
        except Exception as e:
            current_app.logger.error(f"Error calculating last transactions: {str(e)}")
            return jsonify({'error': f'Error calculating last transactions: {str(e)}'}), 500

        # 5) Формируем JSON с добавлением количества активных акций и последних транзакций
        try:
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
            current_app.logger.info(f"Response data prepared with {len(response_data)} machines")
            return jsonify(response_data), 200
        except Exception as e:
            current_app.logger.error(f"Error preparing response: {str(e)}")
            return jsonify({'error': f'Error preparing response: {str(e)}'}), 500
    except Exception as e:
        current_app.logger.error(f"Unhandled error in get_machines_list: {str(e)}")
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@main.route('/promotion/<int:promotion_id>/machines', methods=['GET'])
def get_promotion_machines(promotion_id):
    promo = Promotion.query.get(promotion_id)
    if not promo:
        return jsonify([]), 404
    # promo.machines уже содержит список PromotionMachine
    return jsonify([
        {
            "serialNumber": m.serialNumber,
            "humanName": m.humanName
        } for m in promo.machines
    ]), 200


promotions_store = []  # список в памяти, пока без БД

@main.route('/create_promotion', methods=['POST'])
@jwt_required()
def create_promotion():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    data = request.get_json()
    #current_app.logger.info(f"Creating promotion with data: {data}")

    promotion_name = data.get("promotionName")
    discount = data.get("discount", 0)
    discount_type = data.get("discount_type", "percentage")
    is_single_use = data.get("is_single_use", False)
    is_renewable = data.get("is_renewable", False)
    expiration_date_str = data.get("expiration_date")
    machines = data.get("machines", [])
    customer_id = generate_unique_code()

    expiration_date = None
    if expiration_date_str:
        expiration_date = datetime.fromisoformat(expiration_date_str)

    new_promo = Promotion(
        user_id=user.id,
        name=promotion_name,
        discount_type=discount_type,
        discount_value=discount,
        is_renewable=is_renewable,
        expiration_date=expiration_date,
        is_single_use=is_single_use,
        creation_date=datetime.utcnow(),
        is_active=True,
        customer_id=customer_id,
    )

    db.session.add(new_promo)
    db.session.flush()  # получить new_promo.id до коммита

    # Теперь сохраняем машины
    for serial in machines:
        # Получаем существующую машину пользователя
        user_machine = UserMachine.query.filter_by(
            user_id=user.id,
            serialNumber=serial
        ).first()
        
        if user_machine:
            pm = PromotionMachine(
                promotion_id=new_promo.id,
                serialNumber=serial,
                humanName=user_machine.humanName
            )
            db.session.add(pm)

    img = create_qr_code_image(customer_id)
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)  # Создаёт все вложенные папки при необходимости
    qr_filename = f"qr_promo_{new_promo.id}.png"
    full_path = os.path.join(upload_folder, qr_filename)
    img.save(full_path, format="PNG")
    new_promo.qr_filename = qr_filename

    db.session.commit()

    return jsonify({
        "message": "Promotion created",
        "promotion_id": new_promo.id,
        "customer_id": new_promo.customer_id,
        "qr_filename": qr_filename
    }), 201

@main.route('/promotion/<int:promotion_id>/download_qr', methods=['GET'])
def download_qr(promotion_id):
    promo = Promotion.query.get(promotion_id)
    if not promo or not promo.qr_filename:
        return jsonify({"error": "No QR available for this promo"}), 404

    full_path = os.path.join(current_app.config["UPLOAD_FOLDER"], promo.qr_filename)
    if not os.path.exists(full_path):
        return jsonify({"error": "QR file not found"}), 404

    return send_file(
        full_path,
        as_attachment=True,
        download_name=promo.qr_filename,     # или f"{promo.qr_filename}.png"
        mimetype="image/png"
    )

@main.route('/download_archive/<int:mg_id>', methods=['GET', 'OPTIONS'])
def download_archive(mg_id):
    # current_app.logger.info(f"Downloading archive for mass generation {mg_id}")
    # current_app.logger.info(f"Current app config: {current_app.config}")
    # current_app.logger.info(f"UPLOAD_FOLDER path: {current_app.config.get('UPLOAD_FOLDER')}")
    
    mass_generation = MassGeneration.query.get_or_404(mg_id)
    current_app.logger.info(f"Found mass generation: {mass_generation.id}, archive: {mass_generation.archive_filename}")
    
    if not mass_generation.archive_filename:
        current_app.logger.error(f"No archive filename for mass generation {mg_id}")
        return jsonify({"error": "Archive not found"}), 404

    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], mass_generation.archive_filename)
    # current_app.logger.info(f"Looking for file at: {filepath}")
    # current_app.logger.info(f"File exists: {os.path.exists(filepath)}")
    # current_app.logger.info(f"Directory contents: {os.listdir(os.path.dirname(filepath))}")
    
    if not os.path.exists(filepath):
        current_app.logger.error(f"Archive file not found at: {filepath}")
        return jsonify({"error": "Archive file not found"}), 404

    current_app.logger.info(f"Sending file: {filepath}")
    response = send_file(filepath, as_attachment=True, download_name=mass_generation.archive_filename)
    return response

@main.route('/promotions', methods=['GET'])
@jwt_required()
def get_promotions():
    try:
        user_id = get_jwt_identity()
        # Получаем параметры пагинации, но не устанавливаем значения по умолчанию
        page = request.args.get('page', type=int)
        per_page = request.args.get('per_page', type=int)
        created_after = request.args.get('created_after')
        created_before = request.args.get('created_before')
        serial_number = request.args.get('serial_number')
        name = request.args.get('name')
        include_deleted = request.args.get('include_deleted', 'false').lower() == 'true'
        
        # Базовый запрос с предварительной загрузкой машин
        query = Promotion.query.filter_by(user_id=user_id)
        
        # По умолчанию не включаем удаленные промоакции
        if not include_deleted:
            query = query.filter_by(is_deleted=False)
        
        # Применяем фильтры
        if created_after:
            query = query.filter(Promotion.creation_date >= datetime.fromisoformat(created_after))
        if created_before:
            query = query.filter(Promotion.creation_date <= datetime.fromisoformat(created_before))
        if name:
            query = query.filter(Promotion.name.ilike(f'%{name}%'))
        if serial_number:
            query = query.join(Promotion.machines).filter(
                PromotionMachine.serialNumber.like(f"%{serial_number}%")
            )

        # Получаем общее количество записей
        total = query.count()
        
        # Применяем пагинацию только если указаны оба параметра
        if page is not None and per_page is not None:
            promotions = query.order_by(Promotion.creation_date.desc())\
                .offset(page * per_page)\
                .limit(per_page)\
                .all()
        else:
            # Если параметры пагинации не указаны, возвращаем все записи
            promotions = query.order_by(Promotion.creation_date.desc()).all()
        
        # Формируем ответ
        return jsonify({
            'promotions': [{
                'id': p.id,
                'creation_date': p.creation_date.isoformat(),
                'name': p.name,
                'discount_type': p.discount_type,
                'discount_value': p.discount_value,
                'expiration_date': p.expiration_date.isoformat() if p.expiration_date else None,
                'is_single_use': p.is_single_use,
                'is_renewable': p.is_renewable,
                'activation_count': p.activation_count,
                'remaining_uses': p.remaining_uses,
                'is_active': p.is_active,
                'is_deleted': p.is_deleted,
                'deleted_at': p.deleted_at.isoformat() if p.deleted_at else None,
                'mass_generation_id': p.mass_generation_id,
                'machines': [{
                    'serialNumber': m.serialNumber,
                    'humanName': m.humanName
                } for m in p.machines]
            } for p in promotions],
            'total': total,
            'page': page if page is not None else 0,
            'per_page': per_page if per_page is not None else len(promotions)
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error fetching promotions: {str(e)}")
        return jsonify({"error": "Failed to fetch promotions"}), 500

@main.route('/promotion/<int:promotion_id>/add-machines', methods=['POST'])
def add_machines_to_promotion(promotion_id):
    promo = Promotion.query.get(promotion_id)
    if not promo:
        return jsonify({"error": "Promotion not found"}), 404

    data = request.get_json()
    new_machines = data.get('machines', [])

    # Get the list of all user machines to check for deleted status
    user_machines = UserMachine.query.filter_by(user_id=promo.user_id).all()
    user_machines_dict = {m.serialNumber: m for m in user_machines}

    existing_serials = {m.serialNumber for m in promo.machines}
    for machine_data in new_machines:
        serial = machine_data['serialNumber']
        humanName = machine_data.get('humanName', 'Unknown')
        
        # Skip if the machine is already in the promotion
        if serial in existing_serials:
            continue
            
        # Skip if the machine is deleted (not in our database)
        if serial not in user_machines_dict:
            current_app.logger.info(f"Skipping deleted machine {serial} for promotion {promotion_id}")
            continue
            
        pm = PromotionMachine(
            promotion_id=promo.id,
            serialNumber=serial,
            humanName=humanName
        )
        db.session.add(pm)
    
    db.session.commit()
    return jsonify({"message": "Machines added"}), 200


@main.route('/promotion/<int:promotion_id>/machines', methods=['DELETE'])
def remove_all_machines_from_promotion(promotion_id):
    promo = Promotion.query.get(promotion_id)
    if not promo:
        return jsonify({"error": "Promotion not found"}), 404

    # Delete all PromotionMachine records for this promotion
    deleted_count = PromotionMachine.query.filter_by(promotion_id=promotion_id).delete()
    db.session.commit()
    
    return jsonify({
        "message": f"All machines ({deleted_count}) successfully removed from promotion",
        "removed_count": deleted_count
    }), 200


@main.route('/promotion/<int:promotion_id>/machines/<string:serialNumber>', methods=['DELETE'])
def remove_machine_from_promotion(promotion_id, serialNumber):
    promo = Promotion.query.get(promotion_id)
    if not promo:
        return jsonify({"error": "Promotion not found"}), 404

    pm = PromotionMachine.query.filter_by(promotion_id=promotion_id, serialNumber=serialNumber).first()
    if pm:
        db.session.delete(pm)
        db.session.commit()
        return jsonify({"message": "Machine removed"}), 200
    return jsonify({"message": "Machine not found"}), 404

@main.route('/promotion/<int:promotion_id>/deactivate', methods=['PUT'])
def deactivate_promotion(promotion_id):
    promo = Promotion.query.get(promotion_id)
    if not promo:
        return jsonify({"error": "Promotion not found"}), 404
    
    promo.is_active = False
    db.session.commit()
    return jsonify({"message": "Promotion deactivated"}), 200

@main.route('/promotion/<int:promotion_id>/soft-delete', methods=['PUT'])
@jwt_required()
def soft_delete_promotion(promotion_id):
    user_id = get_jwt_identity()
    promo = Promotion.query.get(promotion_id)
    
    if not promo:
        return jsonify({"error": "Promotion not found"}), 404
    
    # Проверяем, что пользователь имеет право удалять эту промоакцию
    if promo.user_id != user_id:
        return jsonify({"error": "Unauthorized"}), 403
    
    # Проверяем, не удалена ли уже промоакция
    if promo.is_deleted:
        return jsonify({"error": "Promotion already deleted"}), 400
    
    # Выполняем мягкое удаление
    promo.soft_delete()
    db.session.commit()
    
    return jsonify({
        "message": "Promotion marked as deleted",
        "promotion_id": promotion_id,
        "deleted_at": promo.deleted_at.isoformat()
    }), 200

@main.route('/promotion/<int:promotion_id>/restore', methods=['PUT'])
@jwt_required()
def restore_promotion(promotion_id):
    user_id = get_jwt_identity()
    promo = Promotion.query.get(promotion_id)
    
    if not promo:
        return jsonify({"error": "Promotion not found"}), 404
    
    # Проверяем, что пользователь имеет право восстанавливать эту промоакцию
    if promo.user_id != user_id:
        return jsonify({"error": "Unauthorized"}), 403
    
    # Проверяем, удалена ли промоакция
    if not promo.is_deleted:
        return jsonify({"error": "Promotion is not deleted"}), 400
    
    # Восстанавливаем промоакцию
    promo.restore()
    db.session.commit()
    
    return jsonify({
        "message": "Promotion restored successfully",
        "promotion_id": promotion_id
    }), 200

@main.route('/mass_generation/<int:mg_id>/add-machines', methods=['POST'])
def add_machines_to_mass_generation(mg_id):
    logging.info(f"Adding machines to mass generation {mg_id}")
    mg = MassGeneration.query.get(mg_id)
    if not mg:
        logging.error(f"Mass Generation {mg_id} not found")
        return jsonify({"error": "Mass Generation not found"}), 404

    data = request.get_json()
    new_machines = data.get('machines', [])
    logging.info(f"New machines: {new_machines}")

    # Получаем все промо, связанные с этой масс-генерацией
    promotions = mg.promotions  # список объектов Promotion

    # Для проверки дубликатов: собираем уже существующие (promotion_id, serialNumber)
    # (Можно и другими способами — например, запросом в БД).
    existing_pairs = set(
        (pm.promotion_id, pm.serialNumber)
        for pm in PromotionMachine.query.filter(
            PromotionMachine.promotion_id.in_([p.id for p in promotions])
        )
    )

    for machine_data in new_machines:
        serial = machine_data['serialNumber']
        humanName = machine_data.get('humanName', 'Unknown')

        # Привязываем каждую новую машину ко всем промо текущей Mass Generation
        for promotion in promotions:
            if (promotion.id, serial) not in existing_pairs:
                pm = PromotionMachine(
                    promotion_id=promotion.id,     # Важно: реальный promotion_id
                    serialNumber=serial,
                    humanName=humanName,
                    mass_generation_id=mg.id
                )
                db.session.add(pm)
                existing_pairs.add((promotion.id, serial))

    db.session.commit()
    logging.info(f"Machines added to mass generation {mg_id}")

    # Возвращаем обновленный список машин,
    # возможно, вы хотите вернуть их в другом формате или одним списком, 
    # тут на ваше усмотрение:
    updated_machines = []
    for promotion in promotions:
        for pm in promotion.machines:
            updated_machines.append({
                "promotion_id": pm.promotion_id,
                "serialNumber": pm.serialNumber,
                "humanName": pm.humanName
            })

    return jsonify({
        "message": "Machines added to all promotions of this mass generation",
        "machines": updated_machines
    }), 200

@main.route('/mass_generation/<int:mg_id>/remove-machine/<string:serialNumber>', methods=['DELETE'])
def remove_machine_from_mass_generation(mg_id, serialNumber):
    mg = MassGeneration.query.get(mg_id)
    if not mg:
        return jsonify({"error": "Mass Generation not found"}), 404

    pm = PromotionMachine.query.filter_by(
        mass_generation_id=mg.id, 
        serialNumber=serialNumber
    ).first()
    if pm:
        db.session.delete(pm)
        db.session.commit()
        return jsonify({"message": "Machine removed from mass generation"}), 200
    return jsonify({"message": "Machine not found"}), 404

@main.route('/resend-verification', methods=['POST'])
@jwt_required()
def resend_verification_email():
    # Get user from JWT token
    user_id = get_jwt_identity()
    user = User.query.get(user_id)

    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Check if email already verified
    if user.email_verified:
        return jsonify({'message': 'Email already verified'}), 400

    try:
        # Generate new verification token and send email
        token = generate_jwt_token(user.email)
        send_verification_email(user.email, token)
        
        return jsonify({
            'message': 'Verification email sent successfully',
            'email': user.email
        }), 200

    except Exception as e:
        current_app.logger.error(f"Error sending verification email: {str(e)}")
        return jsonify({
            'error': 'Failed to send verification email'
        }), 500

@main.route('/transactions', methods=['GET'])
@jwt_required()
def get_transactions():
    try:
        user_id = get_jwt_identity()
        
        # Получаем все транзакции для текущего пользователя
        transactions = Transaction.query.filter_by(user_id=user_id)\
            .order_by(Transaction.timestamp.desc())\
            .all()
        
        return jsonify([{
            'id': t.id,
            'machine_id': t.machine_id,
            'product_id': t.product_id,
            'price': t.price,
            'discounted_price': t.discounted_price,
            'timestamp': t.timestamp.isoformat() if t.timestamp else None
        } for t in transactions]), 200
        
    except Exception as e:
        current_app.logger.error(f"Error fetching transactions: {str(e)}")
        return jsonify({"error": "Failed to fetch transactions"}), 500

@main.route('/mass_generation/<int:mg_id>/soft-delete', methods=['PUT'])
@jwt_required()
def soft_delete_mass_generation(mg_id):
    user_id = get_jwt_identity()
    mg = MassGeneration.query.get(mg_id)
    
    if not mg:
        return jsonify({"error": "Mass Generation not found"}), 404
    
    # Проверяем, что пользователь имеет право удалять эту массовую генерацию
    if mg.user_id != user_id:
        return jsonify({"error": "Unauthorized"}), 403
    
    # Проверяем, не удалена ли уже массовая генерация
    if mg.is_deleted:
        return jsonify({"error": "Mass Generation already deleted"}), 400
    
    # Выполняем мягкое удаление
    mg.soft_delete()
    db.session.commit()
    
    return jsonify({
        "message": "Mass Generation marked as deleted",
        "mass_generation_id": mg_id,
        "deleted_at": mg.deleted_at.isoformat(),
        "affected_promotions": len(mg.promotions)
    }), 200

@main.route('/mass_generation/<int:mg_id>/restore', methods=['PUT'])
@jwt_required()
def restore_mass_generation(mg_id):
    user_id = get_jwt_identity()
    mg = MassGeneration.query.get(mg_id)
    
    if not mg:
        return jsonify({"error": "Mass Generation not found"}), 404
    
    # Проверяем, что пользователь имеет право восстанавливать эту массовую генерацию
    if mg.user_id != user_id:
        return jsonify({"error": "Unauthorized"}), 403
    
    # Проверяем, удалена ли массовая генерация
    if not mg.is_deleted:
        return jsonify({"error": "Mass Generation is not deleted"}), 400
    
    # Восстанавливаем массовую генерацию
    mg.restore()
    db.session.commit()
    
    return jsonify({
        "message": "Mass Generation restored successfully",
        "mass_generation_id": mg_id,
        "affected_promotions": len(mg.promotions)
    }), 200

@main.route('/cleanup-database', methods=['POST'])
@jwt_required()
def cleanup_database():
    """
    Очищает базу данных от некорректных записей в таблице promotion_machines.
    """
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        if not user or not user.is_admin:
            return jsonify({'error': 'Unauthorized'}), 403
        
        # Находим все записи с NULL promotion_id
        invalid_records = db.session.query(PromotionMachine).filter(PromotionMachine.promotion_id.is_(None)).all()
        
        if not invalid_records:
            return jsonify({'message': 'No invalid records found'}), 200
        
        # Удаляем некорректные записи
        for record in invalid_records:
            db.session.delete(record)
        
        db.session.commit()
        
        return jsonify({
            'message': f'Successfully cleaned up {len(invalid_records)} invalid records',
            'count': len(invalid_records)
        }), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error cleaning up database: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to clean up database: {str(e)}'}), 500
