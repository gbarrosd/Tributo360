from django.core.exceptions import ValidationError
from django.db import models

from core.tenancy import TenantModel
from core.validators import UF_CHOICES, limpar_documento, validar_cnae, validar_cnpj, validar_municipio_da_uf


class Empresa(TenantModel):
    """Empresa da carteira de um escritório."""

    class Regime(models.TextChoices):
        REGULAR = "regular", "Regime regular"
        SIMPLES = "simples", "Simples Nacional"
        MEI = "mei", "MEI"

    class CRT(models.IntegerChoices):
        SIMPLES = 1, "1 · Simples Nacional"
        SIMPLES_EXCESSO = 2, "2 · Simples Nacional, excesso de sublimite"
        NORMAL = 3, "3 · Regime normal"
        MEI = 4, "4 · MEI"

    # CRT coerente com o regime informado.
    CRT_POR_REGIME = {
        Regime.REGULAR: {CRT.NORMAL},
        Regime.SIMPLES: {CRT.SIMPLES, CRT.SIMPLES_EXCESSO},
        Regime.MEI: {CRT.MEI},
    }

    campo_empresa = "pk"

    cnpj = models.CharField(
        "CNPJ", max_length=14, validators=[validar_cnpj], help_text="Sem máscara; aceita alfanumérico."
    )
    razao_social = models.CharField(max_length=200)
    nome_fantasia = models.CharField(max_length=200, blank=True)
    regime = models.CharField(max_length=10, choices=Regime.choices)
    crt = models.PositiveSmallIntegerField("CRT", choices=CRT.choices)
    cnae_principal = models.CharField("CNAE principal", max_length=7, validators=[validar_cnae])
    uf = models.CharField("UF", max_length=2, choices=UF_CHOICES)
    municipio_ibge = models.CharField("município (código IBGE)", max_length=7)
    ativa = models.BooleanField(default=True)

    # Memberships com acesso restrito que podem ver esta empresa.
    membros_restritos = models.ManyToManyField("contas.Membership", blank=True, related_name="empresas_permitidas")

    class Meta:
        ordering = ["razao_social"]
        constraints = [
            models.UniqueConstraint(fields=["escritorio", "cnpj"], name="empresa_cnpj_unico_por_escritorio"),
        ]

    def __str__(self):
        return f"{self.razao_social} ({self.cnpj})"

    def clean(self):
        self.cnpj = limpar_documento(self.cnpj)
        self.cnae_principal = limpar_documento(self.cnae_principal)
        erros = {}
        if self.regime and self.crt and self.crt not in self.CRT_POR_REGIME[self.regime]:
            erros["crt"] = f"CRT {self.crt} não combina com o regime {self.get_regime_display()}."
        try:
            validar_municipio_da_uf(self.municipio_ibge, self.uf)
        except ValidationError as exc:
            erros.update(exc.message_dict)
        if erros:
            raise ValidationError(erros)
