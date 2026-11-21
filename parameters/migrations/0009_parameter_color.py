from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('parameters', '0008_parameter_formula'),
    ]

    operations = [
        migrations.AddField(
            model_name='parameter',
            name='color',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Hex color such as #38bdf8. Leave empty to keep the automatic chart color.',
                max_length=7,
                verbose_name='Chart color',
            ),
        ),
    ]
