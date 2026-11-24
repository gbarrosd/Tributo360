from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from core.tenancy import TenantModel


class Auditoria(TenantModel):
    """Registro imutável de quem fez o quê, com o antes e o depois."""

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    acao = models.CharField(max_length=60)
    objeto_tipo = models.CharField(max_length=60)
    objeto_id = models.CharField(max_length=64)
    antes = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    depois = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        verbose_name = "registro de auditoria"
        verbose_name_plural = "auditoria"
        ordering = ["-criado_em"]
        indexes = [models.Index(fields=["escritorio", "objeto_tipo", "objeto_id"])]

    def __str__(self):
        return f"{self.acao} {self.objeto_tipo}:{self.objeto_id}"
