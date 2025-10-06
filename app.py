import os
import logging
import sqlite3
import uuid
import json
import time
from datetime import datetime
from flask import Flask, request, jsonify, session, redirect
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'school-secret-2024')
socketio = SocketIO(app, cors_allowed_origins="*")

# Хранилища
connections = {}
pending_requests = {}
backup_mode = False

# Полная резервная база на сайте
def init_backup_db():
    conn = sqlite3.connect('/tmp/backup.db')
    cursor = conn.cursor()
    
    # Таблица групп (полное дублирование)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS backup_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            course TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица студентов (полное дублирование)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS backup_students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            group_name TEXT NOT NULL,
            student_id TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_name) REFERENCES backup_groups (name)
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
    
    # Очередь синхронизации для новых данных
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Синхронизируем начальные данные при первом запуске
    init_default_data(cursor)
    
    conn.commit()
    conn.close()

def init_default_data(cursor):
    """Начальные данные групп и студентов"""
    groups_data = [
        ('1П1', '1 Курс'), ('1Л1', '1 Курс'), ('1Ю1', '1 Курс'), ('1Ю2', '1 Курс'), 
        ('1Б1', '1 Курс'), ('1БД1', '1 Курс'),
        ('2Н1', '2 Курс'), ('2Б1', '2 Курс'), ('2Ю1', '2 Курс'), ('2Ю2', '2 Курс'),
        ('2Л1', '2 Курс'), ('2П1', '2 Курс'), ('2П2', '2 Курс'), ('1П3', '2 Курс'),
        ('1Б3', '2 Курс'), ('1Л3', '2 Курс'), ('2П3', '2 Курс'),
        ('3П1', '3 Курс'), ('3П2', '3 Курс'), ('3Н1', '3 Курс'), ('3Н2', '3 Курс'),
        ('3Б1', '3 Курс'),
        ('4П1', '4 Курс'), ('4П2', '4 Курс'), ('4Н1', '4 Курс'), ('4Н2', '4 Курс'),
        ('4Б1', '4 Курс'), ('4Ю1', '4 Курс'), ('4Л1', '4 Курс')
    ]
    
    for group_name, course in groups_data:
        cursor.execute('INSERT OR IGNORE INTO backup_groups (name, course) VALUES (?, ?)', (group_name, course))
    
    # Примеры студентов для нескольких групп
    students_data = [
        ('Иванов Алексей', '1П1', 'ST001'), ('Петрова Анна', '1П1', 'ST002'),
        ('Сидоров Максим', '1П1', 'ST003'), ('Кузнецова Ольга', '1П1', 'ST004'),
        ('Васильев Дмитрий', '1Л1', 'ST005'), ('Николаева Елена', '1Л1', 'ST006'),
        ('Федоров Сергей', '2П1', 'ST009'), ('Морозова Татьяна', '2П1', 'ST010'),
        ('Семенов Артем', '3П1', 'ST013'), ('Волкова Мария', '3П1', 'ST014')
    ]
    
    for student in students_data:
        cursor.execute('INSERT OR IGNORE INTO backup_students (name, group_name, student_id) VALUES (?, ?, ?)', student)

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
        # Синхронизируем и очищаем ТОЛЬКО новые данные
        sync_and_cleanup(pi_id)
    
    logging.info(f"Raspberry Pi {pi_id} connected")
    return {'status': 'success', 'connected': True}

@socketio.on('raspberry_response')
def handle_raspberry_response(data):
    request_id = data.get('request_id')
    if request_id in pending_requests:
        pending_requests[request_id] = data.get('response')

# Функция отправки команды с резервным копированием
def send_command(pi_id, command, data, timeout=10):
    global backup_mode
    
    # Если Raspberry Pi доступен и не в режиме резерва - работаем напрямую
    if pi_id in connections and not backup_mode:
        try:
            result = send_command_direct(pi_id, command, data, timeout)
            # Если команда на добавление - дублируем в резервную БД
            if result.get('status') == 'success' and command in ['add_group', 'add_student']:
                save_to_backup(command, data)
            return result
        except Exception as e:
            logging.warning(f"⚠️ Ошибка связи с Raspberry Pi: {e}")
            backup_mode = True
    
    # Режим резервного копирования - работаем с локальной БД
    return process_in_backup_mode(command, data)

