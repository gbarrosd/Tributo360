"""Tabelas globais (sem escritório) ligadas aos motores.

- `VersaoMotor`: cada combinação motor × versão do app × versão da base que já rodou aqui.
- `AliquotaNominal`: alíquotas de IBS/CBS por esfera e vigência, com fonte (decisão D2).
- `ItemTabela` e `SincronizacaoTabela`: cópia local das tabelas oficiais (CST, cClassTrib, NCM, NBS),
  com histórico por versão da base.

Nada aqui é apagado: versões, alíquotas e itens de tabela só ganham registros novos.
"""

import hashlib
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .contratos import Motor


class VersaoMotor(models.Model):
    motor = models.CharField(max_length=20, choices=[(m.value, m.value) for m in Motor])
    versao_app = models.CharField("versão do app", max_length=40)
    versao_db = models.CharField("versão da base", max_length=40, blank=True)
    data_db = models.DateField("data da base", null=True, blank=True)
    descricao_db = models.TextField("descrição da base", blank=True)
    ambiente = models.CharField(max_length=40, blank=True)
    vista_primeiro_em = models.DateTimeField("vista pela primeira vez em", default=timezone.now)
    vista_por_ultimo_em = models.DateTimeField("vista por último em", default=timezone.now)

    class Meta:
        verbose_name = "versão de motor"
        verbose_name_plural = "versões dos motores"
        ordering = ["motor", "-vista_primeiro_em"]
        constraints = [
            models.UniqueConstraint(fields=["motor", "versao_app", "versao_db"], name="versao_motor_unica"),
        ]

    def __str__(self):
        base = f" · base {self.versao_db}" if self.versao_db else ""
        return f"{self.motor} {self.versao_app}{base}"

    @classmethod
    def registrar(cls, info):
        """Grava (ou toca) a versão. `info` é um `VersaoInfo`. Complementa data/descrição quando vierem."""
        agora = timezone.now()
        versao, criada = cls.objects.get_or_create(
            motor=str(info.motor),
            versao_app=info.versao_app,
            versao_db=info.versao_db,
            defaults={
                "data_db": info.data_db,
                "descricao_db": info.descricao_db,
                "ambiente": info.ambiente,
                "vista_primeiro_em": agora,
                "vista_por_ultimo_em": agora,
            },
        )
        if not criada:
            campos = {"vista_por_ultimo_em": agora}
            for campo in ("data_db", "descricao_db", "ambiente"):
                if getattr(info, campo) and not getattr(versao, campo):
                    campos[campo] = getattr(info, campo)
            cls.objects.filter(pk=versao.pk).update(**campos)
            for campo, valor in campos.items():
                setattr(versao, campo, valor)
        return versao


