# core/urls.py
from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home_view, name="home"),
    path("upload/", views.upload_view, name="upload"),
    path("dna/", views.display_dna_view, name="display_dna"), # Consolidated DNA display
    # Auth and user-specific paths
    path("signup/", views.signup_view, name="signup"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    # path("dashboard/", views.dashboard_view, name="dashboard"), # Removed, consolidated into dna/
    path("dashboard/update-privacy/", views.update_privacy_view, name="update_privacy"),
    # Public profile (Stretch Goal)
    path("u/<str:username>/", views.public_profile_view, name="public_profile"),
]
