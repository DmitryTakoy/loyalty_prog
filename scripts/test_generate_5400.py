import os
import shutil
import tempfile
from datetime import datetime

from app import create_app, db
from app.models.auth import User
from flask_jwt_extended import create_access_token


def main():
    # Create app in testing mode (uses in-memory SQLite)
    app = create_app('testing')

    # Use a temporary uploads directory to avoid polluting the repo
    temp_upload_dir = tempfile.mkdtemp(prefix='uploads_test_')
    app.config['UPLOAD_FOLDER'] = temp_upload_dir

    total_to_generate = 5400
    batch_size = 1000  # safer batches

    with app.app_context():
        # Create a temporary user
        user = User(email='test_mass@example.com', api_key='test_key', user_id='test_user')
        user.set_password('testpass123')
        db.session.add(user)
        db.session.commit()

        # Issue JWT for this user
        token = create_access_token(identity=user.id)

        client = app.test_client()

        generated = 0
        batch_index = 0
        errors = []

        while generated < total_to_generate:
            batch_index += 1
            to_make = min(batch_size, total_to_generate - generated)

            payload = {
                'num_codes': to_make,
                'discount_type': 'percentage',
                'discount_value': 5,
                'is_single_use': True,
                'is_renewable': False,
                'expiration_date': None,
                'name': f'TEST_5400_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}_b{batch_index}',
                'machines': []
            }

            resp = client.post(
                '/api/generate_codes',
                json=payload,
                headers={'Authorization': f'Bearer {token}'}
            )

            if resp.status_code != 200:
                errors.append({
                    'batch': batch_index,
                    'size': to_make,
                    'status': resp.status_code,
                    'body': resp.get_json(silent=True)
                })
                break

            generated += to_make

        print({'generated': generated, 'errors': errors, 'upload_dir': temp_upload_dir})

    # Cleanup temp uploads
    try:
        shutil.rmtree(temp_upload_dir)
    except Exception as cleanup_err:
        print({'cleanup_error': str(cleanup_err), 'upload_dir': temp_upload_dir})


if __name__ == '__main__':
    main()


