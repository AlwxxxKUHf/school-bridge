from flask import Flask, request, jsonify
from flask_cors import CORS
import datetime
import os
import time

app = Flask(__name__)
CORS(app)  # Разрешаем запросы с локальных компьютеров

# Простое хранилище в памяти (переживает перезапуски на Render)
class TaskManager:
    def __init__(self):
        self.teacher_tasks = {}
        self.pi_responses = {}
        self.last_cleanup = time.time()
    
    def cleanup_old_tasks(self):
        """Очистка старых задач для экономии памяти"""
        current_time = time.time()
        if current_time - self.last_cleanup > 3600:  # Каждый час
            # Удаляем задачи старше 6 часов и выполненные результаты старше 1 часа
            cutoff_time = current_time - 21600  # 6 часов
            
            # Очищаем старые задачи
            old_tasks = [
                task_id for task_id, task in self.teacher_tasks.items()
                if datetime.datetime.fromisoformat(task['created']).timestamp() < cutoff_time
            ]
            for task_id in old_tasks:
                del self.teacher_tasks[task_id]
            
            # Очищаем старые результаты
            old_results = [
                task_id for task_id, result in self.pi_responses.items()
                if datetime.datetime.fromisoformat(result['completed']).timestamp() < current_time - 3600
            ]
            for task_id in old_results:
                del self.pi_responses[task_id]
            
            self.last_cleanup = current_time
            print(f"🧹 Очищено {len(old_tasks)} задач и {len(old_results)} результатов")

task_manager = TaskManager()

@app.route('/api/teacher/task', methods=['POST'])
def add_teacher_task():
    """Учитель отправляет задачу"""
    task_manager.cleanup_old_tasks()
    
    data = request.json
    task_id = f"task_{int(time.time() * 1000)}"
    
    task_manager.teacher_tasks[task_id] = {
        'task_id': task_id,
        'type': data['type'],
        'data': data['data'],
        'teacher_id': data.get('teacher_id', 'unknown'),
        'status': 'pending',
        'created': datetime.datetime.now().isoformat()
    }
    
    print(f"📨 Получена задача {data['type']} от учителя {data.get('teacher_id')}")
    return jsonify({'status': 'ok', 'task_id': task_id})

@app.route('/api/pi/get_task', methods=['GET'])
def get_teacher_task():
    """Raspberry Pi забирает задачу"""
    task_manager.cleanup_old_tasks()
    
    for task_id, task in task_manager.teacher_tasks.items():
        if task['status'] == 'pending':
            task_manager.teacher_tasks[task_id]['status'] = 'processing'
            print(f"📤 Отправлена задача {task['type']} на Pi")
            return jsonify(task)
    
    return jsonify({'status': 'no_tasks'})

@app.route('/api/pi/task_done', methods=['POST'])
def task_done():
    """Raspberry Pi сообщает о выполнении"""
    data = request.json
    task_id = data['task_id']
    
    task_manager.pi_responses[task_id] = {
        'task_id': task_id,
        'result': data['result'],
        'completed': datetime.datetime.now().isoformat()
    }
    
    if task_id in task_manager.teacher_tasks:
        task_manager.teacher_tasks[task_id]['status'] = 'completed'
    
    print(f"✅ Задача {task_id} выполнена: {data['result']['status']}")
    return jsonify({'status': 'ok'})

@app.route('/api/teacher/result/<task_id>', methods=['GET'])
def get_result(task_id):
    """Учитель получает результат"""
    if task_id in task_manager.pi_responses:
        result = task_manager.pi_responses[task_id]
        # Не удаляем сразу, учитель может запросить повторно
        return jsonify(result)
    return jsonify({'status': 'not_ready'})

@app.route('/health', methods=['GET'])
def health_check():
    """Проверка здоровья сервиса"""
    pending_tasks = len([t for t in task_manager.teacher_tasks.values() if t['status'] == 'pending'])
    return jsonify({
        'status': 'healthy', 
        'server_time': datetime.datetime.now().isoformat(),
        'pending_tasks': pending_tasks,
        'total_tasks': len(task_manager.teacher_tasks),
        'completed_results': len(task_manager.pi_responses)
    })

@app.route('/')
def home():
    return jsonify({
        'message': 'School Bridge API', 
        'status': 'running',
        'endpoints': {
            'teacher_task': '/api/teacher/task (POST)',
            'pi_get_task': '/api/pi/get_task (GET)', 
            'pi_task_done': '/api/pi/task_done (POST)',
            'teacher_result': '/api/teacher/result/<task_id> (GET)',
            'health': '/health (GET)'
        }
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"🚀 School Bridge запущен на порту {port}")
    app.run(host='0.0.0.0', port=port, debug=False)