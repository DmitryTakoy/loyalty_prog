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
from sqlalchemy import func, case, or_
from sqlalchemy.sql import text
import threading
import json

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
    return jsonify({"message": "Welcome to the Loyalty Program API"}), 200

@main.route('/mass_generations', methods=['GET'])
@jwt_required()
def get_mass_generations():
    try:
        from sqlalchemy import func, asc, desc
        user_id = get_jwt_identity()

        # params
        include_deleted = request.args.get('include_deleted', 'false').lower() == 'true'
        try:
            page = max(int(request.args.get('page', 0)), 0)  # 0-based
        except:
            page = 0
        try:
            per_page = max(int(request.args.get('per_page', 25)), 1)
        except:
            per_page = 25

        name = request.args.get('name', '').strip() or None
        created_after_raw = request.args.get('created_after')
        created_before_raw = request.args.get('created_before')
        sort_by = (request.args.get('sort_by') or 'creation_date').strip()
        sort_dir = (request.args.get('sort_dir') or 'desc').lower()

        # parse dates
        def parse_iso(dt_str):
            if not dt_str:
                return None
            try:
                return datetime.fromisoformat(dt_str)
            except:
                return None

        created_after = parse_iso(created_after_raw)
        created_before = parse_iso(created_before_raw)
        if created_before:
            # включаем конец дня
            created_before = created_before.replace(hour=23, minute=59, second=59, microsecond=999999)

        # base filtered query
        base_q = MassGeneration.query.filter(MassGeneration.user_id == user_id)
        if not include_deleted:
            base_q = base_q.filter(MassGeneration.is_deleted == False)
        if name:
            base_q = base_q.filter(MassGeneration.name.ilike(f'%{name}%'))
        if created_after:
            base_q = base_q.filter(MassGeneration.creation_date >= created_after)
        if created_before:
            base_q = base_q.filter(MassGeneration.creation_date <= created_before)

        total = base_q.count()

        # promotions_count subquery
        count_subq = (
            db.session.query(
                Promotion.mass_generation_id.label('mg_id'),
                func.count(Promotion.id).label('promotions_count')
            )
            .group_by(Promotion.mass_generation_id)
            .subquery()
        )

        # main query with outer join to counts
        q = (
            db.session.query(
                MassGeneration,
                func.coalesce(count_subq.c.promotions_count, 0).label('promotions_count')
            )
            .outerjoin(count_subq, count_subq.c.mg_id == MassGeneration.id)
            .filter(MassGeneration.user_id == user_id)
        )
        if not include_deleted:
            q = q.filter(MassGeneration.is_deleted == False)
        if name:
            q = q.filter(MassGeneration.name.ilike(f'%{name}%'))
        if created_after:
            q = q.filter(MassGeneration.creation_date >= created_after)
        if created_before:
            q = q.filter(MassGeneration.creation_date <= created_before)

        # sorting
        sort_map = {
            'creation_date': MassGeneration.creation_date,
            'name': MassGeneration.name,
            'expiration_date': MassGeneration.expiration_date,
            'is_deleted': MassGeneration.is_deleted,
            'promotions_count': func.coalesce(count_subq.c.promotions_count, 0),
        }
        sort_col = sort_map.get(sort_by, MassGeneration.creation_date)
        order = desc(sort_col) if sort_dir == 'desc' else asc(sort_col)

        items = (
            q.order_by(order)
             .offset(page * per_page)
             .limit(per_page)
             .all()
        )

        now = datetime.utcnow()
        payload = []
        for mg, promos_cnt in items:
            payload.append({
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
                'promotions_count': int(promos_cnt or 0),
                'is_expired': mg.expiration_date is not None and mg.expiration_date < now,
            })

        return jsonify({
            'mass_generations': payload,
            'total': total,
            'page': page,
            'per_page': per_page,
        }), 200
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
    # if promo.discount_type == 'free_drinks' or promo.discount_type == 'free_drink':
    #     promo.remaining_uses = promo.drinks_limit
    # elif promo.is_single_use:
    #     promo.remaining_uses = 1
    # else:
    #     promo.remaining_uses = None

    promo.is_used = False  # Можно сбрасывать, если было is_used

    db.session.commit()
    return jsonify({'message': 'Promotion renewed successfully'}), 200

