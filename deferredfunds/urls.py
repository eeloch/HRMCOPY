from django.urls import path

from . import views

urlpatterns = [
    path("", views.OverviewAPIView.as_view(), name="deferred-funds"),
    path("accounts/", views.EnrolAPIView.as_view(), name="deferred-fund-enrol"),
    path("accounts/<int:account_id>/", views.AccountAPIView.as_view(), name="deferred-fund-account"),
    path("accounts/<int:account_id>/ledger/", views.LedgerAPIView.as_view(), name="deferred-fund-ledger"),
    path("accounts/<int:account_id>/adjust/", views.AdjustAPIView.as_view(), name="deferred-fund-adjust"),
    path("withdrawals/", views.WithdrawalListCreateAPIView.as_view(), name="deferred-fund-withdrawals"),
    path("withdrawals/bank-upload/preview/", views.WithdrawalBankPreviewAPIView.as_view()),
    path("withdrawals/bank-upload/", views.WithdrawalBankDownloadAPIView.as_view()),
    path("withdrawals/mark-paid/", views.WithdrawalBulkPayAPIView.as_view()),
    path("withdrawals/<int:withdrawal_id>/approve/", views.WithdrawalApproveAPIView.as_view()),
    path("withdrawals/<int:withdrawal_id>/decline/", views.WithdrawalDeclineAPIView.as_view()),
    path("withdrawals/<int:withdrawal_id>/cancel/", views.WithdrawalCancelAPIView.as_view()),
    path("withdrawals/<int:withdrawal_id>/pay/", views.WithdrawalPayAPIView.as_view()),
]
