from flask import Blueprint, request, jsonify, current_app, redirect, make_response
from app.models.auth import User
from app.models.email_verification import EmailVerification
from app.utils.auth_utils import (
    generate_jwt_token,
    send_verification_email,
    verify_vending_machines
)
from app import db
from datetime import datetime, timedelta
import secrets
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr
from flask_jwt_extended import create_access_token
from flask_jwt_extended import jwt_required, get_jwt_identity


EMAIL_SENDER = "efimdima@ya.ru"
EMAIL_PASSWORD = "ioupvzsjmalhfobl"
SMTP_SERVER = "smtp.yandex.ru"
SMTP_PORT = 587

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['POST'])
def register():
    try:
        data = request.get_json()
        current_app.logger.info(f"Registration attempt with data: {data}")

        # Проверяем, что все необходимые поля присутствуют
        required_fields = ['email', 'password', 'api_key', 'user_id']
        if not all(field in data for field in required_fields):
            return jsonify({'error': 'Missing required fields'}), 400

        # Проверяем, не существует ли уже пользователь с таким email
        if User.query.filter_by(email=data['email'].lower()).first():
            return jsonify({'error': 'Email already registered'}), 400

        if 'machines_confirmed' not in data or not data['machines_confirmed']:
            return jsonify({
                'success': False,
                'error': 'Machines must be confirmed before registration'
            }), 400
        
        # Перепроверяем машины перед регистрацией
        machines = verify_vending_machines(data['api_key'], data['user_id'])
        if not machines:
            return jsonify({
                'success': False,
                'error': 'Invalid API key or user ID'
            }), 400
        
        # Создаем нового пользователя
        user = User(
            email=data['email'].lower(),
            api_key=data['api_key'],
            user_id=data['user_id'],
            email_verified=False
        )
        user.set_password(data['password'])
        
        try:
            db.session.add(user)
            db.session.flush()  # Получаем id пользователя
            
            # Создаем запись верификации
            verification = EmailVerification(
                user_id=user.id,
                token=secrets.token_urlsafe(32)
            )
            db.session.add(verification)
            db.session.commit()
            
            # Отправляем письмо для верификации
            if not send_verification_email(user.email, verification.token):
                return jsonify({'error': 'Failed to send verification email'}), 500

            # Создаем JWT токен
            access_token = create_access_token(identity=user.id)
            
            return jsonify({
                'message': 'Registration successful',
                'token': access_token,
                'user': {
                    'email': user.email,
                    'api_key': user.api_key,
                    'user_id': user.user_id
                }
            }), 201

        except Exception as e:
            current_app.logger.error(f"Database error: {str(e)}")
            db.session.rollback()
            return jsonify({'error': 'Database error'}), 500

    except Exception as e:
        current_app.logger.error(f"Registration error: {str(e)}")
        return jsonify({'error': str(e)}), 500


def send_verification_email(recipient_email, token):
    try:
        verification_url = f"http://localhost:3000/verify-email?token={token}"
        
        html = f"""
        <html>
          <head>
            <style>
              body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
              .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
              .header {{ background-color: #4A90E2; color: white; padding: 20px; text-align: center; }}
              .content {{ padding: 20px; background-color: #f9f9f9; }}
              .button {{ 
                display: inline-block;
                padding: 10px 20px;
                background-color: #4A90E2;
                color: white;
                text-decoration: none;
                border-radius: 5px;
                margin: 20px 0;
              }}
              .footer {{ text-align: center; color: #666; font-size: 12px; }}
            </style>
          </head>
          <body>
            <div class="container">
              <div class="header">
                <h1>SmartVend Loyalty</h1>
              </div>
              <div class="content">
                <h2>Подтверждение email</h2>
                <p>Здравствуйте!</p>
                <p>Для завершения регистрации в системе SmartVend Loyalty необходимо подтвердить ваш email.</p>
                <p>Нажмите на кнопку ниже:</p>
                <p style="text-align: center;">
                  <a href="{verification_url}" class="button">Подтвердить email</a>
                </p>
                <p>Или перейдите по ссылке:</p>
                <p><a href="{verification_url}">{verification_url}</a></p>
                <p>Если вы не регистрировались в системе SmartVend Loyalty, просто проигнорируйте это письмо.</p>
              </div>
              <div class="footer">
                <p>© 2024 SmartVend Loyalty. Все права защищены.</p>
              </div>
            </div>
          </body>
        </html>
        """
        
        msg = MIMEMultipart('alternative')
        msg['Subject'] = Header('Подтверждение email для SmartVend Loyalty', 'utf-8')
        msg['From'] = formataddr(('SmartVend Loyalty', EMAIL_SENDER))
        msg['To'] = recipient_email

        text = f"""
        Для подтверждения вашего email перейдите по ссылке:
        {verification_url}
        """
        
        part1 = MIMEText(text, 'plain', 'utf-8')
        part2 = MIMEText(html, 'html', 'utf-8')

        msg.attach(part1)
        msg.attach(part2)

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
            
        current_app.logger.info(f"Verification email sent successfully to {recipient_email}")
        return True
        
    except Exception as e:
        current_app.logger.error(f"Failed to send verification email: {str(e)}", exc_info=True)
        return False

