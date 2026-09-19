from django.conf import settings
from django.db import models

from employees.models import Employee


class BuildingKind(models.TextChoices):
    COMPANY = "company", "Company accommodation"
    EXTERNAL = "external", "Outside accommodation"


class Building(models.Model):
    name = models.CharField(max_length=120, unique=True)
    kind = models.CharField(max_length=20, choices=BuildingKind.choices, default=BuildingKind.COMPANY)
    address = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "sort_order", "name"]
        permissions = [
            ("view_accommodation", "Can view accommodation"),
            ("manage_accommodation", "Can manage accommodation"),
        ]

    def __str__(self):
        return self.name


class Room(models.Model):
    building = models.ForeignKey(Building, on_delete=models.PROTECT, related_name="rooms")
    name = models.CharField(max_length=60)
    # How many people the room takes. Empty means not known yet.
    capacity = models.PositiveSmallIntegerField(null=True, blank=True)
    capacity_estimated = models.BooleanField(default=False)
    notes = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["building__sort_order", "building__name", "name"]
        constraints = [models.UniqueConstraint(fields=["building", "name"], name="unique_room_per_building")]

    def __str__(self):
        return f"{self.building.name} {self.name}"


class RoomAssignment(models.Model):
    """Where an employee sleeps. One place per person."""

    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name="room_assignment")
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name="assignments")
    bed_number = models.PositiveSmallIntegerField(null=True, blank=True)
    assigned_on = models.DateField(auto_now_add=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["room__name", "bed_number", "employee__employee_id"]
        constraints = [
            models.UniqueConstraint(fields=["room", "bed_number"], condition=models.Q(bed_number__isnull=False), name="one_person_per_bed"),
        ]