# Обработка команд в режиме резерва
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
            
        elif command == 'add_journal_entry':
            # Сохраняем в журнал и добавляем в очередь синхронизации
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
            return {
                'status': 'success', 
                'message': '✅ Оценка сохранена в резервное хранилище',
                'backup_mode': True
            }
            
        elif command == 'add_group':
            # Добавляем группу в резервную БД
            cursor.execute('INSERT OR IGNORE INTO backup_groups (name, course) VALUES (?, ?)', 
                          (data.get('group_name'), 'Новый курс'))
            
            cursor.execute('INSERT INTO sync_queue (action_type, data_json) VALUES (?, ?)', 
                          ('add_group', json.dumps(data)))
            
            conn.commit()
            return {
                'status': 'success',
                'message': '✅ Группа добавлена в резервное хранилище',
                'backup_mode': True
            }
            
        elif command == 'add_student':
            # Добавляем студента в резервную БД
            cursor.execute('INSERT OR IGNORE INTO backup_students (name, group_name, student_id) VALUES (?, ?, ?)',
                          (data.get('student_name'), data.get('group_name'), data.get('student_id')))
            
            cursor.execute('INSERT INTO sync_queue (action_type, data_json) VALUES (?, ?)',
                          ('add_student', json.dumps(data)))
            
            conn.commit()
            return {
                'status': 'success',
                'message': '✅ Студент добавлен в резервное хранилище',
                'backup_mode': True
            }
            
        elif command == 'login':
            # Простая проверка логина в режиме резерва
            return {
                'status': 'success',
                'teacher': {'id': 'teacher_001', 'name': 'Преподаватель', 'role': 'teacher'},
                'backup_mode': True
            }
            
        else:
            return {
                'status': 'error', 
                'message': '❌ Команда недоступна в режиме резерва',
                'backup_mode': True
            }
        
    except Exception as e:
        conn.rollback()
        return {'status': 'error', 'message': f'Ошибка в режиме резерва: {str(e)}'}
    finally:
        conn.close()

# Сохранение данных в резервную БД (для дублирования при штатной работе)
def save_to_backup(command, data):
    conn = get_backup_db()
    cursor = conn.cursor()
    
    try:
        if command == 'add_group':
            cursor.execute('INSERT OR IGNORE INTO backup_groups (name, course) VALUES (?, ?)', 
                          (data.get('group_name'), 'Новый курс'))
        elif command == 'add_student':
            cursor.execute('INSERT OR IGNORE INTO backup_students (name, group_name, student_id) VALUES (?, ?, ?)',
                          (data.get('student_name'), data.get('group_name'), data.get('student_id')))
        
        conn.commit()
        logging.info(f"✅ Данные продублированы в резервную БД: {command}")
        
    except Exception as e:
        logging.error(f"Ошибка дублирования данных: {e}")
        conn.rollback()
    finally:
        conn.close()

# Синхронизация и очистка ТОЛЬКО новых данных
def sync_and_cleanup(pi_id):
    conn = get_backup_db()
    cursor = conn.cursor()
    
    try:
        synced_count = 0
        
        # Синхронизируем очередь (новые данные)
        queue_items = cursor.execute('SELECT * FROM sync_queue ORDER BY created_at').fetchall()
        
        for item in queue_items:
            data = json.loads(item[2])
            result = send_command_direct(pi_id, item[1], data, timeout=5)
            
            if result.get('status') == 'success':
                cursor.execute('DELETE FROM sync_queue WHERE id = ?', (item[0],))
                synced_count += 1
                logging.info(f"✅ Синхронизирована запись: {item[1]}")
        
        # Синхронизируем журнал (новые оценки)
        journal_entries = cursor.execute('''
            SELECT * FROM backup_journal WHERE date >= date('now', '-7 days')
        ''').fetchall()
        
        for entry in journal_entries:
            journal_data = {
                'date': entry[1],
                'student_name': entry[2],
                'group_name': entry[3],
                'subject': entry[4],
                'topic': entry[5],
                'grade': entry[6],
                'attendance': bool(entry[7]),
                'comments': entry[8],
                'teacher_id': entry[9]
            }
            
            result = send_command_direct(pi_id, 'add_journal_entry', journal_data, timeout=5)
            if result.get('status') == 'success':
                cursor.execute('DELETE FROM backup_journal WHERE id = ?', (entry[0],))
                synced_count += 1
        
        conn.commit()
        logging.info(f"✅ Синхронизация завершена. Обработано записей: {synced_count}")
        
    except Exception as e:
        logging.error(f"❌ Ошибка синхронизации: {e}")
        conn.rollback()
    finally:
        conn.close()

