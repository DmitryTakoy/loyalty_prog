from flask import current_app
import click
from flask.cli import with_appcontext
from datetime import datetime, timedelta
from app.models.promotion import Promotion
from app import db
from sqlalchemy import text


@click.command('check-expired-promotions')
@with_appcontext
def check_expired_promotions():
    """Check and update status of expired promotions."""
    try:
        current_datetime = datetime.utcnow()
        current_app.logger.info(f"Starting check for expired promotions at {current_datetime}")
        
        # Check specific promotion by ID (for debugging)
        promo_431 = Promotion.query.get(431)
        if promo_431:
            current_app.logger.info(f"Promotion 431 details:")
            current_app.logger.info(f"  - name: {promo_431.name}")
            current_app.logger.info(f"  - expiration_date: {promo_431.expiration_date}")
            current_app.logger.info(f"  - is_active: {promo_431.is_active}")
            current_app.logger.info(f"  - is_deleted: {promo_431.is_deleted}")
            current_app.logger.info(f"  - is_expired: {promo_431.expiration_date is not None and promo_431.expiration_date < current_datetime}")
            
            # Format dates for easier comparison
            if promo_431.expiration_date:
                exp_str = promo_431.expiration_date.strftime('%Y-%m-%d %H:%M:%S')
                now_str = current_datetime.strftime('%Y-%m-%d %H:%M:%S')
                current_app.logger.info(f"  - Comparison: expiration ({exp_str}) < current ({now_str}) = {promo_431.expiration_date < current_datetime}")
        else:
            current_app.logger.info("Promotion 431 not found")
        
        # Find all active promotions with passed expiration date
        # Use more explicit filtering to ensure it catches the expired promotions
        expired_promotions = Promotion.query.filter(
            Promotion.is_active == True,
            Promotion.is_deleted == False,
            Promotion.expiration_date.isnot(None),
            Promotion.expiration_date < current_datetime
        ).all()
        
        # Dump the query results
        current_app.logger.info(f"Found {len(expired_promotions)} expired promotions")
        for promo in expired_promotions:
            current_app.logger.info(f"Expired promotion: ID={promo.id}, name={promo.name}, exp_date={promo.expiration_date}")
        
        if not expired_promotions:
            # Try a direct SQL query to check
            raw_query = text("""
                SELECT id, name, expiration_date, is_active, is_deleted
                FROM promotions
                WHERE is_active = 1
                AND is_deleted = 0
                AND expiration_date IS NOT NULL
                AND expiration_date < :current_datetime
            """)
            result = db.session.execute(raw_query, {"current_datetime": current_datetime})
            rows = result.fetchall()
            
            current_app.logger.info(f"Raw SQL query found {len(rows)} expired promotions")
            for row in rows:
                current_app.logger.info(f"SQL result: {row}")
            
            if not rows:
                current_app.logger.info("No expired promotions found via SQL either")
                click.echo("No expired promotions found")
                return
        
        updated_count = 0
        for promo in expired_promotions:
            promo.is_active = False
            updated_count += 1
            current_app.logger.info(f"Marking promotion {promo.id} (name: {promo.name}) as inactive due to expiration")
        
        db.session.commit()
        
        click.echo(f"Updated {updated_count} expired promotions")
        current_app.logger.info(f"Successfully updated {updated_count} expired promotions")
    except Exception as e:
        db.session.rollback()
        error_msg = f"Error updating expired promotions: {str(e)}"
        current_app.logger.error(error_msg, exc_info=True)
        click.echo(error_msg, err=True)


@click.command('renew-monthly-promotions')
@with_appcontext
def renew_monthly_promotions():
    """Renew all monthly renewable promotions: продлевает срок и сбрасывает лимиты."""
    try:
        current_datetime = datetime.utcnow()
        renewed_count = 0
        # Находим все renewable промоакции, которые не удалены
        renewable_promos = Promotion.query.filter(
            Promotion.is_renewable == True,
            Promotion.is_deleted == False
        ).all()
        for promo in renewable_promos:
            # Продлеваем expiration_date на месяц (30 дней)
            if promo.expiration_date is None or promo.expiration_date < current_datetime:
                promo.expiration_date = current_datetime.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=30)
            else:
                promo.expiration_date += timedelta(days=30)
            # Сбрасываем лимиты
            if promo.discount_type in ('free_drinks', 'free_drink'):
                if promo.drinks_limit is not None:
                    promo.remaining_uses = promo.drinks_limit
                elif promo.discount_value:
                    promo.remaining_uses = promo.discount_value
            elif promo.discount_type == 'percentage':
                if promo.is_single_use:
                    promo.remaining_uses = 1
                elif promo.drinks_limit is not None:
                    promo.remaining_uses = promo.drinks_limit
            # Активируем, если вдруг неактивна
            promo.is_active = True
            promo.is_used = False
            renewed_count += 1
            current_app.logger.info(f"Renewed promotion {promo.id} (name: {promo.name}) until {promo.expiration_date}")
        db.session.commit()
        click.echo(f"Renewed {renewed_count} monthly renewable promotions.")
        current_app.logger.info(f"Successfully renewed {renewed_count} monthly renewable promotions.")
    except Exception as e:
        db.session.rollback()
        error_msg = f"Error renewing monthly promotions: {str(e)}"
        current_app.logger.error(error_msg, exc_info=True)
        click.echo(error_msg, err=True)


def init_app(app):
    """Register CLI commands."""
    app.cli.add_command(check_expired_promotions)
    app.cli.add_command(renew_monthly_promotions) 