@main.route('/generate_codes', methods=['POST'])
@jwt_required()
def generate_codes():
    try:
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

        # 2) Создаем ZIP-файл сразу на диске (без хранения всего архива в памяти)
        upload_folder = current_app.config['UPLOAD_FOLDER']
        if not os.path.exists(upload_folder):
            os.makedirs(upload_folder)

        archive_filename = f"mass_generation_{mass_generation.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        archive_path = os.path.join(upload_folder, archive_filename)

        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            codes_txt = io.StringIO()

            for idx in range(num_codes):
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

                # Периодически коммитим, чтобы не держать одну гигантскую транзакцию
                if (idx + 1) % 500 == 0:
                    db.session.commit()

            # 5) Добавляем все сгенерированные коды в файл codes.txt внутри архива
            codes_txt.write('\n'.join(codes))
            zip_file.writestr('codes.txt', codes_txt.getvalue())

        # 6) Финальный коммит добавленных записей (mass_generation, promotions, machines)
        db.session.commit()

        # Обновляем запись массовой генерации с именем архива
        mass_generation.archive_filename = archive_filename
        db.session.commit()

        # Возвращаем клиенту файл напрямую с диска
        return send_file(
            archive_path,
            mimetype='application/zip',
            as_attachment=True,
            download_name=archive_filename
        )

    except Exception as e:
        current_app.logger.error(f"Generate codes error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ================= Asynchronous mass generation =================

def _status_file_path(mg_id: int) -> str:
    upload_folder = current_app.config['UPLOAD_FOLDER']
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)
    return os.path.join(upload_folder, f"mg_{mg_id}.status.json")

def _write_status(mg_id: int, status: dict):
    try:
        with open(_status_file_path(mg_id), 'w') as f:
            json.dump(status, f)
    except Exception as e:
        current_app.logger.error(f"Failed to write status for mg {mg_id}: {e}")

