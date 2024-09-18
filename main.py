
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

import os
import io
import base64
import string
import random
import zipfile
import qrcode
from flask import Flask, request, jsonify, render_template, send_file, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from PIL import Image
from datetime import datetime, timedelta

app = Flask(__name__)

# Настройки для подключения к базе данных SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///employees.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
CORS(app)

# Модель для таблицы сотрудников
class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.String(32), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    discount_type = db.Column(db.String(20), nullable=False)  # 'percentage' or 'free_drinks'
    discount_value = db.Column(db.Integer, nullable=False)
    drinks_limit = db.Column(db.Integer, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    expiration_date = db.Column(db.DateTime, nullable=True)
    is_renewable = db.Column(db.Boolean, default=False)
    is_single_use = db.Column(db.Boolean, default=False)
    is_used = db.Column(db.Boolean, default=False)
    creation_date = db.Column(db.DateTime, default=datetime.utcnow)
    activation_count = db.Column(db.Integer, default=0)
    remaining_uses = db.Column(db.Integer)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.discount_type == 'free_drinks':
            self.remaining_uses = self.drinks_limit
        elif self.is_single_use:
            self.remaining_uses = 1
        else:
            self.remaining_uses = None

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.String(32), db.ForeignKey('employee.employee_id'), nullable=False)
    machine_id = db.Column(db.String(8), nullable=False)
    product_id = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Integer, nullable=False)
    discounted_price = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# Рендеринг главной страницы
@app.route('/')
def index():
    return render_template('main.html')

# Добавление нового сотрудника в БД (для тестов)
@app.route('/add_employee', methods=['POST'])
def add_employee():
    data = request.json
    expiration_date = None
    if data.get('expiration_date'):
        expiration_date = datetime.fromisoformat(data['expiration_date'])
    
    creation_date = datetime.utcnow()
    if data.get('creation_date'):
        creation_date = datetime.fromisoformat(data['creation_date'])
    
    activation_count = data.get('activation_count', 0)
    
    new_employee = Employee(
        employee_id=data['employee_id'],
        name=data['name'],
        discount_type=data['discount_type'],
        discount_value=data['discount_value'],
        drinks_limit=data.get('drinks_limit'),
        is_renewable=data.get('is_renewable', False),
        expiration_date=expiration_date,
        is_single_use=data.get('is_single_use', False),
        creation_date=creation_date,
        activation_count=activation_count,
    )
    db.session.add(new_employee)
    db.session.commit()
    return jsonify({'message': 'Employee added successfully'}), 201

@app.route('/update_employee/<string:employee_id>', methods=['PUT'])
def update_employee(employee_id):
    employee = Employee.query.filter_by(employee_id=employee_id).first()
    if not employee:
        return jsonify({'error': 'Employee not found'}), 404
    
    data = request.json
    for key, value in data.items():
        setattr(employee, key, value)
    
    db.session.commit()
    return jsonify({'message': 'Employee updated successfully'}), 200

@app.route('/deactivate_employee/<string:employee_id>', methods=['PUT'])
def deactivate_employee(employee_id):
    employee = Employee.query.filter_by(employee_id=employee_id).first()
    if not employee:
        return jsonify({'error': 'Employee not found'}), 404
    
    employee.is_active = False
    db.session.commit()
    return jsonify({'message': 'Employee deactivated successfully'}), 200

@app.route('/renew_employee/<string:employee_id>', methods=['PUT'])
def renew_employee(employee_id):
    employee = Employee.query.filter_by(employee_id=employee_id).first()
    if not employee:
        return jsonify({'error': 'Employee not found'}), 404
    
    if not employee.is_renewable:
        return jsonify({'error': 'This employee\'s discount is not renewable'}), 400
    
    # Reset the employee's benefits
    employee.expiration_date = datetime.utcnow() + timedelta(days=30)  # Renew for 30 days
    if employee.discount_type == 'free_drinks':
        employee.remaining_uses = employee.drinks_limit  # Reset the number of free drinks
    
    db.session.commit()
    return jsonify({'message': 'Employee discount renewed successfully'}), 200

@app.route('/get_stats/<string:employee_id>', methods=['GET'])
def get_stats(employee_id):
    employee = Employee.query.filter_by(employee_id=employee_id).first()
    if not employee:
        return jsonify({'error': 'Employee not found'}), 404
    
    transactions = Transaction.query.filter_by(employee_id=employee_id).all()
    total_drinks = len(transactions)
    total_spent = sum(t.discounted_price for t in transactions)
    
    return jsonify({
        'employee_id': employee_id,
        'name': employee.name,
        'total_drinks': total_drinks,
        'total_spent': total_spent
    }), 200

#  the get_employees route to handle filters
@app.route('/employees', methods=['GET'])
def get_employees():
    query = Employee.query

    # Get filter parameters from query string
    created_after = request.args.get('created_after')
    created_before = request.args.get('created_before')
    is_used = request.args.get('is_used')
    is_reusable = request.args.get('is_reusable')
    is_expired = request.args.get('is_expired')
    is_indefinite = request.args.get('is_indefinite')

    # Apply filters only if they are provided
    if created_after:
        query = query.filter(Employee.creation_date >= datetime.fromisoformat(created_after))
    if created_before:
        query = query.filter(Employee.creation_date <= datetime.fromisoformat(created_before))
    if is_used:
        query = query.filter(Employee.activation_count > 0 if is_used == 'true' else Employee.activation_count == 0)
    if is_reusable:
        query = query.filter(Employee.is_single_use == (is_reusable != 'true'))
    if is_expired:
        current_time = datetime.utcnow()
        if is_expired == 'true':
            query = query.filter(Employee.expiration_date < current_time)
        else:
            query = query.filter((Employee.expiration_date >= current_time) | (Employee.expiration_date == None))
    if is_indefinite:
        query = query.filter(Employee.expiration_date == None if is_indefinite == 'true' else Employee.expiration_date != None)

    employees = query.all()
    print("All employees in database:", Employee.query.all())
    print("Filtered employees:", employees)
    print("SQL Query:", query)

    return jsonify([
        {
            'id': e.id,
            'employee_id': e.employee_id,
            'name': e.name,
            'discount_type': e.discount_type,
            'discount_value': e.discount_value,
            'drinks_limit': e.drinks_limit,
            'is_active': e.is_active,
            'is_renewable': e.is_renewable,
            'expiration_date': e.expiration_date.isoformat() if e.expiration_date else None,
            'is_single_use': e.is_single_use,
            'is_used': e.activation_count > 0,
            'creation_date': e.creation_date.isoformat(),
            'activation_count': e.activation_count,
            'remaining_uses': e.remaining_uses
        } for e in employees
    ]), 200

