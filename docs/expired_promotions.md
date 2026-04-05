# Expired Promotions Feature

This documentation explains the implementation of the expired promotions feature in the Loyalty Program application.

## Problem

Promotions with an expiration date were still showing as "Active" in the UI even after their expiration date had passed. This was because the system wasn't checking if the expiration date had passed when returning the promotion status.

## Solution

Several changes were made to fix this issue:

1. Added an `is_expired` flag to the API responses for both individual promotions and mass generation
2. Added a `is_expired` property to the Promotion model for easy checking if a promotion has expired
3. Created a new API endpoint `/api/promotions/update-expired` to update the status of expired promotions
4. Created a CLI command `check-expired-promotions` that can be run periodically (via cron) to check and update expired promotions
5. Added a cron job setup script to automatically check for expired promotions every hour

## Usage

### Checking if a Promotion is Expired

The API now returns an `is_expired` flag in the promotion response. If `is_expired` is true, the promotion has passed its expiration date.

```json
{
  "promotions": [
    {
      "id": 431,
      "name": "11",
      "is_active": true,
      "is_expired": true,
      "expiration_date": "2025-03-06T21:00:00"
    }
  ]
}
```

### Updating Expired Promotions

To manually update the status of all expired promotions, you can call the following API endpoint:

```
PUT /api/promotions/update-expired
```

This will check all active promotions and mark those with passed expiration dates as inactive.

### Cron Job

A cron job has been set up to automatically check for expired promotions every hour. The cron job runs the following command:

```
FLASK_APP=run.py flask check-expired-promotions
```

### Manual Check

To manually check for expired promotions via CLI, run:

```bash
cd /path/to/loyalty_prog
FLASK_APP=run.py flask check-expired-promotions
```

## Behavior

1. When a promotion's expiration date passes, it will be automatically marked as inactive in the database during the next hourly check
2. The frontend will display expired promotions with the "Expired" status instead of "Active"
3. Expired promotions can't be used in vending machines 