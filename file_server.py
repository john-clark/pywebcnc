import os
import io
import re
import stat
import math
import shutil
import asyncio
import zipfile
import pathlib
import datetime
import mimetypes
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

# ---------------------------------------------------------------------------
# Server / App Setup
# ---------------------------------------------------------------------------
app = FastAPI(title="Local LAN File Manager")

# Root directory for file server - defaults to current working directory
ROOT_DIR = os.path.abspath(os.getcwd())

def safe_path(rel_path: str) -> str:
    """
    Resolves relative path against ROOT_DIR and strictly prevents directory traversal.
    """
    if not rel_path or rel_path == "/":
        return ROOT_DIR
    clean_path = rel_path.lstrip("/\\")
    target_path = os.path.abspath(os.path.join(ROOT_DIR, clean_path))
    if not target_path.startswith(ROOT_DIR):
        raise HTTPException(status_code=403, detail="Access denied: Directory traversal detected")
    return target_path

def get_file_metadata(path: str, rel_path: str):
    """Generates detailed file metadata dictionary."""
    st = os.stat(path)
    mode = st.st_mode
    is_dir = stat.S_ISDIR(mode)
    
    owner = "unknown"
    try:
        import pwd
        owner = pwd.getpwuid(st.st_uid).pw_name
    except Exception:
        owner = str(st.st_uid)

    return {
        "name": os.path.basename(path) or "/",
        "path": rel_path,
        "is_dir": is_dir,
        "size": st.st_size if not is_dir else 0,
        "modified": datetime.datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
        "permissions": oct(mode & 0o777)[2:],
        "owner": owner,
        "extension": os.path.splitext(path)[1].lower() if not is_dir else ""
    }

# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/directory")
async def list_directory(path: str = Query("")):
    target = safe_path(path)
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="Path not found")
    if not os.path.isdir(target):
        raise HTTPException(status_code=400, detail="Path is not a directory")

    items = []
    try:
        for entry in os.scandir(target):
            entry_rel = os.path.relpath(entry.path, ROOT_DIR).replace("\\", "/")
            items.append(get_file_metadata(entry.path, "/" + entry_rel))
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied reading directory")

    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return {"path": path or "/", "items": items}

@app.get("/api/file")
async def read_file(path: str = Query("")):
    """
    Returns file metadata and content (if under 10MB) or an indicator if the file
    is large and should be streamed via /api/file-stream.
    Always returns JSON.
    """
    target = safe_path(path)
    if not os.path.exists(target) or os.path.isdir(target):
        raise HTTPException(status_code=404, detail="File not found")
    
    st = os.stat(target)
    meta = get_file_metadata(target, path)
    
    # If file is larger than 10MB, return JSON with a flag indicating it is large
    if st.st_size > 10 * 1024 * 1024:
        return JSONResponse({"is_large": True, "content": None, "meta": meta})
    
    try:
        with open(target, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        return JSONResponse({"is_large": False, "content": content, "meta": meta})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/file-stream")
async def stream_file(path: str = Query("")):
    """
    Dedicated endpoint to stream raw file content chunk-by-chunk for large files.
    """
    target = safe_path(path)
    if not os.path.exists(target) or os.path.isdir(target):
        raise HTTPException(status_code=404, detail="File not found")
    
    def iterfile():
        with open(target, 'rb') as f:
            while chunk := f.read(64 * 1024):
                yield chunk

    return StreamingResponse(iterfile(), media_type="text/plain; charset=utf-8")

@app.put("/api/file")
async def save_file(request: Request):
    data = await request.json()
    rel_path = data.get("path", "")
    content = data.get("content", "")
    target = safe_path(rel_path)
    
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'w', encoding='utf-8') as f:
            f.write(content)
        return {"status": "success", "meta": get_file_metadata(target, rel_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/upload")
async def upload_file(files: List[UploadFile] = File(...), paths: List[str] = Form(...)):
    uploaded = []
    for file, target_rel in zip(files, paths):
        target = safe_path(target_rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as f:
            while chunk := await file.read(64 * 1024):
                f.write(chunk)
        uploaded.append(target_rel)
    return {"status": "success", "uploaded": uploaded}

@app.delete("/api/file")
async def delete_item(path: str = Query("")):
    target = safe_path(path)
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="Item not found")
    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/move")
async def move_item(request: Request):
    data = await request.json()
    src = safe_path(data.get("src", ""))
    dst = safe_path(data.get("dst", ""))
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/copy")
async def copy_item(request: Request):
    data = await request.json()
    src = safe_path(data.get("src", ""))
    dst = safe_path(data.get("dst", ""))
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/mkdir")
async def make_directory(request: Request):
    data = await request.json()
    target = safe_path(data.get("path", ""))
    try:
        os.makedirs(target, exist_ok=True)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/newfile")
async def new_file(request: Request):
    data = await request.json()
    target = safe_path(data.get("path", ""))
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'a') as f:
            pass
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/search")
async def search_files(
    query: str = Query(...),
    path: str = Query(""),
    is_content: bool = Query(False),
    is_regex: bool = Query(False),
    case_sensitive: bool = Query(False),
    whole_word: bool = Query(False)
):
    target_base = safe_path(path)
    results = []
    
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern_str = query
    if whole_word and not is_regex:
        pattern_str = r'\b' + re.escape(query) + r'\b'
    elif not is_regex:
        pattern_str = re.escape(query)

    try:
        regex = re.compile(pattern_str, flags)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {str(e)}")

    for root, dirs, files in os.walk(target_base):
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = "/" + os.path.relpath(full_path, ROOT_DIR).replace("\\", "/")
            
            if not is_content:
                if regex.search(file):
                    results.append({"path": rel_path, "name": file, "match": "Filename match"})
            else:
                try:
                    with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                results.append({
                                    "path": rel_path,
                                    "name": file,
                                    "line": line_num,
                                    "match": line.strip()[:100]
                                })
                                if len(results) >= 200: # Limit results
                                    break
                except Exception:
                    continue
        if len(results) >= 200:
            break

    return {"results": results}

@app.get("/api/download")
async def download_file(path: str = Query("")):
    target = safe_path(path)
    if not os.path.exists(target) or os.path.isdir(target):
        raise HTTPException(status_code=404, detail="File not found")
    
    filename = os.path.basename(target)
    return StreamingResponse(
        open(target, 'rb'),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\""}
    )

