#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Script Runner - Сервер для загрузки, установки и запуска Python скриптов из GitHub
Полный аналог Telegram бота, но работает через веб-интерфейс
"""

import os
import sys
import re
import json
import time
import subprocess
import importlib.util
import logging
import signal
import threading
import shutil
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import base64

# ============================================================
# НАСТРОЙКИ
# ============================================================
GITHUB_PAT = "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"
GITHUB_REPO = "HenonBank/Website"
GITHUB_BRANCH = "main"

# Директория для скриптов
SCRIPT_DIR = "/root/Bin"  # Как в Telegram боте
os.makedirs(SCRIPT_DIR, exist_ok=True)

# Настройки логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Разрешаем запросы с любых доменов

# Глобальные переменные
running_processes = {}
process_logs = {}
script_status = {}

# Встроенные модули (не требуют установки)
BUILTIN_MODULES = {
    'os', 'sys', 're', 'json', 'datetime', 'sqlite3', 'logging',
    'subprocess', 'math', 'random', 'time', 'asyncio', 'threading',
    'collections', 'itertools', 'functools', 'hashlib', 'socket',
    'typing', 'pathlib', 'urllib', 'ssl', 'email', 'csv', 'io',
    'argparse', 'getpass', 'shutil', 'tempfile', 'glob', 'telegram',
    'flask', 'requests', 'base64', 'hmac', 'pickle', 'struct'
}

# ============================================================
# ФУНКЦИИ РАБОТЫ С GITHUB
# ============================================================

def github_request(method, endpoint, data=None):
    """Выполнение запроса к GitHub API"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/{endpoint}"
    headers = {
        'Authorization': f'token {GITHUB_PAT}',
        'Accept': 'application/vnd.github.v3+json'
    }
    
    try:
        if method == 'GET':
            resp = requests.get(url, headers=headers, timeout=15)
        elif method == 'PUT':
            resp = requests.put(url, headers=headers, json=data, timeout=15)
        elif method == 'DELETE':
            resp = requests.delete(url, headers=headers, json=data, timeout=15)
        else:
            return None
        
        if resp.status_code == 401:
            logger.error("GitHub 401: Invalid token")
            return {'error': 'Unauthorized - invalid GitHub token'}
        elif resp.status_code == 403:
            logger.error("GitHub 403: Token lacks permissions")
            return {'error': 'Forbidden - token lacks permissions'}
        elif resp.status_code == 404:
            return None
        
        return resp.json() if resp.ok else None
    except Exception as e:
        logger.error(f"GitHub request error: {e}")
        return {'error': str(e)}

def get_github_files():
    """Получение списка .py файлов из GitHub репозитория"""
    try:
        data = github_request('GET', f'contents?ref={GITHUB_BRANCH}')
        if not data or isinstance(data, dict) and 'error' in data:
            return []
        return [f for f in data if f.get('name', '').endswith('.py') and f.get('type') == 'file']
    except Exception as e:
        logger.error(f"Error fetching GitHub files: {e}")
        return []

def get_github_file_content(path):
    """Получение содержимого файла из GitHub"""
    try:
        data = github_request('GET', f'contents/{path}?ref={GITHUB_BRANCH}')
        if not data or isinstance(data, dict) and 'error' in data:
            return None
        content = base64.b64decode(data.get('content', '')).decode('utf-8')
        return content
    except Exception as e:
        logger.error(f"Error fetching file content: {e}")
        return None

def sync_from_github():
    """Синхронизация локальной папки с GitHub"""
    files = get_github_files()
    count = 0
    for f in files:
        path = f.get('name')
        content = get_github_file_content(path)
        if content:
            local_path = os.path.join(SCRIPT_DIR, path)
            with open(local_path, 'w', encoding='utf-8') as fp:
                fp.write(content)
            count += 1
            logger.info(f"Synced: {path}")
    return count

