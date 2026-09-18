from django.db import migrations


def approve_records_of_finalised_periods(apps, schema_editor):
    """Approving a payroll period used to leave every employee record in it as "draft",
    so their payslips read Draft. Bring records of already-finalised periods into line."""
    Payroll = apps.get_model("payroll", "EmployeePayroll")
    for period_status, record_status in (("approved", "approved"), ("paid", "paid"), ("closed", "paid")):
        Payroll.objects.filter(payroll_period__status=period_status, status__in=["draft", "review"]).update(status=record_status)


class Migration(migrations.Migration):
    dependencies = [("payroll", "0003_payrolllineitem_unique_system_payroll_line_source")]
    operations = [migrations.RunPython(approve_records_of_finalised_periods, migrations.RunPython.noop)]
