FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_PORT=8420 \
    APP_HOST=0.0.0.0

WORKDIR /app

# Instalar dependencias del sistema necesarias
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

# Recolectar archivos estáticos para WhiteNoise
RUN python manage.py collectstatic --noinput

EXPOSE 8420

# Al iniciar el contenedor: aplica migraciones en PostgreSQL y levanta Gunicorn en el puerto 8420
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi:application -c gunicorn.conf.py"]
