from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('parameters', '0007_parameter_format_decimals'),
    ]

    operations = [
        migrations.AddField(
            model_name='parameter',
            name='formula',
            field=models.TextField(
                blank=True,
                default='',
                help_text=(
                    'Leave empty for a measured parameter. A formula is plotted and not stored. '
                    'Same syntax as Compare: Room/Parameter, numbers, + − * /, parentheses, '
                    'min(), max(), and avg(). The unit is the Unit field. '
                    'Names are matched as written, so rename a room or parameter and update the formula.'
                ),
                verbose_name='Formula',
            ),
        ),
    ]
