from django.urls import path
from .views import (
    UploadAPIView,
    DashboardAPIView,
    ReviewAPIView,
    UploadHistoryAPIView,
    FailedRowsAPIView,
    CompanyAPIView,
    BulkReviewAPIView,
    AuditLogAPIView
)

urlpatterns = [
    path('upload/', UploadAPIView.as_view()),

    path('dashboard/', DashboardAPIView.as_view()),

    path(
        'records/<uuid:record_id>/review/',
        ReviewAPIView.as_view()
    ),

    path(
        'uploads/',
        UploadHistoryAPIView.as_view()
    ),

    path(
        'failed-rows/',
        FailedRowsAPIView.as_view()
    ),
    path(
    'companies/',
    CompanyAPIView.as_view()
),
path(
    'records/bulk-review/',
    BulkReviewAPIView.as_view()
),


path('audit-logs/', AuditLogAPIView.as_view()),
]