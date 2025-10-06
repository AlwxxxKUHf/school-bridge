import random
import os
import logging
import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from collections import defaultdict
from flask import Flask, request, jsonify, session, render_template_string, redirect
from flask_socketio import SocketIO

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'school-secret-2024')
socketio = SocketIO(app, cors_allowed_origins="*", ping_timeout=60, ping_interval=25)

# Локальная SQLite база для кэширования
CACHE_DB_PATH = '/tmp/school_cache.db'

# Глобальные переменные
active_pi_connections = {}
pending_requests = defaultdict(dict)
activity_bot_running = False

class CacheManager:
    def __init__(self):
        self.init_cache_database()
    
    def init_cache_database(self):
        """Инициализация кэш-базы на Render.com"""
        conn = sqlite3.connect(CACHE_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS teacher_sessions (
                session_id TEXT PRIMARY KEY,
                teacher_id TEXT NOT NULL,
                teacher_name TEXT NOT NULL,
                is_admin BOOLEAN DEFAULT FALSE,
                login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        logging.info("Cache database initialized")
    
    def get_db_connection(self):
        return sqlite3.connect(CACHE_DB_PATH)

class RaspberryPiManager:
    def __init__(self):
        self.connections = {}
        self.last_heartbeat = {}
        self.cache_manager = CacheManager()
    
    def register_connection(self, pi_id, socket_id):
        self.connections[pi_id] = socket_id
        self.last_heartbeat[pi_id] = time.time()
        logging.info(f"Raspberry Pi {pi_id} connected")
    
    def send_command(self, pi_id, command, data, timeout=15):
        if pi_id not in self.connections:
            raise Exception("Raspberry Pi not connected")
        
        request_id = str(uuid.uuid4())
        command_data = {
            'request_id': request_id,
            'command': command,
            'data': data,
            'timestamp': time.time()
        }
        
        pending_requests[pi_id][request_id] = command_data
        socketio.emit('command', command_data, room=self.connections[pi_id])
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            if request_id in pending_requests[pi_id] and 'response' in pending_requests[pi_id][request_id]:
                response = pending_requests[pi_id][request_id]['response']
                del pending_requests[pi_id][request_id]
                return response
            time.sleep(0.1)
        
        if request_id in pending_requests[pi_id]:
            del pending_requests[pi_id][request_id]
        raise Exception("Request timeout")

class ActivityBot:
    def __init__(self, pi_manager):
        self.pi_manager = pi_manager
        self.running = False
        self.thread = None
    
    def start(self):
        """Запуск бота активности"""
        if self.running:
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._bot_loop, daemon=True)
        self.thread.start()
        logging.info("Activity bot started")
    
    def stop(self):
        """Остановка бота активности"""
        self.running = False
        if self.thread:
            self.thread.join()
        logging.info("Activity bot stopped")
    
    def _bot_loop(self):
        """Основной цикл бота активности"""
        actions = [
            self._simulate_health_check,
            self._simulate_group_view,
            self._simulate_teacher_login,
            self._simulate_admin_actions
        ]
        
        while self.running:
            try:
                # Выполняем случайное действие каждые 5-15 минут
                action = random.choice(actions)
                action()
                
                # Случайная пауза между действиями (5-15 минут)
                sleep_time = random.randint(300, 900)
                logging.info(f"Activity bot: next action in {sleep_time//60} minutes")
                
                # Прерываем sleep если нужно остановить бота
                for _ in range(sleep_time):
                    if not self.running:
                        return
                    time.sleep(1)
                    
            except Exception as e:
                logging.error(f"Activity bot error: {e}")
                time.sleep(60)
    
    def _simulate_health_check(self):
        """Имитация проверки здоровья"""
        logging.info("🤖 Activity: Health check")
        # Просто обращаемся к health endpoint
        try:
            with app.test_client() as client:
                client.get('/health')
        except:
            pass
    
    def _simulate_group_view(self):
        """Имитация просмотра групп"""
        logging.info("🤖 Activity: Viewing groups")
        pi_id = 'default_pi'
        if pi_id in self.pi_manager.connections:
            try:
                self.pi_manager.send_command(pi_id, 'get_groups', {})
            except:
                pass
    
    def _simulate_teacher_login(self):
        """Имитация входа преподавателя"""
        logging.info("🤖 Activity: Teacher login simulation")
        teachers = [
            {'teacher_id': 'teacher_001', 'password': '123456'},
            {'teacher_id': 'teacher_002', 'password': '123456'}
        ]
        
        teacher = random.choice(teachers)
        pi_id = 'default_pi'
        if pi_id in self.pi_manager.connections:
            try:
                self.pi_manager.send_command(pi_id, 'login', teacher)
            except:
                pass
    
    def _simulate_admin_actions(self):
        """Имитация действий администратора"""
        logging.info("🤖 Activity: Admin actions simulation")
        pi_id = 'default_pi'
        if pi_id in self.pi_manager.connections:
            # Получаем список преподавателей
            try:
                self.pi_manager.send_command(pi_id, 'get_all_teachers', {})
            except:
                pass

# Инициализация менеджеров
pi_manager = RaspberryPiManager()
activity_bot = ActivityBot(pi_manager)

# WebSocket события
@socketio.on('connect')
def handle_connect():
    logging.info("Client connected")

@socketio.on('raspberry_connect')
def handle_raspberry_connect(data):
    pi_id = data.get('pi_id', 'default_pi')
    pi_manager.register_connection(pi_id, request.sid)
    
    # Запускаем бот активности при первом подключении Raspberry Pi
    global activity_bot_running
    if not activity_bot_running:
        activity_bot.start()
        activity_bot_running = True
    
    # ✅ ИСПРАВЛЕНИЕ: Возвращаем подтверждение подключения
    return {
        'status': 'success', 
        'connected_at': time.time(),
        'raspberry_pi_connected': True,
        'message': f'Raspberry Pi {pi_id} connected successfully'
    }

@socketio.on('raspberry_response')
def handle_raspberry_response(data):
    pi_id = data.get('pi_id', 'default_pi')
    request_id = data.get('request_id')
    response_data = data.get('response', {})
    
    if pi_id in pending_requests and request_id in pending_requests[pi_id]:
        pending_requests[pi_id][request_id]['response'] = response_data

@socketio.on('heartbeat')
def handle_heartbeat(data):
    pi_id = data.get('pi_id', 'default_pi')
    pi_manager.last_heartbeat[pi_id] = time.time()
    return {'status': 'ok'}

# HTTP API endpoints
@app.route('/api/groups')
def get_groups():
    """Просто пересылаем запрос на Raspberry Pi"""
    try:
        result = pi_manager.send_command('default_pi', 'get_groups', {}, timeout=10)
        return jsonify(result)
    except Exception as e:
        return jsonify({
            'status': 'error', 
            'message': f'Raspberry Pi unavailable: {str(e)}',
            'error_code': 1337
        })

@app.route('/api/students/<group_name>')
def get_students(group_name):
    """Просто пересылаем запрос на Raspberry Pi"""
    try:
        result = pi_manager.send_command('default_pi', 'get_students', {'group_name': group_name}, timeout=10)
        return jsonify(result)
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Raspberry Pi unavailable: {str(e)}',
            'error_code': 1337
        })

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    try:
        result = pi_manager.send_command('default_pi', 'login', data, timeout=10)
        
        if result.get('status') == 'success':
            teacher_data = result.get('teacher', {})
            session_id = str(uuid.uuid4())
            
            conn = pi_manager.cache_manager.get_db_connection()
            conn.execute('''
                INSERT OR REPLACE INTO teacher_sessions 
                (session_id, teacher_id, teacher_name, is_admin, last_activity)
                VALUES (?, ?, ?, ?, ?)
            ''', (session_id, teacher_data['id'], teacher_data['name'], teacher_data['role'] == 'admin', datetime.now().isoformat()))
            conn.commit()
            conn.close()
            
            return jsonify({
                'status': 'success',
                'session_id': session_id,
                'teacher': teacher_data
            })
        
        return jsonify(result)
    
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Raspberry Pi unavailable: {str(e)}',
            'error_code': 1337
        })

