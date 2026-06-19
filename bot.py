#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Script Manager - Веб-сервер для управления Python скриптами
Аналог Telegram бота, но с веб-интерфейсом и интеграцией с GitHub
"""

import os
import re
import json
import subprocess
import importlib.util
import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template_string, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import requests
import threading
import time

# ============================================================
# НАСТРОЙКИ
# ============================================================
GITHUB_PAT = "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"
GITHUB_REPO = "HenonBank/Website"
GITHUB_BRANCH = "main"

# Директория для скриптов
SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
os.makedirs(SCRIPT_DIR, exist_ok=True)

# Настройки логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Глобальные переменные
running_processes = {}
monitoring_tasks = {}
monitor_counter = 0

BUILTIN_MODULES = {
    'os', 'sys', 're', 'json', 'datetime', 'sqlite3', 'logging',
    'subprocess', 'math', 'random', 'time', 'asyncio', 'threading',
    'collections', 'itertools', 'functools', 'hashlib', 'socket',
    'typing', 'pathlib', 'urllib', 'ssl', 'email', 'csv', 'io',
    'argparse', 'getpass', 'shutil', 'tempfile', 'glob'
}

# ============================================================
# ФУНКЦИИ РАБОТЫ С GITHUB
# ============================================================

def github_request(method, endpoint, data=None, raw=False):
    """Выполнение запроса к GitHub API"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/{endpoint}"
    headers = {
        'Authorization': f'token {GITHUB_PAT}',
        'Accept': 'application/vnd.github.v3+json'
    }
    
    if method == 'GET':
        resp = requests.get(url, headers=headers)
    elif method == 'PUT':
        resp = requests.put(url, headers=headers, json=data)
    elif method == 'DELETE':
        resp = requests.delete(url, headers=headers, json=data)
    else:
        raise ValueError(f"Unsupported method: {method}")
    
    if raw:
        return resp
    return resp.json() if resp.ok else None

def get_github_files():
    """Получение списка .py файлов из GitHub репозитория"""
    try:
        data = github_request('GET', f'contents?ref={GITHUB_BRANCH}')
        if not data:
            return []
        return [f for f in data if f.get('name', '').endswith('.py') and f.get('type') == 'file']
    except Exception as e:
        logger.error(f"Error fetching GitHub files: {e}")
        return []

def get_github_file_content(path):
    """Получение содержимого файла из GitHub"""
    try:
        data = github_request('GET', f'contents/{path}?ref={GITHUB_BRANCH}')
        if not data:
            return None
        import base64
        content = base64.b64decode(data.get('content', '')).decode('utf-8')
        return content
    except Exception as e:
        logger.error(f"Error fetching file content: {e}")
        return None

def push_to_github(path, content, message=None):
    """Отправка файла в GitHub"""
    try:
        # Проверяем, существует ли файл
        existing = github_request('GET', f'contents/{path}?ref={GITHUB_BRANCH}')
        sha = existing.get('sha') if existing else None
        
        import base64
        encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
        
        payload = {
            'message': message or f'Обновлён скрипт {path}',
            'content': encoded,
            'branch': GITHUB_BRANCH
        }
        if sha:
            payload['sha'] = sha
        
        result = github_request('PUT', f'contents/{path}', payload)
        return result
    except Exception as e:
        logger.error(f"Error pushing to GitHub: {e}")
        return None

def delete_from_github(path):
    """Удаление файла из GitHub"""
    try:
        existing = github_request('GET', f'contents/{path}?ref={GITHUB_BRANCH}')
        if not existing:
            return False
        
        payload = {
            'message': f'Удалён скрипт {path}',
            'sha': existing.get('sha'),
            'branch': GITHUB_BRANCH
        }
        
        result = github_request('DELETE', f'contents/{path}', payload)
        return result is not None
    except Exception as e:
        logger.error(f"Error deleting from GitHub: {e}")
        return False

def sync_from_github():
    """Синхронизация локальной папки с GitHub"""
    files = get_github_files()
    for f in files:
        path = f.get('name')
        content = get_github_file_content(path)
        if content:
            local_path = os.path.join(SCRIPT_DIR, path)
            with open(local_path, 'w', encoding='utf-8') as fp:
                fp.write(content)
            logger.info(f"Synced: {path}")
    return len(files)

# ============================================================
# ФУНКЦИИ УПРАВЛЕНИЯ СКРИПТАМИ
# ============================================================

def is_package_installed(package_name):
    """Проверка установки пакета"""
    return importlib.util.find_spec(package_name) is not None

