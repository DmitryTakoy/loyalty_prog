# config.py

import os
from pathlib import Path

class Config:
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Используем текущую директорию как базовую
    BASE_DIR = Path(__file__).resolve().parent
    
    # Папка для архивов относительно текущей директории
    ARCHIVE_FOLDER = os.path.join(os.path.dirname(__file__), 'archives')
    MAX_ARCHIVES = 5

    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')

    # JWT Settings
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-secret-key-here'
    
    # Email settings
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    
    # URLs
    SITE_URL = os.environ.get('SITE_URL', 'http://localhost:3000')
    PARENT_SERVICE_URL = os.environ.get('PARENT_SERVICE_URL', 'http://parent-service-api.com')

class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{str(Config.BASE_DIR / "employees_dev.db")}'
    # Логи в папке logs в текущей директории
    LOG_FILE = str(Config.BASE_DIR / 'logs' / 'app_dev.log')

    # Development specific settings
    ENV = 'development'
    CORS_ORIGINS = ['http://localhost:3000', 'http://127.0.0.1:3000']

    SITE_URL = 'http://localhost:3000'
    PARENT_SERVICE_URL = 'http://localhost:8000'  # URL родительского сервиса для разработки

class ProductionConfig(Config):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = 'sqlite:////root/loyapro/back/data/employees.db'
    LOG_FILE = '/root/loyapro/back/logs/app.log'
    SOCKET_PATH = '/home/flaskuser/loyapro/loyapro.sock'

    # Production specific settings
    ENV = 'production'
    CORS_ORIGINS = ['http://your-production-domain.com']

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}