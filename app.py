# Verificar se temos FFmpeg, senão usar conversão interna
import shutil
if shutil.which("ffmpeg") is None:
    print("AVISO: FFmpeg não encontrado. Usando conversão interna...")
    # Forçar yt-dlp a usar conversor interno
    os.environ['YTDLP_NO_EXTERNAL_FFMPEG'] = '1'

from flask import Flask, render_template, request, send_file, jsonify, send_from_directory, Response
import yt_dlp
import os
import threading
import queue
import time
from datetime import datetime
import shutil
import logging
from werkzeug.utils import secure_filename

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configurações para Render
app.config['DOWNLOAD_FOLDER'] = 'downloads'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB máximo
app.config['CLEANUP_AGE_HOURS'] = 6  # Limpar arquivos após 6 horas
app.config['MAX_CONCURRENT_DOWNLOADS'] = 1  # Apenas 1 download por vez
app.config['MAX_VIDEO_DURATION'] = 600  # 10 minutos máximo

# Criar pasta de downloads se não existir
if not os.path.exists(app.config['DOWNLOAD_FOLDER']):
    os.makedirs(app.config['DOWNLOAD_FOLDER'])
    logger.info(f"Pasta de downloads criada: {app.config['DOWNLOAD_FOLDER']}")

# Sistema de fila para downloads
download_queue = queue.Queue(maxsize=3)
download_status = {}

def cleanup_old_files():
    """Limpar arquivos antigos automaticamente"""
    try:
        current_time = time.time()
        for filename in os.listdir(app.config['DOWNLOAD_FOLDER']):
            filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
            if os.path.isfile(filepath):
                file_age = current_time - os.path.getctime(filepath)
                if file_age > app.config['CLEANUP_AGE_HOURS'] * 3600:
                    os.remove(filepath)
                    logger.info(f"Arquivo removido (antigo): {filename}")
    except Exception as e:
        logger.error(f"Erro na limpeza: {e}")

def download_worker():
    """Worker thread para processar downloads"""
    while True:
        try:
            task_id, url = download_queue.get(timeout=30)
            
            try:
                # Verificar se já existe download em andamento
                active_downloads = sum(1 for status in download_status.values() 
                                     if status.get('status') == 'downloading')
                
                if active_downloads >= app.config['MAX_CONCURRENT_DOWNLOADS']:
                    download_status[task_id] = {
                        'status': 'queued',
                        'position': download_queue.qsize() + 1,
                        'message': 'Aguardando na fila...'
                    }
                    # Recolocar na fila
                    time.sleep(5)
                    download_queue.put((task_id, url))
                    continue
                
                download_status[task_id] = {
                    'status': 'downloading',
                    'message': 'Iniciando download...',
                    'progress': 0
                }
                
                # Opções do yt-dlp
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'outtmpl': os.path.join(app.config['DOWNLOAD_FOLDER'], '%(title)s.%(ext)s'),
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }],
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': False,
                    'noplaylist': True,
                    'progress_hooks': [lambda d: progress_hook(d, task_id)],
                    'socket_timeout': 30,
                    'retries': 3,
                }
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    
                    # Verificar duração do vídeo
                    duration = info.get('duration', 0)
                    if duration > app.config['MAX_VIDEO_DURATION']:
                        raise Exception(f"Vídeo muito longo ({duration//60}min). Máximo: 10min")
                    
                    filename = ydl.prepare_filename(info)
                    mp3_file = filename.rsplit('.', 1)[0] + '.mp3'
                    
                    if os.path.exists(mp3_file):
                        file_size = os.path.getsize(mp3_file)
                        if file_size > 100 * 1024 * 1024:  # 100MB
                            os.remove(mp3_file)
                            raise Exception("Arquivo muito grande (>100MB)")
                        
                        download_status[task_id] = {
                            'status': 'success',
                            'filename': os.path.basename(mp3_file),
                            'title': info.get('title', 'audio').replace('/', '-').replace('\\', '-'),
                            'filesize': file_size,
                            'duration': duration,
                            'thumbnail': info.get('thumbnail', ''),
                            'timestamp': datetime.now().isoformat()
                        }
                    else:
                        raise Exception("Falha na conversão para MP3")
                
                # Limpar arquivos antigos após cada download
                cleanup_old_files()
                
            except Exception as e:
                logger.error(f"Erro no download {task_id}: {e}")
                download_status[task_id] = {
                    'status': 'error',
                    'message': str(e),
                    'timestamp': datetime.now().isoformat()
                }
            
            download_queue.task_done()
            
        except queue.Empty:
            continue
        except Exception as e:
            logger.error(f"Erro no worker: {e}")