def install_deps(code):
    """Установка зависимостей"""
    imports = re.findall(r'^\s*import\s+(\w+)|^\s*from\s+(\w+)', code, re.M)
    packages = {imp[0] or imp[1] for imp in imports if imp and (imp[0] or imp[1]) not in BUILTIN_MODULES}
    
    if not packages:
        return "Нет внешних зависимостей"
    
    to_install = [pkg for pkg in packages if not is_package_installed(pkg)]
    if not to_install:
        return f"Все зависимости установлены: {', '.join(packages)}"
    
    try:
        subprocess.run(["pip", "install", *to_install], capture_output=True, check=True)
        return f"Установлены: {', '.join(to_install)}"
    except subprocess.CalledProcessError as e:
        return f"Ошибка установки: {e.stderr.decode('utf-8') if e.stderr else 'Неизвестная ошибка'}"

def has_dangerous_code(code):
    """Проверка на опасные команды"""
    dangerous = [
        "os.system", "subprocess.run", "eval(", "exec(",
        "__import__", "compile(", "globals()", "locals()"
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
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            code = f.read()
        
        if has_dangerous_code(code):
            return {"status": "error", "message": "Обнаружены опасные команды! Запуск отменён"}
        
        dep_msg = install_deps(code)
        process = subprocess.Popen(["python3", file_path])
        running_processes[filename] = process
        
        return {
            "status": "success",
            "message": f"Скрипт {filename} запущен!",
            "pid": process.pid,
            "deps": dep_msg
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
        process.terminate()
        process.wait(timeout=5)
        del running_processes[filename]
        return {"status": "success", "message": f"Скрипт {filename} остановлен"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def restart_script(filename):
    """Перезапуск скрипта"""
    stop_script(filename)
    return start_script(filename)

def delete_script(filename):
    """Удаление скрипта"""
    file_path = os.path.join(SCRIPT_DIR, filename)
    if not os.path.exists(file_path):
        return {"status": "error", "message": f"Файл {filename} не найден"}
    
    try:
        os.remove(file_path)
        if filename in running_processes:
            stop_script(filename)
        return {"status": "success", "message": f"Файл {filename} удалён"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def get_scripts_status():
    """Получение статуса всех скриптов"""
    result = []
    files = [f for f in os.listdir(SCRIPT_DIR) if f.endswith('.py')]
    
    for f in files:
        status = "stopped"
        pid = None
        if f in running_processes:
            process = running_processes[f]
            if process.poll() is None:
                status = "running"
                pid = process.pid
            else:
                del running_processes[f]
        
        result.append({
            "name": f,
            "status": status,
            "pid": pid
        })
    
    return result

# ============================================================
# FLASK ПРИЛОЖЕНИЕ
# ============================================================

app = Flask(__name__)
CORS(app)

# HTML шаблон (упрощенная версия, но полностью рабочая)
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GitHub Script Manager</title>
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
        .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
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
        .script-list { display: flex; flex-direction: column; gap: 8px; }
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
        .script-item .name { flex: 2; font-weight: 500; color: #D6E0F0; }
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
        .actions { display: flex; gap: 4px; margin-left: auto; }
        .actions button {
            background: transparent;
            border: none;
            color: #5F6A7A;
            width: 32px;
            height: 32px;
            border-radius: 50%;
            cursor: pointer;
            transition: 0.2s;
        }
        .actions button:hover { background: #1E2530; color: #D6E0F0; }
        .actions .play { color: #4ADE80; }
        .actions .play:hover { background: rgba(74,222,128,0.12); }
        .actions .stop { color: #F87171; }
        .actions .stop:hover { background: rgba(248,113,113,0.12); }
        .actions .edit { color: #60A5FA; }
        .actions .edit:hover { background: rgba(96,165,250,0.1); }
        .actions .delete { color: #F87171; }
        .actions .delete:hover { background: rgba(248,113,113,0.12); }
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
            max-width: 600px;
            width: 90%;
            border: 1px solid rgba(255,255,255,0.06);
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
        .flex { display: flex; gap: 12px; }
        .flex-1 { flex: 1; }
        @media (max-width: 768px) {
            .header h1 { font-size: 1.3rem; }
            .stats { grid-template-columns: repeat(2, 1fr); }
            .script-item { flex-wrap: wrap; }
            .actions { margin-left: 0; width: 100%; justify-content: flex-end; }
        }
    </style>
</head>
<body>
<div class="container">

    <!-- HEADER -->
    <div class="header">
        <h1><i class="fas fa-code"></i> GitHub Script Manager</h1>
        <div class="badge"><i class="fas fa-circle"></i> Система активна</div>
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
                    <div class="hint">Файл будет отправлен в GitHub репозиторий</div>
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
                <button class="btn-secondary" id="syncBtn">
                    <i class="fas fa-sync-alt"></i> Синхронизировать с GitHub
                </button>
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
                        <span>Готов к работе</span>
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
            <button class="close" onclick="closeModal()">&times;</button>
        </div>
        <div class="modal-body">
            <p style="color: #8E9AAF; font-size: 0.85rem; margin-bottom: 10px;" id="editFileName">main.py</p>
            <textarea id="editContent"></textarea>
        </div>
        <div class="modal-footer">
            <button class="btn-secondary" onclick="closeModal()">Отмена</button>
            <button class="btn-primary" id="saveEditBtn">
                <i class="fas fa-save"></i> Сохранить
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
    let successCount = 0;
    let errorCount = 0;

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
        setTimeout(() => { el.innerHTML = ''; }, 8000);
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
                row.innerHTML = `
                    <div class="name"><i class="fas fa-file-code"></i> ${s.name}</div>
                    <div class="status">
                        <span class="dot ${statusClass}"></span>
                        <span class="status-text ${statusClass}">${statusText}</span>
                        ${s.pid ? `(PID: ${s.pid})` : ''}
                    </div>
                    <span class="type">Скрипт</span>
                    <div class="actions">
                        <button class="play" onclick="actionScript('${s.name}', 'start')" title="Запустить">
                            <i class="fas fa-play"></i>
                        </button>
                        <button class="stop" onclick="actionScript('${s.name}', 'stop')" title="Остановить">
                            <i class="fas fa-pause"></i>
                        </button>
                        <button class="edit" onclick="editScript('${s.name}')" title="Редактировать">
                            <i class="fas fa-edit"></i>
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

            // Обновляем последний коммит
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
        try {
            const resp = await fetch(`/api/script/${name}/${action}`, { method: 'POST' });
            const data = await resp.json();
            addConsole(getTime(), data.status === 'success' ? 'success' : 'error', data.message);
            if (data.status === 'success') {
                if (action === 'start') successCount++;
                if (action === 'delete') errorCount = 0;
                showStatus(data.message);
            } else {
                errorCount++;
                showStatus(data.message, true);
            }
            document.getElementById('successCount').textContent = successCount;
            document.getElementById('errorCount').textContent = errorCount;
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

    function closeModal() {
        document.getElementById('editModal').classList.remove('active');
        editingFile = null;
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
                successCount++;
                document.getElementById('successCount').textContent = successCount;
                closeModal();
                await loadScripts();
            } else {
                showStatus('Ошибка: ' + data.message, true);
            }
        } catch (e) {
            showStatus('Ошибка: ' + e.message, true);
        }
    });

    // Закрытие модалки по клику вне
    document.getElementById('editModal').addEventListener('click', (e) => {
        if (e.target === e.currentTarget) closeModal();
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
                successCount++;
                document.getElementById('successCount').textContent = successCount;
                selectedFile = null;
                fileInput.value = '';
                uploadZone.querySelector('p').textContent = 'Выберите .py файл или перетащите сюда';
                uploadZone.querySelector('.hint').textContent = 'Файл будет отправлен в GitHub репозиторий';
                await loadScripts();
            } else {
                showStatus('❌ ' + data.message, true);
                errorCount++;
                document.getElementById('errorCount').textContent = errorCount;
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
            btn.innerHTML = '<i class="fas fa-sync-alt"></i> Синхронизировать с GitHub';
        }
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
        if result['status'] == 'success':
            # Удаляем из GitHub тоже
            delete_from_github(name)
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
        
        # Отправляем в GitHub
        result = push_to_github(name, content, f"Обновлён скрипт {name}")
        if result:
            return jsonify({"status": "success", "message": f"Файл {name} обновлён"})
        else:
            return jsonify({"status": "error", "message": "Ошибка отправки в GitHub"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

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
    result = push_to_github(name, content, f"Добавлен скрипт {name}")
    if result:
        return jsonify({"status": "success", "message": f"Файл {name} отправлен в GitHub"})
    else:
        return jsonify({"status": "error", "message": "Ошибка отправки в GitHub"})

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
        if data and data.get('sha'):
            return jsonify({"sha": data['sha']})
        return jsonify({"sha": None})
    except Exception as e:
        return jsonify({"sha": None, "error": str(e)})

@app.route('/api/download/<name>')
def api_download(name):
    """Скачивание скрипта"""
    path = os.path.join(SCRIPT_DIR, name)
    if not os.path.exists(path):
        return jsonify({"status": "error", "message": "Файл не найден"})
    return send_file(path, as_attachment=True)

# ============================================================
# ЗАПУСК
# ============================================================

def main():
    """Запуск сервера"""
    print("""
    ╔═══════════════════════════════════════════════════════════════╗
    ║           GitHub Script Manager — Веб-сервер                  ║
    ║   Управление Python скриптами с интеграцией GitHub           ║
    ╚═══════════════════════════════════════════════════════════════╝
    """)
    print(f"📁 Директория скриптов: {SCRIPT_DIR}")
    print(f"📦 Репозиторий: {GITHUB_REPO}")
    print(f"🌐 Сервер запущен: http://0.0.0.0:5000")
    print("=" * 60)
    
    # Синхронизация при старте
    print("🔄 Синхронизация с GitHub...")
    count = sync_from_github()
    print(f"✅ Синхронизировано {count} файлов")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

if __name__ == '__main__':
    main()
