from app import db
from datetime import datetime

class Transaction(db.Model):
    __tablename__ = 'transactions'
    
    id = db.Column(db.Integer, primary_key=True)
    # Заменяем employee_id на user_id
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    machine_id = db.Column(db.String(8), nullable=False)
    product_id = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Integer, nullable=False)
    discounted_price = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    # Привязка к промокоду (nullable: старые транзакции не привязаны)
    promotion_id = db.Column(db.Integer, db.ForeignKey('promotions.id'), nullable=True)
    promotion = db.relationship('Promotion', backref=db.backref('transactions', lazy=True))

    # Была ли успешная выдача со скидкой (nullable: для старых записей неизвестно)
    success = db.Column(db.Boolean, nullable=True)

    # Добавляем связь с User
    user = db.relationship('User', backref=db.backref('transactions', lazy=True))