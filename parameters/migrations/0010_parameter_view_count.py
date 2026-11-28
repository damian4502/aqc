from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("parameters", "0009_parameter_color"),
    ]

    operations = [
        migrations.AddField(
            model_name="parameter",
            name="view_count",
            field=models.PositiveIntegerField(
                default=0,
                help_text="How many times the parameter page was opened. Lists use this, highest first.",
                verbose_name="Views",
            ),
        ),
        migrations.AlterModelOptions(
            name="parameter",
            options={"ordering": ["-view_count", "order", "name"]},
        ),
    ]
