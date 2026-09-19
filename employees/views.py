from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from django.db import transaction

from notifications.models import NotificationSeverity
from notifications.services import NotificationService

from rest_framework import status

from rest_framework.permissions import (
    IsAuthenticated,
)

from rest_framework.response import Response

from rest_framework.views import APIView

from .models import (
    Department,
    Position,
    Employee,
    EmploymentType,
    EmploymentCategory,
)

from .serializers import (
    DepartmentSerializer,
    PositionSerializer,
    EmployeeSerializer,
    EmployeeCreateUpdateSerializer,
    EmployeeProfileSerializer
)

from .importers import (
    read_employee_file,
    read_salary_file,
    validate_employee_rows,
    normalize_value,
    parse_salary,
)
from audit.models import AuditSeverity
from audit.services import AuditService

class DepartmentListAPIView(APIView):

    permission_classes = [
        IsAuthenticated,
    ]

    def get(self, request):

        departments = (
            Department.objects
            .all()
            .order_by("name")
        )

        serializer = DepartmentSerializer(
            departments,
            many=True,
        )

        return Response(
            serializer.data
        )


class PositionListAPIView(APIView):

    permission_classes = [
        IsAuthenticated,
    ]

    def get(self, request):

        positions = (
            Position.objects
            .select_related(
                "department"
            )
            .order_by(
                "department__name",
                "name",
            )
        )

        department_id = (
            request.query_params.get(
                "department"
            )
        )

        if department_id:
            positions = positions.filter(
                department_id=department_id
            )

        serializer = PositionSerializer(
            positions,
            many=True,
        )

        return Response(
            serializer.data
        )


class EmployeeListCreateAPIView(APIView):

    permission_classes = [
        IsAuthenticated,
    ]

    def get(self, request):

        employees = (
            Employee.objects
            .select_related(
                "department",
                "position",
            )
            .prefetch_related(
                "shift_assignments__shift"
            )
            .order_by(
                "first_name",
                "last_name",
            )
        )

        search = (
            request.query_params
            .get(
                "search",
                "",
            )
            .strip()
        )

        status_filter = (
            request.query_params.get(
                "status"
            )
        )

        department = (
            request.query_params.get(
                "department"
            )
        )

        if search:

            employees = employees.filter(
                Q(
                    employee_id__icontains=
                    search
                )
                |
                Q(
                    biometric_user_id__icontains=
                    search
                )
                |
                Q(
                    first_name__icontains=
                    search
                )
                |
                Q(
                    middle_name__icontains=
                    search
                )
                |
                Q(
                    last_name__icontains=
                    search
                )
                |
                Q(
                    phone__icontains=
                    search
                )
            )

        if status_filter:
            employees = employees.filter(
                status=status_filter
            )

        if department:
            employees = employees.filter(
                department_id=department
            )

        serializer = EmployeeSerializer(
            employees,
            many=True,
            context={"request": request},
        )

        return Response({
            "count": employees.count(),
            "results": serializer.data,
        })


    def post(self, request):

        serializer = (
            EmployeeCreateUpdateSerializer(
                data=request.data,
                context={"request": request},
            )
        )

        serializer.is_valid(
            raise_exception=True
        )

        employee = serializer.save()

        AuditService.log(
            event_type="employee.created",
            module="employees",
            employee=employee,
            actor=request.user,
            object=employee,
            severity=AuditSeverity.SUCCESS,
            title="Employee created",
            description=f"Employee {employee.full_name} was created.",
            metadata={"employee_id": employee.employee_id},
        )

        output = EmployeeSerializer(
            employee,
            context={"request": request},
        )

        return Response(
            output.data,
            status=
                status.HTTP_201_CREATED,
        )