@auth_bp.route('/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        user = User.query.filter_by(email=data['email'].lower()).first()

        if not user or not user.check_password(data['password']):
            return jsonify({'error': 'Invalid email or password'}), 401

        access_token = create_access_token(identity=user.id)
        
        return jsonify({
            'token': access_token,
            'user': {
                'email': user.email,
                'api_key': user.api_key,
                'user_id': user.user_id
            }
        }), 200

    except Exception as e:
        current_app.logger.error(f"Login error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@auth_bp.route("/verify-email", methods=["GET"])
def verify_email():
    token = request.args.get('token')
    current_app.logger.info(f"Received verification request with token: {token}")
    
    if not token:
        current_app.logger.warning("No token provided")
        return jsonify({"error": "Verification token is missing"}), 400
    
    try:
        # Ищем верификацию по токену
        verification = EmailVerification.query.filter_by(token=token).first()
        current_app.logger.info(f"Found verification: {verification}")
        
        if not verification:
            current_app.logger.warning(f"No verification found for token: {token}")
            return jsonify({"error": "Invalid or expired verification token"}), 400

        # Проверяем срок действия токена
        if verification.expires_at < datetime.utcnow():
            current_app.logger.warning(f"Token expired for user {verification.user_id}")
            db.session.delete(verification)
            db.session.commit()
            return jsonify({"error": "Verification token has expired"}), 400

        user = verification.user
        current_app.logger.info(f"Found user: {user.email}")

        if user.email_verified:
            current_app.logger.info(f"User {user.email} already verified")
            # Удаляем использованный токен
            db.session.delete(verification)
            db.session.commit()
            
            # Создаём новый токен для автоматического входа
            access_token = create_access_token(identity=user.id)
            return jsonify({
                "message": "Email already verified",
                "token": access_token,
                "user": {
                    "email": user.email,
                    "verified": True,
                    "api_key": user.api_key,
                    "user_id": user.user_id
                }
            }), 200

        # Подтверждаем email
        user.email_verified = True
        # Удаляем использованный токен верификации
        db.session.delete(verification)
        db.session.commit()
        current_app.logger.info(f"Successfully verified email for user {user.email}")

        # Создаём токен для автоматического входа
        access_token = create_access_token(identity=user.id)
        
        return jsonify({
            "message": "Email verified successfully",
            "token": access_token,
            "user": {
                "email": user.email,
                "verified": True,
                "api_key": user.api_key,
                "user_id": user.user_id
            }
        }), 200

    except Exception as e:
        current_app.logger.error(f"Verification error: {str(e)}", exc_info=True)
        db.session.rollback()
        return jsonify({"error": "Verification failed"}), 500

@auth_bp.route("/resend-verification", methods=["POST"])
@jwt_required()
def resend_verification():
    try:
        user = get_jwt_identity()
        user = User.query.get(user['id'])
        
        if user.email_verified:
            return jsonify({"error": "Email уже подтвержден"}), 400

        # Удаляем старые верификации
        EmailVerification.query.filter_by(user_id=user.id).delete()
        
        new_verification = create_verification_entry(user.id)
        db.session.add(new_verification)
        db.session.commit()

        if not send_verification_email(user.email, new_verification.token):
            raise Exception("Failed to send email")

        return jsonify({"message": "Письмо отправлено повторно"}), 200

    except Exception as e:
        current_app.logger.error(f"Resend error: {str(e)}", exc_info=True)
        return jsonify({"error": "Не удалось отправить письмо"}), 500


@auth_bp.before_request
def log_request_info():
    """Логируем информацию о каждом запросе"""
    current_app.logger.info(f"=== New Request ===")
    current_app.logger.info(f"Method: {request.method}")
    current_app.logger.info(f"URL: {request.url}")
    current_app.logger.info(f"Headers: {dict(request.headers)}")
    if request.is_json:
        current_app.logger.info(f"JSON Data: {request.get_json()}")
    current_app.logger.info("==================")

@auth_bp.route('/check-credentials', methods=['POST'])
def check_credentials():
    try:
        current_app.logger.info("Processing check-credentials request")
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No JSON data received'}), 400
            
        current_app.logger.info(f"Received data: {data}")
        
        if not all(k in data for k in ('api_key', 'user_id')):
            return jsonify({'error': 'Missing api_key or user_id'}), 400
            
        result = verify_vending_machines(data['api_key'], data['user_id'])
        current_app.logger.info(f"Verification result: {result}")
        
        return jsonify(result)
        
    except Exception as e:
        current_app.logger.error(f"Error in check-credentials: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500
    
@auth_bp.route('/test-api', methods=['POST'])
def test_api():
    """Endpoint for testing API connection with full headers"""
    try:
        data = request.get_json()
        if not data or 'api_key' not in data or 'user_id' not in data:
            return jsonify({'error': 'Missing api_key or user_id'}), 400
            
        url = "https://api.smartvend.ru/v1/get-list-of-controllers"
        headers = {
            'x-organization-key': data['api_key'],
            'x-user-id': data['user_id'],
            'Content-Type': 'application/json',
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'User-Agent': 'Python/3.7 Requests/2.31.0'
        }
        
        # Отправляем пустой JSON в теле запроса
        response = requests.post(url, headers=headers, json={}, timeout=10)
        
        return jsonify({
            'status_code': response.status_code,
            'headers': dict(response.headers),
            'body': response.text
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@auth_bp.route('/test', methods=['GET'])
def test_route():
    current_app.logger.info("Test route called")
    return jsonify({'message': 'Auth routes are working'}), 200

@auth_bp.before_request
def skip_jwt_for_options():
    if request.method == 'OPTIONS':
        return  # Просто отдать 200 и CORS-заголовки

@auth_bp.route('/user', methods=['GET'])
@jwt_required()
def get_user():
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
            
        return jsonify({
            'id': user.id,
            'email': user.email,
            'email_verified': user.email_verified,
            'api_key': user.api_key,
            'user_id': user.user_id,
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'last_login': user.last_login.isoformat() if user.last_login else None
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error getting user data: {str(e)}")
        return jsonify({'error': 'Failed to get user data'}), 500

@auth_bp.route('/user', methods=['PUT'])
@jwt_required()
def update_user():
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
            
        data = request.get_json()
        
        # Update user fields if provided in request
        if 'first_name' in data:
            user.first_name = data['first_name']
        if 'last_name' in data:
            user.last_name = data['last_name']
        if 'phone' in data:
            user.phone = data['phone']
        if 'company' in data:
            user.company = data['company']
        if 'settings' in data:
            user.settings = data['settings']
            
        db.session.commit()
        
        return jsonify({
            'id': user.id,
            'email': user.email,
            'first_name': user.first_name if hasattr(user, 'first_name') else None,
            'last_name': user.last_name if hasattr(user, 'last_name') else None,
            'phone': user.phone if hasattr(user, 'phone') else None,
            'company': user.company if hasattr(user, 'company') else None,
            'settings': user.settings if hasattr(user, 'settings') else None
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error updating user data: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to update user data'}), 500