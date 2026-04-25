from django.db import models

class Room(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    order = models.PositiveIntegerField(
        default=999,
    )

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']
