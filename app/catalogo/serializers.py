from rest_framework import serializers

from simulacoes.serializers import EmpresaDoEscritorioField

from .models import ItemCatalogo
from .servicos import limpar_codigo


class ItemCatalogoSerializer(serializers.ModelSerializer):
    """Item do catálogo. Status, validação e aprovação são calculados, nunca enviados."""

    empresa = EmpresaDoEscritorioField()
    aprovado_por = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = ItemCatalogo
        fields = [
            "id",
            "empresa",
            "tipo",
            "codigo",
            "descricao",
            "ncm",
            "nbs",
            "cst",
            "c_class_trib",
            "c_ativ_sn",
            "ativo",
            "status_classificacao",
            "validacao",
            "validado_em",
            "aprovado_por",
            "aprovado_em",
            "criado_em",
            "atualizado_em",
        ]
        read_only_fields = [
            "id",
            "status_classificacao",
            "validacao",
            "validado_em",
            "aprovado_por",
            "aprovado_em",
            "criado_em",
            "atualizado_em",
        ]
        # A unicidade (empresa, código) é conferida em validate(), com mensagem em português.
        validators = []

    def to_representation(self, instancia):
        dados = super().to_representation(instancia)
        dados["empresa"] = str(instancia.empresa_id)
        return dados

    def to_internal_value(self, data):
        if hasattr(data, "copy"):
            data = data.copy()
            for campo in ("ncm", "nbs", "cst", "c_class_trib", "c_ativ_sn"):
                if campo in data:
                    data[campo] = limpar_codigo(data[campo])
        return super().to_internal_value(data)

    def validate(self, attrs):
        empresa = attrs.get("empresa", getattr(self.instance, "empresa", None))
        if self.instance is not None and "empresa" in attrs and attrs["empresa"].pk != self.instance.empresa_id:
            raise serializers.ValidationError({"empresa": "A empresa não pode ser trocada."})
        codigo = attrs.get("codigo", getattr(self.instance, "codigo", None))
        repetido = ItemCatalogo.tenant(self.context["request"]).filter(empresa=empresa, codigo=codigo)
        if self.instance is not None:
            repetido = repetido.exclude(pk=self.instance.pk)
        if repetido.exists():
            raise serializers.ValidationError({"codigo": "Já existe item com este código nesta empresa."})
        return attrs


class ImportacaoSerializer(serializers.Serializer):
    empresa = EmpresaDoEscritorioField()
    arquivo = serializers.FileField()


class AprovacaoSerializer(serializers.Serializer):
    itens = serializers.ListField(child=serializers.UUIDField(), allow_empty=False, max_length=1000)
