from django.urls import path
from . import views

app_name = "recepcion"

urlpatterns = [
    path("", views.index, name="index"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("api/catalogo/", views.api_catalogo, name="api_catalogo"),
    path("api/recepciones/guardar/", views.api_guardar_recepcion, name="api_guardar_recepcion"),
    path("api/recepciones/<int:pk>/", views.api_detalle_recepcion, name="api_detalle_recepcion"),
    path("api/recepciones/<int:pk>/eliminar/", views.api_eliminar_recepcion, name="api_eliminar_recepcion"),
    # Endpoints de Gestión exclusiva para el Admin
    path("api/admin/usuarios/crear/", views.api_admin_crear_usuario, name="api_admin_crear_usuario"),
    path("api/admin/usuarios/<int:pk>/eliminar/", views.api_admin_eliminar_usuario, name="api_admin_eliminar_usuario"),
    path("api/admin/usuarios/<int:pk>/cambiar-rol/", views.api_admin_cambiar_rol_usuario, name="api_admin_cambiar_rol_usuario"),
    path("api/admin/parametros/crear/", views.api_admin_crear_parametro, name="api_admin_crear_parametro"),
    path("api/admin/parametros/<int:pk>/toggle/", views.api_admin_toggle_parametro, name="api_admin_toggle_parametro"),
    path("api/admin/productos/crear/", views.api_admin_crear_producto_variedad, name="api_admin_crear_producto_variedad"),
    path("api/admin/limpiar-pruebas/", views.api_admin_limpiar_recepciones_prueba, name="api_admin_limpiar_recepciones_prueba"),
    # Fotos de evidencia y escaneo de guía física escrita a mano
    path("api/recepciones/<int:pk>/fotos/subir/", views.api_subir_foto_evidencia, name="api_subir_foto_evidencia"),
    path("api/fotos/<int:foto_id>/eliminar/", views.api_eliminar_foto_evidencia, name="api_eliminar_foto_evidencia"),
    path("api/guias/escanear/", views.api_escanear_guia_manuscrita, name="api_escanear_guia_manuscrita"),
    # Exportaciones CSV y Excel (.xlsx)
    path("exportar/csv/", views.exportar_csv, name="exportar_csv"),
    path("exportar/excel/", views.exportar_excel, name="exportar_excel"),
    path("exportar/excel/<int:pk>/", views.exportar_excel, name="exportar_excel_guia"),
]
