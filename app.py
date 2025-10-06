import os
import logging
import sqlite3
import uuid
import json
import time
from datetime import datetime
from flask import Flask, request, jsonify, session, redirect, render_template_string
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'school-secret-2024')
socketio = SocketIO(app, cors_allowed_origins="*")

# Хранилища
connections = {}
pending_requests = {}
backup_mode = False

# Инициализация резервной БД
def init_backup_db():
    conn = sqlite3.connect('/tmp/backup.db')
    cursor = conn.cursor()
    
    # Таблица преподавателей (должна быть всегда актуальной)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS backup_teachers (
            teacher_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'teacher',
            subject TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица групп
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS backup_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            course TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица студентов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS backup_students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            group_name TEXT NOT NULL,
            student_id TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица журнала
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS backup_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            student_name TEXT NOT NULL,
            group_name TEXT NOT NULL,
            subject TEXT NOT NULL,
            topic TEXT NOT NULL,
            grade INTEGER,
            attendance BOOLEAN DEFAULT TRUE,
            comments TEXT,
            teacher_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица домашних заданий
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS backup_homework (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name TEXT NOT NULL,
            subject TEXT NOT NULL,
            homework_text TEXT NOT NULL,
            date_assigned TEXT NOT NULL,
            date_due TEXT,
            teacher_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Очередь синхронизации
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Добавляем стандартных преподавателей если их нет
    default_teachers = [
        ('admin', 'Администратор Системы', 'admin123', 'admin', 'Администрирование'),
        ('teacher_001', 'Иванова Мария Сергеевна', '123456', 'teacher', 'Математика'),
        ('teacher_002', 'Петров Алексей Владимирович', '123456', 'teacher', 'Русский язык'),
        ('teacher_003', 'Сидорова Елена Ивановна', '123456', 'teacher', 'История')
    ]
    
    for teacher in default_teachers:
        cursor.execute('''
            INSERT OR IGNORE INTO backup_teachers (teacher_id, name, password, role, subject)
            VALUES (?, ?, ?, ?, ?)
        ''', teacher)
    
    conn.commit()
    conn.close()

init_backup_db()

def get_backup_db():
    return sqlite3.connect('/tmp/backup.db')

# WebSocket события
@socketio.on('connect')
def handle_connect():
    logging.info(f"Client connected: {request.sid}")

@socketio.on('raspberry_connect')
def handle_raspberry_connect(data):
    pi_id = data.get('pi_id', 'default_pi')
    connections[pi_id] = request.sid
    
    global backup_mode
    if backup_mode:
        logging.info(f"✅ Raspberry Pi восстановил соединение! Начинаем синхронизацию...")
        backup_mode = False
        sync_and_cleanup(pi_id)
    
    logging.info(f"Raspberry Pi {pi_id} connected")
    return {'status': 'success', 'connected': True}

@socketio.on('raspberry_response')
def handle_raspberry_response(data):
    request_id = data.get('request_id')
    if request_id in pending_requests:
        pending_requests[request_id] = data.get('response')

# Функция проверки прав доступа
def check_permission(teacher_id, required_role=None, required_subject=None):
    """Проверяет права доступа преподавателя"""
    conn = get_backup_db()
    cursor = conn.cursor()
    
    teacher = cursor.execute(
        'SELECT * FROM backup_teachers WHERE teacher_id = ?', (teacher_id,)
    ).fetchone()
    
    conn.close()
    
    if not teacher:
        return False
    
    teacher_data = {
        'id': teacher[0],
        'name': teacher[1],
        'role': teacher[3],
        'subject': teacher[4]
    }
    
    # Админ имеет все права
    if teacher_data['role'] == 'admin':
        return teacher_data
    
    # Проверка роли
    if required_role and teacher_data['role'] != required_role:
        return False
    
    # Проверка предмета
    if required_subject and teacher_data['subject'] != required_subject:
        return False
    
    return teacher_data

# Основная функция отправки команд
def send_command(pi_id, command, data, timeout=10):
    global backup_mode
    
    if pi_id in connections and not backup_mode:
        try:
            result = send_command_direct(pi_id, command, data, timeout)
            # Дублируем важные данные в резерв
            if result.get('status') == 'success' and command in ['add_group', 'add_student', 'add_homework', 'add_teacher']:
                save_to_backup(command, data)
            return result
        except Exception as e:
            logging.warning(f"⚠️ Ошибка связи: {e}")
            backup_mode = True
    
    return process_in_backup_mode(command, data)

# Обработка в режиме резерва
def process_in_backup_mode(command, data):
    conn = get_backup_db()
    cursor = conn.cursor()
    
    try:
        if command == 'get_groups':
            groups = cursor.execute('SELECT * FROM backup_groups ORDER BY course, name').fetchall()
            return {
                'status': 'success', 
                'data': [{'id': g[0], 'name': g[1], 'course': g[2]} for g in groups],
                'backup_mode': True
            }
            
        elif command == 'get_students':
            group_name = data.get('group_name')
            students = cursor.execute(
                'SELECT * FROM backup_students WHERE group_name = ? ORDER BY name', (group_name,)
            ).fetchall()
            return {
                'status': 'success',
                'data': [{'id': s[0], 'name': s[1], 'group_name': s[2], 'student_id': s[3]} for s in students],
                'backup_mode': True
            }
            
        elif command == 'get_all_students':
            students = cursor.execute('SELECT * FROM backup_students ORDER BY group_name, name').fetchall()
            return {
                'status': 'success',
                'data': [{'id': s[0], 'name': s[1], 'group_name': s[2], 'student_id': s[3]} for s in students],
                'backup_mode': True
            }
            
        elif command == 'get_teachers':
            teachers = cursor.execute('SELECT * FROM backup_teachers ORDER BY name').fetchall()
            return {
                'status': 'success',
                'data': [{'id': t[0], 'name': t[1], 'role': t[3], 'subject': t[4]} for t in teachers],
                'backup_mode': True
            }
            
        elif command == 'login':
            teacher_id = data.get('teacher_id')
            password = data.get('password')
            
            teacher = cursor.execute(
                'SELECT * FROM backup_teachers WHERE teacher_id = ? AND password = ?', 
                (teacher_id, password)
            ).fetchone()
            
            if teacher:
                return {
                    'status': 'success',
                    'teacher': {
                        'id': teacher[0],
                        'name': teacher[1],
                        'role': teacher[3],
                        'subject': teacher[4]
                    },
                    'backup_mode': True
                }
            else:
                return {'status': 'error', 'message': 'Неверный ID или пароль'}
            
        elif command == 'add_journal_entry':
            # Проверяем права доступа
            teacher_info = check_permission(data.get('teacher_id'))
            if not teacher_info:
                return {'status': 'error', 'message': 'Доступ запрещен'}
            
            # Преподаватель может ставить оценки только по своему предмету
            if teacher_info['role'] == 'teacher' and teacher_info['subject'] != data.get('subject'):
                return {'status': 'error', 'message': f'Вы можете ставить оценки только по предмету: {teacher_info["subject"]}'}
            
            cursor.execute('''
                INSERT INTO backup_journal 
                (date, student_name, group_name, subject, topic, grade, attendance, comments, teacher_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                data.get('date', datetime.now().strftime('%Y-%m-%d')),
                data.get('student_name'),
                data.get('group_name'),
                data.get('subject'),
                data.get('topic'),
                data.get('grade'),
                data.get('attendance', True),
                data.get('comments', ''),
                data.get('teacher_id')
            ))
            
            cursor.execute('INSERT INTO sync_queue (action_type, data_json) VALUES (?, ?)', 
                          ('add_journal_entry', json.dumps(data)))
            
            conn.commit()
            return {'status': 'success', 'message': '✅ Оценка сохранена', 'backup_mode': True}
            
        elif command == 'add_group':
            # Только админ может добавлять группы
            if not check_permission(data.get('teacher_id'), 'admin'):
                return {'status': 'error', 'message': 'Только администратор может добавлять группы'}
            
            cursor.execute('INSERT OR IGNORE INTO backup_groups (name, course) VALUES (?, ?)', 
                          (data.get('group_name'), 'Новый курс'))
            cursor.execute('INSERT INTO sync_queue (action_type, data_json) VALUES (?, ?)', 
                          ('add_group', json.dumps(data)))
            conn.commit()
            return {'status': 'success', 'message': '✅ Группа добавлена', 'backup_mode': True}
            
        elif command == 'add_student':
            # Только админ может добавлять студентов
            if not check_permission(data.get('teacher_id'), 'admin'):
                return {'status': 'error', 'message': 'Только администратор может добавлять студентов'}
            
            cursor.execute('INSERT OR IGNORE INTO backup_students (name, group_name, student_id) VALUES (?, ?, ?)',
                          (data.get('student_name'), data.get('group_name'), data.get('student_id')))
            cursor.execute('INSERT INTO sync_queue (action_type, data_json) VALUES (?, ?)',
                          ('add_student', json.dumps(data)))
            conn.commit()
            return {'status': 'success', 'message': '✅ Студент добавлен', 'backup_mode': True}
            
        elif command == 'add_teacher':
            # Только админ может добавлять преподавателей
            if not check_permission(data.get('teacher_id'), 'admin'):
                return {'status': 'error', 'message': 'Только администратор может добавлять преподавателей'}
            
            cursor.execute('INSERT OR IGNORE INTO backup_teachers (teacher_id, name, password, role, subject) VALUES (?, ?, ?, ?, ?)',
                          (data.get('new_teacher_id'), data.get('new_teacher_name'), data.get('new_teacher_password'), 
                           data.get('new_teacher_role', 'teacher'), data.get('new_teacher_subject')))
            conn.commit()
            return {'status': 'success', 'message': '✅ Преподаватель добавлен', 'backup_mode': True}
            
        elif command == 'add_homework':
            # Проверяем права доступа
            teacher_info = check_permission(data.get('teacher_id'))
            if not teacher_info:
                return {'status': 'error', 'message': 'Доступ запрещен'}
            
            # Преподаватель может добавлять ДЗ только по своему предмету
            if teacher_info['role'] == 'teacher' and teacher_info['subject'] != data.get('subject'):
                return {'status': 'error', 'message': f'Вы можете добавлять ДЗ только по предмету: {teacher_info["subject"]}'}
            
            cursor.execute('''
                INSERT INTO backup_homework 
                (group_name, subject, homework_text, date_assigned, date_due, teacher_id)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                data.get('group_name'),
                data.get('subject'),
                data.get('homework_text'),
                data.get('date_assigned', datetime.now().strftime('%Y-%m-%d')),
                data.get('date_due'),
                data.get('teacher_id')
            ))
            cursor.execute('INSERT INTO sync_queue (action_type, data_json) VALUES (?, ?)',
                          ('add_homework', json.dumps(data)))
            conn.commit()
            return {'status': 'success', 'message': '✅ ДЗ добавлено', 'backup_mode': True}
            
        elif command == 'get_homework':
            group_name = data.get('group_name')
            homeworks = cursor.execute(
                'SELECT * FROM backup_homework WHERE group_name = ? ORDER BY date_assigned DESC', (group_name,)
            ).fetchall()
            return {
                'status': 'success',
                'data': [{
                    'id': h[0], 'group_name': h[1], 'subject': h[2], 
                    'homework_text': h[3], 'date_assigned': h[4], 'date_due': h[5], 'teacher_id': h[6]
                } for h in homeworks],
                'backup_mode': True
            }
            
        else:
            return {'status': 'error', 'message': '❌ Команда недоступна', 'backup_mode': True}
        
    except Exception as e:
        conn.rollback()
        return {'status': 'error', 'message': f'Ошибка: {str(e)}'}
    finally:
        conn.close()

