import json
import unittest
import secrets
from datetime import datetime
from app import create_app, db
from app.models import Promotion, PromotionMachine, Transaction, User
import unittest.mock

class CompletionEndpointTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()
        
        # Create test user
        self.user = User(
            email='test@example.com',
            api_key=secrets.token_hex(16),
            user_id=secrets.token_hex(8)
        )
        self.user.set_password('password')
        db.session.add(self.user)
        db.session.commit()
        
        # Create test data
        self.promotion = Promotion(
            name='Test Promotion',
            customer_id='test_customer',
            discount_type='percentage',
            discount_value=10,
            is_single_use=True,
            is_used=False,
            activation_count=0,
            user_id=self.user.id
        )
        db.session.add(self.promotion)
        db.session.commit()
        
        self.machine = PromotionMachine(
            promotion_id=self.promotion.id,
            serialNumber='test_machine',
            humanName='Test Machine'
        )
        db.session.add(self.machine)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_valid_completion_request(self):
        """Test a valid completion request"""
        data = {
            'customer': 'test_customer',
            'machine': 'test_machine',
            'price': 100,
            'product': 1,
            'success': True,
            'unixtime': hex(int(datetime.now().timestamp()))[2:].upper()
        }
        
        response = self.client.post(
            '/completion',
            data=json.dumps(data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data)['message'], 'Transaction completed')
        
        # Check that the promotion was updated
        updated_promo = Promotion.query.get(self.promotion.id)
        self.assertTrue(updated_promo.is_used)
        self.assertEqual(updated_promo.activation_count, 1)
        
        # Check that a transaction was created
        transaction = Transaction.query.filter_by(user_id=self.user.id).first()
        self.assertIsNotNone(transaction)
        self.assertEqual(transaction.machine_id, 'test_machine')
        self.assertEqual(transaction.price, 100)

    def test_missing_data_completion_request(self):
        """Test a completion request with missing data"""
        data = {
            'machine': 'test_machine',
            'price': 100,
            'product': 1,
            'success': True
        }
        
        response = self.client.post(
            '/completion',
            data=json.dumps(data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.data)['error'], 'Missing customer or machine')

    def test_nonexistent_promotion(self):
        """Test a completion request for a nonexistent promotion"""
        data = {
            'customer': 'nonexistent_customer',
            'machine': 'test_machine',
            'price': 100,
            'product': 1,
            'success': True
        }
        
        response = self.client.post(
            '/completion',
            data=json.dumps(data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data)['message'], 'No such promotion')

    def test_invalid_machine(self):
        """Test a completion request for an invalid machine"""
        data = {
            'customer': 'test_customer',
            'machine': 'invalid_machine',
            'price': 100,
            'product': 1,
            'success': True
        }
        
        response = self.client.post(
            '/completion',
            data=json.dumps(data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data)['message'], 'Promotion not valid for this machine')

    def test_invalid_json(self):
        """Test a completion request with invalid JSON"""
        response = self.client.post(
            '/completion',
            data='invalid json',
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 500)
        self.assertIn('error', json.loads(response.data))

    def test_error_handling(self):
        """Test error handling in the completion endpoint"""
        data = {
            'customer': 'test_customer',
            'machine': 'test_machine',
            'price': 100,
            'product': 1,
            'success': True,
            'unixtime': 'not-a-hex-string'
        }
        
        # Get the count of transactions before the request
        transaction_count_before = Transaction.query.count()
        
        response = self.client.post(
            '/completion',
            data=json.dumps(data),
            content_type='application/json'
        )
        
        # Check that the request was successful despite the invalid unixtime
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data)['message'], 'Transaction completed')
        
        # Check that a transaction was created
        transaction_count_after = Transaction.query.count()
        self.assertEqual(transaction_count_after, transaction_count_before + 1)
        
        # Get the created transaction
        transaction = Transaction.query.order_by(Transaction.id.desc()).first()
        
        # Check that the transaction has the correct data
        self.assertEqual(transaction.machine_id, 'test_machine')
        self.assertEqual(transaction.price, 100)
        self.assertEqual(transaction.product_id, 1)
        
        # Check that the timestamp is set to a default value (not None)
        self.assertIsNotNone(transaction.timestamp)

    def test_server_error(self):
        """Test a server error in the completion endpoint"""
        # Create a request with missing required fields that would cause a server error
        data = {
            'customer': 'test_customer',
            'machine': 'test_machine',
            # Missing price and product fields
            'success': True
        }
        
        # Mock the Transaction model to raise an exception when creating a new transaction
        with unittest.mock.patch('app.models.transaction.Transaction', side_effect=Exception('Simulated database error')):
            response = self.client.post(
                '/completion',
                data=json.dumps(data),
                content_type='application/json'
            )
            
            # Check that the request returns a 500 error
            self.assertEqual(response.status_code, 500)
            self.assertIn('error', json.loads(response.data))

if __name__ == '__main__':
    unittest.main() 