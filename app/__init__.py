from flask import Flask, jsonify, request, current_app
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_migrate import Migrate
from flask_caching import Cache
from pathlib import Path
import logging
from flask_jwt_extended import JWTManager
import os
from datetime import timedelta
from logging.handlers import RotatingFileHandler
from config import config

db = SQLAlchemy()
migrate = Migrate()
cache = Cache()
jwt = JWTManager()

def create_app(config_name='default'):
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    
    # Configure logging
    if not os.path.exists('logs'):
        os.mkdir('logs')
    file_handler = RotatingFileHandler('logs/loyaltypro.log', maxBytes=10240, backupCount=10)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info('LoyaltyPro startup')

    # Load config
    app.config.from_object(config[config_name])
    #config[config_name].init_app(app)  # Создаем необходимые папки

    # JWT Configuration
    app.config['JWT_SECRET_KEY'] = 'p0101527'
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=7)  # 7 days for access token
    app.config['JWT_REFRESH_TOKEN_EXPIRES'] = timedelta(days=30)  # 30 days for refresh token

    # Ensure upload directory exists
    upload_folder = app.config.get('UPLOAD_FOLDER')
    if upload_folder:
        os.makedirs(upload_folder, exist_ok=True)
        app.logger.info(f"Ensuring upload directory exists at: {upload_folder}")
        app.logger.info(f"Upload directory exists: {os.path.exists(upload_folder)}")
        app.logger.info(f"Upload directory contents: {os.listdir(upload_folder) if os.path.exists(upload_folder) else 'directory does not exist'}")

    jwt.init_app(app)

    @jwt.invalid_token_loader
    def invalid_token_loader_callback(reason):
        current_app.logger.warning(f"Invalid token: {reason}")
        return jsonify({
            "error": "Invalid token",
            "reason": reason
        }), 401

    @jwt.unauthorized_loader
    def unauthorized_loader(reason):
        current_app.logger.warning(f"Unauthorized: {reason}")
        return jsonify(msg=reason), 401

    @jwt.revoked_token_loader
    def revoked_token_loader(jwt_header, jwt_data):
        current_app.logger.warning("Token revoked")
        return jsonify(msg="Token revoked"), 401

    # CORS configuration
    CORS(app, 
         resources={
             r"/api/*": {
                 "origins": [
                     "http://localhost:3000",
                     "http://91.236.196.187",
                     "http://91.236.196.187:80",
                     "http://91.236.196.187:5000"
                 ],
                 "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                 "allow_headers": ["Content-Type", "Authorization", "X-XSRF-TOKEN", "Accept", "Origin", "X-Requested-With"],
                 "expose_headers": ["Content-Range", "X-Content-Range"],
                 "supports_credentials": True,
                 "max_age": 600
             },
             r"/(request|completion)": {  # Add CORS for vending machine endpoints
                 "origins": "*",  # Allow from any origin
                 "methods": ["POST", "OPTIONS"],
                 "allow_headers": ["Content-Type"],
                 "max_age": 600
             }
         },
         supports_credentials=True,
         allow_headers=["Content-Type", "Authorization", "X-XSRF-TOKEN", "Accept", "Origin", "X-Requested-With"],
         expose_headers=["Content-Range", "X-Content-Range"])

    # Add CORS headers to all responses
    @app.after_request
    def after_request(response):
        origin = request.headers.get('Origin')
        
        # For web frontend
        if origin in ["http://localhost:3000", "http://91.236.196.187", "http://91.236.196.187:3000"]:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-XSRF-TOKEN, Accept, Origin, X-Requested-With'
            response.headers['Access-Control-Expose-Headers'] = 'Content-Range, X-Content-Range'
        
        # For vending machine endpoints
        elif request.path in ['/request', '/completion']:
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
            
        return response

    # Настройка кэша
    app.config['CACHE_TYPE'] = 'simple'
    app.config['CACHE_DEFAULT_TIMEOUT'] = 3000
    app.config['PROPAGATE_EXCEPTIONS'] = True
    cache.init_app(app)

    # Настройка логирования
    logging.basicConfig(
        filename=app.config['LOG_FILE'],
        level=logging.DEBUG,
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    db.init_app(app)
    migrate.init_app(app, db)
    
    with app.app_context():
        from app.routes.auth import auth_bp
        from app.routes.main import main
        from app.routes.vending import vending_bp
        from app.models.auth import User
        from app.models.mass_generation import MassGeneration
        from app.models.promotion import Promotion, PromotionMachine
        from app.models.transaction import Transaction
        
        app.register_blueprint(auth_bp, url_prefix='/api/auth')
        app.register_blueprint(main, url_prefix='/api')
        app.register_blueprint(vending_bp)  # Register without prefix for vending machines
        
        # Register CLI commands
        from app.cli import init_app as init_cli
        init_cli(app)
        
        db.create_all()
    
    return app