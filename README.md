# Sistema de Recepción y Calidad de Planta

Sistema web integral desarrollado en **Django** para la gestión de recepción de cosecha, control de calidad, muestreo estadístico y lectura asistida por IA (Gemini Vision).

---

## 🚀 Arquitectura y Tecnologías
- **Backend:** Python 3.13 / Django 6.1
- **Servidor de Producción:** Gunicorn 27 con WhiteNoise (archivos estáticos optimizados)
- **Base de Datos:** PostgreSQL 16 Alpine
- **Contenedorización:** Docker & Docker Compose
- **IA:** Google Gemini Vision (lectura de guías escritas a mano)

---

## ⚙️ Configuración de Puertos y Variables de Entorno

Para evitar cualquier conflicto con otras aplicaciones que ya corren en el VPS, esta aplicación utiliza por defecto el puerto **`8420`**.

El puerto es 100% configurable a través de variables de entorno sin necesidad de modificar el código ni el archivo `docker-compose.yml`.

### Variables Principales (`.env`)
```bash
# Entorno Django
DJANGO_SECRET_KEY=clave_secreta_super_segura_de_produccion
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,TU_IP_O_DOMINIO_DEL_VPS
CSRF_TRUSTED_ORIGINS=http://TU_IP_DEL_VPS:8420,https://tu-dominio.com

# Puerto de la aplicación (Cámbialo si el 8420 ya estuviese ocupado)
APP_PORT=8420
# 0.0.0.0 para acceso directo o 127.0.0.1 si usas Nginx como proxy inverso en el VPS
APP_BIND_IP=0.0.0.0

# Base de datos PostgreSQL aislada en Docker
DB_NAME=calidad_planta
DB_USER=postgres
DB_PASSWORD=contrasena_segura_aqui
DB_HOST=db
DB_PORT=5432

# IA Gemini
GEMINI_API_KEY=tu_api_key_de_gemini
```

> **Nota sobre Base de Datos:** El contenedor de PostgreSQL (`calidad_planta_db`) **no expone su puerto 5432 al VPS**, sino que se comunica de forma interna y privada con el contenedor de la aplicación. Esto garantiza que no colisionará si ya tienes otro PostgreSQL o MySQL corriendo en tu servidor.

---

## 🛠️ Despliegue en VPS (Hostinger)

### 1. Clonar el Repositorio en el VPS
Accede por SSH a tu VPS de Hostinger y clona el repositorio:
```bash
git clone https://github.com/renealejo96/rev_planta.git
cd rev_planta
```

### 2. Crear el archivo `.env`
Copia la plantilla y configura tus valores reales:
```bash
cp .env.example .env
nano .env
```
*(Guarda los cambios con `Ctrl + O` y sal con `Ctrl + X`)*.

### 3. Levantar los Contenedores
Ejecuta Docker Compose para construir la imagen y arrancar los servicios en segundo plano:
```bash
docker compose up -d --build
```
*(Si tu versión de Docker es más antigua, usa `docker-compose up -d --build`)*.

### 4. Verificar el Estado
```bash
docker compose ps
docker compose logs -f web
```

Tu aplicación estará corriendo en:
`http://<IP_DE_TU_VPS>:8420`

---

## 🔄 Flujo de Actualizaciones en Producción (`Git Pull`)

Cada vez que realices cambios locales y hagas `push` a GitHub, actualiza el VPS con un solo comando:

```bash
git pull origin main && docker compose up -d --build
```

Esto:
1. Descargará los cambios del repositorio.
2. Reconstruirá la imagen si hubo cambios en dependencias o código.
3. Ejecutará automáticamente las migraciones pendientes (`migrate`).
4. Recolectará archivos estáticos actualizados (`collectstatic`).
5. Reiniciará los contenedores sin pérdida de datos en la base de datos ni en las fotos.

---

## 🌐 Configuración Opcional con Nginx Inverso (Dominio y SSL)

Si deseas conectar un subdominio (ej: `calidad.tudominio.com`) con HTTPS mediante Certbot:

En `.env` configura:
```bash
APP_BIND_IP=127.0.0.1
CSRF_TRUSTED_ORIGINS=https://calidad.tudominio.com
DJANGO_ALLOWED_HOSTS=calidad.tudominio.com,127.0.0.1
```

Bloque de configuración en `/etc/nginx/sites-available/calidad.conf`:
```nginx
server {
    server_name calidad.tudominio.com;

    location / {
        proxy_pass http://127.0.0.1:8420;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```
Actívalo y genera el certificado SSL:
```bash
sudo ln -s /etc/nginx/sites-available/calidad.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d calidad.tudominio.com
```
