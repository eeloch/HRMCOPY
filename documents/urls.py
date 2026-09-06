from django.urls import path
from .views import (
    EmployeeDocumentDetailAPIView,
    EmployeeDocumentDownloadAPIView,
    EmployeeDocumentListCreateAPIView,
)

urlpatterns = [

    path(
        "",
        EmployeeDocumentListCreateAPIView.as_view(),
        name="documents",
    ),

    path(
        "<int:pk>/",
        EmployeeDocumentDetailAPIView.as_view(),
        name="document-detail",
    ),

    path(
        "<int:pk>/download/",
        EmployeeDocumentDownloadAPIView.as_view(),
        name="document-download",
    ),

]
