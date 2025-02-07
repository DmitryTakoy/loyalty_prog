import jwt
from datetime import datetime, timedelta
from flask import current_app
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from app import cache
import secrets
from app.models.email_verification import EmailVerification

def generate_jwt_token(user_id):
    payload = {
        'exp': datetime.utcnow() + timedelta(days=1),
        'iat': datetime.utcnow(),
        'sub': user_id
    }
    return jwt.encode(
        payload,
        current_app.config.get('SECRET_KEY'),
        algorithm='HS256'
    )

def verify_jwt_token(token):
    try:
        payload = jwt.decode(
            token,
            current_app.config.get('SECRET_KEY'),
            algorithms=['HS256']
        )
        return payload['sub']
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def send_verification_email(email, token):
    sender_email = current_app.config['MAIL_USERNAME']
    password = current_app.config['MAIL_PASSWORD']

    message = MIMEMultipart()
    message["From"] = sender_email
    message["To"] = email
    message["Subject"] = "Verify your email"

    verification_link = f"{current_app.config['SITE_URL']}/verify-email/{token}"
    body = f"""
    Welcome to Loyalty Pro!
    
    Please click the following link to verify your email:
    {verification_link}
    
    This link will expire in 24 hours.
    
    If you didn't request this, please ignore this email.
    """
    
    message.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, password)
            server.send_message(message)
        return True
    except Exception as e:
        current_app.logger.error(f"Failed to send email: {str(e)}")
        return False


# def verify_vending_machines(api_key, user_id):
#     try:
#         current_app.logger.info(f"Starting verification for user_id: {user_id}")
        
#         url = "https://api.smartvend.ru/v1/get-list-of-controllers"
        
#         headers = {
#             'x-organization-key': api_key,
#             'x-user-id': user_id,
#             'Content-Type': 'application/json',
#             'Accept': '*/*',
#             'Accept-Encoding': 'gzip, deflate, br',
#             'Connection': 'keep-alive',
#             'User-Agent': 'Python/3.7 Requests/2.31.0'
#         }

#         # Пустое тело запроса в виде JSON
#         data = {}
        
#         current_app.logger.info(f"Making request to: {url}")
#         current_app.logger.debug(f"Headers: {headers}")
        
#         response = requests.post(
#             url,
#             headers=headers,
#             json=data,  # Используем json параметр вместо data
#             timeout=10
#         )
        
#         current_app.logger.info(f"Response status: {response.status_code}")
#         current_app.logger.debug(f"Response headers: {dict(response.headers)}")
#         current_app.logger.debug(f"Response body: {response.text}")
        
#         if response.status_code == 200:
#             try:
#                 data = response.json()
#                 if data.get('result', {}).get('$case') == 'success':
#                     machines = data['result']['success']
#                     return {
#                         'success': True,
#                         'machines': [
#                             {
#                                 'serialNumber': machine['serialNumber'],
#                                 'humanName': machine.get('humanName', 'Unknown')
#                             }
#                             for machine in machines[:5]
#                         ]
#                     }
#                 else:
#                     return {
#                         'success': False,
#                         'error': 'Invalid response format from SmartVend API'
#                     }
#             except ValueError:
#                 current_app.logger.error(f"Failed to parse JSON response: {response.text}")
#                 return {
#                     'success': False,
#                     'error': 'Invalid JSON response from SmartVend API'
#                 }
#         else:
#             error_message = f'SmartVend API error: {response.status_code}'
#             try:
#                 error_data = response.json()
#                 if error_data.get('message'):
#                     error_message = error_data['message']
#             except:
#                 pass
                
#             return {
#                 'success': False,
#                 'error': error_message
#             }
            
#     except requests.exceptions.RequestException as e:
#         current_app.logger.error(f"Request failed: {str(e)}")
#         return {
#             'success': False,
#             'error': f'Failed to connect to SmartVend API: {str(e)}'
#         }
#     except Exception as e:
#         current_app.logger.error(f"Unexpected error: {str(e)}")
#         return {
#             'success': False,
#             'error': f'An unexpected error occurred: {str(e)}'
#         }

def verify_vending_machines(api_key, user_id):
    cache_key = f"machines_{api_key}_{user_id}"

    # Сначала проверяем кэш
    cached_data = cache.get(cache_key)
    if cached_data:
        current_app.logger.info(f"Returning cached data for user_id: {user_id}")
        return cached_data

    # Если кэша нет - делаем запрос к API
    try:
        current_app.logger.info(f"Starting verification for user_id: {user_id}")
        
        url = "https://api.smartvend.ru/v1/get-list-of-controllers"
        headers = {
            'x-organization-key': api_key,
            'x-user-id': user_id,
            'Content-Type': 'application/json',
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'User-Agent': 'Python/3.7 Requests/2.31.0'
        }
        data = {}

        current_app.logger.info(f"Making request to: {url}")
        current_app.logger.debug(f"Headers: {headers}")
        
        response = requests.post(
            url,
            headers=headers,
            json=data,
            timeout=10
        )
        
        current_app.logger.info(f"Response status: {response.status_code}")
        current_app.logger.debug(f"Response headers: {dict(response.headers)}")
        current_app.logger.debug(f"Response body: {response.text}")
        
        if response.status_code == 200:
            try:
                response_data = response.json()
                if response_data.get('result', {}).get('$case') == 'success':
                    machines = response_data['result']['success']
                    result = {
                        'success': True,
                        'machines': [
                            {
                                'serialNumber': machine['serialNumber'],
                                'humanName': machine.get('humanName', 'Unknown')
                            }
                            for machine in machines
                        ]
                    }
                    # Сохраняем в кэш
                    cache.set(cache_key, result, timeout=300)  # Можете указать другой таймаут
                    return result
                else:
                    return {
                        'success': False,
                        'error': 'Invalid response format from SmartVend API'
                    }
            except ValueError:
                current_app.logger.error(f"Failed to parse JSON response: {response.text}")
                return {
                    'success': False,
                    'error': 'Invalid JSON response from SmartVend API'
                }
        else:
            error_message = f'SmartVend API error: {response.status_code}'
            try:
                error_data = response.json()
                if error_data.get('message'):
                    error_message = error_data['message']
            except:
                pass
                
            return {
                'success': False,
                'error': error_message
            }
            
    except requests.exceptions.RequestException as e:
        current_app.logger.error(f"Request failed: {str(e)}")
        return {
            'success': False,
            'error': f'Failed to connect to SmartVend API: {str(e)}'
        }
    except Exception as e:
        current_app.logger.error(f"Unexpected error: {str(e)}")
        return {
            'success': False,
            'error': f'An unexpected error occurred: {str(e)}'
        }

def create_verification_entry(user_id):
    verification = EmailVerification(
        user_id=user_id,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.utcnow() + timedelta(hours=24)
    )
    return verification