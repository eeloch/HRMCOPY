from django.apps import AppConfig


class EmployeesConfig(AppConfig):
    name = 'employees'

    def ready(self):
        from employees import signals  # noqa: F401 - registers the audit of status / exit / type changes