def _read_status(mg_id: int) -> dict:
    try:
        with open(_status_file_path(mg_id), 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def _generate_codes_job(app_obj, mg_id: int, payload: dict, user_id: int):
    # Background job: runs inside app context
    try:
        with app_obj.app_context():
            mg = MassGeneration.query.get(mg_id)
            if not mg:
                return

            num_codes = payload.get('num_codes', 1)
            discount_type = payload.get('discount_type', 'percentage')
            discount_value = payload.get('discount_value', 0)
            is_single_use = payload.get('is_single_use', True)
            is_renewable = payload.get('is_renewable', False)
            expiration_date_str = payload.get('expiration_date', None)
            machines = payload.get('machines', [])

            if expiration_date_str:
                expiration_date = datetime.fromisoformat(expiration_date_str)
            else:
                expiration_date = None

            # Prepare archive path
            upload_folder = current_app.config['UPLOAD_FOLDER']
            if not os.path.exists(upload_folder):
                os.makedirs(upload_folder)
            archive_filename = f"mass_generation_{mg.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            archive_path = os.path.join(upload_folder, archive_filename)

            generated = 0
            _write_status(mg_id, {
                'state': 'running',
                'generated': generated,
                'total': num_codes
            })

            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                codes = []
                codes_txt = io.StringIO()
                for idx in range(num_codes):
                    code = generate_unique_code()
                    img = create_qr_code_image(code)

                    img_bytes = io.BytesIO()
                    img.save(img_bytes, format='PNG')
                    img_bytes.seek(0)
                    zip_file.writestr(f"{code}.png", img_bytes.getvalue())

                    codes.append(code)

                    promotion = Promotion(
                        name=mg.name,
                        discount_type=discount_type,
                        discount_value=discount_value,
                        is_single_use=is_single_use,
                        is_renewable=is_renewable,
                        expiration_date=expiration_date,
                        creation_date=datetime.utcnow(),
                        is_active=True,
                        activation_count=0,
                        remaining_uses=None,
                        mass_generation_id=mg.id,
                        customer_id=code,
                        user_id=user_id
                    )
                    db.session.add(promotion)
                    db.session.flush()

                    for machine_dict in machines:
                        pm = PromotionMachine(
                            promotion_id=promotion.id,
                            serialNumber=machine_dict.get('serialNumber', ''),
                            humanName=machine_dict.get('humanName', ''),
                            mass_generation_id=mg.id
                        )
                        db.session.add(pm)

                    generated += 1
                    if generated % 500 == 0:
                        db.session.commit()
                        _write_status(mg_id, {
                            'state': 'running',
                            'generated': generated,
                            'total': num_codes
                        })

                codes_txt.write('\n'.join(codes))
                zip_file.writestr('codes.txt', codes_txt.getvalue())

            db.session.commit()
            mg.archive_filename = archive_filename
            db.session.commit()
            _write_status(mg_id, {
                'state': 'ready',
                'generated': generated,
                'total': num_codes,
                'archive_filename': archive_filename
            })
    except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Async generation failed for mg {mg_id}: {e}")
            _write_status(mg_id, {
                'state': 'error',
                'error': str(e)
            })


@main.route('/generate_codes_async', methods=['POST'])
@jwt_required()
def generate_codes_async():
    try:
        data = request.json or {}

        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404

        name = data.get('name', 'MassGeneratedPromo')
        discount_type = data.get('discount_type', 'percentage')
        discount_value = data.get('discount_value', 0)
        is_single_use = data.get('is_single_use', True)
        is_renewable = data.get('is_renewable', False)
        expiration_date_str = data.get('expiration_date')
        expiration_date = datetime.fromisoformat(expiration_date_str) if expiration_date_str else None

        mg = MassGeneration(
            name=name,
            discount_type=discount_type,
            discount_value=discount_value,
            is_single_use=is_single_use,
            is_renewable=is_renewable,
            expiration_date=expiration_date,
            user_id=user.id,
        )
        db.session.add(mg)
        db.session.flush()

        # Initial status
        _write_status(mg.id, {
            'state': 'queued',
            'generated': 0,
            'total': data.get('num_codes', 0)
        })

        # Start background thread
        app_obj = current_app._get_current_object()
        t = threading.Thread(target=_generate_codes_job, args=(app_obj, mg.id, data, user.id), daemon=True)
        t.start()

        db.session.commit()
        return jsonify({'mg_id': mg.id, 'status': 'queued'}), 202
    except Exception as e:
        current_app.logger.error(f"Error queuing async generation: {e}")
        return jsonify({'error': str(e)}), 500


@main.route('/mass_generation/<int:mg_id>/status', methods=['GET'])
@jwt_required()
def mass_generation_status(mg_id):
    try:
        mg = MassGeneration.query.get(mg_id)
        if not mg:
            return jsonify({'error': 'Mass Generation not found'}), 404
        status = _read_status(mg_id)
        # Fallback inference
        if not status:
            if mg.archive_filename:
                status = {'state': 'ready', 'archive_filename': mg.archive_filename}
            else:
                status = {'state': 'unknown'}
        return jsonify(status), 200
    except Exception as e:
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
                    # Bulk delete promotion_machines for removed serialNumbers, limited to this user's active promotions
                    subq = (
                        db.session.query(PromotionMachine.id)
                        .join(Promotion, PromotionMachine.promotion_id == Promotion.id)
                        .filter(
                            Promotion.user_id == user.id,
                            Promotion.is_deleted == False,
                            PromotionMachine.serialNumber.in_(list(removed_machines))
                        )
                    ).subquery()

                    deleted_count = (
                        db.session.query(PromotionMachine)
                        .filter(PromotionMachine.id.in_(subq))
                        .delete(synchronize_session=False)
                    )
                    db.session.commit()
                    current_app.logger.info(f"Bulk removed {deleted_count} promotion_machines for removed machines")
                except Exception as e:
                    current_app.logger.error(f"Error processing promotions (bulk delete): {str(e)}")
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
            # Aggregate counts in a single query by serialNumber
            counts_rows = (
                db.session.query(
                    PromotionMachine.serialNumber,
                    func.count(PromotionMachine.id)
                )
                .join(Promotion, PromotionMachine.promotion_id == Promotion.id)
                .filter(
                    Promotion.user_id == user.id,
                    Promotion.is_active == True,
                    or_(
                        Promotion.expiration_date.is_(None),
                        Promotion.expiration_date > datetime.utcnow()
                    )
                )
                .group_by(PromotionMachine.serialNumber)
                .all()
            )
            active_promos_count = {serial: int(cnt) for serial, cnt in counts_rows}
            current_app.logger.info("Active promotions count aggregated")
        except Exception as e:
            current_app.logger.error(f"Error calculating active promotions (aggregate): {str(e)}")
            return jsonify({'error': f'Error calculating active promotions: {str(e)}'}), 500

        # 4) Получаем последние транзакции для каждой машины
        try:
            # Aggregate last transaction timestamps in a single query per machine
            last_rows = (
                db.session.query(
                    Transaction.machine_id,
                    func.max(Transaction.timestamp)
                )
                .filter(
                    Transaction.user_id == user.id,
                    Transaction.discounted_price < Transaction.price
                )
                .group_by(Transaction.machine_id)
                .all()
            )
            last_transactions = {machine_id: ts for machine_id, ts in last_rows}
            current_app.logger.info("Last transactions aggregated")
        except Exception as e:
            current_app.logger.error(f"Error calculating last transactions (aggregate): {str(e)}")
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
    # drinks_limit carries the N-use cap for both free_drinks AND percentage types.
    # The frontend sends it only when the operator explicitly sets a use limit.
    drinks_limit_raw = data.get("drinks_limit", None)
    drinks_limit = int(drinks_limit_raw) if drinks_limit_raw is not None else None
    customer_id = generate_unique_code()

    expiration_date = None
    if expiration_date_str:
        expiration_date = datetime.fromisoformat(expiration_date_str)

    new_promo = Promotion(
        user_id=user.id,
        name=promotion_name,
        discount_type=discount_type,
        discount_value=discount,
        drinks_limit=drinks_limit,
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

        # Параметры пагинации и фильтров
        page = request.args.get('page', type=int)
        per_page = request.args.get('per_page', type=int)
        created_after = request.args.get('created_after')
        created_before = request.args.get('created_before')
        serial_number = request.args.get('serial_number')
        name = request.args.get('name')
        machine_name = request.args.get('machine_name')  # фильтр по имени контроллера
        include_deleted = request.args.get('include_deleted', 'false').lower() == 'true'

        # Параметры сортировки
        allowed_sort_fields = [
            'creation_date', 'name', 'id',
            'discount_type', 'discount_value',
            'expiration_date',
            'is_single_use', 'is_renewable',
            'activation_count', 'remaining_uses',
            'is_active',
            'mass_generation_id',
        ]
        sort_by = request.args.get('sort_by', 'id')
        sort_dir = request.args.get('sort_dir', 'asc')

        if sort_by not in allowed_sort_fields:
            current_app.logger.warning(f"Invalid sort_by '{sort_by}', using default 'id'")
            sort_by = 'id'
        if sort_dir not in ['asc', 'desc']:
            current_app.logger.warning(f"Invalid sort_dir '{sort_dir}', using default 'asc'")
            sort_dir = 'asc'

        # Базовый запрос
        query = Promotion.query.filter_by(user_id=user_id)
        if not include_deleted:
            query = query.filter_by(is_deleted=False)

        # Фильтры
        if created_after:
            query = query.filter(Promotion.creation_date >= datetime.fromisoformat(created_after))
        if created_before:
            query = query.filter(Promotion.creation_date <= datetime.fromisoformat(created_before))
        if name:
            query = query.filter(Promotion.name.ilike(f'%{name}%'))

        if serial_number or machine_name:
            query = query.join(Promotion.machines)
            if serial_number:
                query = query.filter(PromotionMachine.serialNumber.like(f"%{serial_number}%"))
            if machine_name:
                query = query.filter(PromotionMachine.humanName.ilike(f"%{machine_name}%"))

        # Общее количество до пагинации
        total = query.count()

        # Сортировка с предсказуемым порядком NULL
        base_col = getattr(Promotion, sort_by)
        order_expr = []
        if sort_by in ['expiration_date', 'remaining_uses', 'mass_generation_id']:
            # NULL в конец при asc, в начало при desc
            nulls_flag = case((base_col.is_(None), 1), else_=0)
            order_expr.append(nulls_flag.asc() if sort_dir == 'asc' else nulls_flag.desc())

        order_expr.append(base_col.desc() if sort_dir == 'desc' else base_col.asc())
        current_app.logger.info(f"Sorting promotions by {sort_by} {sort_dir}")

        # Пагинация
        if page is not None and per_page is not None:
            promotions = (
                query.order_by(*order_expr)
                .offset(page * per_page)
                .limit(per_page)
                .all()
            )
        else:
            promotions = query.order_by(*order_expr).all()

        current_datetime = datetime.utcnow()

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
                'is_expired': p.expiration_date is not None and p.expiration_date < current_datetime,
                'machines': [{
                    'serialNumber': m.serialNumber,
                    'humanName': m.humanName
                } for m in p.machines]
            } for p in promotions],
            'total': total,
            'page': page if page is not None else 0,
            'per_page': per_page if per_page is not None else len(promotions),
            'sort_by': sort_by,
            'sort_dir': sort_dir
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

