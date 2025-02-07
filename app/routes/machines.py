from flask import request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from loyaltypro.services.machines import verify_vending_machines
from loyaltypro.models.user import User

machines_bp = Blueprint('machines', __name__)

@machines_bp.before_request
def log_request_info():
    current_app.logger.info("=== Machines Request ===")
    current_app.logger.info(f"Headers: {dict(request.headers)}")
    current_app.logger.info("=====================")

@machines_bp.route('/machines', methods=['GET'])
@jwt_required()
def get_machines():
    try:
        # Получаем API ключ и ID пользователя из заголовков
        api_key = request.headers.get('X-API-Key')
        user_id = request.headers.get('X-User-ID')

        if not api_key or not user_id:
            current_app.logger.error("Missing API key or user ID in headers")
            return jsonify({'error': 'Отсутствуют необходимые заголовки'}), 400

        current_app.logger.info(f"Getting machines for user_id: {user_id}")
        
        # Получаем ID пользователя из токена
        user_id_from_token = get_jwt_identity()
        current_app.logger.info(f"User ID from token: {user_id_from_token}")
        
        # Проверяем существование пользователя
        user = User.query.get(user_id_from_token)
        if not user or not user.email_verified:
            return jsonify({'error': 'Unauthorized'}), 401

        # Получаем список автоматов
        result = verify_vending_machines(api_key, user_id)
        current_app.logger.info(f"Machines result: {result}")
        
        if result.get('success'):
            return jsonify(result.get('machines', [])), 200
        else:
            return jsonify({'error': 'Ошибка получения данных'}), 400

    except Exception as e:
        current_app.logger.error(f"Error getting machines: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500 