class EmployeeDetailAPIView(APIView):

    permission_classes = [
        IsAuthenticated,
    ]

    def get_employee(
        self,
        employee_id,
    ):

        try:

            return (
                Employee.objects
                .select_related(
                    "department",
                    "position",
                )
                .prefetch_related("biometric_identities")
                .get(
                    id=employee_id
                )
            )

        except Employee.DoesNotExist:

            return None


    def get(
        self,
        request,
        employee_id,
    ):

        employee = self.get_employee(
            employee_id
        )

        if not employee:

            return Response(
                {
                    "detail":
                        "Employee not found."
                },
                status=
                    status.HTTP_404_NOT_FOUND,
            )

        serializer = EmployeeSerializer(
            employee,
            context={"request": request},
        )

        return Response(
            serializer.data
        )


    def patch(
        self,
        request,
        employee_id,
    ):

        employee = self.get_employee(
            employee_id
        )

        if not employee:

            return Response(
                {
                    "detail":
                        "Employee not found."
                },
                status=
                    status.HTTP_404_NOT_FOUND,
            )

        serializer = (
            EmployeeCreateUpdateSerializer(
                employee,
                data=request.data,
                partial=True,
                context={"request": request},
            )
        )

        serializer.is_valid(
            raise_exception=True
        )

        updated_fields = list(serializer.validated_data.keys())
        employee = serializer.save()

        AuditService.log(
            event_type="employee.updated",
            module="employees",
            employee=employee,
            actor=request.user,
            object=employee,
            severity=AuditSeverity.SUCCESS,
            title="Employee updated",
            description=f"Employee {employee.full_name} was updated.",
            metadata={
                "employee_id": employee.employee_id,
                "updated_fields": updated_fields,
            },
        )

        return Response(
            EmployeeSerializer(
                employee,
                context={"request": request},
            ).data
        )

class EmployeeImportPreviewAPIView(
    APIView
):

    permission_classes = [
        IsAuthenticated,
    ]


    def post(
        self,
        request,
    ):

        uploaded_file = (
            request.FILES.get(
                "file"
            )
        )

        if not uploaded_file:

            return Response(
                {
                    "detail":
                        "Please upload a CSV or XLSX file."
                },
                status=
                    status.HTTP_400_BAD_REQUEST,
            )


        try:

            rows = read_employee_file(
                uploaded_file
            )

        except Exception as exc:

            return Response(
                {
                    "detail":
                        str(exc)
                },
                status=
                    status.HTTP_400_BAD_REQUEST,
            )


        update_existing = request.data.get(
            "update_existing"
        ) in ("true", "1", "True", True)

        results = validate_employee_rows(
            rows,
            update_existing=update_existing,
        )


        valid_count = sum(
            1
            for item in results
            if item["valid"]
        )

        error_count = (
            len(results)
            - valid_count
        )


        return Response({

            "total_rows":
                len(results),

            "valid_rows":
                valid_count,

            "error_rows":
                error_count,

            "can_import":
                (
                    len(results) > 0
                    and error_count == 0
                ),

            "results":
                results,

        })


