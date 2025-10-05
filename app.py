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
            CREATE TABLE IF NOT EXISTS cached_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cached_students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                group_name TEXT NOT NULL,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
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
    
    def cache_groups(self, groups):
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM cached_groups')
        for group in groups:
            cursor.execute('INSERT INTO cached_groups (name) VALUES (?)', (group['name'],))
        
        conn.commit()
        conn.close()
        logging.info(f"Cached {len(groups)} groups")
    
    def get_cached_groups(self):
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        groups = cursor.execute('SELECT * FROM cached_groups ORDER BY name').fetchall()
        conn.close()
        return [{'id': g[0], 'name': g[1]} for g in groups]
    
    def cache_students(self, group_name, students):
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM cached_students WHERE group_name = ?', (group_name,))
        for student in students:
            cursor.execute('INSERT INTO cached_students (name, group_name) VALUES (?, ?)', (student['name'], group_name))
        
        conn.commit()
        conn.close()
        logging.info(f"Cached {len(students)} students for group {group_name}")
    
    def get_cached_students(self, group_name):
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        students = cursor.execute(
            'SELECT * FROM cached_students WHERE group_name = ? ORDER BY name', (group_name,)
        ).fetchall()
        conn.close()
        return [{'id': s[0], 'name': s[1], 'group_name': s[2]} for s in students]

class RaspberryPiManager:
    def __init__(self):
        self.connections = {}
        self.last_heartbeat = {}
        self.cache_manager = CacheManager()
    
    def register_connection(self, pi_id, socket_id):
        self.connections[pi_id] = socket_id
        self.last_heartbeat[pi_id] = time.time()
        logging.info(f"Raspberry Pi {pi_id} connected")
        self.sync_groups_cache(pi_id)
    
    def sync_groups_cache(self, pi_id):
        try:
            groups_response = self.send_command(pi_id, 'get_groups', {})
            if groups_response.get('status') == 'success':
                self.cache_manager.cache_groups(groups_response.get('data', []))
        except Exception as e:
            logging.error(f"Failed to sync groups cache: {e}")
    
    def send_command(self, pi_id, command, data, timeout=15):
        if pi_id not in self.connections:
            return {'status': 'error', 'message': 'Raspberry Pi not connected'}
        
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
        return {'status': 'error', 'message': 'Request timeout'}

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
            self.pi_manager.send_command(pi_id, 'get_groups', {})
    
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
            self.pi_manager.send_command(pi_id, 'login', teacher)
    
    def _simulate_admin_actions(self):
        """Имитация действий администратора"""
        logging.info("🤖 Activity: Admin actions simulation")
        pi_id = 'default_pi'
        if pi_id in self.pi_manager.connections:
            # Получаем список преподавателей
            self.pi_manager.send_command(pi_id, 'get_all_teachers', {})

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
    
    return {'status': 'connected'}

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
    cached_groups = pi_manager.cache_manager.get_cached_groups()
    if cached_groups:
        return jsonify({'status': 'success', 'data': cached_groups, 'source': 'cache'})
    
    pi_id = 'default_pi'
    if pi_id in pi_manager.connections:
        result = pi_manager.send_command(pi_id, 'get_groups', {})
        if result.get('status') == 'success':
            pi_manager.cache_manager.cache_groups(result.get('data', []))
            result['source'] = 'raspberry_pi'
        return jsonify(result)
    
    return jsonify({'status': 'error', 'message': 'No data available'})

@app.route('/api/students/<group_name>')
def get_students(group_name):
    cached_students = pi_manager.cache_manager.get_cached_students(group_name)
    if cached_students:
        return jsonify({'status': 'success', 'data': cached_students, 'source': 'cache'})
    
    pi_id = 'default_pi'
    if pi_id in pi_manager.connections:
        result = pi_manager.send_command(pi_id, 'get_students', {'group_name': group_name})
        if result.get('status') == 'success':
            pi_manager.cache_manager.cache_students(group_name, result.get('data', []))
            result['source'] = 'raspberry_pi'
        return jsonify(result)
    
    return jsonify({'status': 'error', 'message': 'No data available'})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    pi_id = 'default_pi'
    
    if pi_id not in pi_manager.connections:
        return jsonify({'status': 'error', 'message': 'Raspberry Pi not connected'})
    
    result = pi_manager.send_command(pi_id, 'login', data)
    
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