@app.route('/api/admin/add_group', methods=['POST'])
def add_group():
    data = request.json
    try:
        result = pi_manager.send_command('default_pi', 'add_group', data, timeout=10)
        return jsonify(result)
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Raspberry Pi unavailable: {str(e)}',
            'error_code': 1337
        })

@app.route('/api/admin/add_student', methods=['POST'])
def add_student():
    data = request.json
    try:
        result = pi_manager.send_command('default_pi', 'add_student', data, timeout=10)
        return jsonify(result)
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Raspberry Pi unavailable: {str(e)}',
            'error_code': 1337
        })

@app.route('/api/teacher/topics', methods=['GET', 'POST'])
def teacher_topics():
    try:
        if request.method == 'GET':
            teacher_id = request.args.get('teacher_id')
            subject = request.args.get('subject', 'Русский язык')
            result = pi_manager.send_command('default_pi', 'get_teacher_topics', {
                'teacher_id': teacher_id, 'subject': subject
            }, timeout=10)
        else:
            data = request.json
            result = pi_manager.send_command('default_pi', 'add_teacher_topic', data, timeout=10)
        
        return jsonify(result)
    
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Raspberry Pi unavailable: {str(e)}',
            'error_code': 1337
        })

@app.route('/api/journal/entry', methods=['POST'])
def add_journal_entry():
    data = request.json
    try:
        result = pi_manager.send_command('default_pi', 'add_journal_entry', data, timeout=10)
        return jsonify(result)
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Raspberry Pi unavailable: {str(e)}',
            'error_code': 1337
        })

