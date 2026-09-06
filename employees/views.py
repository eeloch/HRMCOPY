from django.db.models import Q

from django.db import transaction

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
    validate_employee_rows,
    normalize_value,
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
        )

        return Response({
            "count": employees.count(),
            "results": serializer.data,
        })


    def post(self, request):

        serializer = (
            EmployeeCreateUpdateSerializer(
                data=request.data
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
            employee
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
            employee
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
                employee
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


        results = validate_employee_rows(
            rows
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

        results = validate_employee_rows(
            rows
        )
        invalid_rows = [
            item
            for item in results
            if not item["valid"]
        ]

        if not results or invalid_rows:
            return Response(
                {
                    "detail": (
                        "The spreadsheet must pass validation "
                        "before it can be imported."
                    ),
                    "summary": {
                        "imported": 0,
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

        serializers = []
        serialization_errors = []

        for item in results:
            serializer = EmployeeCreateUpdateSerializer(
                data=item["data"]
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

        if serialization_errors:
            return Response(
                {
                    "detail": (
                        "The spreadsheet could not be imported."
                    ),
                    "summary": {
                        "imported": 0,
                        "skipped": len(results) - len(serialization_errors),
                        "failed": len(serialization_errors),
                    },
                    "errors": serialization_errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        for serializer in serializers:
            serializer.save()

        return Response(
            {
                "summary": {
                    "imported": len(serializers),
                    "skipped": 0,
                    "failed": 0,
                },
            },
            status=status.HTTP_201_CREATED,
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
