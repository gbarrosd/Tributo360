from rest_framework import serializers

from .models import AliquotaNominal, VersaoMotor


class VersaoMotorSerializer(serializers.ModelSerializer):
    class Meta:
        model = VersaoMotor
        fields = [
            "id",
            "motor",
            "versao_app",
            "versao_db",
            "data_db",
            "descricao_db",
            "ambiente",
            "vista_primeiro_em",
            "vista_por_ultimo_em",
        ]


class AliquotaNominalSerializer(serializers.ModelSerializer):
    class Meta:
        model = AliquotaNominal
        fields = [
            "id",
            "esfera",
            "codigo_ibge",
            "vigencia_inicio",
            "vigencia_fim",
            "aliquota",
            "fonte",
            "data_publicacao",
            "observacao",
            "cadastrada_em",
        ]


class ConsultaAliquotasSerializer(serializers.Serializer):
    data = serializers.DateField()
    municipio = serializers.RegexField(r"^\d{7}$", error_messages={"invalid": "Use o código IBGE com 7 dígitos."})