@main.route('/promotion/<int:promotion_id>/mark-used', methods=['PUT'])
@jwt_required()
def mark_promotion_used(promotion_id):
    """Marks a promotion as used based on its activation count"""
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
        
    promo = Promotion.query.get(promotion_id)
    if not promo:
        return jsonify({'error': 'Promotion not found'}), 404
        
    # Check if user owns this promotion
    if promo.user_id != user.id:
        return jsonify({'error': 'Not authorized to modify this promotion'}), 403
        
    # Если акция уже использована, ничего не делаем
    if promo.is_used:
        return jsonify({'message': 'Promotion already marked as used'}), 200
        
    # Если activation_count > 0, отмечаем как использованную
    if promo.activation_count > 0:
        # Для одноразовых акций любого типа
        if promo.is_single_use:
            promo.is_used = True
            promo.is_active = False
            db.session.commit()
            return jsonify({'message': 'Promotion marked as used'}), 200
        # Для многоразовых акций с ограниченным количеством использований
        elif promo.remaining_uses is not None and promo.remaining_uses <= 0:
            promo.is_used = True
            promo.is_active = False
            db.session.commit()
            return jsonify({'message': 'Promotion marked as used'}), 200
        # Для многоразовых акций без ограничения на количество использований
        elif promo.remaining_uses is None:
            promo.is_used = True
            db.session.commit()
            return jsonify({'message': 'Promotion marked as used'}), 200
    
    return jsonify({'message': 'No changes made to promotion'}), 200

