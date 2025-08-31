# core/urls.py
from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home_view, name="home"),
    path("upload/", views.upload_view, name="upload"),
    path("dna/", views.display_dna_view, name="display_dna"),
    path("signup/", views.signup_view, name="signup"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("dashboard/update-privacy/", views.update_privacy_view, name="update_privacy"),
    path("u/<str:username>/", views.public_profile_view, name="public_profile"),
    path("dashboard/update-name/", views.update_display_name_view, name="update_name"),
    path("api/update-username/", views.update_username_api, name="api_update_username"),
    path("task/<str:task_id>/", views.task_status_view, name="task_status"),
    path("api/task-result/<str:task_id>/", views.get_task_result_view, name="get_task_result"),
]