class AliquotaNominal(models.Model):
    """Alíquota nominal (%) de uma esfera numa vigência. Nunca é editada: corrige-se com registro novo.

    Quando dois registros cobrem a mesma data, vale o cadastrado por último.
    """

    class Esfera(models.TextChoices):
        UNIAO = "uniao", "União (CBS)"
        UF = "uf", "UF (IBS estadual)"
        MUNICIPIO = "municipio", "Município (IBS municipal)"

    esfera = models.CharField(max_length=10, choices=Esfera.choices)
    codigo_ibge = models.CharField(
        "código IBGE", max_length=7, blank=True, help_text="Vazio para União; 2 dígitos para UF; 7 para município."
    )
    vigencia_inicio = models.DateField("início da vigência")
    vigencia_fim = models.DateField("fim da vigência", null=True, blank=True)
    aliquota = models.DecimalField("alíquota (%)", max_digits=7, decimal_places=4)
    fonte = models.TextField(help_text="Ato normativo ou URL oficial que publica a alíquota. Obrigatória.")
    data_publicacao = models.DateField("data de publicação da fonte")
    observacao = models.TextField("observação", blank=True)
    cadastrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cadastrada_em = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        verbose_name = "alíquota nominal"
        verbose_name_plural = "alíquotas nominais"
        ordering = ["esfera", "codigo_ibge", "-vigencia_inicio", "-cadastrada_em"]
        indexes = [models.Index(fields=["esfera", "codigo_ibge", "vigencia_inicio"])]
        constraints = [
            models.CheckConstraint(condition=~Q(fonte__regex=r"^\s*$"), name="aliquota_nominal_exige_fonte"),
            models.CheckConstraint(
                condition=Q(vigencia_fim__isnull=True) | Q(vigencia_fim__gte=models.F("vigencia_inicio")),
                name="aliquota_nominal_vigencia_valida",
            ),
            models.CheckConstraint(
                condition=Q(aliquota__gte=0) & Q(aliquota__lte=100), name="aliquota_nominal_entre_0_e_100"
            ),
        ]

    def __str__(self):
        local = f" {self.codigo_ibge}" if self.codigo_ibge else ""
        return f"{self.get_esfera_display()}{local}: {self.aliquota}% desde {self.vigencia_inicio:%d/%m/%Y}"

    TAMANHO_CODIGO = {Esfera.UNIAO: 0, Esfera.UF: 2, Esfera.MUNICIPIO: 7}

    def clean(self):
        erros = {}
        if not (self.fonte or "").strip():
            erros["fonte"] = "Informe a fonte (ato normativo ou URL oficial) da alíquota."
        tamanho = self.TAMANHO_CODIGO.get(self.esfera)
        codigo = self.codigo_ibge or ""
        if tamanho is not None and (len(codigo) != tamanho or (codigo and not codigo.isdigit())):
            erros["codigo_ibge"] = (
                "Deixe vazio para a União." if tamanho == 0 else f"Use o código IBGE com {tamanho} dígitos."
            )
        if self.vigencia_fim and self.vigencia_inicio and self.vigencia_fim < self.vigencia_inicio:
            erros["vigencia_fim"] = "O fim da vigência não pode ser antes do início."
        if erros:
            raise ValidationError(erros)

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise ValidationError("Alíquotas nominais não são editadas. Cadastre um registro novo com a correção.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Alíquotas nominais não são apagadas: o histórico sustenta simulações antigas.")

    @classmethod
    def vigente(cls, esfera, data, codigo_ibge=""):
        """Registro vigente na data (o cadastrado por último, se houver sobreposição), ou `None`."""
        return (
            cls.objects.filter(esfera=esfera, codigo_ibge=codigo_ibge or "", vigencia_inicio__lte=data)
            .filter(Q(vigencia_fim__isnull=True) | Q(vigencia_fim__gte=data))
            .order_by("-cadastrada_em", "-pk")
            .first()
        )


class SincronizacaoTabela(models.Model):
    """Uma execução da cópia das tabelas oficiais a partir dos dados abertos do motor."""

    class Status(models.TextChoices):
        EM_ANDAMENTO = "em_andamento", "Em andamento"
        CONCLUIDA = "concluida", "Concluída"
        FALHOU = "falhou", "Falhou"

    versao_motor = models.ForeignKey(VersaoMotor, on_delete=models.PROTECT, related_name="sincronizacoes")
    data_referencia = models.DateField("data de vigência consultada")
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.EM_ANDAMENTO)
    contagens = models.JSONField(default=dict, blank=True)
    erro = models.TextField(blank=True)
    iniciada_em = models.DateTimeField(default=timezone.now)
    concluida_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "sincronização de tabelas"
        verbose_name_plural = "sincronizações de tabelas"
        ordering = ["-iniciada_em"]

    def __str__(self):
        return f"{self.versao_motor} em {self.iniciada_em:%d/%m/%Y %H:%M} ({self.get_status_display()})"


class ItemTabela(models.Model):
    """Uma linha de tabela oficial numa versão. Mudou o conteúdo = registro novo; o antigo fica."""

    class Tabela(models.TextChoices):
        CST = "cst", "CST (CBS/IBS)"
        CCLASSTRIB = "cclasstrib", "cClassTrib (CBS/IBS)"
        NCM = "ncm", "NCM"
        NBS = "nbs", "NBS"

    tabela = models.CharField(max_length=12, choices=Tabela.choices)
    codigo = models.CharField(max_length=20)
    descricao = models.TextField(blank=True)
    dados = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    hash_dados = models.CharField(max_length=64)
    versao_db_origem = models.CharField("primeira versão da base", max_length=40)
    versao_db_ultima = models.CharField("última versão da base em que apareceu", max_length=40)
    vigente = models.BooleanField(default=True, help_text="Faz parte da última sincronização concluída.")
    criado_em = models.DateTimeField(default=timezone.now)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "item de tabela oficial"
        verbose_name_plural = "itens de tabelas oficiais"
        ordering = ["tabela", "codigo", "-criado_em"]
        constraints = [
            models.UniqueConstraint(fields=["tabela", "codigo", "hash_dados"], name="item_tabela_versao_unica"),
        ]
        indexes = [models.Index(fields=["tabela", "vigente", "codigo"])]

    def __str__(self):
        return f"{self.tabela} {self.codigo}"

    @staticmethod
    def calcular_hash(dados):
        texto = json.dumps(dados, sort_keys=True, ensure_ascii=False, cls=DjangoJSONEncoder)
        return hashlib.sha256(texto.encode()).hexdigest()