def progress_hook(d, task_id):
    """Callback para progresso do download"""
    if d['status'] == 'downloading':
        if 'total_bytes' in d and d['total_bytes']:
            percent = (d['downloaded_bytes'] / d['total_bytes']) * 100
            download_status[task_id]['progress'] = int(percent)
            download_status[task_id]['message'] = f"Baixando... {percent:.1f}%"
    elif d['status'] == 'processing':
        download_status[task_id]['message'] = 'Convertendo para MP3...'

# Iniciar worker thread
worker_thread = threading.Thread(target=download_worker, daemon=True)
worker_thread.start()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/health')
def health():
    return jsonify({
        'status': 'online',
        'disk_usage': get_disk_usage(),
        'queue_size': download_queue.qsize(),
        'active_downloads': sum(1 for status in download_status.values() 
                              if status.get('status') == 'downloading')
    })

@app.route('/api/download', methods=['POST'])
def download():
    data = request.json
    url = data.get('url')
    
    if not url:
        return jsonify({'error': 'URL é obrigatória'}), 400
    
    # Validar URL do YouTube
    if 'youtube.com' not in url and 'youtu.be' not in url:
        return jsonify({'error': 'URL do YouTube inválida'}), 400
    
    # Verificar espaço em disco
    if get_disk_usage() > 90:  # 90% de uso
        cleanup_old_files()
        if get_disk_usage() > 90:
            return jsonify({'error': 'Espaço em disco insuficiente'}), 507
    
    # Gerar ID único
    task_id = f"task_{int(time.time())}_{hash(url) % 10000}"
    
    try:
        download_queue.put_nowait((task_id, url))
        download_status[task_id] = {
            'status': 'queued',
            'position': download_queue.qsize(),
            'message': 'Na fila de espera...'
        }
        
        return jsonify({
            'task_id': task_id,
            'status': 'queued',
            'position': download_queue.qsize(),
            'message': 'Download adicionado à fila'
        })
        
    except queue.Full:
        return jsonify({'error': 'Fila cheia. Tente novamente em alguns minutos.'}), 503

@app.route('/api/status/<task_id>')
def check_status(task_id):
    if task_id in download_status:
        status = download_status[task_id].copy()
        
        # Limpar status antigos (mais de 1 hora)
        if 'timestamp' in status:
            status_time = datetime.fromisoformat(status['timestamp'])
            if (datetime.now() - status_time).seconds > 3600:
                download_status.pop(task_id, None)
        
        return jsonify(status)
    else:
        return jsonify({'status': 'not_found'}), 404

@app.route('/api/files')
def list_files():
    files = []
    try:
        for filename in os.listdir(app.config['DOWNLOAD_FOLDER']):
            if filename.endswith('.mp3'):
                filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
                if os.path.isfile(filepath):
                    files.append({
                        'name': filename,
                        'size': os.path.getsize(filepath),
                        'created': datetime.fromtimestamp(os.path.getctime(filepath)).isoformat(),
                        'url': f'/api/files/{filename}'
                    })
        
        # Ordenar por data (mais recente primeiro)
        files.sort(key=lambda x: x['created'], reverse=True)
        
        # Manter apenas últimos 10 arquivos
        if len(files) > 10:
            for old_file in files[10:]:
                try:
                    os.remove(os.path.join(app.config['DOWNLOAD_FOLDER'], old_file['name']))
                except:
                    pass
            files = files[:10]
        
    except Exception as e:
        logger.error(f"Erro ao listar arquivos: {e}")
    
    return jsonify({'files': files})

@app.route('/api/files/<filename>')
def get_file(filename):
    # Prevenir path traversal
    filename = secure_filename(filename)
    filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
    
    if os.path.exists(filepath):
        return send_file(
            filepath,
            as_attachment=True,
            download_name=filename,
            mimetype='audio/mpeg'
        )
    
    return jsonify({'error': 'Arquivo não encontrado'}), 404

@app.route('/api/cleanup', methods=['POST'])
def cleanup():
    try:
        deleted_count = 0
        for filename in os.listdir(app.config['DOWNLOAD_FOLDER']):
            filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
            if os.path.isfile(filepath):
                os.remove(filepath)
                deleted_count += 1
        
        return jsonify({
            'success': True,
            'message': f'{deleted_count} arquivos removidos',
            'deleted_count': deleted_count
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def get_disk_usage():
    """Verificar uso de disco"""
    try:
        total, used, free = shutil.disk_usage(app.config['DOWNLOAD_FOLDER'])
        return (used / total) * 100
    except:
        return 0

# Limpeza inicial
@app.before_first_request
def startup():
    cleanup_old_files()
    logger.info("Aplicação iniciada no Render")

# Limpeza periódica (a cada hora)
import atexit
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()
scheduler.add_job(func=cleanup_old_files, trigger="interval", hours=1)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