@app.route('/api/status')
def get_status():
    """Упрощенная проверка статуса - тестовая команда"""
    try:
        # Пробуем отправить тестовую команду
        result = pi_manager.send_command('default_pi', 'get_groups', {}, timeout=5)
        
        return jsonify({
            'status': 'success',
            'raspberry_pi_connected': result.get('status') == 'success',
            'connected_at': pi_manager.last_heartbeat.get('default_pi'),
            'error_code': 0 if result.get('status') == 'success' else 1337
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'raspberry_pi_connected': False,
            'error_code': 1337,
            'message': str(e)
        })

@app.route('/api/test_connection')
def test_connection():
    """Тест подключения к Raspberry Pi"""
    try:
        result = pi_manager.send_command('default_pi', 'get_groups', {}, timeout=10)
        
        if result.get('status') == 'success':
            return jsonify({
                'status': 'success',
                'raspberry_pi_connected': True,
                'message': 'Raspberry Pi responsive',
                'test_data_received': len(result.get('data', [])),
                'error_code': 0
            })
        else:
            return jsonify({
                'status': 'error', 
                'raspberry_pi_connected': False,
                'message': result.get('message', 'No response from Raspberry Pi'),
                'error_code': 1337
            })
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'raspberry_pi_connected': False, 
            'message': f'Connection test failed: {str(e)}',
            'error_code': 1337
        })

@app.route('/health')
def health():
    pi_status = "connected" if pi_manager.connections else "disconnected"
    bot_status = "running" if activity_bot_running else "stopped"
    return jsonify({
        'status': 'healthy',
        'raspberry_pi': pi_status,
        'activity_bot': bot_status,
        'timestamp': datetime.now().isoformat()
    })

