# app/models/mass_generation.py
from app import db
from datetime import datetime

class MassGeneration(db.Model):
    __tablename__ = 'mass_generations'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    discount_type = db.Column(db.String(50), nullable=False)  # 'percentage', 'free_drink', or 'free_drinks'
    discount_value = db.Column(db.Integer, default=0)
    drinks_limit = db.Column(db.Integer, nullable=True)
    is_single_use = db.Column(db.Boolean, default=False)
    is_renewable = db.Column(db.Boolean, default=False)
    expiration_date = db.Column(db.DateTime, nullable=True)
    archive_filename = db.Column(db.String(255), nullable=True)
    creation_date = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Поля для мягкого удаления
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime, nullable=True)

    # Связь с Promotion (One MassGeneration -> Many Promotions)
    promotions = db.relationship('Promotion', back_populates='mass_generation')

    # Связь с PromotionMachine (One MassGeneration -> Many PromotionMachines)
    machines = db.relationship('PromotionMachine', back_populates='mass_generation')

    # Добавить связь с пользователем
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    user = db.relationship('User', backref=db.backref('mass_generations', lazy=True))
    
    def soft_delete(self):
        """Помечает массовую генерацию как удаленную"""
        self.is_deleted = True
        self.deleted_at = datetime.utcnow()
        
        # Также помечаем как удаленные все связанные промоакции
        for promotion in self.promotions:
            if not promotion.is_deleted:
                promotion.soft_delete()
        
    def restore(self):
        """Восстанавливает удаленную массовую генерацию"""
        self.is_deleted = False
        self.deleted_at = None
        
        # Восстанавливаем все связанные промоакции
        for promotion in self.promotions:
            if promotion.is_deleted:
                promotion.restore()