def push_to_github(filename, content):
    """Отправка файла в GitHub"""
    try:
        encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
        
        # Проверяем существование файла
        existing = github_request('GET', f'contents/{filename}?ref={GITHUB_BRANCH}')
        sha = None
        if existing and not isinstance(existing, dict) and 'error' not in existing:
            sha = existing.get('sha')
        
        payload = {
            'message': f'Добавлен/обновлён скрипт {filename}',
            'content': encoded,
            'branch': GITHUB_BRANCH
        }
        if sha:
            payload['sha'] = sha
        
        result = github_request('PUT', f'contents/{filename}', payload)
        return result
    except Exception as e:
        logger.error(f"Error pushing to GitHub: {e}")
        return None

def delete_from_github(filename):
    """Удаление файла из GitHub"""
    try:
        existing = github_request('GET', f'contents/{filename}?ref={GITHUB_BRANCH}')
        if not existing or isinstance(existing, dict) and 'error' in existing:
            return False
        
        payload = {
            'message': f'Удалён скрипт {filename}',
            'sha': existing.get('sha'),
            'branch': GITHUB_BRANCH
        }
        
        result = github_request('DELETE', f'contents/{filename}', payload)
        return result is not None
    except Exception as e:
        logger.error(f"Error deleting from GitHub: {e}")
        return False

# ============================================================
# УПРАВЛЕНИЕ СКРИПТАМИ
# ============================================================

def is_package_installed(package_name):
    """Проверка установки пакета"""
    try:
        return importlib.util.find_spec(package_name) is not None
    except:
        return False