# HTML шаблоны
def render_login_page(error=False, message=""):
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Вход в систему</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 400px; margin: 100px auto; padding: 20px; }
            .login-form { border: 1px solid #ddd; padding: 20px; border-radius: 5px; }
            input { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ccc; border-radius: 3px; }
            button { width: 100%; padding: 10px; background: #007cba; color: white; border: none; border-radius: 3px; cursor: pointer; }
            .error { color: red; margin-top: 10px; }
            .status { margin: 10px 0; padding: 10px; border-radius: 3px; }
            .connected { background: #d4edda; color: #155724; }
            .disconnected { background: #f8d7da; color: #721c24; }
        </style>
        <script>
            async function checkRaspberryStatus() {
                try {
                    const response = await fetch('/api/test_connection');
                    const data = await response.json();
                    
                    const statusDiv = document.getElementById('raspberry-status');
                    if (data.raspberry_pi_connected) {
                        statusDiv.innerHTML = '<div class="status connected">✅ База данных доступна</div>';
                    } else {
                        statusDiv.innerHTML = '<div class="status disconnected">❌ Ошибка 1337: База данных недоступна</div>';
                    }
                } catch (error) {
                    document.getElementById('raspberry-status').innerHTML = 
                        '<div class="status disconnected">❌ Ошибка проверки подключения</div>';
                }
            }
            
            // Проверяем статус при загрузке страницы
            document.addEventListener('DOMContentLoaded', function() {
                checkRaspberryStatus();
                setInterval(checkRaspberryStatus, 10000);
            });
        </script>
    </head>
    <body>
        <div class="login-form">
            <h2>Вход в систему</h2>
            <div id="raspberry-status">Проверка подключения к базе данных...</div>
            <form method="POST" action="/login">
                <input name="teacher_id" placeholder="ID преподавателя" required>
                <input name="password" type="password" placeholder="Пароль" required>
                <button type="submit">Войти</button>
            </form>
            ''' + (f'<div class="error">{message}</div>' if error else '') + '''
        </div>
    </body>
    </html>
    '''

def render_dashboard(teacher_name, is_admin, teacher_id):
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Панель преподавателя</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            .header {{ background: #f8f9fa; padding: 20px; border-radius: 5px; margin-bottom: 20px; }}
            .groups-section {{ border: 1px solid #ddd; padding: 20px; border-radius: 5px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Панель преподавателя</h1>
            <p>Добро пожаловать, {teacher_name}!</p>
            <p>ID: {teacher_id}</p>
            <p>Роль: {"Администратор" if is_admin else "Преподаватель"}</p>
        </div>
        
        <div class="groups-section">
            <h3>Группы студентов</h3>
            <div id="groups-list">Загрузка групп...</div>
            <button onclick="loadGroups()">Обновить группы</button>
        </div>

        <script>
            function loadGroups() {{
                fetch('/api/groups')
                    .then(r => r.json())
                    .then(data => {{
                        if(data.status === 'success') {{
                            const groupsHtml = data.data.map(g => 
                                `<div style="padding: 10px; margin: 5px; background: #f0f0f0; border-radius: 3px;">
                                    {g.name}
                                </div>`
                            ).join('');
                            document.getElementById('groups-list').innerHTML = groupsHtml;
                        }} else {{
                            document.getElementById('groups-list').innerHTML = 'Ошибка загрузки групп: ' + data.message;
                        }}
                    }});
            }}
            loadGroups();
        </script>
    </body>
    </html>
    '''

@app.route('/')
def index():
    # Простая проверка сессии
    if 'teacher_id' in session:
        return render_dashboard(
            session.get('teacher_name', 'Учитель'),
            session.get('is_admin', False),
            session.get('teacher_id')
        )
    return render_login_page()

@app.route('/login', methods=['POST'])
def login_http():
    # Упрощенная HTTP авторизация
    teacher_id = request.form.get('teacher_id')
    password = request.form.get('password')
    
    try:
        result = pi_manager.send_command('default_pi', 'login', {
            'teacher_id': teacher_id, 'password': password
        }, timeout=10)
        
        if result.get('status') == 'success':
            teacher_data = result.get('teacher', {})
            session['teacher_id'] = teacher_data['id']
            session['teacher_name'] = teacher_data['name']
            session['is_admin'] = teacher_data['role'] == 'admin'
            return redirect('/')
        else:
            return render_login_page(error=True, message=result.get('message', 'Ошибка входа'))
    
    except Exception as e:
        return render_login_page(error=True, message=f'Ошибка подключения к базе данных: {str(e)}')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    logging.info(f"Starting server on port {port}")
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)