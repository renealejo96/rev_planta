"""
Configuración de Gunicorn para despliegue en VPS detrás de Nginx.
Usa por defecto el puerto personalizado 8420 (configurable con APP_PORT)
para evitar conflictos con puertos por defecto (8000 / 5000) de otras apps en el VPS.
"""

import multiprocessing
import os

port = os.environ.get("APP_PORT", "8420")
host = os.environ.get("APP_HOST", "0.0.0.0")

bind = f"{host}:{port}"
workers = int(os.environ.get("GUNICORN_WORKERS", min(4, multiprocessing.cpu_count() * 2 + 1)))
timeout = 120
accesslog = "-"
errorlog = "-"
loglevel = "info"
