import re
from datetime import date

from rest_framework import serializers

from catalogo.models import ItemCatalogo
from empresas.models import Empresa
from engines.contratos import ANEXOS_SN
from engines.serializers import VersaoMotorSerializer

from . import servicos
from .models import AliquotaEfetivaSN, Simulacao, SimulacaoItem

MAX_ITENS = 500


def _valor(**kwargs):
    return serializers.DecimalField(max_digits=18, decimal_places=2, **kwargs)


def _aliquota(**kwargs):
    return serializers.DecimalField(max_digits=7, decimal_places=4, min_value=0, max_value=100, **kwargs)


class EmpresaDoEscritorioField(serializers.UUIDField):
    """UUID de uma empresa que a membership enxerga. Empresa de outro escritório = "não encontrada"."""

    def to_internal_value(self, data):
        pk = super().to_internal_value(data)
        empresa = Empresa.tenant(self.context["request"]).filter(pk=pk, ativa=True).first()
        if empresa is None:
            raise serializers.ValidationError("Empresa não encontrada ou inativa.")
        return empresa


class ItemCatalogoDoEscritorioField(serializers.UUIDField):
    """UUID de um item do catálogo que a membership enxerga."""

    def to_internal_value(self, data):
        pk = super().to_internal_value(data)
        item = ItemCatalogo.tenant(self.context["request"]).filter(pk=pk, ativo=True).first()
        if item is None:
            raise serializers.ValidationError("Item do catálogo não encontrado.")
        return item


CAMPOS_DO_CATALOGO = ("ncm", "nbs", "cst", "c_class_trib", "c_ativ_sn")


def completar_com_catalogo(attrs, campos):
    """Campo não informado no item da simulação vem do item do catálogo escolhido."""
    item = attrs.get("item_catalogo")
    if item is not None:
        for campo in campos:
            if not attrs.get(campo):
                attrs[campo] = getattr(item, campo)
    return attrs


def checar_empresa_dos_itens(attrs):
    empresa = attrs.get("empresa")
    erros = {}
    for posicao, item in enumerate(attrs.get("itens") or []):
        catalogo = item.get("item_catalogo")
        if catalogo is not None and empresa is not None and catalogo.empresa_id != empresa.pk:
            erros[f"itens[{posicao}].item_catalogo"] = ["O item do catálogo é de outra empresa."]
    if erros:
        raise serializers.ValidationError(erros)
    return attrs


# --- Regime regular -----------------------------------------------------------------------------


class AliquotasNominaisSerializer(serializers.Serializer):
    """Alíquotas (%) informadas pelo contador. Campo ausente = usar a tabela do Tributo360."""

    cbs = _aliquota(required=False, allow_null=True)
    ibs_estadual = _aliquota(required=False, allow_null=True)
    ibs_municipal = _aliquota(required=False, allow_null=True)
    is_ad_valorem = _aliquota(required=False, allow_null=True)
    is_ad_rem = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=0, required=False, allow_null=True)


class ImpostoSeletivoSerializer(serializers.Serializer):
    cst = serializers.RegexField(r"^\d{3}$")
    c_class_trib = serializers.RegexField(r"^\d{6}$")
    base_calculo = _valor(min_value=0)
    quantidade = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=0, required=False)
    unidade = serializers.CharField(max_length=6, required=False, allow_blank=True)
    imposto_informado = _valor(min_value=0, required=False)


class ItemRegularSerializer(serializers.Serializer):
    item_catalogo = ItemCatalogoDoEscritorioField(
        required=False, allow_null=True, help_text="Completa NCM/NBS/CST/cClassTrib."
    )
    ncm = serializers.RegexField(r"^\d{8}$", required=False, allow_blank=True)
    nbs = serializers.RegexField(r"^\d{9}$", required=False, allow_blank=True)
    cst = serializers.RegexField(r"^\d{3}$", required=False, allow_blank=True)
    c_class_trib = serializers.RegexField(r"^\d{6}$", required=False, allow_blank=True)
    # Sem base, com Imposto Seletivo: o motor deriva a base de CBS/IBS (base do IS + IS).
    base_calculo = _valor(min_value=0, required=False)
    quantidade = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=0, required=False)
    unidade = serializers.CharField(max_length=6, required=False, allow_blank=True)
    aliquotas = AliquotasNominaisSerializer(required=False)
    imposto_seletivo = ImpostoSeletivoSerializer(required=False)

    def validate(self, attrs):
        completar_com_catalogo(attrs, ("ncm", "nbs", "cst", "c_class_trib"))
        faltando = {c: ["Este campo é obrigatório."] for c in ("cst", "c_class_trib") if not attrs.get(c)}
        if faltando:
            raise serializers.ValidationError(faltando)
        if not attrs.get("ncm") and not attrs.get("nbs"):
            raise serializers.ValidationError("Informe o NCM (mercadoria) ou o NBS (serviço).")
        if attrs.get("base_calculo") is None and not attrs.get("imposto_seletivo"):
            raise serializers.ValidationError({"base_calculo": "Informe a base de cálculo."})
        return attrs


