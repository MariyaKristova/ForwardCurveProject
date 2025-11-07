from django.urls import path
from . import views

urlpatterns = [
    path('', views.upload_view, name='upload'),

    # Download views for generated files
    path('download/csv/<str:filename>/', views.download_csv, name='download_csv'),
    path('download/png/<str:filename>/', views.download_png, name='download_png'),
]