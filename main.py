
### todo. check if cust-r didnt scan one qr twice   
    # # Открываем новую транзакцию
    # transactions[f"{customer}_{machine}"] = {
    #     'start_time': time.time(),
    #     'status': 'open'
    # }
    
    # return jsonify({'discount': discount}), 200

# @app.route('/completion', methods=['POST'])
# def handle_completion():
#     data = request.json
#     customer = data['customer']
#     machine = data['machine']
#     price = data['price']
#     product = data['product']
#     success = data['success']
#     unixtime = data['unixtime']
    
#     transaction_key = f"{customer}_{machine}"
    
#     if transaction_key in transactions:
#         if success:
#             transactions[transaction_key]['status'] = 'completed'
#         else:
#             transactions[transaction_key]['status'] = 'failed'
    
#     return '', 200

# if __name__ == '__main__':
#     app.run(debug=True, port=5000, host='0.0.0.0')

from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS

app = Flask(__name__)

# Настройки для подключения к базе данных SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///employees.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
CORS(app)

# Модель для таблицы сотрудников
class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer = db.Column(db.String(32), nullable=False, unique=True)
    discount = db.Column(db.Float, nullable=False)

# Создаем таблицы в базе данных
with app.app_context():
    db.create_all()

# Рендеринг главной страницы
@app.route('/')
def index():
    return render_template('main.html')

# Добавление нового сотрудника в БД (для тестов)
@app.route('/add_employee', methods=['POST'])
def add_employee():
    data = request.json
    customer = data['employeeId']
    discount = data['discount']

    # Добавляем нового сотрудника в базу данных
    new_employee = Employee(customer=customer, discount=discount)
    db.session.add(new_employee)
    db.session.commit()

    return jsonify({'message': 'Employee added successfully!'}), 200

# Обработка запроса на предоставление скидки
@app.route('/request', methods=['POST'])
def handle_request():
    data = request.json
    customer = data['customer']

    # Поиск клиента в базе данных по customer (id клиента)
    employee = Employee.query.filter_by(customer=customer).first()

    if employee:
        # Если клиент найден, возвращаем его скидку
        return jsonify({'discount': employee.discount}), 200
    else:
        # Если клиент не найден, возвращаем ошибку 404
        return jsonify({'error': 'Customer not found'}), 404

if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')
