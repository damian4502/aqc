from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rooms', '0005_room_air_flow'),
    ]

    operations = [
        migrations.AddField(
            model_name='room',
            name='color',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Hex color such as #34d399. Leave empty to keep the automatic chart color.',
                max_length=7,
                verbose_name='Chart color',
            ),
        ),
    ]
