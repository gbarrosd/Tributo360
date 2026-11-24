from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from core.tenancy import TenantModel


class ItemCatalogo(TenantModel):
    """Produto ou serviço de uma empresa, com a classificação fiscal usada nas simulações.

    Fluxo da classificação (planejamento, Fase 3, item 9):

    - ``pendente``: cadastrado ou importado, sem erro de formato nem do motor; falta aprovação;
    - ``revisar``: código em formato errado, ausente das tabelas oficiais ou recusado pelo motor
      (o erro fica em ``validacao``);
    - ``aprovado``: alguém com responsabilidade profissional conferiu (papel com
      ``Acao.APROVAR_CLASSIFICACAO``). Mudar a classificação de um item aprovado volta para pendente.
    """

    class Tipo(models.TextChoices):
        PRODUTO = "produto", "Produto (NCM)"
        SERVICO = "servico", "Serviço (NBS)"

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente de aprovação"
        REVISAR = "revisar", "Revisar"
        APROVADO = "aprovado", "Aprovado"

    CAMPOS_CLASSIFICACAO = ("tipo", "ncm", "nbs", "cst", "c_class_trib", "c_ativ_sn")

    campo_empresa = "empresa"

    empresa = models.ForeignKey("empresas.Empresa", on_delete=models.PROTECT, related_name="itens_catalogo")
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    codigo = models.CharField("código interno", max_length=60)
    descricao = models.CharField("descrição", max_length=300)
    ncm = models.CharField("NCM", max_length=8, blank=True)
    nbs = models.CharField("NBS", max_length=9, blank=True)
    cst = models.CharField("CST", max_length=3, blank=True)
    c_class_trib = models.CharField("cClassTrib", max_length=6, blank=True)
    c_ativ_sn = models.CharField("cAtivSN", max_length=2, blank=True, help_text="Atividade no Simples Nacional.")
    status_classificacao = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE)
    # {"erros": [{"origem": "formato"|"tabela"|"motor", "campo", "mensagem", "codigo"}],
    #  "motor": "regime_regular 1.7.2 / V0073" | "", "avisos": [...]}
    validacao = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    validado_em = models.DateTimeField(null=True, blank=True)
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    aprovado_em = models.DateTimeField(null=True, blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "item do catálogo"
        verbose_name_plural = "itens do catálogo"
        ordering = ["empresa", "codigo"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "codigo"], name="item_catalogo_codigo_unico_por_empresa"),
        ]
        indexes = [models.Index(fields=["escritorio", "empresa", "status_classificacao"])]

    def __str__(self):
        return f"{self.codigo} · {self.descricao}"

    @property
    def classificacao(self):
        return {campo: getattr(self, campo) for campo in self.CAMPOS_CLASSIFICACAO}

    @property
    def erros_validacao(self):
        return (self.validacao or {}).get("erros", [])
