from app import create_app, db
from app.models.promotion import Promotion

app = create_app('production')
with app.app_context():
    promo = Promotion.query.get(431)
    if promo:
        print(f"Promotion 431 Details:")
        print(f"- customer_id: {promo.customer_id}")
        print(f"- is_active: {promo.is_active}")
        print(f"- is_used: {promo.is_used}")
        print(f"- creation_date: {promo.creation_date}")
        print(f"- expiration_date: {promo.expiration_date}")
        print(f"- activation_count: {promo.activation_count}")
        print(f"- remaining_uses: {promo.remaining_uses}")
        print(f"- is_single_use: {promo.is_single_use}")
        print(f"- discount_type: {promo.discount_type}")
        print(f"- discount_value: {promo.discount_value}")
        
        # Also check if machines are associated with this promotion
        if hasattr(promo, 'machines') and promo.machines:
            print(f"- machines: {[m.serialNumber for m in promo.machines]}")
        else:
            print("- No machines associated with this promotion")
    else:
        print("Promotion 431 not found") 