class SimulacaoRegularSerializer(serializers.Serializer):
    empresa = EmpresaDoEscritorioField()
    data_fato_gerador = serializers.DateTimeField()
    uf = serializers.CharField(max_length=2, required=False, help_text="Padrão: UF da empresa.")
    municipio = serializers.RegexField(r"^\d{7}$", required=False, help_text="Padrão: município da empresa.")
    tp_doc = serializers.IntegerField(required=False, min_value=1)
    rotulo = serializers.CharField(max_length=120, required=False, allow_blank=True)
    aliquotas = AliquotasNominaisSerializer(required=False, help_text="Vale para todos os itens sem alíquota própria.")
    itens = ItemRegularSerializer(many=True, allow_empty=False, max_length=MAX_ITENS)

    def validate_empresa(self, empresa):
        if empresa.regime != Empresa.Regime.REGULAR:
            raise serializers.ValidationError("Esta empresa não é do regime regular. Use a simulação do Simples.")
        return empresa

    def validate(self, attrs):
        return checar_empresa_dos_itens(attrs)


# --- Simples Nacional ---------------------------------------------------------------------------


class ParAliquotaSNSerializer(serializers.Serializer):
    cbs = _aliquota()
    ibs = _aliquota()


class AnexoSNSerializer(ParAliquotaSNSerializer):
    sem_redistribuicao_iss = ParAliquotaSNSerializer(required=False)


class AliquotasEfetivasSNSerializer(serializers.Serializer):
    """Alíquotas efetivas do PGDAS-D do período, informadas pelo contador (decisão D3)."""

    fator_r = serializers.BooleanField()
    impedimento_sublimite = serializers.BooleanField(default=False)
    anexos = serializers.DictField(child=AnexoSNSerializer(), allow_empty=False)

    def validate_anexos(self, anexos):
        invalidos = sorted(set(anexos) - set(ANEXOS_SN))
        if invalidos:
            raise serializers.ValidationError(f"Anexos inválidos: {', '.join(invalidos)}. Use I, II, III, IV ou V.")
        return anexos


class ItemSimplesSerializer(serializers.Serializer):
    item_catalogo = ItemCatalogoDoEscritorioField(required=False, allow_null=True, help_text="Completa os códigos.")
    c_ativ_sn = serializers.RegexField(r"^\d{2}$", required=False, allow_blank=True)
    c_class_trib = serializers.RegexField(r"^\d{6}$", required=False, allow_blank=True)
    ncm = serializers.RegexField(r"^\d{8}$", required=False, allow_blank=True)
    nbs = serializers.RegexField(r"^\d{9}$", required=False, allow_blank=True)
    base_calculo = _valor(min_value=0)
    valor_imposto_seletivo = _valor(min_value=0, required=False)
    trib_issqn = serializers.CharField(max_length=2, required=False, allow_blank=True)
    tp_ret_issqn = serializers.CharField(max_length=2, required=False, allow_blank=True)
    tp_bm = serializers.CharField(max_length=2, required=False, allow_blank=True)

    def validate(self, attrs):
        completar_com_catalogo(attrs, ("ncm", "nbs", "c_class_trib", "c_ativ_sn"))
        faltando = {c: ["Este campo é obrigatório."] for c in ("c_ativ_sn", "c_class_trib") if not attrs.get(c)}
        if faltando:
            raise serializers.ValidationError(faltando)
        return attrs


TP_DOCS_SN = ("55", "65", "57", "67", "63", "93", "62", "91")


class SimulacaoSimplesSerializer(serializers.Serializer):
    empresa = EmpresaDoEscritorioField()
    data = serializers.DateField()
    tp_doc = serializers.ChoiceField(choices=TP_DOCS_SN, help_text="55 NF-e, 65 NFC-e, 91 NFS-e...")
    rotulo = serializers.CharField(max_length=120, required=False, allow_blank=True)
    crt = serializers.CharField(max_length=1, required=False, allow_blank=True, help_text="Padrão: CRT da empresa.")
    op_simp_nac = serializers.CharField(max_length=1, required=False, allow_blank=True)
    reg_ap_ibscbs_sn = serializers.CharField(max_length=1, required=False, allow_blank=True)
    reg_ap_trib_sn = serializers.CharField(max_length=1, required=False, allow_blank=True)
    aliquotas_efetivas = AliquotasEfetivasSNSerializer(
        required=False, help_text="Só para esta simulação. Padrão: o cadastro da empresa para o mês da operação."
    )
    itens = ItemSimplesSerializer(many=True, allow_empty=False, max_length=MAX_ITENS)

    def validate_empresa(self, empresa):
        if empresa.regime not in {Empresa.Regime.SIMPLES, Empresa.Regime.MEI}:
            raise serializers.ValidationError(
                "Esta empresa não é do Simples Nacional. Use a simulação do regime regular."
            )
        return empresa

    def validate(self, attrs):
        return checar_empresa_dos_itens(attrs)


