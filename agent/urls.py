from django.urls import path
from .views import (
    StartSessionView,
    SessionDetailView,
    SessionListView,
    RepositoryListView,
    RepoSessionsView,
)

urlpatterns = [
    path("sessions/", StartSessionView.as_view()),
    path("sessions/list/", SessionListView.as_view()),
    path("sessions/<int:pk>/", SessionDetailView.as_view()),
    path("repos/", RepositoryListView.as_view()),
    path("repos/<int:pk>/sessions/", RepoSessionsView.as_view()),
]
