# import unittest
# from main import app, db, Employee

# class MainAppTestCase(unittest.TestCase):
#     def setUp(self):
#         """Настройка тестового клиента и базы данных перед каждым тестом"""
#         self.app = app.test_client()
#         self.app_context = app.app_context()
#         self.app_context.push()
#         db.create_all()  # Создаем тестовую базу данных

#     def tearDown(self):
#         """Очищаем базу данных после каждого теста"""
#         db.session.remove()
#         db.drop_all()
#         self.app_context.pop()

#     def test_index(self):
#         """Тестируем, что главная страница загружается корректно"""
#         response = self.app.get('/')
#         self.assertEqual(response.status_code, 200)
#         self.assertIn(b'Add Employee', response.data)
    
#     def test_add_employee(self):
#         """Тестируем добавление нового сотрудника"""
#         new_employee = {
#             "employee_id": "test123",
#             "name": "Test Employee",
#             "discount_type": "percentage",
#             "discount_value": 10
#         }
#         response = self.app.post('/add_employee', json=new_employee)
#         self.assertEqual(response.status_code, 201)
#         self.assertIn(b'Employee added successfully', response.data)
    
#     def test_add_employee_persisted(self):
#         """Тестируем, что сотрудник действительно сохраняется в базе данных"""
#         new_employee = {
#             "employee_id": "test123",
#             "name": "Test Employee",
#             "discount_type": "percentage",
#             "discount_value": 10
#         }
#         self.app.post('/add_employee', json=new_employee)
        
#         # Ищем добавленного сотрудника в базе данных
#         employee = Employee.query.filter_by(employee_id="test123").first()
#         self.assertIsNotNone(employee)
#         self.assertEqual(employee.name, "Test Employee")

#     def test_update_employee(self):
#         """Тестируем обновление сотрудника"""
#         new_employee = {
#             "employee_id": "test124",
#             "name": "Old Name",
#             "discount_type": "percentage",
#             "discount_value": 5
#         }
#         self.app.post('/add_employee', json=new_employee)
        
#         # Обновляем данные сотрудника
#         updated_data = {
#             "name": "New Name",
#             "discount_value": 15
#         }
#         response = self.app.put('/update_employee/test124', json=updated_data)
#         self.assertEqual(response.status_code, 200)
        
#         # Проверяем, что данные обновлены в базе данных
#         employee = Employee.query.filter_by(employee_id="test124").first()
#         self.assertEqual(employee.name, "New Name")
#         self.assertEqual(employee.discount_value, 15)
        
#     def test_deactivate_employee(self):
#         """Тестируем деактивацию сотрудника"""
#         new_employee = {
#             "employee_id": "test125",
#             "name": "Deactivating Employee",
#             "discount_type": "percentage",
#             "discount_value": 10
#         }
#         self.app.post('/add_employee', json=new_employee)
        
#         # Деактивируем сотрудника
#         response = self.app.put('/deactivate_employee/test125')
#         self.assertEqual(response.status_code, 200)
        
#         # Проверяем, что сотрудник деактивирован в базе данных
#         employee = Employee.query.filter_by(employee_id="test125").first()
#         self.assertFalse(employee.is_active)

import unittest
from datetime import datetime, timedelta
from main import app, db, Employee, Transaction
from flask import json

class MainAppTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app_context = app.app_context()
        self.app_context.push()
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_index(self):
        response = self.app.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Add Employee', response.data)

    def test_add_employee(self):
        new_employee = {
            "employee_id": "test123",
            "name": "Test Employee",
            "discount_type": "percentage",
            "discount_value": 10
        }
        response = self.app.post('/add_employee', json=new_employee)
        self.assertEqual(response.status_code, 201)
        self.assertIn(b'Employee added successfully', response.data)

    def test_add_employee_persisted(self):
        new_employee = {
            "employee_id": "test123",
            "name": "Test Employee",
            "discount_type": "percentage",
            "discount_value": 10
        }
        self.app.post('/add_employee', json=new_employee)
        employee = Employee.query.filter_by(employee_id="test123").first()
        self.assertIsNotNone(employee)
        self.assertEqual(employee.name, "Test Employee")

    def test_update_employee(self):
        new_employee = {
            "employee_id": "test124",
            "name": "Old Name",
            "discount_type": "percentage",
            "discount_value": 5
        }
        self.app.post('/add_employee', json=new_employee)
        updated_data = {
            "name": "New Name",
            "discount_value": 15
        }
        response = self.app.put('/update_employee/test124', json=updated_data)
        self.assertEqual(response.status_code, 200)
        employee = Employee.query.filter_by(employee_id="test124").first()
        self.assertEqual(employee.name, "New Name")
        self.assertEqual(employee.discount_value, 15)

    def test_deactivate_employee(self):
        new_employee = {
            "employee_id": "test125",
            "name": "Deactivating Employee",
            "discount_type": "percentage",
            "discount_value": 10
        }
        self.app.post('/add_employee', json=new_employee)
        response = self.app.put('/deactivate_employee/test125')
        self.assertEqual(response.status_code, 200)
        employee = Employee.query.filter_by(employee_id="test125").first()
        self.assertFalse(employee.is_active)

    def test_renew_employee(self):
        new_employee = {
            "employee_id": "test126",
            "name": "Renewable Employee",
            "discount_type": "free_drinks",
            "discount_value": 5,
            "drinks_limit": 3,
            "is_renewable": True,
            "expiration_date": (datetime.utcnow() - timedelta(days=1)).isoformat()
        }
        self.app.post('/add_employee', json=new_employee)
        response = self.app.put('/renew_employee/test126')
        self.assertEqual(response.status_code, 200)
        employee = Employee.query.filter_by(employee_id="test126").first()
        self.assertGreater(employee.expiration_date, datetime.utcnow())
        self.assertEqual(employee.remaining_uses, 3)

    def test_renew_non_renewable_employee(self):
        new_employee = {
            "employee_id": "test127",
            "name": "Non-Renewable Employee",
            "discount_type": "percentage",
            "discount_value": 10,
            "is_renewable": False
        }
        self.app.post('/add_employee', json=new_employee)
        response = self.app.put('/renew_employee/test127')
        self.assertEqual(response.status_code, 400)

    def test_get_stats(self):
        new_employee = {
            "employee_id": "test128",
            "name": "Stats Employee",
            "discount_type": "percentage",
            "discount_value": 10
        }
        self.app.post('/add_employee', json=new_employee)
        
        # Add some transactions
        transaction1 = Transaction(employee_id="test128", machine_id="machine1", product_id=1, price=100, discounted_price=90)
        transaction2 = Transaction(employee_id="test128", machine_id="machine2", product_id=2, price=200, discounted_price=180)
        db.session.add(transaction1)
        db.session.add(transaction2)
        db.session.commit()

        response = self.app.get('/get_stats/test128')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['total_drinks'], 2)
        self.assertEqual(data['total_spent'], 270)

    def test_get_employees(self):
        # Add some employees with different attributes
        employees = [
            {
                "employee_id": "test129",
                "name": "Recent Employee",
                "discount_type": "percentage",
                "discount_value": 10,
                "creation_date": datetime.utcnow().isoformat()
            },
            {
                "employee_id": "test130",
                "name": "Old Employee",
                "discount_type": "free_drinks",
                "discount_value": 5,
                "drinks_limit": 3,
                "creation_date": (datetime.utcnow() - timedelta(days=60)).isoformat(),
                "is_single_use": True,
                "activation_count": 1
            },
            {
                "employee_id": "test131",
                "name": "Expired Employee",
                "discount_type": "percentage",
                "discount_value": 15,
                # Set creation_date to 60 days ago
                "creation_date": (datetime.utcnow() - timedelta(days=60)).isoformat(),
                "expiration_date": (datetime.utcnow() - timedelta(days=1)).isoformat()
            }
        ]
        for employee in employees:
            self.app.post('/add_employee', json=employee)

        # Test without filters
        response = self.app.get('/employees')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(len(data), 3)

        # Test with filters
        response = self.app.get('/employees?created_after=' + (datetime.utcnow() - timedelta(days=30)).isoformat())
        data = json.loads(response.data)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['name'], "Recent Employee")

        response = self.app.get('/employees?is_used=true')
        data = json.loads(response.data)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['name'], "Old Employee")

        response = self.app.get('/employees?is_expired=true')
        data = json.loads(response.data)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['name'], "Expired Employee")

    def test_handle_request(self):
        # Add an employee with a percentage discount
        new_employee = {
            "employee_id": "test132",
            "name": "Discount Employee",
            "discount_type": "percentage",
            "discount_value": 10
        }
        self.app.post('/add_employee', json=new_employee)

        # Test valid request
        request_data = {
            "customer": "test132",
            "machine": "machine1",
            "product": 1,
            "unixtime": hex(int(datetime.utcnow().timestamp()))[2:]
        }
        response = self.app.post('/request', json=request_data)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['discount'], 10)

        # Test inactive employee
        self.app.put('/deactivate_employee/test132')
        response = self.app.post('/request', json=request_data)
        self.assertEqual(response.status_code, 404)

        # Test expired discount
        new_employee = {
            "employee_id": "test133",
            "name": "Expired Discount Employee",
            "discount_type": "percentage",
            "discount_value": 15,
            "expiration_date": (datetime.utcnow() - timedelta(days=1)).isoformat()
        }
        self.app.post('/add_employee', json=new_employee)
        request_data["customer"] = "test133"
        response = self.app.post('/request', json=request_data)
        self.assertEqual(response.status_code, 404)

        # Test single-use discount
        new_employee = {
            "employee_id": "test134",
            "name": "Single Use Employee",
            "discount_type": "percentage",
            "discount_value": 20,
            "is_single_use": True
        }
        self.app.post('/add_employee', json=new_employee)
        request_data["customer"] = "test134"
        response = self.app.post('/request', json=request_data)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['discount'], 20)

        # Try to use the single-use discount again
        response = self.app.post('/request', json=request_data)
        self.assertEqual(response.status_code, 404)

        # Test free drink discount
        new_employee = {
            "employee_id": "test135",
            "name": "Free Drink Employee",
            "discount_type": "free_drinks",
            "discount_value": 100,
            "drinks_limit": 2
        }
        self.app.post('/add_employee', json=new_employee)
        request_data["customer"] = "test135"
        response = self.app.post('/request', json=request_data)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['discount'], 100)

        # Use up the free drinks
        self.app.post('/request', json=request_data)
        response = self.app.post('/request', json=request_data)
        self.assertEqual(response.status_code, 404)

if __name__ == '__main__':
    unittest.main()