#!/bin/bash
# Setup cron jobs for expired-promotion check and monthly renewal.
#
# Two root causes fixed here:
#   1. FLASK_APP was pointing at run.py which does not exist; correct file is main.py.
#   2. The flask binary lives inside the venv and is not on cron's PATH;
#      use the absolute path /root/loyapro/back/venv/bin/flask.
#   3. The renew-monthly-promotions job was never actually written to crontab
#      because the script had a logic gap when check-expired-promotions already
#      existed (the temp-file was already flushed before the renewal block ran its
#      own `crontab` call, but the grep target was absent so that was fine —
#      however in practice the job was missing, so we now always write both).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_PATH="$SCRIPT_DIR"
FLASK_BIN="$(dirname "$SCRIPT_DIR")/venv/bin/flask"

if [ ! -x "$FLASK_BIN" ]; then
    echo "ERROR: Flask binary not found at $FLASK_BIN" >&2
    exit 1
fi

TEMP_CRONTAB=$(mktemp)
crontab -l > "$TEMP_CRONTAB" 2>/dev/null

# --- check-expired-promotions (daily at midnight) ---
if ! grep -q "check-expired-promotions" "$TEMP_CRONTAB"; then
    echo "# Check expired promotions once daily at midnight" >> "$TEMP_CRONTAB"
    echo "0 0 * * * cd $APP_PATH && FLASK_APP=main.py $FLASK_BIN check-expired-promotions >> $APP_PATH/logs/cron_expired_promotions.log 2>&1" >> "$TEMP_CRONTAB"
    echo "Daily expiry-check cron job added."
else
    # Fix any old entry that still references run.py or a bare 'flask'
    sed -i \
        -e "s|FLASK_APP=run\.py flask|FLASK_APP=main.py $FLASK_BIN|g" \
        -e "s| flask check-expired| $FLASK_BIN check-expired|g" \
        "$TEMP_CRONTAB"
    echo "Daily expiry-check cron job already present (patched if needed)."
fi

# --- renew-monthly-promotions (1st of month at 00:10) ---
if ! grep -q "renew-monthly-promotions" "$TEMP_CRONTAB"; then
    echo "# Renew monthly renewable promotions on the 1st of every month at 00:10" >> "$TEMP_CRONTAB"
    echo "10 0 1 * * cd $APP_PATH && FLASK_APP=main.py $FLASK_BIN renew-monthly-promotions >> $APP_PATH/logs/cron_renew_monthly_promotions.log 2>&1" >> "$TEMP_CRONTAB"
    echo "Monthly renewal cron job added."
else
    sed -i \
        -e "s|FLASK_APP=run\.py flask|FLASK_APP=main.py $FLASK_BIN|g" \
        -e "s| flask renew-monthly| $FLASK_BIN renew-monthly|g" \
        "$TEMP_CRONTAB"
    echo "Monthly renewal cron job already present (patched if needed)."
fi

crontab "$TEMP_CRONTAB"
rm "$TEMP_CRONTAB"

echo ""
echo "Current crontab:"
crontab -l
echo ""
echo "Manual test commands:"
echo "  cd $APP_PATH && FLASK_APP=main.py $FLASK_BIN check-expired-promotions"
echo "  cd $APP_PATH && FLASK_APP=main.py $FLASK_BIN renew-monthly-promotions"