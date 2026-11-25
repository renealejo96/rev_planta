#!/bin/bash
set -e

echo "🚀 [1/3] Descargando últimos cambios desde GitHub..."
git pull origin main

echo "📦 [2/3] Reconstruyendo y levantando contenedores Docker..."
docker compose up -d --build

echo "✅ [3/3] Despliegue completado con éxito. Estado de los contenedores:"
docker compose ps
