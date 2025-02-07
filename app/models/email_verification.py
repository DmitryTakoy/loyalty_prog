from app import db
from datetime import datetime, timedelta
import secrets

class EmailVerification(db.Model):
    __tablename__ = 'email_verifications'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token = db.Column(db.String(100), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    
    user = db.relationship('User', backref=db.backref('verifications', lazy=True))

    def __init__(self, user_id, token=None, expires_in=24):
        self.user_id = user_id
        self.token = token or secrets.token_urlsafe(32)
        self.expires_at = datetime.utcnow() + timedelta(hours=expires_in) 