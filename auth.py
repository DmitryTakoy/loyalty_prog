from flask import Blueprint, request, jsonify, current_app, redirect
from app.models.auth import User, EmailVerification
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


EMAIL_SENDER = "efimdima@ya.ru"
EMAIL_PASSWORD = "ioupvzsjmalhfobl"
SMTP_SERVER = "smtp.yandex.ru"
SMTP_PORT = 587

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    
    # Проверяем обязательные поля
    required_fields = ['email', 'password', 'api_key', 'user_id']
    if not all(field in data for field in required_fields):
        return jsonify({'error': 'Missing required fields'}), 400
    
    # Проверяем, не занят ли email
    if User.query.filter_by(email=data['email']).first():
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
    user.generate_verification_token()
    
    try:
        db.session.add(user)
        db.session.flush()  # генерируем user.id
        
        # Создаем запись для верификации email
        verification = EmailVerification(
            user_id=user.id,
            token=secrets.token_urlsafe(32),
            expires_at=datetime.utcnow() + timedelta(hours=24)
        )
        
        db.session.add(verification)
        db.session.commit()
        
        # Отправляем email для верификации
        if send_verification_email(user.email, verification.token):
            token = generate_jwt_token(user.id)
            return jsonify({
                'message': 'Registration successful. Please verify your email.',
                'token': token,
                'vending_machines': machines
            }), 201
        else:
            return jsonify({'error': 'Failed to send verification email'}), 500
            
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
    

def send_verification_email(recipient_email, token):
    try:
        sender_email = EMAIL_SENDER
        password = EMAIL_PASSWORD
        subject = "Verify your email"
        
        # Формируем ссылку для верификации
        verification_link = f"http://91.236.196.187/verify-email/{token}"

        body = f"""
        Здравствуйте!

        Спасибо за регистрацию. Для подтверждения вашего email, пожалуйста, перейдите по ссылке:

        {verification_link}

        Если вы не регистрировались на нашем сайте, проигнорируйте это письмо.
        """

        message = MIMEMultipart()
        message['From'] = formataddr((str(Header('SmartVend', 'utf-8')), sender_email))
        message['To'] = recipient_email
        message['Subject'] = Header(subject, 'utf-8')
        message.attach(MIMEText(body, 'plain', 'utf-8'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(sender_email, password)
        text = message.as_string()
        server.sendmail(sender_email, recipient_email, text)
        server.quit()

        return True
    except Exception as e:
        # Логируем ошибку отправки письма
        current_app.logger.error(f"Failed to send email: {e}", exc_info=True)
        return False

@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    
    if not data.get('email') or not data.get('password'):
        return jsonify({'error': 'Missing email or password'}), 400
    
    user = User.query.filter_by(email=data['email'].lower()).first()
    
    if not user or not user.check_password(data['password']):
        return jsonify({'error': 'Invalid email or password'}), 401
    
    if not user.email_verified:
        return jsonify({'error': 'Please verify your email first'}), 403
    
    # Обновляем время последнего входа
    user.last_login = datetime.utcnow()
    db.session.commit()
    
    token = create_access_token(identity=user.id)
    return jsonify({
        'token': token,
        'user': {
            'email': user.email,
            'verified': user.email_verified
        }
    }), 200

@auth_bp.route("/verify-email/<token>", methods=["GET"])
def verify_email(token):
    verification = EmailVerification.query.filter_by(token=token).first()
    if not verification:
        return jsonify({"error": "Invalid verification token"}), 404

    if verification.expires_at < datetime.utcnow():
        return jsonify({"error": "Verification token has expired"}), 400

    # Вот здесь нам нужен verification.user:
    user = verification.user  # <-- заработает, если прописан relationship
    user.email_verified = True

    db.session.delete(verification)
    db.session.commit()

    # допустим, делаем редирект
    return redirect("http://localhost:3000/dashboard", code=302)

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