@main.route('/promotions/update-used-status', methods=['PUT'])
@jwt_required()
def update_all_used_promotions():
    """Updates the is_used status for all promotions with activation_count > 0"""
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    # Найти все промоакции пользователя, у которых activation_count > 0, но is_used = False
    promotions = Promotion.query.filter(
        Promotion.user_id == user.id,
        Promotion.activation_count > 0,
        Promotion.is_used == False
    ).all()
    
    updated_count = 0
    for promo in promotions:
        # Для одноразовых акций любого типа
        if promo.is_single_use:
            promo.is_used = True
            promo.is_active = False
            updated_count += 1
        # Для многоразовых акций с ограниченным количеством использований
        elif promo.remaining_uses is not None and promo.remaining_uses <= 0:
            promo.is_used = True
            promo.is_active = False
            updated_count += 1
        # Для многоразовых акций без ограничения на количество использований
        # Помечаем как использованные для отслеживания статистики, но оставляем активными
        elif promo.remaining_uses is None and not promo.is_single_use:
            promo.is_used = True
            # НЕ деактивируем безлимитные акции (is_active остается True)
            updated_count += 1
    
    db.session.commit()
    
    return jsonify({
        'message': f'Updated {updated_count} promotions',
        'updated_count': updated_count
    }), 200

