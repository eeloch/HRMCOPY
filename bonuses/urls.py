from django.urls import path

from . import views

urlpatterns = [
    path("", views.BonusListCreateAPIView.as_view(), name="bonuses"),
    path("people/", views.PeopleAPIView.as_view(), name="bonus-people"),
    path("<int:bonus_id>/approve/", views.BonusApproveAPIView.as_view()),
    path("<int:bonus_id>/decline/", views.BonusDeclineAPIView.as_view()),
    path("<int:bonus_id>/cancel/", views.BonusCancelAPIView.as_view()),
    path("employee-of-the-month/", views.EotmOverviewAPIView.as_view(), name="eotm"),
    path("employee-of-the-month/propose/", views.EotmCreateAPIView.as_view(), name="eotm-propose"),
    path("employee-of-the-month/<int:entry_id>/approve/", views.EotmApproveAPIView.as_view()),
    path("employee-of-the-month/<int:entry_id>/decline/", views.EotmDeclineAPIView.as_view()),
    path("employee-of-the-month/<int:entry_id>/cancel/", views.EotmCancelAPIView.as_view()),
]
