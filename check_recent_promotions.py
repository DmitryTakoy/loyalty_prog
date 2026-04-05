from app import create_app, db
from app.models.promotion import Promotion
from datetime import datetime, timedelta

app = create_app('production')
with app.app_context():
    # Get promotions created in the last 7 days
    one_week_ago = datetime.utcnow() - timedelta(days=7)
    recent_promos = Promotion.query.filter(Promotion.creation_date >= one_week_ago).order_by(Promotion.id.desc()).limit(20).all()
    
    print(f"Found {len(recent_promos)} recent promotions")
    active_count = 0
    inactive_count = 0
    
    for promo in recent_promos:
        status = "ACTIVE" if promo.is_active else "INACTIVE"
        used = "USED" if promo.is_used else "NOT USED"
        print(f"ID: {promo.id}, Created: {promo.creation_date}, Status: {status}, Used: {used}, Customer ID: {promo.customer_id}")
        
        if promo.is_active:
            active_count += 1
        else:
            inactive_count += 1
    
    print(f"\nSummary: {active_count} active, {inactive_count} inactive") 