@app.route('/api/admin/add_group', methods=['POST'])
def add_group():
    data = request.json
    pi_id = 'default_pi'
    
    if pi_id not in pi_manager.connections:
        return jsonify({'status': 'error', 'message': 'Raspberry Pi not connected'})
    
    result = pi_manager.send_command(pi_id, 'add_group', data)
    if result.get('status') == 'success':
        pi_manager.sync_groups_cache(pi_id)
    
    return jsonify(result)

@app.route('/api/admin/add_student', methods=['POST'])
def add_student():
    data = request.json
    pi_id = 'default_pi'
    
    if pi_id not in pi_manager.connections:
        return jsonify({'status': 'error', 'message': 'Raspberry Pi not connected'})
    
    result = pi_manager.send_command(pi_id, 'add_student', data)
    if result.get('status') == 'success':
        group_name = data.get('group_name')
        pi_manager.send_command(pi_id, 'get_students', {'group_name': group_name})
    
    return jsonify(result)

@app.route('/api/teacher/topics', methods=['GET', 'POST'])
def teacher_topics():
    pi_id = 'default_pi'
    if pi_id not in pi_manager.connections:
        return jsonify({'status': 'error', 'message': 'Raspberry Pi not connected'})
    
    if request.method == 'GET':
        teacher_id = request.args.get('teacher_id')
        subject = request.args.get('subject', 'Русский язык')
        result = pi_manager.send_command(pi_id, 'get_teacher_topics', {
            'teacher_id': teacher_id, 'subject': subject
        })
    else:
        data = request.json
        result = pi_manager.send_command(pi_id, 'add_teacher_topic', data)
    
    return jsonify(result)

@app.route('/api/journal/entry', methods=['POST'])
def add_journal_entry():
    data = request.json
    pi_id = 'default_pi'
    
    if pi_id not in pi_manager.connections:
        return jsonify({'status': 'error', 'message': 'Raspberry Pi not connected'})
    
    result = pi_manager.send_command(pi_id, 'add_journal_entry', data)
    return jsonify(result)

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

# HTML шаблоны (упрощенные для примера)
def render_login_page(error=False, message=""):
    return f'''
    <!DOCTYPE html>
    <html>
    <head><title>Login</title></head>
    <body>
        <h2>Вход в систему</h2>
        <form method="POST" action="/login">
            <input name="teacher_id" placeholder="ID" value="teacher_001" required>
            <input name="password" type="password" placeholder="Пароль" value="123456" required>
            <button type="submit">Войти</button>
        </form>
        {f'<p style="color:red">{message}</p>' if error else ''}
    </body>
    </html>
    '''

def render_dashboard(teacher_name, is_admin, teacher_id):
    return f'''
    <!DOCTYPE html>
    <html>
    <head><title>Dashboard</title></head>
    <body>
        <h1>Панель преподавателя</h1>
        <p>Добро пожаловать, {teacher_name}!</p>
        <div id="groups-list">Загрузка групп...</div>
        <button onclick="loadGroups()">Обновить группы</button>
        <script>
            function loadGroups() {{
                fetch('/api/groups')
                    .then(r => r.json())
                    .then(data => {{
                        if(data.status === 'success') {{
                            document.getElementById('groups-list').innerHTML = 
                                'Группы: ' + data.data.map(g => g.name).join(', ');
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
    
    result = pi_manager.send_command('default_pi', 'login', {
        'teacher_id': teacher_id, 'password': password
    })
    
    if result.get('status') == 'success':
        teacher_data = result.get('teacher', {})
        session['teacher_id'] = teacher_data['id']
        session['teacher_name'] = teacher_data['name']
        session['is_admin'] = teacher_data['role'] == 'admin'
        return redirect('/')
    else:
        return render_login_page(error=True, message=result.get('message', 'Ошибка'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    logging.info(f"Starting server on port {port}")
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)