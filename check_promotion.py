from app import create_app, db
from app.models.promotion import Promotion

app = create_app('production')
with app.app_context():
    promo = Promotion.query.get(994)
    if promo:
        print(f'Promotion 994: is_active={promo.is_active}, is_used={promo.is_used}, customer_id={promo.customer_id}')
    else:
        print('Promotion 994 not found') 