# Обработка запроса на предоставление скидки
@app.route('/request', methods=['POST'])
def handle_request():
    data = request.json
    employee = Employee.query.filter_by(employee_id=data['customer']).first()
    
    if not employee or not employee.is_active:
        return '', 404  # Return empty response for unauthorized access
    
    if employee.expiration_date and employee.expiration_date < datetime.utcnow():
        return '', 404  # Treat expired discount as unauthorized
    
    if employee.is_single_use and employee.is_used:
        return '', 404  # Treat used single-use discount as unauthorized
    
    discount = 0
    
    if employee.discount_type == 'percentage':
        discount = employee.discount_value
    elif employee.discount_type == 'free_drinks':
        if employee.drinks_limit > 0:
            discount = 100  # 100% discount for free drinks
            employee.drinks_limit -= 1
        else:
            return '', 404  # Treat exceeded drink limit as unauthorized
    
    transaction = Transaction(
        employee_id=data['customer'],
        machine_id=data['machine'],
        product_id=data['product'],
        price=0,  # We don't have the price information
        discounted_price=0,  # We don't have the price information
        timestamp=datetime.fromtimestamp(int(data['unixtime'], 16))
    )
    db.session.add(transaction)
    
    if employee.is_single_use:
        employee.is_used = True
    
    db.session.commit()
    
    return jsonify({'discount': discount}), 200

# Массовая генерация qr и кодов
# Генерация уникального кода
def generate_unique_code(length=28):
    chars = string.ascii_letters + string.digits
    while True:
        code = ''.join(random.choice(chars) for _ in range(length))
        if not Employee.query.filter_by(employee_id=code).first():
            return code
# Генерация QR кода      
def create_qr_code_image(code):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(code)
    qr.make(fit=True)
    img_qr = qr.make_image(fill_color="black", back_color="white").convert('RGBA')

    # Make white background transparent
    datas = img_qr.getdata()
    newData = []
    for item in datas:
        if item[:3] == (255, 255, 255):
            newData.append((255, 255, 255, 0))  # Set transparency
        else:
            newData.append(item)
    img_qr.putdata(newData)
    return img_qr

@app.route('/generate_codes', methods=['POST'])
def generate_codes():
    data = request.json
    num_codes = data.get('num_codes', 1)
    discount_type = data.get('discount_type', 'discount')  # 'discount' or 'free_drink'
    discount_value = data.get('discount_value', 0)  # Discount amount or number of drinks
    is_single_use = data.get('is_single_use', True)
    is_renewable = data.get('is_renewable', False)
    expiration_date_str = data.get('expiration_date', None)  # None for indefinite
    name = data.get('name', 'MassGeneratedCode')  # Default name if not provided

    # Parse expiration date
    if expiration_date_str:
        expiration_date = datetime.fromisoformat(expiration_date_str)
    else:
        expiration_date = None  # Indefinite

    codes = []

    # Create an in-memory zip file
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'a', zipfile.ZIP_DEFLATED) as zip_file:
        # StringIO for codes.txt
        codes_txt = io.StringIO()

        for _ in range(num_codes):
            # Generate unique code
            code = generate_unique_code()

            # Create QR code image with transparent background
            img = create_qr_code_image(code)

            # Save image to in-memory bytes buffer
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='PNG')
            img_bytes.seek(0)

            # Add image to zip archive
            img_filename = f"{code}.png"
            zip_file.writestr(img_filename, img_bytes.getvalue())

            # Append code to codes list
            codes.append(code)

            # Save the code to the database
            employee = Employee(
                employee_id=code,
                name=name,
                discount_type=discount_type,
                discount_value=discount_value,
                is_single_use=is_single_use,
                is_renewable=is_renewable,
                expiration_date=expiration_date,
                creation_date=datetime.utcnow(),
                is_active=True,
                activation_count=0,
                remaining_uses=None,  # Adjust as needed
                # Add other necessary fields
            )
            db.session.add(employee)

        # Write all codes to codes.txt and add to zip
        codes_txt.write('\n'.join(codes))
        zip_file.writestr('codes.txt', codes_txt.getvalue())

    # Commit the new codes to the database
    db.session.commit()

    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name='codes_and_images.zip'
    )


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000, host='0.0.0.0')


## добавить генерацию счетов + куар массовая только латиница и цифры 
## добавить валиадцию на проценты не более 100 
## добавить отображение существующих клиентов таблицей DONE
## добавить логику каждый 5-10 напиток в подарок, соответственно 3й вид купонов
## добавить массовое удаление куаров 
## добавить фильтры на отражения купонов созданные по дате, DONE
# однораз/многораз, истекшие/актуальные, воспользованные или не пользованные DONE
## хранить последние удаленные 15 дней, сделать список действующих и удаленных 
## вкладки акции, чтобы массово 