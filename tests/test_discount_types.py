import json
import unittest
import secrets
from datetime import datetime, timedelta
from app import create_app, db
from app.models import Promotion, PromotionMachine, Transaction, User, MassGeneration

class DiscountTypeTestCase(unittest.TestCase):
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
        
        # Login to get JWT token
        response = self.client.post(
            '/api/auth/login',
            data=json.dumps({'email': 'test@example.com', 'password': 'password'}),
            content_type='application/json'
        )
        self.token = json.loads(response.data)['token']
        
        # Create test machine
        self.machine_serial = 'test_machine_123'
        self.machine_name = 'Test Machine'

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()
    
    def create_promotion(self, discount_type, is_single_use=True, drinks_limit=None):
        """Helper method to create a promotion with the specified discount type"""
        customer_id = f"test_customer_{secrets.token_hex(8)}"
        
        promotion = Promotion(
            name=f'Test {discount_type}',
            customer_id=customer_id,
            discount_type=discount_type,
            discount_value=10 if discount_type == 'percentage' else 1,
            is_single_use=is_single_use,
            is_used=False,
            activation_count=0,
            drinks_limit=drinks_limit,
            user_id=self.user.id
        )
        db.session.add(promotion)
        db.session.commit()
        
        # Add machine to promotion
        machine = PromotionMachine(
            promotion_id=promotion.id,
            serialNumber=self.machine_serial,
            humanName=self.machine_name
        )
        db.session.add(machine)
        db.session.commit()
        
        return promotion
    
    def test_percentage_discount_single_use(self):
        """Test that a single-use percentage discount is marked as used after completion"""
        promotion = self.create_promotion('percentage', is_single_use=True)
        
        # Send request to vending endpoint
        request_data = {
            'customer': promotion.customer_id,
            'machine': self.machine_serial,
            'product': 1
        }
        
        # First check the request endpoint returns the correct discount
        response = self.client.post(
            '/request',
            data=json.dumps(request_data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data)['discount'], 10)
        
        # Now send a completion request
        completion_data = {
            'customer': promotion.customer_id,
            'machine': self.machine_serial,
            'price': 100,
            'product': 1,
            'success': True
        }
        
        response = self.client.post(
            '/completion',
            data=json.dumps(completion_data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        
        # Check that the promotion was marked as used
        updated_promo = Promotion.query.get(promotion.id)
        self.assertTrue(updated_promo.is_used)
        self.assertEqual(updated_promo.activation_count, 1)
        
        # Try to use it again - should return empty response
        response = self.client.post(
            '/request',
            data=json.dumps(request_data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data), {})
    
    def test_free_drink_singular_single_use(self):
        """Test that a single-use free_drink (singular) is marked as used after completion"""
        promotion = self.create_promotion('free_drink', is_single_use=True)
        
        # Send request to vending endpoint
        request_data = {
            'customer': promotion.customer_id,
            'machine': self.machine_serial,
            'product': 1
        }
        
        # First check the request endpoint returns the correct discount
        response = self.client.post(
            '/request',
            data=json.dumps(request_data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data)['discount'], 100)
        
        # Now send a completion request
        completion_data = {
            'customer': promotion.customer_id,
            'machine': self.machine_serial,
            'price': 100,
            'product': 1,
            'success': True
        }
        
        response = self.client.post(
            '/completion',
            data=json.dumps(completion_data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        
        # Check that the promotion was marked as used
        updated_promo = Promotion.query.get(promotion.id)
        self.assertTrue(updated_promo.is_used)
        self.assertEqual(updated_promo.activation_count, 1)
        
        # Try to use it again - should return empty response
        response = self.client.post(
            '/request',
            data=json.dumps(request_data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data), {})
    
    def test_free_drinks_plural_single_use(self):
        """Test that a single-use free_drinks (plural) is marked as used after completion"""
        promotion = self.create_promotion('free_drinks', is_single_use=True, drinks_limit=1)
        
        # Send request to vending endpoint
        request_data = {
            'customer': promotion.customer_id,
            'machine': self.machine_serial,
            'product': 1
        }
        
        # First check the request endpoint returns the correct discount
        response = self.client.post(
            '/request',
            data=json.dumps(request_data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data)['discount'], 100)
        
        # Now send a completion request
        completion_data = {
            'customer': promotion.customer_id,
            'machine': self.machine_serial,
            'price': 100,
            'product': 1,
            'success': True
        }
        
        response = self.client.post(
            '/completion',
            data=json.dumps(completion_data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        
        # Check that the promotion was marked as used
        updated_promo = Promotion.query.get(promotion.id)
        self.assertTrue(updated_promo.is_used)
        self.assertEqual(updated_promo.activation_count, 1)
        self.assertEqual(updated_promo.remaining_uses, 0)
        
        # Try to use it again - should return empty response
        response = self.client.post(
            '/request',
            data=json.dumps(request_data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data), {})
    
    def test_free_drinks_plural_multi_use(self):
        """Test that a multi-use free_drinks (plural) is marked as used after all uses"""
        promotion = self.create_promotion('free_drinks', is_single_use=False, drinks_limit=2)
        
        # Send request to vending endpoint
        request_data = {
            'customer': promotion.customer_id,
            'machine': self.machine_serial,
            'product': 1
        }
        
        # First check the request endpoint returns the correct discount
        response = self.client.post(
            '/request',
            data=json.dumps(request_data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data)['discount'], 100)
        
        # Now send a completion request
        completion_data = {
            'customer': promotion.customer_id,
            'machine': self.machine_serial,
            'price': 100,
            'product': 1,
            'success': True
        }
        
        response = self.client.post(
            '/completion',
            data=json.dumps(completion_data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        
        # Check that the promotion was NOT marked as used yet
        updated_promo = Promotion.query.get(promotion.id)
        self.assertFalse(updated_promo.is_used)
        self.assertEqual(updated_promo.activation_count, 1)
        self.assertEqual(updated_promo.remaining_uses, 1)
        
        # Try to use it again - should still work
        response = self.client.post(
            '/request',
            data=json.dumps(request_data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data)['discount'], 100)
        
        # Send another completion request
        response = self.client.post(
            '/completion',
            data=json.dumps(completion_data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        
        # Now the promotion should be marked as used
        updated_promo = Promotion.query.get(promotion.id)
        self.assertTrue(updated_promo.is_used)
        self.assertEqual(updated_promo.activation_count, 2)
        self.assertEqual(updated_promo.remaining_uses, 0)
        
        # Try to use it again - should return empty response
        response = self.client.post(
            '/request',
            data=json.dumps(request_data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data), {})
    
    def test_mark_used_endpoint(self):
        """Test the mark-used endpoint for fixing promotions"""
        # Create a promotion with activation_count > 0 but is_used = False
        promotion = self.create_promotion('free_drink', is_single_use=True)
        promotion.activation_count = 2
        promotion.is_used = False
        db.session.commit()
        
        # Call the mark-used endpoint
        headers = {'Authorization': f'Bearer {self.token}'}
        response = self.client.put(
            f'/api/promotion/{promotion.id}/mark-used',
            headers=headers
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data)['message'], 'Promotion marked as used')
        
        # Check that the promotion was marked as used
        updated_promo = Promotion.query.get(promotion.id)
        self.assertTrue(updated_promo.is_used)
    
    def test_mass_generation_free_drink(self):
        """Test that mass-generated free_drink promotions are handled correctly"""
        # Create a mass generation
        mass_gen = MassGeneration(
            name='Test Mass Gen',
            discount_type='free_drink',
            discount_value=1,
            is_single_use=True,
            user_id=self.user.id
        )
        db.session.add(mass_gen)
        db.session.commit()
        
        # Create a promotion from this mass generation
        customer_id = f"mass_gen_customer_{secrets.token_hex(8)}"
        promotion = Promotion(
            name='Test Mass Gen Promo',
            customer_id=customer_id,
            discount_type='free_drink',
            discount_value=1,
            is_single_use=True,
            is_used=False,
            activation_count=0,
            mass_generation_id=mass_gen.id,
            user_id=self.user.id
        )
        db.session.add(promotion)
        db.session.commit()
        
        # Add machine to promotion
        machine = PromotionMachine(
            promotion_id=promotion.id,
            serialNumber=self.machine_serial,
            humanName=self.machine_name,
            mass_generation_id=mass_gen.id
        )
        db.session.add(machine)
        db.session.commit()
        
        # Send request to vending endpoint
        request_data = {
            'customer': promotion.customer_id,
            'machine': self.machine_serial,
            'product': 1
        }
        
        # Check the request endpoint returns the correct discount
        response = self.client.post(
            '/request',
            data=json.dumps(request_data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data)['discount'], 100)
        
        # Send a completion request
        completion_data = {
            'customer': promotion.customer_id,
            'machine': self.machine_serial,
            'price': 100,
            'product': 1,
            'success': True
        }
        
        response = self.client.post(
            '/completion',
            data=json.dumps(completion_data),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        
        # Check that the promotion was marked as used
        updated_promo = Promotion.query.get(promotion.id)
        self.assertTrue(updated_promo.is_used)
        self.assertEqual(updated_promo.activation_count, 1)

if __name__ == '__main__':
    unittest.main() 