@app.get("/api/zip")
async def download_zip(path: str = Query("")):
    target = safe_path(path)
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="Path not found")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        if os.path.isfile(target):
            zip_file.write(target, os.path.basename(target))
        else:
            for root, dirs, files in os.walk(target):
                for file in files:
                    full_path = os.path.join(root, file)
                    arcname = os.path.relpath(full_path, target)
                    zip_file.write(full_path, arcname)

    zip_buffer.seek(0)
    folder_name = os.path.basename(target.rstrip("/\\")) or "archive"
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=\"{folder_name}.zip\""}
    )

# ---------------------------------------------------------------------------
# Single-Page UI App Front-End (HTML/CSS/JS)
# ---------------------------------------------------------------------------

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>File Manager - Dark</title>
    <!-- CDN Resources with automatic inline fallbacks -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.39.0/min/vs/loader.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/marked/4.3.0/marked.min.js"></script>
    <style>
        :root {
            --bg-dark: #121316;
            --bg-panel: #1a1c23;
            --bg-hover: #262933;
            --bg-active: #323644;
            --accent: #2196f3;
            --accent-hover: #1e88e5;
            --text-main: #e1e4ea;
            --text-muted: #8b949e;
            --border: #2d313e;
            --danger: #f44336;
            --success: #4caf50;
            --warning: #ff9800;
            --font-mono: 'Consolas', 'Courier New', monospace;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; user-select: none; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-dark);
            color: var(--text-main);
            height: 100vh;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }

        /* Top Bar */
        header {
            height: 48px;
            background: var(--bg-panel);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 16px;
        }
        .logo { font-size: 16px; font-weight: bold; color: var(--accent); display: flex; align-items: center; gap: 8px; }
        .toolbar { display: flex; gap: 8px; align-items: center; }

        /* Buttons & Inputs */
        button {
            background: var(--bg-hover);
            color: var(--text-main);
            border: 1px solid var(--border);
            padding: 6px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 13px;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: all 0.2s;
        }
        button:hover { background: var(--bg-active); border-color: var(--accent); }
        button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
        button.primary:hover { background: var(--accent-hover); }
        input[type="text"], select {
            background: var(--bg-dark);
            color: var(--text-main);
            border: 1px solid var(--border);
            padding: 6px 10px;
            border-radius: 4px;
            font-size: 13px;
            outline: none;
        }
        input[type="text"]:focus { border-color: var(--accent); }

        /* Main Workspace Grid */
        .workspace {
            flex: 1;
            display: flex;
            overflow: hidden;
            position: relative;
        }

        .panel {
            background: var(--bg-panel);
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        /* Resizer Dividers */
        .resizer {
            width: 4px;
            background: var(--border);
            cursor: col-resize;
            transition: background 0.2s;
            z-index: 10;
        }
        .resizer:hover, .resizer.dragging { background: var(--accent); }

        /* Left Panel - Directory Tree & Search */
        #left-panel { width: 280px; min-width: 200px; border-right: 1px solid var(--border); }
        .panel-header {
            padding: 10px 12px;
            background: rgba(0,0,0,0.15);
            border-bottom: 1px solid var(--border);
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .tree-container { flex: 1; overflow-y: auto; padding: 6px 0; }
        .tree-item {
            display: flex;
            align-items: center;
            padding: 4px 12px;
            cursor: pointer;
            font-size: 13px;
            white-space: nowrap;
            user-select: none;
            gap: 6px;
        }
        .tree-item:hover { background: var(--bg-hover); }
        .tree-item.selected { background: var(--bg-active); color: var(--accent); }
        .tree-indent { display: inline-block; width: 14px; }
        .icon { width: 16px; text-align: center; display: inline-block; opacity: 0.8; }

        /* Search Drawer inside Left Panel */
        .search-box { padding: 10px; border-bottom: 1px solid var(--border); background: var(--bg-dark); }
        .search-options { display: flex; gap: 8px; margin-top: 6px; font-size: 11px; color: var(--text-muted); flex-wrap: wrap; }
        .search-options label { display: flex; align-items: center; gap: 4px; cursor: pointer; }
        .search-results { max-height: 200px; overflow-y: auto; background: var(--bg-dark); font-size: 12px; }
        .search-result-item { padding: 6px 10px; border-bottom: 1px solid var(--border); cursor: pointer; }
        .search-result-item:hover { background: var(--bg-hover); }

        /* Center Panel - Editor / Preview */
        #center-panel { flex: 1; min-width: 300px; display: flex; flex-direction: column; background: var(--bg-dark); }
        .editor-tabs {
            display: flex;
            background: var(--bg-panel);
            border-bottom: 1px solid var(--border);
            overflow-x: auto;
        }
        .tab {
            padding: 8px 16px;
            font-size: 13px;
            background: rgba(0,0,0,0.2);
            border-right: 1px solid var(--border);
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 8px;
            max-width: 200px;
        }
        .tab.active { background: var(--bg-dark); color: var(--accent); border-top: 2px solid var(--accent); }
        .tab .unsaved { width: 8px; height: 8px; border-radius: 50%; background: var(--warning); display: none; }
        .tab.has-unsaved .unsaved { display: inline-block; }

        .editor-container { flex: 1; position: relative; width: 100%; height: 100%; }
        #monaco-target, #fallback-editor, #image-preview, #markdown-preview {
            position: absolute; top: 0; left: 0; right: 0; bottom: 0; width: 100%; height: 100%;
        }
        #fallback-editor {
            width: 100%; height: 100%; background: var(--bg-dark); color: var(--text-main);
            border: none; padding: 12px; font-family: var(--font-mono); font-size: 14px; outline: none; resize: none;
            display: none;
        }
        #image-preview { display: none; justify-content: center; align-items: center; padding: 20px; overflow: auto; }
        #image-preview img { max-width: 100%; max-height: 100%; object-fit: contain; }
        #markdown-preview { display: none; padding: 24px; overflow-y: auto; background: var(--bg-dark); line-height: 1.6; }

        /* Right Panel - Metadata Properties */
        #right-panel { width: 260px; min-width: 180px; border-left: 1px solid var(--border); }
        .prop-group { padding: 12px; border-bottom: 1px solid var(--border); }
        .prop-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 4px; }
        .prop-value { font-size: 13px; word-break: break-all; }

        /* Context Menu */
        .context-menu {
            position: fixed;
            background: var(--bg-panel);
            border: 1px solid var(--border);
            box-shadow: 0 4px 12px rgba(0,0,0,0.5);
            border-radius: 4px;
            padding: 4px 0;
            z-index: 1000;
            display: none;
            min-width: 160px;
        }
        .context-menu-item {
            padding: 8px 16px;
            font-size: 13px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .context-menu-item:hover { background: var(--accent); color: #fff; }

        /* Loading Spinner & Upload Progress */
        .overlay-spinner {
            position: absolute; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.6); display: none; justify-content: center; align-items: center; z-index: 500;
        }
        .spinner {
            width: 40px; height: 40px; border: 4px solid var(--border); border-top: 4px solid var(--accent);
            border-radius: 50%; animation: spin 1s linear infinite;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }

        .upload-bar {
            position: fixed; bottom: 20px; right: 20px; background: var(--bg-panel);
            border: 1px solid var(--border); padding: 12px 16px; border-radius: 6px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.4); display: none; width: 300px; z-index: 900;
        }
        .progress-inner { height: 6px; background: var(--accent); width: 0%; border-radius: 3px; transition: width 0.2s; }
        .progress-outer { height: 6px; background: var(--border); border-radius: 3px; margin-top: 8px; overflow: hidden; }

        /* Modals */
        .modal {
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.7); display: none; justify-content: center; align-items: center; z-index: 2000;
        }
        .modal-content {
            background: var(--bg-panel); border: 1px solid var(--border); border-radius: 6px;
            width: 400px; padding: 20px; display: flex; flex-direction: column; gap: 12px;
        }
        .modal-buttons { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
    </style>
</head>
<body>

    <header>
        <div class="logo">⚡ File Manager</div>
        <div class="toolbar">
            <button onclick="createNewFile()"><span class="icon">📄</span> New File</button>
            <button onclick="createNewFolder()"><span class="icon">📁</span> New Folder</button>
            <button onclick="triggerUpload()"><span class="icon">📤</span> Upload</button>
            <button onclick="refreshTree()"><span class="icon">🔄</span> Refresh</button>
            <button class="primary" onclick="saveCurrentFile()"><span class="icon">💾</span> Save (Ctrl+S)</button>
        </div>
    </header>

    <div class="workspace" id="drag-drop-zone">
        
        <!-- Left Panel: Folder Tree & Search -->
        <div class="panel" id="left-panel">
            <div class="panel-header">
                <span>Files</span>
                <span id="root-path-label">/</span>
            </div>
            
            <div class="search-box">
                <input type="text" id="search-input" placeholder="Search..." style="width:100%" onkeyup="if(event.key==='Enter') executeSearch()">
                <div class="search-options">
                    <label><input type="checkbox" id="search-content"> Content</label>
                    <label><input type="checkbox" id="search-regex"> Regex</label>
                    <label><input type="checkbox" id="search-case"> Match Case</label>
                    <label><input type="checkbox" id="search-word"> Whole Word</label>
                </div>
            </div>
            <div class="search-results" id="search-results"></div>

            <div class="tree-container" id="tree-container"></div>
        </div>

        <div class="resizer" id="resizer-1"></div>

        <!-- Center Panel: Monaco / Fallback / Image / MD -->
        <div class="panel" id="center-panel">
            <div class="editor-tabs" id="editor-tabs">
                <div class="tab active" id="current-tab">
                    <span id="tab-title">No File Open</span>
                    <span class="unsaved"></span>
                </div>
            </div>
            
            <div class="editor-container">
                <div id="monaco-target"></div>
                <textarea id="fallback-editor" oninput="markUnsaved()"></textarea>
                <div id="image-preview"><img id="img-target" src="" alt="preview"></div>
                <div id="markdown-preview"></div>
                
                <div class="overlay-spinner" id="loading-spinner">
                    <div class="spinner"></div>
                </div>
            </div>
        </div>

        <div class="resizer" id="resizer-2"></div>

        <!-- Right Panel: Properties -->
        <div class="panel" id="right-panel">
            <div class="panel-header">Properties</div>
            <div class="prop-group">
                <div class="prop-label">Filename</div>
                <div class="prop-value" id="prop-name">-</div>
            </div>
            <div class="prop-group">
                <div class="prop-label">Full Path</div>
                <div class="prop-value" id="prop-path">-</div>
            </div>
            <div class="prop-group">
                <div class="prop-label">Size</div>
                <div class="prop-value" id="prop-size">-</div>
            </div>
            <div class="prop-group">
                <div class="prop-label">Modified</div>
                <div class="prop-value" id="prop-modified">-</div>
            </div>
            <div class="prop-group">
                <div class="prop-label">Permissions</div>
                <div class="prop-value" id="prop-perms">-</div>
            </div>
            <div class="prop-group">
                <div class="prop-label">Owner</div>
                <div class="prop-value" id="prop-owner">-</div>
            </div>
        </div>
    </div>

    <!-- Context Menu -->
    <div class="context-menu" id="context-menu">
        <div class="context-menu-item" onclick="ctxOpen()"><span class="icon">📖</span> Open</div>
        <div class="context-menu-item" onclick="ctxDownload()"><span class="icon">⬇️</span> Download</div>
        <div class="context-menu-item" onclick="ctxRename()"><span class="icon">✏️</span> Rename</div>
        <div class="context-menu-item" onclick="ctxDuplicate()"><span class="icon">📋</span> Duplicate</div>
        <div class="context-menu-item" onclick="ctxDelete()"><span class="icon">🗑️</span> Delete</div>
    </div>

    <!-- Upload Progress Bar -->
    <div class="upload-bar" id="upload-bar">
        <div style="font-size:13px; font-weight:600;" id="upload-status">Uploading files...</div>
        <div class="progress-outer"><div class="progress-inner" id="upload-progress"></div></div>
    </div>

    <!-- General Input Modal -->
    <div class="modal" id="input-modal">
        <div class="modal-content">
            <h3 id="modal-title">Input</h3>
            <input type="text" id="modal-input">
            <div class="modal-buttons">
                <button onclick="closeModal()">Cancel</button>
                <button class="primary" id="modal-confirm">Confirm</button>
            </div>
        </div>
    </div>

    <input type="file" id="file-input" multiple style="display:none;" onchange="handleFileSelect(event)">

    <script>
        // App State
        let currentFile = null;
        let activeTreeItem = null;
        let isUnsaved = false;
        let monacoEditor = null;
        let isMonacoLoaded = false;
        let expandedFolders = new Set(['/']);

        // Initialize Monaco Editor with CDN / Fallback handling
        function initEditor() {
            if (typeof require !== 'undefined') {
                require.config({ paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.39.0/min/vs' }});
                require(['vs/editor/editor.main'], function() {
                    monacoEditor = monaco.editor.create(document.getElementById('monaco-target'), {
                        value: '',
                        language: 'plaintext',
                        theme: 'vs-dark',
                        automaticLayout: true,
                        fontFamily: 'Consolas, monospace',
                        fontSize: 13
                    });
                    
                    monacoEditor.onDidChangeModelContent(() => {
                        markUnsaved();
                    });

                    // Ctrl+S Keyboard Shortcut
                    monacoEditor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, function() {
                        saveCurrentFile();
                    });

                    isMonacoLoaded = true;
                }, function(err) {
                    // Fallback to embedded plain textarea
                    useFallbackEditor();
                });
            } else {
                useFallbackEditor();
            }
        }

        function useFallbackEditor() {
            document.getElementById('monaco-target').style.display = 'none';
            document.getElementById('fallback-editor').style.display = 'block';
        }

        function setEditorValue(val, ext) {
            hidePreviews();
            if (isMonacoLoaded && monacoEditor) {
                document.getElementById('monaco-target').style.display = 'block';
                const lang = getLanguageFromExtension(ext);
                monaco.editor.setModelLanguage(monacoEditor.getModel(), lang);
                monacoEditor.setValue(val);
            } else {
                const fb = document.getElementById('fallback-editor');
                fb.style.display = 'block';
                fb.value = val;
            }
            isUnsaved = false;
            updateTabState();
        }

        function getEditorValue() {
            if (isMonacoLoaded && monacoEditor && document.getElementById('monaco-target').style.display !== 'none') {
                return monacoEditor.getValue();
            }
            return document.getElementById('fallback-editor').value;
        }

        function getLanguageFromExtension(ext) {
            const map = {
                'js': 'javascript', 'ts': 'typescript', 'py': 'python', 'html': 'html',
                'css': 'css', 'json': 'json', 'md': 'markdown', 'sh': 'shell', 'yaml': 'yaml',
                'yml': 'yaml', 'xml': 'xml', 'c': 'c', 'cpp': 'cpp', 'rs': 'rust'
            };
            return map[ext.replace('.', '')] || 'plaintext';
        }

        function hidePreviews() {
            document.getElementById('monaco-target').style.display = 'none';
            document.getElementById('fallback-editor').style.display = 'none';
            document.getElementById('image-preview').style.display = 'none';
            document.getElementById('markdown-preview').style.display = 'none';
        }

        // Tree View Rendering
        async function loadDirectory(path = '') {
            showSpinner(true);
            try {
                const res = await fetch(`/api/directory?path=${encodeURIComponent(path)}`);
                if (!res.ok) throw new Error(await res.text());
                return await res.json();
            } catch (err) {
                alert('Error loading directory: ' + err.message);
            } finally {
                showSpinner(false);
            }
        }

        async function refreshTree() {
            const container = document.getElementById('tree-container');
            container.innerHTML = '';
            const rootData = await loadDirectory('/');
            if (rootData) {
                renderTreeNodes(rootData.items, container, 0);
            }
        }

        async function renderTreeNodes(items, parentEl, depth) {
            for (const item of items) {
                const row = document.createElement('div');
                row.className = 'tree-item';
                row.dataset.path = item.path;
                row.dataset.isDir = item.is_dir;

                // Indentation
                for (let i = 0; i < depth; i++) {
                    const indent = document.createElement('span');
                    indent.className = 'tree-indent';
                    row.appendChild(indent);
                }

                // Expand/Collapse Icon or File Icon
                const icon = document.createElement('span');
                icon.className = 'icon';
                if (item.is_dir) {
                    icon.textContent = expandedFolders.has(item.path) ? '📂' : '📁';
                } else {
                    icon.textContent = getFileIcon(item.extension);
                }
                row.appendChild(icon);

                // Label
                const label = document.createElement('span');
                label.textContent = item.name;
                row.appendChild(label);

                // Selection & Double click
                row.onclick = (e) => {
                    e.stopPropagation();
                    selectTreeItem(row, item);
                    if (item.is_dir) toggleFolder(item.path);
                };

                row.ondblclick = (e) => {
                    e.stopPropagation();
                    if (!item.is_dir) openFile(item.path, item.extension);
                };

                // Right click Context Menu
                row.oncontextmenu = (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    selectTreeItem(row, item);
                    showContextMenu(e.clientX, e.clientY);
                };

                parentEl.appendChild(row);

                // Render children recursively if folder is expanded
                if (item.is_dir && expandedFolders.has(item.path)) {
                    const childData = await loadDirectory(item.path);
                    if (childData && childData.items) {
                        const childContainer = document.createElement('div');
                        parentEl.appendChild(childContainer);
                        await renderTreeNodes(childData.items, childContainer, depth + 1);
                    }
                }
            }
        }

        function getFileIcon(ext) {
            if (['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp'].includes(ext)) return '🖼️';
            if (['.md', '.txt'].includes(ext)) return '📝';
            if (['.py', '.js', '.ts', '.html', '.css', '.json'].includes(ext)) return '💻';
            return '📄';
        }

        async function toggleFolder(path) {
            if (expandedFolders.has(path)) {
                expandedFolders.delete(path);
            } else {
                expandedFolders.add(path);
            }
            refreshTree();
        }

        function selectTreeItem(element, meta) {
            if (activeTreeItem) activeTreeItem.classList.remove('selected');
            element.classList.add('selected');
            activeTreeItem = element;
            updateProperties(meta);
        }

        function updateProperties(meta) {
            document.getElementById('prop-name').textContent = meta.name || '-';
            document.getElementById('prop-path').textContent = meta.path || '-';
            document.getElementById('prop-size').textContent = meta.is_dir ? 'Directory' : formatBytes(meta.size);
            document.getElementById('prop-modified').textContent = meta.modified || '-';
            document.getElementById('prop-perms').textContent = meta.permissions || '-';
            document.getElementById('prop-owner').textContent = meta.owner || '-';
        }

        function formatBytes(bytes) {
            if (bytes === 0) return '0 Bytes';
            const k = 1024;
            const sizes = ['Bytes', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }

        // File Operations
        async function openFile(path, ext) {
            showSpinner(true);
            currentFile = path;
            document.getElementById('tab-title').textContent = path.split('/').pop();

            const imageExts = ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp'];
            if (imageExts.includes(ext)) {
                hidePreviews();
                const imgDiv = document.getElementById('image-preview');
                const img = document.getElementById('img-target');
                img.src = `/api/download?path=${encodeURIComponent(path)}&t=${Date.now()}`;
                imgDiv.style.display = 'flex';
                showSpinner(false);
                return;
            }

            try {
                // /api/file now strictly returns JSON for metadata & small contents
                const res = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
                if (!res.ok) throw new Error(await res.text());
                const data = await res.json();
                
                let contentText = "";
                if (data.is_large) {
                    // Fetch plain-text content from dedicated streaming endpoint
                    const streamRes = await fetch(`/api/file-stream?path=${encodeURIComponent(path)}`);
                    if (!streamRes.ok) throw new Error(await streamRes.text());
                    contentText = await streamRes.text();
                } else {
                    contentText = data.content;
                }

                if (ext === '.md' && typeof marked !== 'undefined') {
                    hidePreviews();
                    const mdDiv = document.getElementById('markdown-preview');
                    mdDiv.innerHTML = marked.parse(contentText);
                    mdDiv.style.display = 'block';
                } else {
                    setEditorValue(contentText, ext);
                }
            } catch (err) {
                alert('Error opening file: ' + err.message);
            } finally {
                showSpinner(false);
            }
        }

        async function saveCurrentFile() {
            if (!currentFile) return;
            const content = getEditorValue();
            showSpinner(true);
            try {
                const res = await fetch('/api/file', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: currentFile, content: content })
                });
                if (!res.ok) throw new Error(await res.text());
                isUnsaved = false;
                updateTabState();
            } catch (err) {
                alert('Error saving file: ' + err.message);
            } finally {
                showSpinner(false);
            }
        }

        function markUnsaved() {
            isUnsaved = true;
            updateTabState();
        }

        function updateTabState() {
            const tab = document.getElementById('current-tab');
            if (isUnsaved) {
                tab.classList.add('has-unsaved');
            } else {
                tab.classList.remove('has-unsaved');
            }
        }

        // Context Menu Handlers
        function showContextMenu(x, y) {
            const menu = document.getElementById('context-menu');
            menu.style.left = `${x}px`;
            menu.style.top = `${y}px`;
            menu.style.display = 'block';
        }

        document.addEventListener('click', () => {
            document.getElementById('context-menu').style.display = 'none';
        });

        function getSelectedPath() {
            return activeTreeItem ? activeTreeItem.dataset.path : '/';
        }

        function ctxOpen() {
            if (!activeTreeItem) return;
            const path = activeTreeItem.dataset.path;
            const isDir = activeTreeItem.dataset.isDir === 'true';
            if (isDir) toggleFolder(path);
            else openFile(path, '.' + path.split('.').pop());
        }

        function ctxDownload() {
            const path = getSelectedPath();
            const isDir = activeTreeItem && activeTreeItem.dataset.isDir === 'true';
            if (isDir) {
                window.location.href = `/api/zip?path=${encodeURIComponent(path)}`;
            } else {
                window.location.href = `/api/download?path=${encodeURIComponent(path)}`;
            }
        }

        function ctxRename() {
            const path = getSelectedPath();
            const oldName = path.split('/').pop();
            showModal('Rename Item', oldName, async (newName) => {
                if (!newName || newName === oldName) return;
                const parent = path.substring(0, path.lastIndexOf('/'));
                const newPath = (parent ? parent : '') + '/' + newName;
                await fetch('/api/move', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ src: path, dst: newPath })
                });
                refreshTree();
            });
        }

        function ctxDuplicate() {
            const path = getSelectedPath();
            const isDir = activeTreeItem && activeTreeItem.dataset.isDir === 'true';
            const copyPath = path + '_copy';
            fetch('/api/copy', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ src: path, dst: copyPath })
            }).then(() => refreshTree());
        }

        function ctxDelete() {
            const path = getSelectedPath();
            if (confirm(`Are you sure you want to delete ${path}?`)) {
                fetch(`/api/file?path=${encodeURIComponent(path)}`, { method: 'DELETE' })
                    .then(() => refreshTree());
            }
        }

        function createNewFile() {
            const baseDir = getSelectedPath();
            showModal('New File Name', 'untitled.txt', async (fileName) => {
                if (!fileName) return;
                const targetPath = (baseDir === '/' ? '' : baseDir) + '/' + fileName;
                await fetch('/api/newfile', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: targetPath })
                });
                refreshTree();
            });
        }

        function createNewFolder() {
            const baseDir = getSelectedPath();
            showModal('New Folder Name', 'New_Folder', async (folderName) => {
                if (!folderName) return;
                const targetPath = (baseDir === '/' ? '' : baseDir) + '/' + folderName;
                await fetch('/api/mkdir', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: targetPath })
                });
                refreshTree();
            });
        }

        // Search Execution
        async function executeSearch() {
            const query = document.getElementById('search-input').value;
            if (!query) return;

            const isContent = document.getElementById('search-content').checked;
            const isRegex = document.getElementById('search-regex').checked;
            const isCase = document.getElementById('search-case').checked;
            const isWord = document.getElementById('search-word').checked;

            const url = `/api/search?query=${encodeURIComponent(query)}&is_content=${isContent}&is_regex=${isRegex}&case_sensitive=${isCase}&whole_word=${isWord}`;
            
            showSpinner(true);
            try {
                const res = await fetch(url);
                const data = await res.json();
                const container = document.getElementById('search-results');
                container.innerHTML = '';
                
                if (data.results.length === 0) {
                    container.innerHTML = '<div style="padding:8px; color:var(--text-muted)">No matches found</div>';
                    return;
                }

                data.results.forEach(item => {
                    const div = document.createElement('div');
                    div.className = 'search-result-item';
                    div.innerHTML = `<strong>${item.name}</strong> <span style="color:var(--text-muted)">${item.path}</span>`;
                    div.onclick = () => openFile(item.path, '.' + item.name.split('.').pop());
                    container.appendChild(div);
                });
            } catch (err) {
                alert('Search error: ' + err.message);
            } finally {
                showSpinner(false);
            }
        }

        // Drag & Drop Upload Handlers
        const dropZone = document.getElementById('drag-drop-zone');
        
        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            dropZone.addEventListener(eventName, preventDefaults, false);
        });

        function preventDefaults(e) { e.preventDefault(); e.stopPropagation(); }

        dropZone.addEventListener('drop', handleDrop, false);

        function handleDrop(e) {
            const dt = e.dataTransfer;
            const files = dt.files;
            uploadFilesArray(Array.from(files));
        }

        function triggerUpload() {
            document.getElementById('file-input').click();
        }

        function handleFileSelect(e) {
            const files = Array.from(e.target.files);
            uploadFilesArray(files);
        }

        async function uploadFilesArray(files) {
            if (files.length === 0) return;
            const formData = new FormData();
            const targetDir = getSelectedPath();

            files.forEach(file => {
                formData.append('files', file);
                const relPath = (targetDir === '/' ? '' : targetDir) + '/' + file.name;
                formData.append('paths', relPath);
            });

            const bar = document.getElementById('upload-bar');
            const progress = document.getElementById('upload-progress');
            bar.style.display = 'block';
            progress.style.width = '30%';

            try {
                const res = await fetch('/api/upload', {
                    method: 'POST',
                    body: formData
                });
                progress.style.width = '100%';
                setTimeout(() => { bar.style.display = 'none'; }, 1000);
                refreshTree();
            } catch (err) {
                alert('Upload failed: ' + err.message);
                bar.style.display = 'none';
            }
        }

        // Modal Helpers
        function showModal(title, defaultValue, callback) {
            const modal = document.getElementById('input-modal');
            const input = document.getElementById('modal-input');
            document.getElementById('modal-title').textContent = title;
            input.value = defaultValue;
            modal.style.display = 'flex';
            input.focus();

            document.getElementById('modal-confirm').onclick = () => {
                modal.style.display = 'none';
                callback(input.value);
            };
        }

        function closeModal() {
            document.getElementById('input-modal').style.display = 'none';
        }

        function showSpinner(show) {
            document.getElementById('loading-spinner').style.display = show ? 'flex' : 'none';
        }

        // Resizable Panel Logic
        function makeResizable(resizer, leftPanel, rightPanel) {
            let x = 0;
            let leftWidth = 0;

            const mouseDownHandler = function(e) {
                x = e.clientX;
                leftWidth = leftPanel.getBoundingClientRect().width;
                resizer.classList.add('dragging');

                document.addEventListener('mousemove', mouseMoveHandler);
                document.addEventListener('mouseup', mouseUpHandler);
            };

            const mouseMoveHandler = function(e) {
                const dx = e.clientX - x;
                const newWidth = leftWidth + dx;
                if (newWidth > 150 && newWidth < 600) {
                    leftPanel.style.width = `${newWidth}px`;
                }
            };

            const mouseUpHandler = function() {
                resizer.classList.remove('dragging');
                document.removeEventListener('mousemove', mouseMoveHandler);
                document.removeEventListener('mouseup', mouseUpHandler);
            };

            resizer.addEventListener('mousedown', mouseDownHandler);
        }

        makeResizable(document.getElementById('resizer-1'), document.getElementById('left-panel'));
        makeResizable(document.getElementById('resizer-2'), document.getElementById('center-panel'), document.getElementById('right-panel'));

        // Initialize App
        window.onload = () => {
            initEditor();
            refreshTree();
        };
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    return HTML_CONTENT

# ---------------------------------------------------------------------------
# Main Execution Entry
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Starting LAN File Manager server on http://0.0.0.0:8088 ...")
    uvicorn.run(app, host="0.0.0.0", port=8088, reload=False)
