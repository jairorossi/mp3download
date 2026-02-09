from flask import Flask, render_template, request, send_file, jsonify
import yt_dlp
import os
import threading
from werkzeug.utils import secure_filename
import time

app = Flask(__name__)
app.config['DOWNLOAD_FOLDER'] = 'downloads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 * 1024  # 16GB

# Criar pasta de downloads se não existir
if not os.path.exists(app.config['DOWNLOAD_FOLDER']):
    os.makedirs(app.config['DOWNLOAD_FOLDER'])

def download_task(url, callback):
    """Função para download em background"""
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
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            mp3_file = filename.rsplit('.', 1)[0] + '.mp3'
            
            callback({
                'status': 'success',
                'filename': os.path.basename(mp3_file),
                'title': info.get('title', 'audio'),
                'filepath': mp3_file
            })
            
    except Exception as e:
        callback({'status': 'error', 'message': str(e)})

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    data = request.json
    url = data.get('url')
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    
    # Iniciar download em thread separada
    def callback(result):
        global download_result
        download_result = result
    
    global download_result
    download_result = None
    
    thread = threading.Thread(target=download_task, args=(url, callback))
    thread.start()
    thread.join(timeout=300)  # Timeout de 5 minutos
    
    if download_result:
        return jsonify(download_result)
    else:
        return jsonify({'status': 'error', 'message': 'Timeout or error occurred'}), 500

@app.route('/download_file/<filename>')
def download_file(filename):
    filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    return jsonify({'error': 'File not found'}), 404

@app.route('/list_downloads')
def list_downloads():
    files = []
    for f in os.listdir(app.config['DOWNLOAD_FOLDER']):
        if f.endswith('.mp3'):
            files.append({
                'name': f,
                'size': os.path.getsize(os.path.join(app.config['DOWNLOAD_FOLDER'], f))
            })
    return jsonify({'files': files})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
