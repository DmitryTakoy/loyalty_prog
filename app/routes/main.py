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
    user_id = get_jwt_identity()  # Получить ID текущего пользователя
    
    # Фильтровать по пользователю
    mass_generations = MassGeneration.query.filter_by(user_id=user_id).all()
    return jsonify([{
        'id': mg.id,
        'name': mg.name,
        'creation_date': mg.creation_date.isoformat(),
        'discount_type': mg.discount_type,
        'discount_value': mg.discount_value,
        'is_single_use': mg.is_single_use,
        'is_renewable': mg.is_renewable,
        'expiration_date': mg.expiration_date.isoformat() if mg.expiration_date else None,
        'promotion_count': len(mg.promotions),  # Используем количество промоакций
        'archive_filename': mg.archive_filename
    } for mg in mass_generations]), 200

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

    if not customer_id or not machine:
        return jsonify({"error": "Missing customer or machine"}), 400

    # 1) Ищем акцию
    promo = Promotion.query.filter_by(customer_id=customer_id).first()
    if not promo:
        # Нет такой акции
        return jsonify({}), 200

    # 2) Проверяем, что promo.active, не истекла, не исчерпана и т.д.
    now = datetime.utcnow()
    if not promo.is_active:
        return jsonify({}), 200
    if promo.is_used:
        # Одноразовая уже использована
        return jsonify({}), 200
    if promo.expiration_date and promo.expiration_date < now:
        # истекла
        return jsonify({}), 200
    if promo.remaining_uses is not None and promo.remaining_uses <= 0:
        # уже исчерпана
        return jsonify({}), 200

    # 3) Проверяем machine: есть ли такая запись в PromotionMachine
    pm = PromotionMachine.query.filter_by(
        promotion_id=promo.id,
        serialNumber=machine
    ).first()
    if not pm:
        # Акция не распространяется на этот автомат
        return jsonify({}), 200

    # 4) Вычисляем discount
    if promo.discount_type == 'percentage':
        return jsonify({"discount": promo.discount_value}), 200
    elif promo.discount_type == 'free_drinks':
        return jsonify({"discount": 100}), 200
    else:
        return jsonify({}), 200

@main.route('/completion', methods=['POST'])
def handle_completion():
    data = request.get_json()
    customer_id = data.get('customer')
    machine = data.get('machine')
    success = data.get('success', False)

    if not customer_id or not machine:
        return jsonify({"error": "Missing customer or machine"}), 400

    promo = Promotion.query.filter_by(customer_id=customer_id).first()
    if not promo:
        return jsonify({"message": "No such promotion"}), 200

    # Проверяем machine
    pm = PromotionMachine.query.filter_by(
        promotion_id=promo.id,
        serialNumber=machine
    ).first()
    if not pm:
        # Акция не распространяется на этот автомат - но раз уж success, ничего не делаем
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
    data = request.json
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

    # 7) Сохраняем zip-файл на диск с уникальным именем
    zip_buffer.seek(0)
    archive_filename = f"mass_generation_{mass_generation.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    archive_path = os.path.join(current_app.config['ARCHIVE_FOLDER'], archive_filename)
    
    with open(archive_path, 'wb') as f:
        f.write(zip_buffer.getvalue())

    # 8) Обновляем запись массовой генерации с именем архива
    mass_generation.archive_filename = archive_filename
    db.session.commit()

    # 9) Возвращаем клиенту zip
    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name='codes_and_images.zip'
    )


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
            
        current_app.logger.info(f"Fetching machines for user {user.email}")
        result = verify_vending_machines(user.api_key, user.user_id)
        current_app.logger.info(f"SmartVend API response: {result}")
        
        if result.get('success'):
            return jsonify({'machines': result['machines']}), 200
        else:
            return jsonify({'error': result.get('error', 'Failed to fetch machines')}), 400
            
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
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    # 1) Всегда идем в SmartVend
    current_app.logger.info(f"Force updating machine list from SmartVend for user {user.email}")
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
            "activePromotions": active_promos_count.get(um.serialNumber, 0),
            "lastQrUsed": last_transaction_time.isoformat() if last_transaction_time else None
        })

    return jsonify(response_data), 200

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
    current_app.logger.info(f"Creating promotion with data: {data}")

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
        # Можно добавить логику для получения humanName из внешнего API или БД
        humanName = "Unknown"  
        pm = PromotionMachine(
            promotion_id=new_promo.id,
            serialNumber=serial,
            humanName=humanName
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

    mass_generation = MassGeneration.query.get_or_404(mg_id)
    if not mass_generation.archive_filename:
        return jsonify({"error": "Archive not found"}), 404

    filepath = os.path.join(current_app.config['ARCHIVE_FOLDER'], mass_generation.archive_filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Archive file not found"}), 404

    response = send_file(filepath, as_attachment=True, download_name=mass_generation.archive_filename)
    return response

@main.route('/promotions', methods=['GET'])
@jwt_required()
def get_promotions():
    try:
        # Получаем user_id из JWT токена
        user_id = get_jwt_identity()
        # Фильтруем промокоды только для текущего пользователя
        promotions = Promotion.query.filter_by(user_id=user_id).all()
        return jsonify([{
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
            'mass_generation_id': p.mass_generation_id,
            'mass_generation_name': p.mass_generation.name if p.mass_generation else None
        } for p in promotions]), 200
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

    existing_serials = {m.serialNumber for m in promo.machines}
    for machine_data in new_machines:
        serial = machine_data['serialNumber']
        human = machine_data.get('humanName', 'Unknown')
        if serial not in existing_serials:
            pm = PromotionMachine(
                promotion_id=promo.id,
                serialNumber=serial,
                humanName=human
            )
            db.session.add(pm)
    
    db.session.commit()
    return jsonify({"message": "Machines added"}), 200


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
        human = machine_data.get('humanName', 'Unknown')

        # Привязываем каждую новую машину ко всем промо текущей Mass Generation
        for promotion in promotions:
            if (promotion.id, serial) not in existing_pairs:
                pm = PromotionMachine(
                    promotion_id=promotion.id,     # Важно: реальный promotion_id
                    serialNumber=serial,
                    humanName=human,
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