def install_dependencies(code):
    """Установка зависимостей из кода (как в Telegram боте)"""
    imports = re.findall(r'^\s*import\s+(\w+)|^\s*from\s+(\w+)', code, re.M)
    packages = set()
    for imp in imports:
        pkg = imp[0] or imp[1]
        if pkg and pkg not in BUILTIN_MODULES:
            packages.add(pkg)
    
    if not packages:
        return {"status": "info", "message": "Нет внешних зависимостей", "installed": []}
    
    to_install = [pkg for pkg in packages if not is_package_installed(pkg)]
    if not to_install:
        return {"status": "info", "message": f"Все зависимости уже установлены: {', '.join(packages)}", "installed": []}
    
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", *to_install],
            capture_output=True,
            text=True,
            timeout=120
        )
        if result.returncode == 0:
            return {"status": "success", "message": f"Установлены: {', '.join(to_install)}", "installed": to_install}
        else:
            return {"status": "error", "message": f"Ошибка установки: {result.stderr[:200]}"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Таймаут установки зависимостей (120 сек)"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def has_dangerous_code(code):
    """Проверка на опасные команды (как в Telegram боте)"""
    dangerous = [
        "os.system", "subprocess.run", "eval(", "exec(",
        "__import__", "compile(", "globals()", "locals()",
        "os.popen", "subprocess.Popen"
    ]
    for cmd in dangerous:
        if cmd in code:
            return True
    return False

def start_script(filename):
    """Запуск скрипта (как в Telegram боте)"""
    file_path = os.path.join(SCRIPT_DIR, filename)
    if not os.path.exists(file_path):
        return {"status": "error", "message": f"Файл {filename} не найден"}
    
    if filename in running_processes:
        process = running_processes[filename]
        if process.poll() is None:
            return {"status": "error", "message": f"Скрипт уже запущен (PID: {process.pid})"}
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            code = f.read()
        
        if has_dangerous_code(code):
            return {"status": "error", "message": "Обнаружены опасные команды! Запуск отменён"}
        
        # Устанавливаем зависимости
        dep_result = install_dependencies(code)
        
        # Создаём лог-файл
        log_file = os.path.join(SCRIPT_DIR, f"{filename}.log")
        with open(log_file, 'w', encoding='utf-8') as log:
            log.write(f"=== Запуск {filename} в {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            log.write(f"Зависимости: {dep_result['message']}\n")
            log.write("=" * 50 + "\n\n")
        
        # Запускаем процесс
        process = subprocess.Popen(
            [sys.executable, file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        running_processes[filename] = process
        process_logs[filename] = []
        script_status[filename] = {
            "status": "running",
            "pid": process.pid,
            "started": datetime.now().isoformat(),
            "deps": dep_result['message']
        }
        
        # Поток для чтения вывода
        def read_output():
            with open(log_file, 'a', encoding='utf-8') as log:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    if filename in process_logs:
                        if len(process_logs[filename]) > 1000:
                            process_logs[filename] = process_logs[filename][-500:]
                        process_logs[filename].append(line)
        
        thread = threading.Thread(target=read_output, daemon=True)
        thread.start()
        
        return {
            "status": "success",
            "message": f"Скрипт {filename} запущен",
            "pid": process.pid,
            "deps": dep_result['message']
        }
    except Exception as e:
        logger.error(f"Error starting script: {e}")
        return {"status": "error", "message": str(e)}

def stop_script(filename):
    """Остановка скрипта (как в Telegram боте)"""
    if filename not in running_processes:
        return {"status": "error", "message": f"Скрипт {filename} не запущен"}
    
    try:
        process = running_processes[filename]
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        
        del running_processes[filename]
        if filename in script_status:
            script_status[filename]["status"] = "stopped"
            script_status[filename]["stopped"] = datetime.now().isoformat()
        
        return {"status": "success", "message": f"Скрипт {filename} остановлен"}
    except Exception as e:
        logger.error(f"Error stopping script: {e}")
        return {"status": "error", "message": str(e)}

def restart_script(filename):
    """Перезапуск скрипта (как в Telegram боте)"""
    stop_result = stop_script(filename)
    if stop_result['status'] == 'error' and 'не запущен' not in stop_result['message']:
        return stop_result
    return start_script(filename)

def delete_script(filename):
    """Удаление скрипта (как в Telegram боте)"""
    if filename in running_processes:
        stop_script(filename)
    
    file_path = os.path.join(SCRIPT_DIR, filename)
    if not os.path.exists(file_path):
        return {"status": "error", "message": f"Файл {filename} не найден"}
    
    try:
        os.remove(file_path)
        log_path = os.path.join(SCRIPT_DIR, f"{filename}.log")
        if os.path.exists(log_path):
            os.remove(log_path)
        if filename in process_logs:
            del process_logs[filename]
        if filename in script_status:
            del script_status[filename]
        return {"status": "success", "message": f"Файл {filename} удалён"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def get_scripts_status():
    """Получение статуса всех скриптов"""
    result = []
    files = [f for f in os.listdir(SCRIPT_DIR) if f.endswith('.py')]
    
    for f in files:
        is_running = f in running_processes and running_processes[f].poll() is None
        status_info = script_status.get(f, {})
        
        result.append({
            "name": f,
            "status": "running" if is_running else "stopped",
            "pid": running_processes[f].pid if is_running else None,
            "started": status_info.get("started"),
            "deps": status_info.get("deps", "")
        })
    
    return result

def get_script_log(filename, lines=100):
    """Получение последних строк лога"""
    log_path = os.path.join(SCRIPT_DIR, f"{filename}.log")
    if not os.path.exists(log_path):
        return []
    
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
            return all_lines[-lines:]
    except:
        return []

# ============================================================
# API ENDPOINTS
# ============================================================

@app.route('/api/health', methods=['GET'])
def api_health():
    """Проверка статуса сервера"""
    return jsonify({
        "status": "ok",
        "scripts_dir": SCRIPT_DIR,
        "scripts_count": len([f for f in os.listdir(SCRIPT_DIR) if f.endswith('.py')]),
        "running_count": len(running_processes),
        "repo": f"{GITHUB_REPO}"
    })

@app.route('/api/scripts', methods=['GET'])
def api_scripts():
    """Получить список скриптов"""
    return jsonify({"scripts": get_scripts_status()})

@app.route('/api/script/<name>/<action>', methods=['POST'])
def api_script_action(name, action):
    """Действие со скриптом: start, stop, restart, delete"""
    actions = {
        'start': start_script,
        'stop': stop_script,
        'restart': restart_script,
        'delete': delete_script
    }
    if action not in actions:
        return jsonify({"status": "error", "message": f"Неизвестное действие: {action}"}), 400
    
    result = actions[action](name)
    return jsonify(result)

@app.route('/api/script/<name>/content', methods=['GET'])
def api_script_content(name):
    """Получить содержимое скрипта"""
    path = os.path.join(SCRIPT_DIR, name)
    if not os.path.exists(path):
        return jsonify({"status": "error", "message": "Файл не найден"}), 404
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({"content": content})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/script/<name>/update', methods=['POST'])
def api_script_update(name):
    """Обновить содержимое скрипта и отправить в GitHub"""
    data = request.get_json()
    if not data or 'content' not in data:
        return jsonify({"status": "error", "message": "Не указано содержимое"}), 400
    
    content = data['content']
    path = os.path.join(SCRIPT_DIR, name)
    
    try:
        # Сохраняем локально
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # Отправляем в GitHub
        push_to_github(name, content)
        
        return jsonify({"status": "success", "message": f"Файл {name} обновлён"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/script/<name>/log', methods=['GET'])
def api_script_log(name):
    """Получить лог скрипта"""
    lines = request.args.get('lines', 100, type=int)
    log_lines = get_script_log(name, lines)
    return jsonify({"log": log_lines})

@app.route('/api/sync', methods=['POST'])
def api_sync():
    """Синхронизировать с GitHub"""
    count = sync_from_github()
    return jsonify({"status": "success", "count": count})

@app.route('/api/push', methods=['POST'])
def api_push():
    """Отправить файл на сервер и в GitHub"""
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "Файл не выбран"}), 400
    
    file = request.files['file']
    if not file.filename.endswith('.py'):
        return jsonify({"status": "error", "message": "Только .py файлы"}), 400
    
    name = file.filename
    content = file.read().decode('utf-8')
    
    # Сохраняем локально
    path = os.path.join(SCRIPT_DIR, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    # Отправляем в GitHub
    try:
        push_to_github(name, content)
        return jsonify({"status": "success", "message": f"Файл {name} отправлен на сервер и в GitHub"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/commit', methods=['GET'])
def api_commit():
    """Получить последний коммит"""
    try:
        data = github_request('GET', f'commits/{GITHUB_BRANCH}')
        if data and not isinstance(data, dict) and 'error' in data:
            return jsonify({"sha": None})
        if data and data.get('sha'):
            return jsonify({"sha": data['sha']})
        return jsonify({"sha": None})
    except Exception as e:
        return jsonify({"sha": None, "error": str(e)}), 500

# ============================================================
# ЗАПУСК
# ============================================================

def main():
    print("""
    ╔═══════════════════════════════════════════════════════════════╗
    ║           GitHub Script Runner — Сервер                      ║
    ║   Загрузка, установка зависимостей и запуск Python скриптов  ║
    ║   Репозиторий: HenonBank/Website                            ║
    ╚═══════════════════════════════════════════════════════════════╝
    """)
    print(f"📁 Директория скриптов: {SCRIPT_DIR}")
    print(f"📦 Репозиторий: {GITHUB_REPO}")
    print(f"🌐 API доступен на http://0.0.0.0:5000")
    print("=" * 60)
    print("📡 API Endpoints:")
    print("   GET  /api/health              - Статус сервера")
    print("   GET  /api/scripts             - Список скриптов")
    print("   POST /api/script/<name>/<action> - Управление (start/stop/restart/delete)")
    print("   GET  /api/script/<name>/content  - Содержимое скрипта")
    print("   POST /api/script/<name>/update   - Обновить скрипт")
    print("   GET  /api/script/<name>/log      - Лог скрипта")
    print("   POST /api/sync                - Синхронизация с GitHub")
    print("   POST /api/push                - Отправить файл")
    print("   GET  /api/commit              - Последний коммит")
    print("=" * 60)
    
    # Синхронизация при старте
    print("🔄 Синхронизация с GitHub...")
    try:
        count = sync_from_github()
        print(f"✅ Синхронизировано {count} файлов")
    except Exception as e:
        print(f"⚠️ Ошибка синхронизации: {e}")
    
    # Проверяем, есть ли уже запущенные скрипты
    files = [f for f in os.listdir(SCRIPT_DIR) if f.endswith('.py')]
    print(f"📄 Всего скриптов: {len(files)}")
    print("=" * 60)
    print("✅ Сервер готов к работе!")
    
    try:
        app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n🛑 Остановка сервера...")
        # Останавливаем все процессы
        for name, process in list(running_processes.items()):
            try:
                process.terminate()
                process.wait(timeout=3)
                print(f"⏹ Остановлен: {name}")
            except:
                pass
        sys.exit(0)

if __name__ == '__main__':
    main()
