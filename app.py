from flask import Flask, render_template, request, send_file, jsonify, send_from_directory
import yt_dlp
import os
import threading
import queue
import time
from datetime import datetime
import json

app = Flask(__name__)
app.config['DOWNLOAD_FOLDER'] = 'downloads'
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024 * 1024  # 1GB
app.config['SECRET_KEY'] = 'sua-chave-secreta-aqui'  # Altere isso!

# Fila para gerenciar downloads
download_queue = queue.Queue()
download_results = {}

# Criar pasta de downloads se não existir
if not os.path.exists(app.config['DOWNLOAD_FOLDER']):
    os.makedirs(app.config['DOWNLOAD_FOLDER'])

def download_worker():
    """Worker thread para processar downloads"""
    while True:
        try:
            task_id, url = download_queue.get(timeout=10)
            
            try:
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'outtmpl': f"{app.config['DOWNLOAD_FOLDER']}/%(title)s.%(ext)s",
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }],
                    'noplaylist': True,
                    'quiet': True,
                    'no_warnings': True,
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['android'],
                        }
                    },
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    }
                }
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info)
                    mp3_file = filename.rsplit('.', 1)[0] + '.mp3'
                    
                    download_results[task_id] = {
                        'status': 'success',
                        'filename': os.path.basename(mp3_file),
                        'title': info.get('title', 'audio'),
                        'filepath': mp3_file,
                        'duration': info.get('duration', 0),
                        'thumbnail': info.get('thumbnail', ''),
                        'timestamp': datetime.now().isoformat()
                    }
                    
            except Exception as e:
                download_results[task_id] = {
                    'status': 'error',
                    'message': str(e),
                    'timestamp': datetime.now().isoformat()
                }
            
            download_queue.task_done()
            
        except queue.Empty:
            continue
        except Exception as e:
            print(f"Worker error: {e}")

# Iniciar worker thread
worker_thread = threading.Thread(target=download_worker, daemon=True)
worker_thread.start()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('.', 'manifest.json')

@app.route('/service-worker.js')
def serve_service_worker():
    return send_from_directory('.', 'service-worker.js')

@app.route('/api/download', methods=['POST'])
def download():
    data = request.json
    url = data.get('url')
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    
    # Gerar ID único para o download
    task_id = f"task_{int(time.time())}_{hash(url) % 10000}"
    
    # Adicionar à fila
    download_queue.put((task_id, url))
    
    return jsonify({
        'task_id': task_id,
        'status': 'queued',
        'message': 'Download added to queue'
    })

@app.route('/api/status/<task_id>')
def check_status(task_id):
    if task_id in download_results:
        return jsonify(download_results[task_id])
    else:
        return jsonify({'status': 'processing', 'message': 'Still processing...'})

@app.route('/api/list')
def list_downloads():
    files = []
    for f in os.listdir(app.config['DOWNLOAD_FOLDER']):
        if f.endswith('.mp3'):
            filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], f)
            files.append({
                'name': f,
                'size': os.path.getsize(filepath),
                'created': datetime.fromtimestamp(os.path.getctime(filepath)).isoformat(),
                'url': f'/api/download/{f}'
            })
    
    # Ordenar por data de criação (mais recente primeiro)
    files.sort(key=lambda x: x['created'], reverse=True)
    
    return jsonify({'files': files})

@app.route('/api/download/<filename>')
def download_file(filename):
    # Verificar segurança do nome do arquivo
    if '..' in filename or filename.startswith('/'):
        return jsonify({'error': 'Invalid filename'}), 400
    
    filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
    if os.path.exists(filepath):
        return send_file(
            filepath,
            as_attachment=True,
            download_name=filename,
            mimetype='audio/mpeg'
        )
    
    return jsonify({'error': 'File not found'}), 404

@app.route('/api/delete/<filename>', methods=['DELETE'])
def delete_file(filename):
    # Verificar segurança do nome do arquivo
    if '..' in filename or filename.startswith('/'):
        return jsonify({'error': 'Invalid filename'}), 400
    
    filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        return jsonify({'success': True, 'message': 'File deleted'})
    
    return jsonify({'error': 'File not found'}), 404

@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'File too large'}), 413

@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    # Verificar se FFmpeg está disponível
    try:
        import subprocess
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        print("FFmpeg encontrado!")
    except:
        print("AVISO: FFmpeg não encontrado. A conversão para MP3 pode não funcionar.")
        print("Instale com: sudo apt install ffmpeg (Linux) ou baixe do site oficial.")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
