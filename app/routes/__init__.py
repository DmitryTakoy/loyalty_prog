from app.routes.auth import auth_bp
from app.routes.main import main

# Если нужно экспортировать что-то еще, добавьте здесь
__all__ = ['auth_bp', 'main']
