#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Script Runner - Сервер для загрузки и запуска Python скриптов из GitHub
Аналог Telegram бота, но работает через веб-интерфейс
"""

import os
import re
import sys
import json
import time
import subprocess
import importlib.util
import logging
import shutil
import signal
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template_string, request, jsonify, send_file
from flask_cors import CORS
import requests
import threading
import queue

# ============================================================
# НАСТРОЙКИ
# ============================================================
GITHUB_PAT = "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"
GITHUB_REPO = "HenonBank/Website"
GITHUB_BRANCH = "main"

# Директория для скриптов
SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
os.makedirs(SCRIPT_DIR, exist_ok=True)

# Файл для хранения состояния
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

# Настройки логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Глобальные переменные
running_processes = {}
process_outputs = {}
process_logs = {}
script_status = {}

BUILTIN_MODULES = {
    'os', 'sys', 're', 'json', 'datetime', 'sqlite3', 'logging',
    'subprocess', 'math', 'random', 'time', 'asyncio', 'threading',
    'collections', 'itertools', 'functools', 'hashlib', 'socket',
    'typing', 'pathlib', 'urllib', 'ssl', 'email', 'csv', 'io',
    'argparse', 'getpass', 'shutil', 'tempfile', 'glob', 'telegram',
    'flask', 'requests', 'base64', 'hashlib', 'hmac'
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
        import base64
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

# ============================================================
# ФУНКЦИИ УПРАВЛЕНИЯ СКРИПТАМИ
# ============================================================

def is_package_installed(package_name):
    """Проверка установки пакета"""
    try:
        return importlib.util.find_spec(package_name) is not None
    except:
        return False

def install_dependencies(code):
    """Установка зависимостей из кода"""
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
        return {"status": "error", "message": "Таймаут установки зависимостей"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def has_dangerous_code(code):
    """Проверка на опасные команды"""
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
    """Запуск скрипта"""
    file_path = os.path.join(SCRIPT_DIR, filename)
    if not os.path.exists(file_path):
        return {"status": "error", "message": f"Файл {filename} не найден"}
    
    # Проверяем, не запущен ли уже
    if filename in running_processes:
        process = running_processes[filename]
        if process.poll() is None:
            return {"status": "error", "message": f"Скрипт {filename} уже запущен (PID: {process.pid})"}
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            code = f.read()
        
        if has_dangerous_code(code):
            return {"status": "error", "message": "Обнаружены опасные команды! Запуск отменён"}
        
        # Устанавливаем зависимости
        dep_result = install_dependencies(code)
        
        # Запускаем процесс
        log_file = os.path.join(SCRIPT_DIR, f"{filename}.log")
        with open(log_file, 'w') as log:
            log.write(f"=== Запуск {filename} в {datetime.now()} ===\n")
            log.write(f"Зависимости: {dep_result['message']}\n")
        
        process = subprocess.Popen(
            [sys.executable, file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        running_processes[filename] = process
        script_status[filename] = {
            "status": "running",
            "pid": process.pid,
            "started": datetime.now().isoformat(),
            "deps": dep_result['message']
        }
        
        # Запускаем поток для чтения вывода
        def read_output():
            with open(log_file, 'a') as log:
                for line in process.stdout:
                    log.write(line)
                    if filename in process_outputs:
                        if len(process_outputs[filename]) > 1000:
                            process_outputs[filename] = process_outputs[filename][-500:]
                        process_outputs[filename].append(line)
        
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
    """Остановка скрипта"""
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
    """Перезапуск скрипта"""
    stop_result = stop_script(filename)
    if stop_result['status'] == 'error' and 'не запущен' not in stop_result['message']:
        return stop_result
    return start_script(filename)

def delete_script(filename):
    """Удаление скрипта"""
    # Сначала останавливаем
    if filename in running_processes:
        stop_script(filename)
    
    file_path = os.path.join(SCRIPT_DIR, filename)
    if not os.path.exists(file_path):
        return {"status": "error", "message": f"Файл {filename} не найден"}
    
    try:
        os.remove(file_path)
        # Удаляем лог
        log_path = os.path.join(SCRIPT_DIR, f"{filename}.log")
        if os.path.exists(log_path):
            os.remove(log_path)
        if filename in process_outputs:
            del process_outputs[filename]
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
        status_info = script_status.get(f, {})
        is_running = f in running_processes and running_processes[f].poll() is None
        
        result.append({
            "name": f,
            "status": "running" if is_running else "stopped",
            "pid": running_processes[f].pid if is_running else None,
            "started": status_info.get("started"),
            "deps": status_info.get("deps", "")
        })
    
    return result

def get_script_log(filename, lines=50):
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
# FLASK ПРИЛОЖЕНИЕ
# ============================================================

app = Flask(__name__)
CORS(app)

# HTML шаблон (полная версия с управлением)
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GitHub Script Runner</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #0C0E13;
            font-family: 'Segoe UI', -apple-system, sans-serif;
            color: #FFFFFF;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 20px 24px;
            background: #171B24;
            border-radius: 16px;
            margin-bottom: 24px;
            border: 1px solid rgba(255,255,255,0.04);
        }
        .header h1 {
            font-size: 1.8rem;
            background: linear-gradient(135deg, #4AD1E0, #B967FF);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .header .badge {
            background: rgba(74,222,128,0.1);
            border: 1px solid rgba(74,222,128,0.2);
            padding: 8px 20px;
            border-radius: 40px;
            color: #4ADE80;
            font-size: 0.9rem;
        }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
        @media (max-width: 1024px) { .grid { grid-template-columns: 1fr; } }
        .card {
            background: #171B24;
            border-radius: 16px;
            padding: 24px;
            border: 1px solid rgba(255,255,255,0.04);
            margin-bottom: 24px;
        }
        .card-header {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 18px;
            color: #D6E0F0;
            font-size: 1.1rem;
        }
        .card-header i { color: #7B8AA8; width: 1.8rem; }
        .card-header span {
            margin-left: auto;
            background: #232A36;
            padding: 4px 16px;
            border-radius: 40px;
            font-size: 0.8rem;
            color: #7B8AA8;
        }
        .upload-zone {
            border: 2px dashed #2E3542;
            border-radius: 12px;
            padding: 40px;
            text-align: center;
            cursor: pointer;
            transition: 0.3s;
            background: #10141C;
            margin-bottom: 16px;
        }
        .upload-zone:hover { border-color: #B967FF; }
        .upload-zone i { font-size: 2.5rem; color: #4A5A72; margin-bottom: 8px; }
        .upload-zone p { color: #8E9AAF; }
        .upload-zone .hint { font-size: 0.8rem; color: #5F6A7A; margin-top: 4px; }
        .btn-primary {
            background: linear-gradient(135deg, #4AD1E0, #B967FF);
            border: none;
            border-radius: 60px;
            padding: 14px 28px;
            color: #fff;
            font-weight: 600;
            width: 100%;
            cursor: pointer;
            transition: 0.2s;
        }
        .btn-primary:hover { opacity: 0.9; transform: scale(0.98); }
        .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-secondary {
            background: #232A36;
            border: 1px solid #2E3542;
            border-radius: 60px;
            padding: 12px 20px;
            color: #D6E0F0;
            width: 100%;
            cursor: pointer;
            transition: 0.2s;
        }
        .btn-secondary:hover { background: #2E3848; }
        .btn-danger {
            background: #2A1518;
            border: 1px solid #4A1A22;
            border-radius: 60px;
            padding: 12px 20px;
            color: #F87171;
            width: 100%;
            cursor: pointer;
            transition: 0.2s;
        }
        .btn-danger:hover { background: #3A1A20; }
        .script-list { display: flex; flex-direction: column; gap: 8px; max-height: 400px; overflow-y: auto; }
        .script-item {
            display: flex;
            align-items: center;
            gap: 14px;
            background: #10141C;
            border-radius: 12px;
            padding: 12px 16px;
            border: 1px solid #1E2530;
            flex-wrap: wrap;
        }
        .script-item .name { flex: 2; font-weight: 500; color: #D6E0F0; min-width: 120px; }
        .script-item .name i { color: #7B8AA8; margin-right: 10px; }
        .script-item .status {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.85rem;
        }
        .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
        .dot.running { background: #4ADE80; }
        .dot.stopped { background: #F87171; }
        .status-text.running { color: #4ADE80; }
        .status-text.stopped { color: #F87171; }
        .script-item .type {
            background: #1E2530;
            padding: 2px 14px;
            border-radius: 30px;
            font-size: 0.7rem;
            color: #7B8AA8;
        }
        .script-item .pid {
            font-size: 0.7rem;
            color: #5F6A7A;
            font-family: monospace;
        }
        .actions { display: flex; gap: 4px; margin-left: auto; flex-wrap: wrap; }
        .actions button {
            background: transparent;
            border: none;
            color: #5F6A7A;
            width: 34px;
            height: 34px;
            border-radius: 50%;
            cursor: pointer;
            transition: 0.2s;
            font-size: 0.9rem;
        }
        .actions button:hover { background: #1E2530; color: #D6E0F0; }
        .actions .play { color: #4ADE80; }
        .actions .play:hover { background: rgba(74,222,128,0.12); }
        .actions .stop { color: #F87171; }
        .actions .stop:hover { background: rgba(248,113,113,0.12); }
        .actions .restart { color: #FBBF24; }
        .actions .restart:hover { background: rgba(251,191,36,0.12); }
        .actions .edit { color: #60A5FA; }
        .actions .edit:hover { background: rgba(96,165,250,0.1); }
        .actions .delete { color: #F87171; }
        .actions .delete:hover { background: rgba(248,113,113,0.12); }
        .actions .log { color: #7B8AA8; }
        .actions .log:hover { background: rgba(123,138,168,0.12); }
        .console {
            background: #0C0E13;
            border-radius: 12px;
            padding: 16px;
            max-height: 250px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 0.8rem;
            border: 1px solid #1E2530;
        }
        .console-line {
            padding: 2px 0;
            border-bottom: 1px solid rgba(255,255,255,0.02);
            white-space: pre-wrap;
            word-break: break-all;
        }
        .console-time { color: #5F6A7A; margin-right: 12px; }
        .console-info { color: #60A5FA; }
        .console-success { color: #4ADE80; }
        .console-error { color: #F87171; }
        .console-warning { color: #FBBF24; }
        .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
        .stat-item {
            background: #10141C;
            border-radius: 12px;
            padding: 14px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.02);
        }
        .stat-number { font-size: 2rem; font-weight: 700; }
        .stat-number.blue { color: #60A5FA; }
        .stat-number.green { color: #4ADE80; }
        .stat-number.red { color: #F87171; }
        .stat-number.purple { color: #B967FF; }
        .stat-label { color: #7B8AA8; font-size: 0.7rem; text-transform: uppercase; margin-top: 4px; }
        .toast {
            background: #1E2530;
            border-left: 4px solid #4ADE80;
            padding: 12px 18px;
            border-radius: 10px;
            margin-top: 12px;
        }
        .toast.error { border-left-color: #F87171; }
        .toast i { margin-right: 10px; }
        .modal {
            display: none;
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.7);
            backdrop-filter: blur(8px);
            z-index: 1000;
            justify-content: center;
            align-items: center;
        }
        .modal.active { display: flex; }
        .modal-content {
            background: #171B24;
            border-radius: 20px;
            padding: 32px;
            max-width: 700px;
            width: 90%;
            border: 1px solid rgba(255,255,255,0.06);
            max-height: 90vh;
        }
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }
        .modal-header h3 { color: #D6E0F0; }
        .modal-header .close {
            background: transparent;
            border: none;
            color: #5F6A7A;
            font-size: 1.5rem;
            cursor: pointer;
        }
        .modal-body {
            max-height: 50vh;
            overflow-y: auto;
        }
        .modal-body textarea {
            width: 100%;
            min-height: 200px;
            background: #0C0E13;
            border: 1px solid #2E3542;
            border-radius: 10px;
            color: #D6E0F0;
            padding: 12px;
            font-family: monospace;
            font-size: 0.85rem;
            resize: vertical;
            outline: none;
        }
        .modal-body textarea:focus { border-color: #B967FF; }
        .modal-body .log-content {
            background: #0C0E13;
            border-radius: 10px;
            padding: 12px;
            font-family: monospace;
            font-size: 0.8rem;
            color: #D6E0F0;
            white-space: pre-wrap;
            word-break: break-all;
            max-height: 400px;
            overflow-y: auto;
        }
        .modal-footer { display: flex; gap: 12px; justify-content: flex-end; margin-top: 16px; }
        .modal-footer .btn-primary { width: auto; padding: 10px 28px; }
        .modal-footer .btn-secondary { width: auto; padding: 10px 28px; }
        .repo-info {
            background: #10141C;
            border-radius: 10px;
            padding: 14px 16px;
            margin-top: 12px;
        }
        .repo-info .link { color: #8E9AAF; word-break: break-all; }
        .repo-info .link i { color: #B967FF; margin-right: 8px; }
        .commit-hash {
            background: #232A36;
            padding: 2px 14px;
            border-radius: 30px;
            font-family: monospace;
            color: #D6E0F0;
        }
        #fileInput { display: none; }
        .status-msg { margin-top: 12px; }
        .flex { display: flex; gap: 12px; flex-wrap: wrap; }
        .flex-1 { flex: 1; min-width: 100px; }
        @media (max-width: 768px) {
            .header h1 { font-size: 1.3rem; }
            .stats { grid-template-columns: repeat(2, 1fr); }
            .script-item { flex-wrap: wrap; }
            .actions { margin-left: 0; width: 100%; justify-content: flex-end; }
            .modal-content { padding: 20px; }
        }
    </style>
</head>
<body>
<div class="container">

    <!-- HEADER -->
    <div class="header">
        <h1><i class="fas fa-code"></i> GitHub Script Runner</h1>
        <div class="badge"><i class="fas fa-circle"></i> Сервер активен</div>
    </div>

    <div class="grid">

        <!-- LEFT COLUMN -->
        <div>

            <!-- Upload Card -->
            <div class="card">
                <div class="card-header">
                    <i class="fas fa-upload"></i> Загрузить скрипт
                    <span>.py</span>
                </div>
                <div class="upload-zone" id="uploadZone">
                    <i class="fas fa-file-code"></i>
                    <p>Выберите <strong>.py</strong> файл или перетащите сюда</p>
                    <div class="hint">Файл будет сохранён и запущен</div>
                </div>
                <input type="file" id="fileInput" accept=".py">
                <button class="btn-primary" id="pushBtn">
                    <i class="fab fa-github"></i> Отправить в GitHub
                </button>
                <div id="statusMessage" class="status-msg"></div>
            </div>

            <!-- GitHub Info -->
            <div class="card">
                <div class="card-header">
                    <i class="fab fa-github"></i> GitHub
                    <span>репозиторий</span>
                </div>
                <div class="flex">
                    <button class="btn-secondary flex-1" id="syncBtn">
                        <i class="fas fa-sync-alt"></i> Синхронизировать
                    </button>
                    <button class="btn-secondary flex-1" id="refreshBtn">
                        <i class="fas fa-refresh"></i> Обновить статус
                    </button>
                </div>
                <div class="repo-info">
                    <div class="link"><i class="fas fa-link"></i> {{ repo_url }}</div>
                    <div style="margin-top: 8px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
                        <i class="fas fa-code-branch"></i>
                        <span>Последний коммит:</span>
                        <span class="commit-hash" id="lastCommit">—</span>
                        <i class="fas fa-check-circle" style="color: #4ADE80;"></i>
                    </div>
                </div>
            </div>

            <!-- Stats -->
            <div class="card">
                <div class="card-header">
                    <i class="fas fa-chart-simple"></i> Статистика
                </div>
                <div class="stats">
                    <div class="stat-item">
                        <div class="stat-number blue" id="totalScripts">0</div>
                        <div class="stat-label">Всего скриптов</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-number green" id="runningScripts">0</div>
                        <div class="stat-label">Запущено</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-number purple" id="successCount">0</div>
                        <div class="stat-label">Успешно</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-number red" id="errorCount">0</div>
                        <div class="stat-label">Ошибок</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- RIGHT COLUMN -->
        <div>

            <!-- Console -->
            <div class="card">
                <div class="card-header">
                    <i class="fas fa-terminal"></i> Вывод
                    <span>Terminal</span>
                </div>
                <div class="console" id="consoleLog">
                    <div class="console-line">
                        <span class="console-time">[—]</span>
                        <span class="console-info">[INFO]</span>
                        <span>Сервер запущен. Готов к работе.</span>
                    </div>
                </div>
            </div>

            <!-- Scripts List -->
            <div class="card">
                <div class="card-header">
                    <i class="fas fa-list-ul"></i> Скрипты
                    <span id="scriptsCount">0</span>
                </div>
                <div class="script-list" id="scriptsList">
                    <div style="color: #5F6A7A; text-align: center; padding: 20px;">
                        <i class="fas fa-info-circle"></i> Загрузите скрипты или синхронизируйтесь с GitHub
                    </div>
                </div>
            </div>

        </div>
    </div>
</div>

<!-- Edit Modal -->
<div class="modal" id="editModal">
    <div class="modal-content">
        <div class="modal-header">
            <h3><i class="fas fa-edit" style="color: #60A5FA; margin-right: 10px;"></i>Редактировать</h3>
            <button class="close" onclick="closeModal('editModal')">&times;</button>
        </div>
        <div class="modal-body">
            <p style="color: #8E9AAF; font-size: 0.85rem; margin-bottom: 10px;" id="editFileName">main.py</p>
            <textarea id="editContent"></textarea>
        </div>
        <div class="modal-footer">
            <button class="btn-secondary" onclick="closeModal('editModal')">Отмена</button>
            <button class="btn-primary" id="saveEditBtn">
                <i class="fas fa-save"></i> Сохранить
            </button>
        </div>
    </div>
</div>

<!-- Log Modal -->
<div class="modal" id="logModal">
    <div class="modal-content">
        <div class="modal-header">
            <h3><i class="fas fa-file-alt" style="color: #7B8AA8; margin-right: 10px;"></i>Лог скрипта</h3>
            <button class="close" onclick="closeModal('logModal')">&times;</button>
        </div>
        <div class="modal-body">
            <p style="color: #8E9AAF; font-size: 0.85rem; margin-bottom: 10px;" id="logFileName">main.py</p>
            <div class="log-content" id="logContent">Загрузка...</div>
        </div>
        <div class="modal-footer">
            <button class="btn-secondary" onclick="closeModal('logModal')">Закрыть</button>
            <button class="btn-secondary" id="refreshLogBtn">
                <i class="fas fa-refresh"></i> Обновить
            </button>
        </div>
    </div>
</div>

<script>
    // ============================================================
    // КЛИЕНТСКАЯ ЛОГИКА
    // ============================================================

    const API_BASE = '';
    let selectedFile = null;
    let editingFile = null;
    let viewingLogFile = null;
    let successCount = 0;
    let errorCount = 0;
    let autoRefreshInterval = null;

    function addConsole(time, level, msg) {
        const log = document.getElementById('consoleLog');
        const line = document.createElement('div');
        line.className = 'console-line';
        const levelClass = level.toLowerCase();
        line.innerHTML = `
            <span class="console-time">[${time}]</span>
            <span class="console-${levelClass}">[${level.toUpperCase()}]</span>
            <span>${msg}</span>
        `;
        log.appendChild(line);
        log.scrollTop = log.scrollHeight;
        while (log.children.length > 200) log.removeChild(log.firstChild);
    }

    function getTime() {
        const d = new Date();
        return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' +
               String(d.getDate()).padStart(2,'0') + ' ' + String(d.getHours()).padStart(2,'0') + ':' +
               String(d.getMinutes()).padStart(2,'0') + ':' + String(d.getSeconds()).padStart(2,'0');
    }

    function showStatus(msg, isError = false) {
        const el = document.getElementById('statusMessage');
        el.innerHTML = `<div class="toast ${isError ? 'error' : ''}">
            <i class="fas ${isError ? 'fa-exclamation-circle' : 'fa-check-circle'}"></i> ${msg}
        </div>`;
        if (!isError) successCount++;
        else errorCount++;
        document.getElementById('successCount').textContent = successCount;
        document.getElementById('errorCount').textContent = errorCount;
        setTimeout(() => { el.innerHTML = ''; }, 8000);
    }

    function closeModal(id) {
        document.getElementById(id).classList.remove('active');
        if (id === 'editModal') editingFile = null;
        if (id === 'logModal') viewingLogFile = null;
    }

    // === Загрузка списка скриптов ===
    async function loadScripts() {
        try {
            const resp = await fetch('/api/scripts');
            const data = await resp.json();
            const list = document.getElementById('scriptsList');
            list.innerHTML = '';

            if (!data.scripts || data.scripts.length === 0) {
                list.innerHTML = '<div style="color: #5F6A7A; text-align: center; padding: 20px;">Скриптов нет</div>';
                document.getElementById('totalScripts').textContent = '0';
                document.getElementById('scriptsCount').textContent = '0';
                document.getElementById('runningScripts').textContent = '0';
                return;
            }

            let running = 0;
            data.scripts.forEach(s => {
                if (s.status === 'running') running++;
                const row = document.createElement('div');
                row.className = 'script-item';
                row.dataset.name = s.name;
                const statusClass = s.status === 'running' ? 'running' : 'stopped';
                const statusText = s.status === 'running' ? 'Запущен' : 'Остановлен';
                const pidText = s.pid ? `PID: ${s.pid}` : '';
                row.innerHTML = `
                    <div class="name"><i class="fas fa-file-code"></i> ${s.name}</div>
                    <div class="status">
                        <span class="dot ${statusClass}"></span>
                        <span class="status-text ${statusClass}">${statusText}</span>
                        <span class="pid">${pidText}</span>
                    </div>
                    <span class="type">Скрипт</span>
                    <div class="actions">
                        <button class="play" onclick="actionScript('${s.name}', 'start')" title="Запустить">
                            <i class="fas fa-play"></i>
                        </button>
                        <button class="stop" onclick="actionScript('${s.name}', 'stop')" title="Остановить">
                            <i class="fas fa-pause"></i>
                        </button>
                        <button class="restart" onclick="actionScript('${s.name}', 'restart')" title="Перезапустить">
                            <i class="fas fa-sync-alt"></i>
                        </button>
                        <button class="edit" onclick="editScript('${s.name}')" title="Редактировать">
                            <i class="fas fa-edit"></i>
                        </button>
                        <button class="log" onclick="viewLog('${s.name}')" title="Лог">
                            <i class="fas fa-file-alt"></i>
                        </button>
                        <button class="delete" onclick="actionScript('${s.name}', 'delete')" title="Удалить">
                            <i class="fas fa-trash-alt"></i>
                        </button>
                    </div>
                `;
                list.appendChild(row);
            });

            document.getElementById('totalScripts').textContent = data.scripts.length;
            document.getElementById('scriptsCount').textContent = data.scripts.length;
            document.getElementById('runningScripts').textContent = running;

            await updateCommit();

        } catch (e) {
            console.error('Error loading scripts:', e);
            addConsole(getTime(), 'error', 'Ошибка загрузки скриптов: ' + e.message);
        }
    }

    async function updateCommit() {
        try {
            const resp = await fetch('/api/commit');
            const data = await resp.json();
            if (data.sha) {
                document.getElementById('lastCommit').textContent = data.sha.substring(0, 7);
            }
        } catch (e) {
            console.error('Error fetching commit:', e);
        }
    }

    // === Действия со скриптами ===
    async function actionScript(name, action) {
        const actionLabels = {
            'start': 'Запуск',
            'stop': 'Остановка',
            'restart': 'Перезапуск',
            'delete': 'Удаление'
        };
        if (action === 'delete' && !confirm(`Удалить скрипт "${name}"?`)) return;

        try {
            const resp = await fetch(`/api/script/${name}/${action}`, { method: 'POST' });
            const data = await resp.json();
            const level = data.status === 'success' ? 'success' : 'error';
            addConsole(getTime(), level, `${actionLabels[action]}: ${data.message}`);
            if (data.status === 'success') {
                showStatus(data.message);
            } else {
                showStatus(data.message, true);
            }
            await loadScripts();
        } catch (e) {
            addConsole(getTime(), 'error', 'Ошибка: ' + e.message);
            showStatus('Ошибка: ' + e.message, true);
        }
    }

    // === Редактирование ===
    async function editScript(name) {
        editingFile = name;
        document.getElementById('editFileName').textContent = '📄 ' + name;
        try {
            const resp = await fetch(`/api/script/${name}/content`);
            const data = await resp.json();
            if (data.content) {
                document.getElementById('editContent').value = data.content;
                document.getElementById('editModal').classList.add('active');
            } else {
                showStatus('Не удалось загрузить содержимое', true);
            }
        } catch (e) {
            showStatus('Ошибка загрузки: ' + e.message, true);
        }
    }

    document.getElementById('saveEditBtn').addEventListener('click', async () => {
        if (!editingFile) return;
        const content = document.getElementById('editContent').value;
        try {
            const resp = await fetch(`/api/script/${editingFile}/update`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: content })
            });
            const data = await resp.json();
            if (data.status === 'success') {
                addConsole(getTime(), 'success', '✅ Файл ' + editingFile + ' обновлён');
                showStatus('✅ ' + editingFile + ' обновлён');
                closeModal('editModal');
                await loadScripts();
            } else {
                showStatus('Ошибка: ' + data.message, true);
            }
        } catch (e) {
            showStatus('Ошибка: ' + e.message, true);
        }
    });

    // === Просмотр лога ===
    async function viewLog(name) {
        viewingLogFile = name;
        document.getElementById('logFileName').textContent = '📄 ' + name;
        document.getElementById('logContent').textContent = 'Загрузка...';
        document.getElementById('logModal').classList.add('active');
        await loadLog(name);
    }

    async function loadLog(name) {
        try {
            const resp = await fetch(`/api/script/${name}/log`);
            const data = await resp.json();
            if (data.log) {
                document.getElementById('logContent').textContent = data.log.join('');
            } else {
                document.getElementById('logContent').textContent = 'Лог пуст';
            }
        } catch (e) {
            document.getElementById('logContent').textContent = 'Ошибка загрузки лога: ' + e.message;
        }
    }

    document.getElementById('refreshLogBtn').addEventListener('click', async () => {
        if (viewingLogFile) {
            await loadLog(viewingLogFile);
        }
    });

    // Закрытие модалок по клику вне
    document.querySelectorAll('.modal').forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target === e.currentTarget) {
                closeModal(e.target.id);
            }
        });
    });

    // === Загрузка файла ===
    const uploadZone = document.getElementById('uploadZone');
    const fileInput = document.getElementById('fileInput');

    uploadZone.addEventListener('click', () => fileInput.click());
    uploadZone.addEventListener('dragover', (e) => { e.preventDefault(); uploadZone.style.borderColor = '#B967FF'; });
    uploadZone.addEventListener('dragleave', () => { uploadZone.style.borderColor = '#2E3542'; });
    uploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadZone.style.borderColor = '#2E3542';
        if (e.dataTransfer.files.length) {
            const file = e.dataTransfer.files[0];
            if (file.name.endsWith('.py')) {
                selectedFile = file;
                uploadZone.querySelector('p').textContent = '✅ ' + file.name;
                uploadZone.querySelector('.hint').textContent = 'Размер: ' + (file.size/1024).toFixed(1) + ' KB';
                showStatus('Файл ' + file.name + ' выбран');
            } else {
                showStatus('Пожалуйста, выберите .py файл', true);
            }
        }
    });

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length) {
            const file = e.target.files[0];
            if (file.name.endsWith('.py')) {
                selectedFile = file;
                uploadZone.querySelector('p').textContent = '✅ ' + file.name;
                uploadZone.querySelector('.hint').textContent = 'Размер: ' + (file.size/1024).toFixed(1) + ' KB';
                showStatus('Файл ' + file.name + ' выбран');
            } else {
                showStatus('Пожалуйста, выберите .py файл', true);
                fileInput.value = '';
            }
        }
    });

    // === Отправка в GitHub ===
    document.getElementById('pushBtn').addEventListener('click', async () => {
        if (!selectedFile) {
            showStatus('Сначала выберите .py файл', true);
            return;
        }

        const btn = document.getElementById('pushBtn');
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-pulse"></i> Отправка...';

        const formData = new FormData();
        formData.append('file', selectedFile);

        try {
            const resp = await fetch('/api/push', {
                method: 'POST',
                body: formData
            });
            const data = await resp.json();
            if (data.status === 'success') {
                addConsole(getTime(), 'success', '✅ ' + data.message);
                showStatus('✅ ' + data.message);
                selectedFile = null;
                fileInput.value = '';
                uploadZone.querySelector('p').textContent = 'Выберите .py файл или перетащите сюда';
                uploadZone.querySelector('.hint').textContent = 'Файл будет сохранён и запущен';
                await loadScripts();
            } else {
                showStatus('❌ ' + data.message, true);
            }
        } catch (e) {
            addConsole(getTime(), 'error', '❌ ' + e.message);
            showStatus('❌ ' + e.message, true);
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="fab fa-github"></i> Отправить в GitHub';
        }
    });

    // === Синхронизация с GitHub ===
    document.getElementById('syncBtn').addEventListener('click', async () => {
        const btn = document.getElementById('syncBtn');
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-pulse"></i> Синхронизация...';
        addConsole(getTime(), 'info', 'Синхронизация с GitHub...');

        try {
            const resp = await fetch('/api/sync', { method: 'POST' });
            const data = await resp.json();
            addConsole(getTime(), 'success', '✅ Синхронизировано ' + data.count + ' файлов');
            showStatus('✅ Синхронизировано ' + data.count + ' файлов');
            await loadScripts();
        } catch (e) {
            addConsole(getTime(), 'error', '❌ ' + e.message);
            showStatus('❌ ' + e.message, true);
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-sync-alt"></i> Синхронизировать';
        }
    });

    // === Обновить статус ===
    document.getElementById('refreshBtn').addEventListener('click', async () => {
        addConsole(getTime(), 'info', 'Обновление статуса...');
        await loadScripts();
        showStatus('✅ Статус обновлён');
    });

    // === Инициализация ===
    loadScripts();
    addConsole(getTime(), 'info', 'Сервер запущен. Готов к работе.');

    // Автообновление каждые 30 секунд
    setInterval(loadScripts, 30000);
</script>
</body>
</html>
'''

# ============================================================
# API РОУТЫ
# ============================================================

@app.route('/')
def index():
    """Главная страница"""
    return render_template_string(
        HTML_TEMPLATE,
        repo_url=f"https://github.com/{GITHUB_REPO}"
    )

@app.route('/api/scripts')
def api_scripts():
    """Получение списка скриптов"""
    scripts = get_scripts_status()
    return jsonify({"scripts": scripts})

@app.route('/api/script/<name>/<action>', methods=['POST'])
def api_script_action(name, action):
    """Действие со скриптом"""
    if action == 'start':
        result = start_script(name)
    elif action == 'stop':
        result = stop_script(name)
    elif action == 'restart':
        result = restart_script(name)
    elif action == 'delete':
        result = delete_script(name)
    else:
        return jsonify({"status": "error", "message": f"Неизвестное действие: {action}"})
    
    return jsonify(result)

@app.route('/api/script/<name>/content')
def api_script_content(name):
    """Получение содержимого скрипта"""
    path = os.path.join(SCRIPT_DIR, name)
    if not os.path.exists(path):
        return jsonify({"status": "error", "message": "Файл не найден"})
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({"content": content})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/script/<name>/update', methods=['POST'])
def api_script_update(name):
    """Обновление содержимого скрипта"""
    data = request.get_json()
    content = data.get('content', '')
    
    path = os.path.join(SCRIPT_DIR, name)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({"status": "success", "message": f"Файл {name} обновлён"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/script/<name>/log')
def api_script_log(name):
    """Получение лога скрипта"""
    log_lines = get_script_log(name, 200)
    return jsonify({"log": log_lines})

@app.route('/api/push', methods=['POST'])
def api_push():
    """Загрузка файла в GitHub"""
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "Файл не выбран"})
    
    file = request.files['file']
    if not file.filename.endswith('.py'):
        return jsonify({"status": "error", "message": "Только .py файлы"})
    
    name = file.filename
    content = file.read().decode('utf-8')
    
    # Сохраняем локально
    path = os.path.join(SCRIPT_DIR, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    # Отправляем в GitHub
    try:
        import base64
        encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
        
        # Проверяем существование
        existing = github_request('GET', f'contents/{name}?ref={GITHUB_BRANCH}')
        sha = existing.get('sha') if existing and not isinstance(existing, dict) and 'error' not in existing else None
        
        payload = {
            'message': f'Добавлен скрипт {name}',
            'content': encoded,
            'branch': GITHUB_BRANCH
        }
        if sha:
            payload['sha'] = sha
        
        result = github_request('PUT', f'contents/{name}', payload)
        if result and 'error' in result:
            return jsonify({"status": "error", "message": result['error']})
        
        return jsonify({"status": "success", "message": f"Файл {name} отправлен в GitHub"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/sync', methods=['POST'])
def api_sync():
    """Синхронизация с GitHub"""
    count = sync_from_github()
    return jsonify({"status": "success", "count": count})

@app.route('/api/commit')
def api_commit():
    """Получение последнего коммита"""
    try:
        data = github_request('GET', f'commits/{GITHUB_BRANCH}')
        if data and not isinstance(data, dict) and 'error' in data:
            return jsonify({"sha": None})
        if data and data.get('sha'):
            return jsonify({"sha": data['sha']})
        return jsonify({"sha": None})
    except Exception as e:
        return jsonify({"sha": None, "error": str(e)})

@app.route('/api/health')
def api_health():
    """Проверка работоспособности"""
    return jsonify({
        "status": "ok",
        "scripts_count": len([f for f in os.listdir(SCRIPT_DIR) if f.endswith('.py')]),
        "running": len(running_processes)
    })

# ============================================================
# ЗАПУСК СЕРВЕРА
# ============================================================

def main():
    """Запуск сервера"""
    print("""
    ╔═══════════════════════════════════════════════════════════════╗
    ║           GitHub Script Runner — Сервер управления            ║
    ║   Загрузка, установка зависимостей и запуск Python скриптов   ║
    ╚═══════════════════════════════════════════════════════════════╝
    """)
    print(f"📁 Директория скриптов: {SCRIPT_DIR}")
    print(f"📦 Репозиторий: {GITHUB_REPO}")
    print(f"🌐 Сервер запущен: http://0.0.0.0:5000")
    print("=" * 60)
    
    # Синхронизация при старте
    print("🔄 Синхронизация с GitHub...")
    try:
        count = sync_from_github()
        print(f"✅ Синхронизировано {count} файлов")
    except Exception as e:
        print(f"⚠️ Ошибка синхронизации: {e}")
    
    print("\n📡 Доступные endpoints:")
    print("   GET  /                - Веб-интерфейс")
    print("   GET  /api/scripts     - Список скриптов")
    print("   POST /api/script/<name>/<action> - Управление скриптом")
    print("   GET  /api/script/<name>/content  - Содержимое скрипта")
    print("   POST /api/script/<name>/update   - Обновление скрипта")
    print("   GET  /api/script/<name>/log      - Лог скрипта")
    print("   POST /api/push        - Отправка файла в GitHub")
    print("   POST /api/sync        - Синхронизация с GitHub")
    print("   GET  /api/commit      - Последний коммит")
    print("   GET  /api/health      - Проверка статуса")
    print("=" * 60)
    
    # Запускаем сервер
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Сервер остановлен")
        # Останавливаем все процессы
        for name, process in list(running_processes.items()):
            try:
                process.terminate()
                process.wait(timeout=3)
                print(f"⏹ Остановлен: {name}")
            except:
                pass
        sys.exit(0)