class EmployeeImportAPIView(APIView):

    permission_classes = [
        IsAuthenticated,
    ]

    @transaction.atomic
    def post(self, request):

        uploaded_file = request.FILES.get(
            "file"
        )

        if not uploaded_file:
            return Response(
                {
                    "detail":
                        "Please upload a CSV or XLSX file."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            rows = read_employee_file(
                uploaded_file
            )
        except Exception as exc:
            return Response(
                {
                    "detail": str(exc)
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        update_existing = request.data.get(
            "update_existing"
        ) in ("true", "1", "True", True)
        skip_invalid = request.data.get(
            "skip_invalid"
        ) in ("true", "1", "True", True)

        results = validate_employee_rows(
            rows,
            update_existing=update_existing,
        )
        invalid_rows = [
            item
            for item in results
            if not item["valid"]
        ]
        rows_to_import = [
            item
            for item in results
            if item["valid"]
        ] if skip_invalid else results

        if not results or (invalid_rows and not skip_invalid) or not rows_to_import:
            return Response(
                {
                    "detail": (
                        "None of the rows in this spreadsheet could be imported."
                        if skip_invalid else
                        "The spreadsheet must pass validation "
                        "before it can be imported."
                    ),
                    "summary": {
                        "imported": 0,
                        "updated": 0,
                        "skipped": len(results) - len(invalid_rows),
                        "failed": len(invalid_rows),
                    },
                    "errors": [
                        {
                            "row": item["row"],
                            "errors": item["errors"],
                        }
                        for item in invalid_rows
                    ],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing_employees = {
            employee.id: employee
            for employee in Employee.objects.filter(
                id__in=[
                    item["data"]["existing_employee_id"]
                    for item in rows_to_import
                    if item["data"].get("existing_employee_id")
                ]
            )
        }

        can_set_salary = request.user.has_perm("employees.view_salary")
        can_set_bank_details = request.user.has_perm("employees.view_bank_details")

        serializers = []
        serialization_errors = []
        placements = {}

        for item in rows_to_import:
            existing_id = item["data"].get("existing_employee_id")
            row_data = item["data"]
            if not can_set_salary:
                # The importer always fills basic_salary (defaulting to 0
                # when the sheet has no value), so treating its mere presence
                # as "trying to set a salary" would fail every row for an
                # importer without this permission, even on sheets that never
                # mention pay. Drop it instead: creates get the model
                # default, updates leave the employee's existing salary untouched.
                row_data = {key: value for key, value in row_data.items() if key != "basic_salary"}
            if not can_set_bank_details:
                # Same reasoning as basic_salary above - drop rather than fail the row.
                row_data = {
                    key: value
                    for key, value in row_data.items()
                    if key not in ("bank_name", "account_number", "bank_code")
                }
            serializer = EmployeeCreateUpdateSerializer(
                instance=existing_employees.get(existing_id),
                data=row_data,
                context={"request": request},
            )

            if not serializer.is_valid():
                serialization_errors.append(
                    {
                        "row": item["row"],
                        "errors": serializer.errors,
                    }
                )
                continue

            serializers.append(serializer)
            placements[id(serializer)] = (row_data.get("accommodation_placement"), row_data.get("room_allocated"))

        if serialization_errors and (not skip_invalid or not serializers):
            return Response(
                {
                    "detail": (
                        "The spreadsheet could not be imported."
                    ),
                    "summary": {
                        "imported": 0,
                        "updated": 0,
                        "skipped": len(rows_to_import) - len(serialization_errors),
                        "failed": len(serialization_errors),
                    },
                    "errors": serialization_errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        created_count = 0
        updated_count = 0
        saved_employees = []

        from accommodation.services import apply_import_placement

        accommodation = {"inside": 0, "inside_no_bed": 0, "unknown_room": 0, "gender_mismatch": 0, "outside": 0, "none": 0, "vacated": 0}
        for serializer in serializers:
            is_update = serializer.instance is not None
            employee = serializer.save()
            saved_employees.append(employee)
            if is_update:
                updated_count += 1
            else:
                created_count += 1
            placement, room_label = placements.get(id(serializer), (None, None))
            outcome = apply_import_placement(employee, placement, room_label, actor=request.user)
            if outcome:
                accommodation[outcome] += 1

        incomplete_employees = [
            employee
            for employee in saved_employees
            if employee.department_id is None
        ]
        if incomplete_employees:
            self._notify_incomplete_profiles(incomplete_employees)

        skipped_rows = [
            {"row": item["row"], "errors": item["errors"]}
            for item in invalid_rows
        ] + serialization_errors

        return Response(
            {
                "summary": {
                    "imported": created_count,
                    "updated": updated_count,
                    "skipped": len(skipped_rows),
                    "failed": 0,
                },
                "accommodation": accommodation,
                "errors": skipped_rows,
            },
            status=status.HTTP_201_CREATED,
        )

    @staticmethod
    def _notify_incomplete_profiles(employees):
        """One notification per admin per import run, not one per employee - avoids flooding the notification bell on a large import."""
        names = ", ".join(employee.full_name for employee in employees[:5])
        if len(employees) > 5:
            names += f", and {len(employees) - 5} more"

        for user in get_user_model().objects.filter(is_superuser=True):
            NotificationService.create(
                recipient=user,
                event_type="employees.import_incomplete_profile",
                title="Employees imported without a department",
                message=f"{len(employees)} employee(s) were imported without a department and need their profile completed: {names}.",
                severity=NotificationSeverity.WARNING,
                related_url="/employees",
            )


class EmployeeImportOrganizationAPIView(APIView):

    permission_classes = [
        IsAuthenticated,
    ]

    def post(self, request):

        uploaded_file = request.FILES.get(
            "file"
        )

        if not uploaded_file:
            return Response(
                {
                    "detail":
                        "Please upload a CSV or XLSX file."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            rows = read_employee_file(
                uploaded_file
            )

        except Exception as exc:
            return Response(
                {
                    "detail": str(exc)
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        organization = {}

        for row in rows:

            department_name = normalize_value(
                row.get("department")
            )

            position_name = normalize_value(
                row.get("position")
            )

            if not department_name:
                continue

            department_key = (
                department_name.casefold()
            )

            if department_key not in organization:
                organization[department_key] = {
                    "name": department_name,
                    "positions": {},
                }

            if position_name:
                position_key = (
                    position_name.casefold()
                )

                organization[
                    department_key
                ]["positions"][
                    position_key
                ] = position_name


        results = []

        for department_data in organization.values():

            department = (
                Department.objects
                .filter(
                    name__iexact=
                        department_data["name"]
                )
                .first()
            )

            position_results = []

            for position_name in (
                department_data[
                    "positions"
                ].values()
            ):

                existing_position = None

                if department:
                    existing_position = (
                        Position.objects
                        .filter(
                            department=department,
                            name__iexact=
                                position_name,
                        )
                        .first()
                    )

                position_results.append({
                    "name":
                        position_name,

                    "exists":
                        existing_position
                        is not None,

                    "id":
                        (
                            existing_position.id
                            if existing_position
                            else None
                        ),
                })


            position_results.sort(
                key=lambda item:
                    item["name"].casefold()
            )


            results.append({
                "name":
                    department_data["name"],

                "exists":
                    department is not None,

                "id":
                    (
                        department.id
                        if department
                        else None
                    ),

                "positions":
                    position_results,
            })


        results.sort(
            key=lambda item:
                item["name"].casefold()
        )


        missing_departments = sum(
            1
            for item in results
            if not item["exists"]
        )

        missing_positions = sum(
            1
            for department in results
            for position
            in department["positions"]
            if not position["exists"]
        )


        return Response({
            "departments":
                results,

            "total_departments":
                len(results),

            "missing_departments":
                missing_departments,

            "missing_positions":
                missing_positions,
        })

class EmployeeImportOrganizationCreateAPIView(APIView):

    permission_classes = [
        IsAuthenticated,
    ]

    @transaction.atomic
    def post(self, request):

        departments = request.data.get(
            "departments",
            []
        )

        if not isinstance(
            departments,
            list
        ):
            return Response(
                {
                    "detail":
                        "Departments must be a list."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )


        created_departments = []
        existing_departments = []

        created_positions = []
        existing_positions = []


        for item in departments:

            department_name = (
                str(
                    item.get(
                        "name",
                        ""
                    )
                )
                .strip()
            )

            positions = item.get(
                "positions",
                []
            )


            if not department_name:
                continue


            department = (
                Department.objects
                .filter(
                    name__iexact=
                        department_name
                )
                .first()
            )


            if department:
                existing_departments.append(
                    department.name
                )

            else:
                department = (
                    Department.objects.create(
                        name=
                            department_name
                    )
                )

                created_departments.append(
                    department.name
                )


            for position_item in positions:

                if isinstance(
                    position_item,
                    str
                ):
                    position_name = (
                        position_item.strip()
                    )

                else:
                    position_name = (
                        str(
                            position_item.get(
                                "name",
                                ""
                            )
                        )
                        .strip()
                    )


                if not position_name:
                    continue


                existing_position = (
                    Position.objects
                    .filter(
                        department=
                            department,
                        name__iexact=
                            position_name,
                    )
                    .first()
                )


                if existing_position:
                    existing_positions.append({
                        "department":
                            department.name,

                        "position":
                            existing_position.name,
                    })

                    continue


                position = (
                    Position.objects.create(
                        department=
                            department,
                        name=
                            position_name,
                    )
                )


                created_positions.append({
                    "department":
                        department.name,

                    "position":
                        position.name,
                })


        return Response({
            "created_departments":
                created_departments,

            "existing_departments":
                existing_departments,

            "created_positions":
                created_positions,

            "existing_positions":
                existing_positions,

            "summary": {
                "departments_created":
                    len(
                        created_departments
                    ),

                "positions_created":
                    len(
                        created_positions
                    ),
            },
        })            
from rest_framework import generics

from .models import Employee


class EmployeeProfileAPIView(generics.RetrieveAPIView):
    """
    Returns a complete employee profile.

    Read-only endpoint.
    """

    queryset = (
        Employee.objects
        .select_related(
            "department",
            "position",
        )
    )

    serializer_class = EmployeeProfileSerializer


class AccommodationStatusReportAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        categories = ["casual", "expatriate", "administrative", "other"]
        housing_types = ["in_house", "external"]
        counts = {housing: {category: 0 for category in categories} for housing in housing_types}

        def category_for(employment_type, employment_category):
            if employment_type == EmploymentType.CASUAL:
                return "casual"
            if employment_type == EmploymentType.EXPATRIATE:
                return "expatriate"
            if employment_category in (EmploymentCategory.MANAGEMENT, EmploymentCategory.EXECUTIVE):
                return "administrative"
            return "other"

        employees = Employee.objects.filter(status="active").only(
            "employment_type",
            "employment_category",
            "lives_in_company_hostel",
            "lives_in_external_accommodation",
        )

        for employee in employees:
            category = category_for(employee.employment_type, employee.employment_category)
            if employee.lives_in_company_hostel:
                counts["in_house"][category] += 1
            if employee.lives_in_external_accommodation:
                counts["external"][category] += 1

        return Response({
            "categories": categories,
            "housing_types": housing_types,
            "counts": counts,
            "totals": {housing: sum(counts[housing].values()) for housing in housing_types},
        })


class EmployeeHiresExitsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        week_start_param = request.query_params.get("week_start")
        if week_start_param:
            try:
                week_start = date.fromisoformat(week_start_param)
            except ValueError:
                return Response(
                    {"detail": "week_start must be an ISO date (YYYY-MM-DD)."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            week_start = timezone.localdate()

        week_start = week_start - timedelta(days=week_start.weekday())
        week_end = week_start + timedelta(days=6)

        hires = (
            Employee.objects
            .filter(employment_date__gte=week_start, employment_date__lte=week_end)
            .select_related("department")
            .order_by("employment_date")
        )
        exits = (
            Employee.objects
            .filter(exit_date__gte=week_start, exit_date__lte=week_end)
            .select_related("department")
            .order_by("exit_date")
        )

        def serialize(queryset, date_field):
            return [
                {
                    "id": employee.pk,
                    "employee_id": employee.employee_id,
                    "name": employee.full_name,
                    "date": getattr(employee, date_field),
                    "department": employee.department.name if employee.department_id else "",
                }
                for employee in queryset
            ]

        return Response({
            "week_start": week_start,
            "week_end": week_end,
            "hires": serialize(hires, "employment_date"),
            "exits": serialize(exits, "exit_date"),
        })


def _build_salary_matches(rows):
    """Match spreadsheet rows to employees by staff number, without saving anything."""
    matched, unmatched, invalid = [], [], []
    employees_by_id = {
        employee.employee_id.strip(): employee
        for employee in Employee.objects.only(
            "id", "employee_id", "first_name", "middle_name", "last_name", "basic_salary"
        )
    }

    for row in rows:
        spreadsheet_row = row.get("_spreadsheet_row")
        raw_id = str(row.get("employee_id") or "").strip()
        raw_salary = row.get("basic_salary")

        if not raw_id:
            invalid.append({"row": spreadsheet_row, "employee_id": raw_id, "reason": "Missing employee ID."})
            continue
        if raw_salary is None or not str(raw_salary).strip():
            invalid.append({"row": spreadsheet_row, "employee_id": raw_id, "reason": "Missing salary value."})
            continue

        try:
            new_salary = parse_salary(raw_salary)
        except ValueError as exc:
            invalid.append({"row": spreadsheet_row, "employee_id": raw_id, "reason": str(exc)})
            continue

        employee = employees_by_id.get(raw_id)
        if employee is None:
            unmatched.append({"row": spreadsheet_row, "employee_id": raw_id})
            continue

        matched.append({"employee": employee, "old_salary": employee.basic_salary, "new_salary": new_salary})

    return matched, unmatched, invalid


class EmployeeSalaryImportPreviewAPIView(APIView):
    """Read-only: match a staff-number + basic-salary spreadsheet against employees, without saving."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.has_perm("employees.view_salary"):
            return Response({"detail": "You don't have permission to import salaries."}, status=status.HTTP_403_FORBIDDEN)

        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return Response({"detail": "Please upload a CSV or XLSX file."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rows = read_salary_file(uploaded_file)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        matched, unmatched, invalid = _build_salary_matches(rows)

        return Response({
            "matched": [
                {
                    "id": item["employee"].pk,
                    "employee_id": item["employee"].employee_id,
                    "name": item["employee"].full_name,
                    "old_salary": str(item["old_salary"]),
                    "new_salary": str(item["new_salary"]),
                    "changed": item["old_salary"] != item["new_salary"],
                }
                for item in matched
            ],
            "unmatched": unmatched,
            "invalid": invalid,
        })


class EmployeeSalaryImportAPIView(APIView):
    """Apply a staff-number + basic-salary spreadsheet, updating only matched, valid rows."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.has_perm("employees.view_salary"):
            return Response({"detail": "You don't have permission to import salaries."}, status=status.HTTP_403_FORBIDDEN)

        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return Response({"detail": "Please upload a CSV or XLSX file."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rows = read_salary_file(uploaded_file)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        matched, unmatched, invalid = _build_salary_matches(rows)

        updated = []
        with transaction.atomic():
            for item in matched:
                employee = item["employee"]
                if employee.basic_salary == item["new_salary"]:
                    continue
                employee.basic_salary = item["new_salary"]
                employee.save(update_fields=["basic_salary", "updated_at"])
                updated.append({
                    "employee_id": employee.employee_id,
                    "name": employee.full_name,
                    "old_salary": str(item["old_salary"]),
                    "new_salary": str(item["new_salary"]),
                })

        AuditService.log(
            event_type="employees.salary_bulk_imported",
            module="employees",
            actor=request.user,
            severity=AuditSeverity.SUCCESS,
            title="Bulk salary import",
            description=f"Updated basic salary for {len(updated)} employee(s) via bulk import.",
            metadata={"updated": len(updated), "unmatched": len(unmatched), "invalid": len(invalid)},
        )

        return Response({
            "updated": len(updated),
            "unmatched": unmatched,
            "invalid": invalid,
            "updated_employees": updated,
        })
