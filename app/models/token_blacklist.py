from app import db
from datetime import datetime

class TokenBlacklist(db.Model):
    __tablename__ = 'token_blacklist'

    id = db.Column(db.Integer, primary_key=True)
    jti = db.Column(db.String(36), nullable=False, unique=True)
    token_type = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    revoked_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires = db.Column(db.DateTime, nullable=False)

    user = db.relationship('User', backref=db.backref('blacklisted_tokens', lazy=True))

    def __repr__(self):
        return f'<TokenBlacklist {self.jti}>' 