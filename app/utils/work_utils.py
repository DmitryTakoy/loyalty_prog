# utils.py
import os
import string
import random
import qrcode
from PIL import Image
from datetime import datetime
from app.models.promotion import Promotion
ARCHIVE_FOLDER = 'archives'
MAX_ARCHIVES = 5

def save_archive(zip_buffer, mass_generation_id):
    archives = os.listdir(ARCHIVE_FOLDER)
    archives.sort(key=lambda x: os.path.getmtime(os.path.join(ARCHIVE_FOLDER, x)), reverse=True)
    
    if len(archives) >= MAX_ARCHIVES:
        os.remove(os.path.join(ARCHIVE_FOLDER, archives[-1]))
    
    filename = f"archive_{mass_generation_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    filepath = os.path.join(ARCHIVE_FOLDER, filename)
    
    with open(filepath, 'wb') as f:
        f.write(zip_buffer.getvalue())
    
    return filename

# Массовая генерация qr и кодов
# Генерация уникального кода

def generate_unique_code(length=28):
    chars = string.ascii_letters + string.digits
    while True:
        code = ''.join(random.choice(chars) for _ in range(length))
        # Проверяем в модели Promotion
        if not Promotion.query.filter_by(customer_id=code).first():
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
