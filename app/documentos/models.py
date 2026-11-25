"""Documentos fiscais importados (NF-e 55; NFS-e nacional na sequência) e as divergências da conferência.

Fluxo (planejamento, seção 3): upload → hash/idempotência → validação do XML no motor → leitura dos
itens → recálculo pelo motor (vira uma `Simulacao`) → comparação item a item com a tolerância do
escritório → `Divergencia` para cada valor fora da tolerância.
"""

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from core.tenancy import TenantModel


class LoteImportacao(TenantModel):
    """Um envio: um XML, vários XMLs ou um .zip. Guarda o resultado de cada arquivo."""

    campo_empresa = "empresa"

    empresa = models.ForeignKey("empresas.Empresa", on_delete=models.PROTECT, related_name="lotes_importacao")
    enviado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    arquivos = models.PositiveIntegerField(default=0)
    novos = models.PositiveIntegerField(default=0)
    duplicados = models.PositiveIntegerField(default=0)
    recusados = models.PositiveIntegerField(default=0)
    # [{"arquivo", "resultado": "novo"|"duplicado"|"recusado", "mensagem", "linha", "coluna", "documento_id"}]
    resultados = models.JSONField(default=list, blank=True, encoder=DjangoJSONEncoder)

    class Meta:
        verbose_name = "lote de importação"
        verbose_name_plural = "lotes de importação"
        ordering = ["-criado_em"]


class DocumentoFiscal(TenantModel):
    class Modelo(models.TextChoices):
        NFE = "55", "NF-e"
        NFSE = "nfse", "NFS-e"

    class Papel(models.TextChoices):
        EMITIDA = "emitida", "Emitida pela empresa"
        RECEBIDA = "recebida", "Recebida pela empresa"

    class Status(models.TextChoices):
        RECEBIDO = "recebido", "Recebido"
        CONFORME = "conforme", "Conforme"
        DIVERGENTE = "divergente", "Com divergência"
        NAO_CONFERIDO = "nao_conferido", "Sem conferência"
        ERRO = "erro", "Erro na conferência"

    campo_empresa = "empresa"

    empresa = models.ForeignKey("empresas.Empresa", on_delete=models.PROTECT, related_name="documentos")
    lote = models.ForeignKey(
        LoteImportacao, on_delete=models.SET_NULL, null=True, blank=True, related_name="documentos"
    )
    modelo = models.CharField(max_length=5, choices=Modelo.choices)
    papel = models.CharField(max_length=10, choices=Papel.choices)
    chave = models.CharField("chave de acesso", max_length=50)
    numero = models.CharField("número", max_length=20)
    serie = models.CharField("série", max_length=5, blank=True)
    emissao = models.DateTimeField("emissão")
    uf = models.CharField("UF do fato gerador", max_length=2, blank=True)
    municipio = models.CharField("município do fato gerador (IBGE)", max_length=7, blank=True)
    emitente_cnpj = models.CharField(max_length=14)
    emitente_nome = models.CharField(max_length=200, blank=True)
    emitente_crt = models.CharField(max_length=1, blank=True)
    destinatario_cnpj = models.CharField(max_length=14, blank=True)
    destinatario_nome = models.CharField(max_length=200, blank=True)
    nome_arquivo = models.CharField(max_length=255, blank=True)
    hash_xml = models.CharField("SHA-256 da nota", max_length=64)
    xml = models.TextField()
    itens = models.JSONField("itens lidos da nota", default=list, encoder=DjangoJSONEncoder)
    totais = models.JSONField("totais destacados na nota", default=dict, encoder=DjangoJSONEncoder)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.RECEBIDO)
    # [{"etapa": "xsd"|"motor"|"recalculo", "mensagem", "linha", "coluna"}]
    mensagens = models.JSONField(default=list, blank=True, encoder=DjangoJSONEncoder)
    validado_xsd = models.BooleanField("XML validado no motor", null=True)
    simulacao = models.ForeignKey(
        "simulacoes.Simulacao", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    tolerancia = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    conferido_em = models.DateTimeField(null=True, blank=True)
    importado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "documento fiscal"
        verbose_name_plural = "documentos fiscais"
        ordering = ["-emissao"]
        constraints = [
            models.UniqueConstraint(fields=["escritorio", "modelo", "chave"], name="documento_chave_unica"),
            models.UniqueConstraint(fields=["escritorio", "hash_xml"], name="documento_hash_unico"),
        ]
        indexes = [models.Index(fields=["escritorio", "empresa", "status", "-emissao"])]

    def __str__(self):
        return f"{self.get_modelo_display()} {self.numero}/{self.serie}"

    @property
    def divergencias_abertas(self):
        return self.divergencias.filter(status=Divergencia.Status.ABERTA)


class Divergencia(TenantModel):
    class Status(models.TextChoices):
        ABERTA = "aberta", "Aberta"
        JUSTIFICADA = "justificada", "Justificada"
        RESOLVIDA = "resolvida", "Resolvida"

    CAMPOS = {
        "base_calculo": "Base de cálculo",
        "cbs": "CBS",
        "ibs_uf": "IBS estadual",
        "ibs_mun": "IBS municipal",
        "ibs": "IBS",
        "imposto_seletivo": "Imposto Seletivo",
        "calculo": "Cálculo do motor",
    }

    campo_empresa = "documento__empresa"

    documento = models.ForeignKey(DocumentoFiscal, on_delete=models.CASCADE, related_name="divergencias")
    numero_item = models.PositiveIntegerField("item", null=True, blank=True)
    campo = models.CharField(max_length=20)
    valor_nota = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    valor_recalculado = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    diferenca = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    mensagem = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ABERTA)
    comentario = models.TextField(blank=True)
    tratado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    tratado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "divergência"
        ordering = ["documento", "numero_item", "campo"]
        indexes = [models.Index(fields=["escritorio", "status"])]

    @property
    def nome_campo(self):
        return self.CAMPOS.get(self.campo, self.campo)
