from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payroll", "0004_approve_employee_payrolls_of_approved_periods")]

    operations = [
        migrations.AddField(
            model_name="employeepayroll",
            name="bank_details_snapshot",
            field=models.JSONField(blank=True, editable=False, null=True),
        ),
    ]
