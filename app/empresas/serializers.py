from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from core.validators import limpar_documento, validar_cnae, validar_cnpj

from .models import Empresa
from .servicos import cnpj_em_uso


def _rodar(validador, valor):
    try:
        validador(valor)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(list(exc.messages)) from exc
    return valor


class EmpresaSerializer(serializers.ModelSerializer):
    # Aceitam máscara na entrada; o valor gravado é limpo e validado abaixo.
    cnpj = serializers.CharField(max_length=18)
    cnae_principal = serializers.CharField(max_length=10)

    class Meta:
        model = Empresa
        fields = [
            "id",
            "cnpj",
            "razao_social",
            "nome_fantasia",
            "regime",
            "crt",
            "cnae_principal",
            "uf",
            "municipio_ibge",
            "ativa",
            "criado_em",
            "atualizado_em",
        ]
        read_only_fields = ["id", "criado_em", "atualizado_em"]

    def validate_cnpj(self, valor):
        valor = _rodar(validar_cnpj, limpar_documento(valor))
        if self.instance is not None and valor != self.instance.cnpj:
            raise serializers.ValidationError("O CNPJ não pode ser alterado. Cadastre outra empresa.")
        return valor

    def validate_cnae_principal(self, valor):
        return _rodar(validar_cnae, limpar_documento(valor))

    def validate(self, attrs):
        escritorio = self.context["request"].membership.escritorio
        cnpj = attrs.get("cnpj", getattr(self.instance, "cnpj", None))
        if cnpj and cnpj_em_uso(escritorio, cnpj, ignorar_pk=getattr(self.instance, "pk", None)):
            raise serializers.ValidationError({"cnpj": "Já existe empresa com este CNPJ no escritório."})

        # Reaproveita as regras do modelo (CRT × regime, município × UF) sem gravar.
        provisoria = Empresa(**{**self._valores_atuais(), **attrs})
        try:
            provisoria.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        return attrs

    def _valores_atuais(self):
        if self.instance is None:
            return {}
        campos = ["cnpj", "regime", "crt", "cnae_principal", "uf", "municipio_ibge"]
        return {campo: getattr(self.instance, campo) for campo in campos}
