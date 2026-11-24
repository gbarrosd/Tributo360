from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from core.tenancy import TenantModel


class Simulacao(TenantModel):
    """Um cálculo feito por um motor, com tudo que é preciso para reproduzi-lo e explicá-lo depois."""

    class Tipo(models.TextChoices):
        REGULAR = "regular", "Regime regular"
        SIMPLES = "simples", "Simples Nacional"

    class Situacao(models.TextChoices):
        CALCULADO = "calculado", "Calculado"
        SIMULADO = "simulado", "Simulado (alíquotas informadas)"
        COM_INCONSISTENCIA = "com_inconsistencia", "Com itens não calculados"

    campo_empresa = "empresa"

    empresa = models.ForeignKey("empresas.Empresa", on_delete=models.PROTECT, related_name="simulacoes")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    rotulo = models.CharField("rótulo/cenário", max_length=120, blank=True)
    data_fato_gerador = models.DateField()
    versao_motor = models.ForeignKey("engines.VersaoMotor", on_delete=models.PROTECT, related_name="simulacoes")
    situacao = models.CharField(max_length=20, choices=Situacao.choices)
    simulado = models.BooleanField(help_text="O motor marcou o resultado como simulado.")
    aviso_simulado = models.CharField(max_length=20, blank=True, help_text="Header x-warning-dados-simulados.")
    # Entrada como o usuário mandou (antes de completar alíquotas), payload enviado e resposta integral.
    entrada = models.JSONField(encoder=DjangoJSONEncoder)
    payload_enviado = models.JSONField(encoder=DjangoJSONEncoder)
    resposta_bruta = models.JSONField(encoder=DjangoJSONEncoder)
    total_base_calculo = models.DecimalField(max_digits=18, decimal_places=2)
    total_cbs = models.DecimalField(max_digits=18, decimal_places=2)
    total_ibs = models.DecimalField(max_digits=18, decimal_places=2)
    total_imposto_seletivo = models.DecimalField(max_digits=18, decimal_places=2)
    duracao_ms = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "simulação"
        verbose_name_plural = "simulações"
        ordering = ["-criado_em"]
        indexes = [models.Index(fields=["escritorio", "empresa", "-criado_em"])]

    def __str__(self):
        return f"{self.get_tipo_display()} · {self.empresa} · {self.data_fato_gerador:%d/%m/%Y}"


class SimulacaoItem(TenantModel):
    campo_empresa = "simulacao__empresa"

    simulacao = models.ForeignKey(Simulacao, on_delete=models.CASCADE, related_name="itens")
    numero = models.PositiveIntegerField()
    estado = models.CharField(max_length=30, help_text="estadoItem devolvido pelo motor.")
    ncm = models.CharField(max_length=8, blank=True)
    nbs = models.CharField(max_length=9, blank=True)
    cst = models.CharField(max_length=3, blank=True)
    c_class_trib = models.CharField(max_length=6, blank=True)
    c_ativ_sn = models.CharField(max_length=2, blank=True)
    base_calculo = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    valores = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    erros = models.JSONField(default=list, encoder=DjangoJSONEncoder)
    memoria = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    # Origem de cada alíquota: motor_2026, tabela_tributo360 (id e fonte) ou editada_pelo_usuario
    # (com o valor que a tabela sugeria). No Simples: informada_pelo_usuario (decisão D3).
    origem_aliquotas = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    extras = models.JSONField(default=dict, encoder=DjangoJSONEncoder)

    class Meta:
        verbose_name = "item de simulação"
        verbose_name_plural = "itens de simulação"
        ordering = ["simulacao", "numero"]
        constraints = [models.UniqueConstraint(fields=["simulacao", "numero"], name="item_simulacao_numero_unico")]

    def __str__(self):
        return f"Item {self.numero} ({self.estado})"
