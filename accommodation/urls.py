from django.urls import path

from . import views

urlpatterns = [
    path("", views.OverviewAPIView.as_view(), name="accommodation"),
    path("people/", views.PeopleAPIView.as_view(), name="accommodation-people"),
    path("buildings/", views.BuildingCreateAPIView.as_view(), name="accommodation-building-create"),
    path("rooms/", views.RoomCreateAPIView.as_view(), name="accommodation-room-create"),
    path("rooms/<int:room_id>/", views.RoomDetailAPIView.as_view(), name="accommodation-room"),
    path("assign/", views.AssignAPIView.as_view(), name="accommodation-assign"),
    path("unassign/", views.UnassignAPIView.as_view(), name="accommodation-unassign"),
    path("import/", views.ImportAPIView.as_view(), name="accommodation-import"),
    path("import-rooms/", views.RoomSetupImportAPIView.as_view(), name="accommodation-import-rooms"),
]