@main.route('/fix-free-drink-promotions', methods=['POST'])
@jwt_required()
def fix_free_drink_promotions():
    """
    Исправляет существующие акции типа free_drink, у которых не установлен drinks_limit,
    но есть discount_value. Устанавливает drinks_limit и remaining_uses равными discount_value.
    """
    try:
        # Получаем все акции типа free_drink с пустым drinks_limit и ненулевым discount_value
        promotions = Promotion.query.filter(
            (Promotion.discount_type == 'free_drink') | (Promotion.discount_type == 'free_drinks'),
            Promotion.drinks_limit.is_(None),
            Promotion.discount_value > 0
        ).all()
        
        updated_count = 0
        for promo in promotions:
            # Устанавливаем drinks_limit равным discount_value
            promo.drinks_limit = promo.discount_value
            
            # Если акция уже использовалась, вычитаем количество использований из remaining_uses
            if promo.activation_count and promo.activation_count > 0:
                promo.remaining_uses = max(0, promo.discount_value - promo.activation_count)
                # Если все использования исчерпаны, помечаем акцию как использованную и неактивную
                if promo.remaining_uses <= 0:
                    promo.is_used = True
                    promo.is_active = False
            else:
                # Если акция еще не использовалась, устанавливаем remaining_uses равным discount_value
                promo.remaining_uses = promo.discount_value
            
            updated_count += 1
        
        db.session.commit()
        
        return jsonify({
            'message': f'Fixed {updated_count} free_drink promotions',
            'updated_count': updated_count
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@main.route('/fix-promotion/<int:promotion_id>', methods=['POST'])
#@jwt_required()
def fix_specific_promotion(promotion_id):
    """
    Исправляет конкретную акцию по ID, устанавливая drinks_limit и remaining_uses
    на основе discount_value и activation_count.
    """
    try:
        promo = Promotion.query.get(promotion_id)
        if not promo:
            return jsonify({'error': 'Promotion not found'}), 404
        
        # Сохраняем старые значения для логирования
        old_drinks_limit = promo.drinks_limit
        old_remaining_uses = promo.remaining_uses
        
        # Если это free_drink или free_drinks и есть discount_value
        if (promo.discount_type == 'free_drink' or promo.discount_type == 'free_drinks') and promo.discount_value > 0:
            # Устанавливаем drinks_limit равным discount_value
            promo.drinks_limit = promo.discount_value
            
            # Если акция уже использовалась, вычитаем количество использований из remaining_uses
            if promo.activation_count and promo.activation_count > 0:
                promo.remaining_uses = max(0, promo.discount_value - promo.activation_count)
                # Если все использования исчерпаны, помечаем акцию как использованную и неактивную
                if promo.remaining_uses <= 0:
                    promo.is_used = True
                    promo.is_active = False
                else:
                    # Если остались использования, убеждаемся что акция активна
                    promo.is_active = True
            else:
                # Если акция еще не использовалась, устанавливаем remaining_uses равным discount_value
                promo.remaining_uses = promo.discount_value
        
        db.session.commit()
        
        return jsonify({
            'message': f'Fixed promotion {promotion_id}',
            'old_drinks_limit': old_drinks_limit,
            'new_drinks_limit': promo.drinks_limit,
            'old_remaining_uses': old_remaining_uses,
            'new_remaining_uses': promo.remaining_uses,
            'activation_count': promo.activation_count,
            'is_active': promo.is_active,
            'is_used': promo.is_used
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@main.route('/promotions/update-expired', methods=['PUT'])
@jwt_required()
def update_expired_promotions():
    """Updates the is_active status for all promotions with passed expiration date"""
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        current_datetime = datetime.utcnow()
        current_app.logger.info(f"Checking expired promotions at {current_datetime}")
        
        # Log time zone info
        current_app.logger.info(f"Current UTC time: {datetime.utcnow()}")
        
        # Check specific promotion by ID (for debugging)
        promo_431 = Promotion.query.filter_by(id=431, user_id=user.id).first()
        if promo_431:
            current_app.logger.info(f"Promotion 431 details:")
            current_app.logger.info(f"  - name: {promo_431.name}")
            current_app.logger.info(f"  - expiration_date: {promo_431.expiration_date}")
            current_app.logger.info(f"  - is_active: {promo_431.is_active}")
            current_app.logger.info(f"  - is_deleted: {promo_431.is_deleted}")
            current_app.logger.info(f"  - is_expired: {promo_431.expiration_date is not None and promo_431.expiration_date < current_datetime}")
            
            # Format dates for easier comparison
            if promo_431.expiration_date:
                exp_str = promo_431.expiration_date.strftime('%Y-%m-%d %H:%M:%S')
                now_str = current_datetime.strftime('%Y-%m-%d %H:%M:%S')
                current_app.logger.info(f"  - Comparison: expiration ({exp_str}) < current ({now_str}) = {promo_431.expiration_date < current_datetime}")
        else:
            current_app.logger.info("Promotion 431 not found or not owned by user")
        
        # Найти все активные промоакции пользователя с истекшим сроком действия
        expired_promotions = Promotion.query.filter(
            Promotion.user_id == user.id,
            Promotion.is_active == True,
            Promotion.is_deleted == False,
            Promotion.expiration_date.isnot(None),
            Promotion.expiration_date < current_datetime
        ).all()
        
        # Log the results
        current_app.logger.info(f"Found {len(expired_promotions)} expired promotions")
        for promo in expired_promotions:
            current_app.logger.info(f"Expired promotion: ID={promo.id}, name={promo.name}, exp_date={promo.expiration_date}")
        
        if not expired_promotions:
            # Try a direct SQL query to check
            raw_query = text("""
                SELECT id, name, expiration_date, is_active, is_deleted
                FROM promotions
                WHERE user_id = :user_id
                AND is_active = 1
                AND is_deleted = 0
                AND expiration_date IS NOT NULL
                AND expiration_date < :current_datetime
            """)
            result = db.session.execute(raw_query, {"user_id": user.id, "current_datetime": current_datetime})
            rows = result.fetchall()
            
            current_app.logger.info(f"Raw SQL query found {len(rows)} expired promotions")
            for row in rows:
                current_app.logger.info(f"SQL result: {row}")
            
            if not rows:
                current_app.logger.info("No expired promotions found via SQL either")
                return jsonify({
                    'message': 'No expired promotions found',
                    'updated_count': 0
                }), 200
        
        updated_count = 0
        for promo in expired_promotions:
            promo.is_active = False
            updated_count += 1
            current_app.logger.info(f"Marking promotion {promo.id} (name: {promo.name}) as inactive due to expiration")
        
        db.session.commit()
        
        return jsonify({
            'message': f'Updated {updated_count} expired promotions',
            'updated_count': updated_count
        }), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating expired promotions: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to update expired promotions: {str(e)}'}), 500

@main.route('/promotions/fix-used-active', methods=['PUT'])
@jwt_required()
def fix_used_single_use_promotions():
    """Fixes single-use promotions that have been used but are still marked as active"""
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        # Check if admin mode is requested
        admin_mode = request.args.get('admin_mode', 'false').lower() == 'true'
        
        # Get a secret key from request if provided
        admin_key = request.args.get('admin_key', '')
        
        # If admin mode is requested, check if user is admin or has the correct key
        if admin_mode:
            # You can set a secure admin key in your config and check it here
            # For now, we'll use a simple check (CHANGE THIS TO A SECURE KEY IN PRODUCTION)
            config_admin_key = current_app.config.get('ADMIN_KEY', 'your_secure_admin_key_here')
            is_authorized = user.is_admin if hasattr(user, 'is_admin') else False
            is_authorized = is_authorized or admin_key == config_admin_key
            
            if not is_authorized:
                return jsonify({'error': 'Unauthorized for admin mode'}), 403
                
            current_app.logger.info(f"Admin mode enabled for fixing used promotions")
            
            # Create a base query without user filter for admin mode
            base_query = Promotion.query.filter(
                Promotion.is_active == True,
                Promotion.is_single_use == True,
                Promotion.activation_count > 0,
                Promotion.is_deleted == False
            )
        else:
            # Normal mode - only user's promotions
            base_query = Promotion.query.filter(
                Promotion.user_id == user.id,
                Promotion.is_active == True,
                Promotion.is_single_use == True,
                Promotion.activation_count > 0,
                Promotion.is_deleted == False
            )
        
        # Find all single-use promotions that have been used but are still active
        used_active_promotions = base_query.all()
        
        # Log the promotions found
        current_app.logger.info(f"Found {len(used_active_promotions)} used single-use promotions that are still active")
        for promo in used_active_promotions:
            current_app.logger.info(f"Will fix promotion: ID={promo.id}, name={promo.name}, type={promo.discount_type}, " 
                                   f"user_id={promo.user_id}, activation_count={promo.activation_count}")
        
        if not used_active_promotions:
            return jsonify({
                'message': 'No used single-use promotions found that need fixing',
                'updated_count': 0
            }), 200
        
        updated_count = 0
        for promo in used_active_promotions:
            # Mark as used and inactive
            promo.is_used = True
            promo.is_active = False
            updated_count += 1
            current_app.logger.info(f"Fixed promotion {promo.id} (name: {promo.name}, user_id: {promo.user_id})")
        
        db.session.commit()
        
        return jsonify({
            'message': f'Fixed {updated_count} used single-use promotions',
            'updated_count': updated_count,
            'admin_mode': admin_mode
        }), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error fixing used single-use promotions: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to fix used single-use promotions: {str(e)}'}), 500

@main.route('/admin/fix-all-used-promotions', methods=['POST'])
def admin_fix_all_used_promotions():
    """Admin-only endpoint to fix all single-use promotions in the database without JWT auth.
    This is intended for one-time fixes across the entire database.
    """
    try:
        # Get admin key from request parameters or JSON body
        admin_key = request.args.get('admin_key', '')
        if not admin_key and request.is_json:
            admin_key = request.json.get('admin_key', '')
            
        # Verify admin key (should be set in your application config)
        config_admin_key = current_app.config.get('ADMIN_KEY', 'your_secure_admin_key_here')
        if admin_key != config_admin_key:
            # Return a generic error to avoid leaking info
            return jsonify({'error': 'Unauthorized'}), 403
            
        # Find all single-use promotions that have been used but are still active
        used_active_promotions = Promotion.query.filter(
            Promotion.is_active == True,
            Promotion.is_single_use == True,
            Promotion.activation_count > 0,
            Promotion.is_deleted == False
        ).all()
        
        # Log the affected promotions
        promotion_count = len(used_active_promotions)
        current_app.logger.info(f"Admin fix: Found {promotion_count} used single-use promotions that are still active")
        
        if not used_active_promotions:
            return jsonify({
                'message': 'No used single-use promotions found that need fixing',
                'updated_count': 0
            }), 200
        
        # Group promotions by user for reporting
        users_affected = set()
        user_counts = {}
        
        updated_count = 0
        for promo in used_active_promotions:
            # Mark as used and inactive
            promo.is_used = True
            promo.is_active = False
            updated_count += 1
            
            # Track affected users
            users_affected.add(promo.user_id)
            user_counts[promo.user_id] = user_counts.get(promo.user_id, 0) + 1
            
            current_app.logger.info(f"Fixed promotion {promo.id} (name: {promo.name}, user_id: {promo.user_id})")
        
        db.session.commit()
        
        # Prepare user stats for response
        user_stats = [{"user_id": user_id, "fixed_count": count} for user_id, count in user_counts.items()]
        
        return jsonify({
            'message': f'Fixed {updated_count} used single-use promotions',
            'updated_count': updated_count,
            'users_affected': len(users_affected),
            'user_stats': user_stats
        }), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in admin fix for used promotions: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to fix used single-use promotions: {str(e)}'}), 500
