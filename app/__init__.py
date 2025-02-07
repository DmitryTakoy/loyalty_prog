from flask import Flask, jsonify, request, current_app
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_migrate import Migrate
from flask_caching import Cache
from pathlib import Path
import logging
from flask_jwt_extended import JWTManager
import os

db = SQLAlchemy()
migrate = Migrate()
cache = Cache()
jwt = JWTManager()

def create_app(config_name='default'):
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    
    from config import config
    app.config.from_object(config[config_name])

    app.config['JWT_SECRET_KEY'] = 'p0101527'
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

    # Простой CORS без заморочек
    CORS(app, 
         resources={r"/api/*": {"origins": "http://localhost:3000"}},
         supports_credentials=True)

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
        from app.models.auth import User
        from app.models.mass_generation import MassGeneration
        from app.models.promotion import Promotion, PromotionMachine
        from app.models.transaction import Transaction
        
        app.register_blueprint(auth_bp, url_prefix='/api/auth')
        app.register_blueprint(main, url_prefix='/api')
        
        db.create_all()
    
    return app