class PeriodoField(serializers.Field):
    """Mês de apuração como "AAAA-MM" (também aceita "MM/AAAA" e "AAAA-MM-DD")."""

    default_error_messages = {"invalido": "Use o formato AAAA-MM."}

    def to_internal_value(self, data):
        texto = str(data or "").strip()
        if m := re.fullmatch(r"(\d{4})-(\d{2})(?:-\d{2})?", texto):
            ano, mes = int(m[1]), int(m[2])
        elif m := re.fullmatch(r"(\d{2})/(\d{4})", texto):
            ano, mes = int(m[2]), int(m[1])
        else:
            self.fail("invalido")
        if not 1 <= mes <= 12 or ano < 2026:
            self.fail("invalido")
        return date(ano, mes, 1)

    def to_representation(self, valor):
        return f"{valor:%Y-%m}"


class AliquotaEfetivaSNSerializer(serializers.ModelSerializer):
    """Cadastro das alíquotas efetivas do PGDAS-D de uma empresa num mês."""

    empresa = EmpresaDoEscritorioField()
    periodo = PeriodoField()
    anexos = serializers.DictField(child=AnexoSNSerializer(), allow_empty=False)
    informado_por = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = AliquotaEfetivaSN
        fields = [
            "id",
            "empresa",
            "periodo",
            "fator_r",
            "impedimento_sublimite",
            "anexos",
            "observacao",
            "informado_por",
            "criado_em",
            "atualizado_em",
        ]
        read_only_fields = ["id", "informado_por", "criado_em", "atualizado_em"]
        validators = []  # unicidade do período conferida em validate(), com mensagem no campo

    def to_representation(self, instancia):
        dados = super().to_representation(instancia)
        dados["empresa"] = str(instancia.empresa_id)
        return dados

    def validate_anexos(self, anexos):
        return AliquotasEfetivasSNSerializer().validate_anexos(anexos)

    def validate(self, attrs):
        empresa = attrs.get("empresa", getattr(self.instance, "empresa", None))
        if self.instance is not None and "empresa" in attrs and attrs["empresa"].pk != self.instance.empresa_id:
            raise serializers.ValidationError({"empresa": "A empresa não pode ser trocada."})
        if empresa.regime not in {Empresa.Regime.SIMPLES, Empresa.Regime.MEI}:
            raise serializers.ValidationError({"empresa": "Alíquotas efetivas só valem para empresas do Simples."})
        periodo = attrs.get("periodo", getattr(self.instance, "periodo", None))
        if servicos.periodo_em_uso(empresa, periodo, ignorar_pk=getattr(self.instance, "pk", None)):
            raise serializers.ValidationError({"periodo": "Já existe cadastro para este período. Edite o existente."})
        return attrs


# --- Saída ----------------------------------------------------------------------------------------


class SimulacaoItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SimulacaoItem
        fields = [
            "numero",
            "item_catalogo",
            "estado",
            "ncm",
            "nbs",
            "cst",
            "c_class_trib",
            "c_ativ_sn",
            "base_calculo",
            "valores",
            "erros",
            "memoria",
            "origem_aliquotas",
            "extras",
        ]


class SimulacaoResumoSerializer(serializers.ModelSerializer):
    empresa = serializers.UUIDField(source="empresa_id")
    versao_motor = serializers.StringRelatedField()

    class Meta:
        model = Simulacao
        fields = [
            "id",
            "empresa",
            "tipo",
            "rotulo",
            "data_fato_gerador",
            "situacao",
            "simulado",
            "versao_motor",
            "total_base_calculo",
            "total_cbs",
            "total_ibs",
            "total_imposto_seletivo",
            "criado_em",
        ]


class SimulacaoDetalheSerializer(SimulacaoResumoSerializer):
    versao_motor = VersaoMotorSerializer()
    itens = SimulacaoItemSerializer(many=True)

    class Meta(SimulacaoResumoSerializer.Meta):
        fields = SimulacaoResumoSerializer.Meta.fields + [
            "aviso_simulado",
            "duracao_ms",
            "itens",
            "entrada",
            "payload_enviado",
            "resposta_bruta",
        ]