def save_to_backup(command, data):
    """Дублирование данных при штатной работе"""
    conn = get_backup_db()
    cursor = conn.cursor()
    
    try:
        if command == 'add_group':
            cursor.execute('INSERT OR IGNORE INTO backup_groups (name, course) VALUES (?, ?)', 
                          (data.get('group_name'), 'Новый курс'))
        elif command == 'add_student':
            cursor.execute('INSERT OR IGNORE INTO backup_students (name, group_name, student_id) VALUES (?, ?, ?)',
                          (data.get('student_name'), data.get('group_name'), data.get('student_id')))
        elif command == 'add_teacher':
            cursor.execute('INSERT OR IGNORE INTO backup_teachers (teacher_id, name, password, role, subject) VALUES (?, ?, ?, ?, ?)',
                          (data.get('new_teacher_id'), data.get('new_teacher_name'), data.get('new_teacher_password'), 
                           data.get('new_teacher_role', 'teacher'), data.get('new_teacher_subject')))
        elif command == 'add_homework':
            cursor.execute('''
                INSERT OR IGNORE INTO backup_homework 
                (group_name, subject, homework_text, date_assigned, date_due, teacher_id)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                data.get('group_name'), data.get('subject'), data.get('homework_text'),
                data.get('date_assigned'), data.get('date_due'), data.get('teacher_id')
            ))
        
        conn.commit()
    except Exception as e:
        logging.error(f"Ошибка дублирования: {e}")
    finally:
        conn.close()

def sync_and_cleanup(pi_id):
    """Синхронизация новых данных"""
    conn = get_backup_db()
    cursor = conn.cursor()
    
    try:
        synced_count = 0
        queue_items = cursor.execute('SELECT * FROM sync_queue ORDER BY created_at').fetchall()
        
        for item in queue_items:
            data = json.loads(item[2])
            result = send_command_direct(pi_id, item[1], data, timeout=5)
            
            if result.get('status') == 'success':
                cursor.execute('DELETE FROM sync_queue WHERE id = ?', (item[0],))
                synced_count += 1
        
        conn.commit()
        logging.info(f"✅ Синхронизировано записей: {synced_count}")
        
    except Exception as e:
        logging.error(f"❌ Ошибка синхронизации: {e}")
    finally:
        conn.close()

def send_command_direct(pi_id, command, data, timeout=10):
    """Прямая отправка команды на Raspberry Pi"""
    if pi_id not in connections:
        raise Exception("Raspberry Pi not connected")
    
    request_id = str(uuid.uuid4())
    command_data = {
        'request_id': request_id,
        'command': command,
        'data': data
    }
    
    pending_requests[request_id] = None
    emit('command', command_data, room=connections[pi_id])
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        if pending_requests.get(request_id) is not None:
            response = pending_requests[request_id]
            del pending_requests[request_id]
            return response
        time.sleep(0.1)
    
    if request_id in pending_requests:
        del pending_requests[request_id]
    raise Exception("Timeout")

# API endpoints
@app.route('/api/groups')
def get_groups():
    result = send_command('default_pi', 'get_groups', {})
    return jsonify(result)

@app.route('/api/students/<group_name>')
def get_students(group_name):
    result = send_command('default_pi', 'get_students', {'group_name': group_name})
    return jsonify(result)

@app.route('/api/all_students')
def get_all_students():
    result = send_command('default_pi', 'get_all_students', {})
    return jsonify(result)

@app.route('/api/teachers')
def get_teachers():
    result = send_command('default_pi', 'get_teachers', {})
    return jsonify(result)

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    result = send_command('default_pi', 'login', data)
    return jsonify(result)

@app.route('/api/admin/add_group', methods=['POST'])
def add_group():
    data = request.json
    result = send_command('default_pi', 'add_group', data)
    return jsonify(result)

@app.route('/api/admin/add_student', methods=['POST'])
def add_student():
    data = request.json
    result = send_command('default_pi', 'add_student', data)
    return jsonify(result)

@app.route('/api/admin/add_teacher', methods=['POST'])
def add_teacher():
    data = request.json
    result = send_command('default_pi', 'add_teacher', data)
    return jsonify(result)

@app.route('/api/journal/entry', methods=['POST'])
def add_journal_entry():
    data = request.json
    result = send_command('default_pi', 'add_journal_entry', data)
    return jsonify(result)

@app.route('/api/homework', methods=['GET', 'POST'])
def homework():
    if request.method == 'GET':
        group_name = request.args.get('group_name')
        result = send_command('default_pi', 'get_homework', {'group_name': group_name})
    else:
        data = request.json
        result = send_command('default_pi', 'add_homework', data)
    return jsonify(result)

@app.route('/api/status')
def get_status():
    global backup_mode
    return jsonify({
        'status': 'success',
        'raspberry_pi_connected': 'default_pi' in connections and not backup_mode,
        'backup_mode': backup_mode
    })

# HTML ШАБЛОНЫ (основной шаблон остается прежним)
def get_base_template():
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Школьная система</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: Arial, sans-serif; background: #f5f5f5; }
            .header { background: #2c3e50; color: white; padding: 1rem; display: flex; justify-content: space-between; align-items: center; }
            .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
            .nav { background: white; padding: 1rem; margin-bottom: 20px; border-radius: 5px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
            .nav a { margin-right: 20px; text-decoration: none; color: #2c3e50; font-weight: bold; padding: 5px 10px; border-radius: 3px; }
            .nav a:hover { background: #ecf0f1; }
            .card { background: white; padding: 20px; margin-bottom: 20px; border-radius: 5px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
            .btn { background: #3498db; color: white; border: none; padding: 10px 20px; border-radius: 3px; cursor: pointer; margin: 5px; }
            .btn:hover { background: #2980b9; }
            .btn-danger { background: #e74c3c; }
            .btn-success { background: #27ae60; }
            .form-group { margin-bottom: 15px; }
            .form-group label { display: block; margin-bottom: 5px; font-weight: bold; }
            .form-group input, .form-group select, .form-group textarea { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 3px; }
            .status-indicator { padding: 10px; border-radius: 5px; margin-bottom: 20px; text-align: center; font-weight: bold; }
            .status-online { background: #d4edda; color: #155724; }
            .status-offline { background: #f8d7da; color: #721c24; }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; }
            table, th, td { border: 1px solid #ddd; }
            th, td { padding: 12px; text-align: left; }
            th { background: #f8f9fa; }
            .teacher-subject { background: #e8f5e8; padding: 5px 10px; border-radius: 3px; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🎓 Школьная система</h1>
            <div>
                {% if session.teacher_name %}
                    <span>{{ session.teacher_name }}</span>
                    {% if session.teacher_subject and session.role != 'admin' %}
                        <span class="teacher-subject" style="margin-left: 10px;">{{ session.teacher_subject }}</span>
                    {% endif %}
                    <a href="/logout" style="color: white; margin-left: 20px;">Выйти</a>
                {% endif %}
            </div>
        </div>
        <div class="container">
            {% if session.teacher_name %}
            <div class="nav">
                <a href="/dashboard">📊 Дашборд</a>
                {% if session.role != 'admin' %}
                <a href="/journal">📝 Журнал</a>
                <a href="/homework">📚 Домашние задания</a>
                {% endif %}
                {% if session.role == 'admin' %}
                <a href="/admin">👑 Админ-панель</a>
                {% endif %}
            </div>
            {% endif %}
            
            <div id="status-indicator" class="status-indicator">
                <!-- Статус подключения -->
            </div>
            
            {% block content %}{% endblock %}
        </div>

        <script>
            async function checkStatus() {
                try {
                    const response = await fetch('/api/status');
                    const data = await response.json();
                    const indicator = document.getElementById('status-indicator');
                    
                    if (data.raspberry_pi_connected) {
                        indicator.innerHTML = '<div class="status-online">✅ База данных подключена</div>';
                    } else {
                        indicator.innerHTML = '<div class="status-offline">⚠️ Режим резервного копирования</div>';
                    }
                } catch (error) {
                    document.getElementById('status-indicator').innerHTML = 
                        '<div class="status-offline">❌ Ошибка подключения</div>';
                }
            }
            
            setInterval(checkStatus, 10000);
            checkStatus();
        </script>
    </body>
    </html>
    '''

@app.route('/')
def index():
    if 'teacher_id' in session:
        return redirect('/dashboard')
    
    return render_template_string(get_base_template() + '''
        <div class="card" style="max-width: 400px; margin: 50px auto;">
            <h2 style="text-align: center; margin-bottom: 20px;">Вход в систему</h2>
            <form method="POST" action="/login">
                <div class="form-group">
                    <label>ID преподавателя:</label>
                    <input type="text" name="teacher_id" required value="admin">
                </div>
                <div class="form-group">
                    <label>Пароль:</label>
                    <input type="password" name="password" required value="admin123">
                </div>
                <button type="submit" class="btn" style="width: 100%;">Войти</button>
            </form>
            <div style="margin-top: 20px; padding: 15px; background: #f8f9fa; border-radius: 5px;">
                <h4>Тестовые аккаунты:</h4>
                <p><strong>Админ:</strong> admin / admin123</p>
                <p><strong>Математика:</strong> teacher_001 / 123456</p>
                <p><strong>Русский язык:</strong> teacher_002 / 123456</p>
                <p><strong>История:</strong> teacher_003 / 123456</p>
            </div>
        </div>
    ''')

@app.route('/login', methods=['POST'])
def login_http():
    teacher_id = request.form.get('teacher_id')
    password = request.form.get('password')
    
    result = send_command('default_pi', 'login', {
        'teacher_id': teacher_id, 'password': password
    })
    
    if result.get('status') == 'success':
        teacher_data = result.get('teacher', {})
        session['teacher_id'] = teacher_data['id']
        session['teacher_name'] = teacher_data['name']
        session['role'] = teacher_data['role']
        session['teacher_subject'] = teacher_data.get('subject', '')
        return redirect('/dashboard')
    else:
        return render_template_string(get_base_template() + '''
            <div class="card" style="max-width: 400px; margin: 50px auto;">
                <h2 style="text-align: center; color: #e74c3c;">Ошибка входа</h2>
                <p style="text-align: center;">Неверный ID или пароль</p>
                <a href="/" class="btn" style="display: block; text-align: center;">Вернуться к входу</a>
            </div>
        ''')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

@app.route('/dashboard')
def dashboard():
    if 'teacher_id' not in session:
        return redirect('/')
    
    role_display = "Администратор" if session['role'] == 'admin' else f"Преподаватель ({session['teacher_subject']})"
    
    return render_template_string(get_base_template() + '''
        <div class="card">
            <h2>📊 Дашборд</h2>
            <p>Добро пожаловать, <strong>{{ session.teacher_name }}</strong>! ({{ role_display }})</p>
            
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-top: 20px;">
                <div class="card">
                    <h3>👥 Группы</h3>
                    <button class="btn" onclick="loadGroups()">Просмотреть группы</button>
                    <div id="groups-list"></div>
                </div>
                
                <div class="card">
                    <h3>📝 Быстрые действия</h3>
                    {% if session.role != 'admin' %}
                    <button class="btn" onclick="location.href='/journal'">📝 Выставить оценку</button>
                    <button class="btn" onclick="location.href='/homework'">📚 Добавить ДЗ</button>
                    {% else %}
                    <button class="btn" onclick="location.href='/admin'">👑 Управление системой</button>
                    {% endif %}
                </div>
            </div>
        </div>

        <script>
            async function loadGroups() {
                const response = await fetch('/api/groups');
                const data = await response.json();
                
                if (data.status === 'success') {
                    const groupsHtml = data.data.map(group => 
                        `<div style="padding: 10px; margin: 5px; background: #f8f9fa; border-radius: 3px;">
                            ${group.name} (${group.course})
                        </div>`
                    ).join('');
                    document.getElementById('groups-list').innerHTML = groupsHtml;
                } else {
                    document.getElementById('groups-list').innerHTML = 'Ошибка загрузки групп';
                }
            }
        </script>
    ''', role_display=role_display)

# Остальные маршруты (/journal, /homework, /admin) остаются аналогичными, 
# но с проверкой прав доступа в каждом шаблоне

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)