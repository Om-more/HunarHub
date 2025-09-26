import google.generativeai as genai
import json
import pandas as pd
from dotenv import load_dotenv
import os
from flask import Flask, render_template, request, redirect, url_for, jsonify
import re
from PIL import Image
import base64
import io
from datetime import datetime
import csv

load_dotenv()

app = Flask(__name__)
UPLOAD_FOLDER = "static/uploads"
DATA_FILE = "data.csv"  # CSV file in root directory
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
API_KEY = os.getenv("API_KEY")
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

def initialize_csv():
    """Initialize CSV file if it doesn't exist"""
    if not os.path.exists(DATA_FILE):
        headers = ['Image', 'Name', 'Category', 'Location', 'Description', 'Price', 'Date_Added']
        with open(DATA_FILE, 'w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(headers)
        print(f"Created new CSV file: {DATA_FILE}")

def save_product_to_csv(product_data):
    """Save product data to CSV file"""
    try:
        # Check if CSV exists, create if not
        initialize_csv()
        
        # Prepare data row
        row = [
            product_data.get('image', ''),
            product_data.get('name', ''),
            product_data.get('category', ''),
            product_data.get('location', ''),
            product_data.get('description', ''),
            product_data.get('price', ''),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ]
        
        # Append to CSV
        with open(DATA_FILE, 'a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(row)
        
        return True, "Product saved successfully"
    except Exception as e:
        return False, f"Error saving product: {str(e)}"

def read_csv_data():
    """Read all data from CSV file"""
    try:
        if not os.path.exists(DATA_FILE):
            return []
        
        df = pd.read_csv(DATA_FILE)
        return df.to_dict('records')
    except Exception as e:
        print(f"Error reading CSV: {str(e)}")
        return []

@app.route('/')
def dashboard():
    return render_template('chat.html')

@app.route('/events.html')
def event():
    return render_template('Events.html')

@app.route('/Addprod.html')
def addprod():
    return render_template('addprod.html')

@app.route('/chat.html')
def chat():
    return render_template('chat.html')

@app.route('/aboutapp.html')
def aboutapp():
    return render_template('aboutapp.html')

@app.route('/AI.html')
def ai_interface():
    return render_template('AI.html')

@app.route('/history.html')
def history():
    return render_template('history.html')

# New API endpoint to save product data
@app.route('/api/save-product', methods=['POST'])
def save_product():
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['name', 'description', 'price']
        for field in required_fields:
            if not data.get(field):
                return jsonify({
                    'success': False, 
                    'error': f'{field.title()} is required'
                }), 400
        
        # Prepare product data
        product_data = {
            'image': data.get('image', 'No image'),
            'name': data.get('name'),
            'category': data.get('category', 'Uncategorized'),
            'location': data.get('location', 'Not specified'),
            'description': data.get('description'),
            'price': data.get('price')
        }
        
        # Save to CSV
        success, message = save_product_to_csv(product_data)
        
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message}), 500
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# New API endpoint to get all products
@app.route('/api/get-products', methods=['GET'])
def get_products():
    try:
        products = read_csv_data()
        return jsonify({'success': True, 'products': products})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/chat', methods=['POST'])
def api_chat():
    try:
        data = request.get_json()
        message = data.get('message', '')
        image_data = data.get('image', None)
        
        if image_data:
            image_bytes = base64.b64decode(image_data.split(',')[1])
            answer = query_with_image(message, image_bytes=image_bytes)
        else:
            answer = query_with_image(message)
        
        return jsonify({
            'success': True,
            'response': answer
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/ai-chat', methods=['GET', 'POST'])
def ai_chat():
    answer = None
    img_url = None
    user_message = None
    if request.method == 'POST':
        user_message = request.form.get('question', '')
        image = request.files.get('image')
        if image and image.filename != '':
            img_path = os.path.join(app.config['UPLOAD_FOLDER'], image.filename)
            image.save(img_path)
            img_url = url_for('static', filename=f'uploads/{image.filename}')
            answer = query_with_image(user_message, img_path)
        else:
            answer = query_with_image(user_message)
    return render_template('AI.html', answer=answer, img_url=img_url, user_message=user_message)

def query_with_image(user_question, image_path=None, image_bytes=None):
    prompt = f"""
    User will enter an image and will ask question related to that art image he/she shared,
    Suggest him according to the question asked related to image which is related more to
    naming it, describing it, market it, price of it according to the trend, platform guidance for Meesho, Amazon Karigar, Etsy (here suggest him some youtube tutorial for creation of seller account in those platforms & guide accordingly).
    If no image is been shared then answer the questions in general which is related to Art, handicrafts, handmade products, handlooms, pottery, etc.
    Also generate a downloadable .txt link for any response if user asks you to do so.
    And don't answer anything else from that.
    Question: {user_question}
    Answer in a friendly and natural way:
    """

    try:
        if image_path is not None:
            with open(image_path, 'rb') as f:
                image_bytes = f.read()
            print(f"Analyzing image: {image_path}")
            response = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": image_bytes}])
            return response.text
        elif image_bytes is not None:
            print("Analyzing uploaded image...")
            response = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": image_bytes}])
            return response.text
        else:
            print("Processing text-only query...")
            response = model.generate_content(prompt)
            return response.text

    except Exception as e:
        return f"Error processing request: {str(e)}"

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    initialize_csv()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)