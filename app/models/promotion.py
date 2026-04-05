# app/models/promotion.py
from app import db
from datetime import datetime
from app.models.mass_generation import MassGeneration  # Импортируем MassGeneration


class Promotion(db.Model):
    __tablename__ = 'promotions'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    
    discount_type = db.Column(db.String(50), nullable=False)  # 'percentage', 'free_drink', or 'free_drinks'
    discount_value = db.Column(db.Integer, default=0)
    drinks_limit = db.Column(db.Integer)  # количество напитков (если нужно)
    is_renewable = db.Column(db.Boolean, default=False)
    expiration_date = db.Column(db.DateTime)
    is_single_use = db.Column(db.Boolean, default=False)
    is_used = db.Column(db.Boolean, default=False)
    creation_date = db.Column(db.DateTime, default=datetime.utcnow)
    activation_count = db.Column(db.Integer, default=0)
    remaining_uses = db.Column(db.Integer)  # оставшиеся использования
    is_active = db.Column(db.Boolean, default=True)
    
    # Поля для мягкого удаления
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime, nullable=True)

    # Связь с массовой генерацией (многие к одному)
    mass_generation_id = db.Column(db.Integer, db.ForeignKey('mass_generations.id'), nullable=True)
    mass_generation = db.relationship('MassGeneration', back_populates='promotions')

    # Уникальный код (если нужно идентифицировать акцию)
    customer_id = db.Column(db.String(50), unique=True, nullable=True)
    qr_filename = db.Column(db.String(255), nullable=True)

    # Привязка к пользователю (User)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    user = db.relationship('User', backref=db.backref('promotions', lazy=True))

    @property
    def is_expired(self):
        """Проверяет, истек ли срок действия акции"""
        if self.expiration_date is None:
            return False
        return datetime.utcnow() > self.expiration_date

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.discount_type == 'free_drinks' or self.discount_type == 'free_drink':
            # If drinks_limit is None, use discount_value as the limit for free_drink promotions
            if self.drinks_limit is None:
                if self.discount_value and self.discount_value > 0:
                    # Use discount_value as the number of drinks
                    self.drinks_limit = self.discount_value
                    self.remaining_uses = self.discount_value
                elif self.is_single_use:
                    self.remaining_uses = 1
                else:
                    self.remaining_uses = None
            else:
                self.remaining_uses = self.drinks_limit
        elif self.discount_type == 'percentage':
            # Для акций типа percentage также устанавливаем remaining_uses
            if self.is_single_use:
                self.remaining_uses = 1
            else:
                # Для многоразовых акций percentage можно установить лимит использований
                # через drinks_limit (хотя название не совсем подходит)
                if self.drinks_limit is not None:
                    self.remaining_uses = self.drinks_limit
                else:
                    self.remaining_uses = None
        elif self.is_single_use:
            self.remaining_uses = 1
        else:
            self.remaining_uses = None
            
    def soft_delete(self):
        """Помечает промоакцию как удаленную"""
        self.is_deleted = True
        self.deleted_at = datetime.utcnow()
        self.is_active = False  # Также деактивируем промоакцию
        
    def restore(self):
        """Восстанавливает удаленную промоакцию"""
        self.is_deleted = False
        self.deleted_at = None
        # Не меняем is_active, так как это отдельное состояние


class PromotionMachine(db.Model):
    __tablename__ = 'promotion_machines'

    id = db.Column(db.Integer, primary_key=True)
    
    # Связь с промокодом
    promotion_id = db.Column(db.Integer, db.ForeignKey('promotions.id'), nullable=False)
    promotion = db.relationship('Promotion', backref=db.backref('machines', lazy=True))

    serialNumber = db.Column(db.String(255), nullable=False)
    humanName = db.Column(db.String(255), nullable=False)

    # Связь с массовой генерацией
    mass_generation_id = db.Column(db.Integer, db.ForeignKey('mass_generations.id'), nullable=True)
    mass_generation = db.relationship('MassGeneration', back_populates='machines')