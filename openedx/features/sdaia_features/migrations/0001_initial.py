from django.db import migrations, models
from django.conf import settings
import storages.backends.s3


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='ImageAsset',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=255)),
                ('image', models.ImageField(storage=storages.backends.s3.S3Storage(bucket_name=settings.ASSET_BUCKET), upload_to='image_assets/')),
                ('description', models.TextField(blank=True, null=True)),
            ],
        ),
    ]