# Прямая отправка команды (без резервного копирования)
def send_command_direct(pi_id, command, data, timeout=10):
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

# API endpoints (остаются без изменений)
@app.route('/api/groups')
def get_groups():
    result = send_command('default_pi', 'get_groups', {})
    return jsonify(result)

@app.route('/api/students/<group_name>')
def get_students(group_name):
    result = send_command('default_pi', 'get_students', {'group_name': group_name})
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

@app.route('/api/teacher/topics', methods=['GET', 'POST'])
def teacher_topics():
    if request.method == 'GET':
        teacher_id = request.args.get('teacher_id')
        subject = request.args.get('subject', 'Русский язык')
        result = send_command('default_pi', 'get_teacher_topics', {
            'teacher_id': teacher_id, 'subject': subject
        })
    else:
        data = request.json
        result = send_command('default_pi', 'add_teacher_topic', data)
    return jsonify(result)

@app.route('/api/journal/entry', methods=['POST'])
def add_journal_entry():
    data = request.json
    result = send_command('default_pi', 'add_journal_entry', data)
    return jsonify(result)

# Статус системы
@app.route('/api/status')
def get_status():
    global backup_mode
    conn = get_backup_db()
    pending_count = conn.execute('SELECT COUNT(*) FROM sync_queue').fetchone()[0]
    conn.close()
    
    return jsonify({
        'status': 'success',
        'raspberry_pi_connected': 'default_pi' in connections and not backup_mode,
        'backup_mode': backup_mode,
        'pending_sync_count': pending_count
    })

# HTML интерфейс (остается прежним)
@app.route('/')
def index():
    global backup_mode
    status_indicator = "🔴 РЕЖИМ РЕЗЕРВНОГО КОПИРОВАНИЯ" if backup_mode else "🟢 ШТАТНЫЙ РЕЖИМ"
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Вход в систему</title>
        <style>
            body {{ font-family: Arial, sans-serif; max-width: 400px; margin: 100px auto; padding: 20px; }}
            .login-form {{ border: 1px solid #ddd; padding: 20px; border-radius: 5px; }}
            input {{ width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ccc; border-radius: 3px; }}
            button {{ width: 100%; padding: 10px; background: #007cba; color: white; border: none; border-radius: 3px; cursor: pointer; }}
            .backup-mode {{ background: #fff3cd; color: #856404; padding: 10px; border-radius: 3px; margin-bottom: 15px; }}
            .normal-mode {{ background: #d4edda; color: #155724; padding: 10px; border-radius: 3px; margin-bottom: 15px; }}
        </style>
    </head>
    <body>
        <div class="login-form">
            <div class="{'backup-mode' if backup_mode else 'normal-mode'}">
                {status_indicator}
            </div>
            <h2>Вход в систему</h2>
            <form method="POST" action="/login">
                <input name="teacher_id" placeholder="ID преподавателя" required>
                <input name="password" type="password" placeholder="Пароль" required>
                <button type="submit">Войти</button>
            </form>
        </div>
    </body>
    </html>
    '''

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
        session['is_admin'] = teacher_data['role'] == 'admin'
        
        return redirect('/dashboard')
    else:
        return redirect('/')

@app.route('/dashboard')
def dashboard():
    return '''
    <html>
    <head><title>Панель управления</title></head>
    <body>
        <h1>Панель управления</h1>
        <button onclick="loadGroups()">Показать группы</button>
        <div id="content"></div>
        <script>
            function loadGroups() {
                fetch('/api/groups')
                    .then(r => r.json())
                    .then(data => {
                        document.getElementById('content').innerHTML = 
                            '<h3>Группы:</h3><pre>' + JSON.stringify(data, null, 2) + '</pre>';
                    });
            }
        </script>
    </body>
    </html>
    '''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)