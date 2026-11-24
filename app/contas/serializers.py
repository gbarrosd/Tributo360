from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from core.validators import limpar_documento, validar_cpf_ou_cnpj
from empresas.models import Empresa

from .models import Convite, Escritorio, Membership, Usuario
from .papeis import Papel


def validar_senha(senha, usuario=None):
    try:
        password_validation.validate_password(senha, user=usuario)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(list(exc.messages)) from exc
    return senha


class UsuarioSerializer(serializers.ModelSerializer):
    class Meta:
        model = Usuario
        fields = ["id", "email", "nome"]
        read_only_fields = fields


class CadastroSerializer(serializers.Serializer):
    nome = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    senha = serializers.CharField(write_only=True, trim_whitespace=False)
    escritorio_razao_social = serializers.CharField(max_length=200)
    escritorio_documento = serializers.CharField(max_length=18)

    def validate_email(self, valor):
        valor = valor.strip().lower()
        if Usuario.objects.filter(email__iexact=valor).exists():
            raise serializers.ValidationError("Já existe conta com este e-mail.")
        return valor

    def validate_escritorio_documento(self, valor):
        try:
            validar_cpf_ou_cnpj(valor)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return limpar_documento(valor)

    def validate(self, attrs):
        validar_senha(attrs["senha"], Usuario(email=attrs["email"], nome=attrs["nome"]))
        return attrs


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    senha = serializers.CharField(write_only=True, trim_whitespace=False)


class SelecionarEscritorioSerializer(serializers.Serializer):
    escritorio = serializers.UUIDField()


class EsqueciSenhaSerializer(serializers.Serializer):
    email = serializers.EmailField()


class RedefinirSenhaSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    nova_senha = serializers.CharField(write_only=True, trim_whitespace=False)


class AlterarSenhaSerializer(serializers.Serializer):
    senha_atual = serializers.CharField(write_only=True, trim_whitespace=False)
    nova_senha = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_senha_atual(self, valor):
        if not self.context["request"].user.check_password(valor):
            raise serializers.ValidationError("Senha atual incorreta.")
        return valor

    def validate_nova_senha(self, valor):
        return validar_senha(valor, self.context["request"].user)


class EscritorioSerializer(serializers.ModelSerializer):
    documento = serializers.CharField(max_length=18)
    assinatura = serializers.SerializerMethodField()

    class Meta:
        model = Escritorio
        fields = ["id", "razao_social", "documento", "status", "assinatura", "criado_em"]
        read_only_fields = ["id", "status", "assinatura", "criado_em"]

    def validate_documento(self, valor):
        try:
            validar_cpf_ou_cnpj(valor)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return limpar_documento(valor)

    def get_assinatura(self, escritorio):
        assinatura = getattr(escritorio, "assinatura", None)
        if assinatura is None:
            return None
        return {
            "plano": assinatura.plano,
            "status": assinatura.status,
            "limite_empresas": assinatura.limite_empresas,
            "limite_usuarios": assinatura.limite_usuarios,
            "fim": assinatura.fim,
        }


class EmpresasDoEscritorioField(serializers.ListField):
    """Lista de UUIDs de empresas, validada contra o que a membership de quem edita enxerga."""

    child = serializers.UUIDField()

    def to_internal_value(self, data):
        ids = set(super().to_internal_value(data))
        encontradas = list(Empresa.tenant(self.context["request"]).filter(pk__in=ids))
        if len(encontradas) != len(ids):
            raise serializers.ValidationError("Empresa inexistente ou fora do seu acesso.")
        return encontradas


class MembroSerializer(serializers.ModelSerializer):
    usuario = UsuarioSerializer(read_only=True)
    empresas = EmpresasDoEscritorioField(required=False, write_only=True)
    empresas_permitidas = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = ["id", "usuario", "papel", "acesso_todas_empresas", "empresas", "empresas_permitidas", "criado_em"]
        read_only_fields = ["id", "usuario", "criado_em"]

    def get_empresas_permitidas(self, membership):
        if membership.acesso_todas_empresas:
            return None
        return [str(pk) for pk in membership.empresas_permitidas.values_list("pk", flat=True)]


class ConviteSerializer(serializers.ModelSerializer):
    empresas = EmpresasDoEscritorioField(required=False, write_only=True)

    class Meta:
        model = Convite
        fields = ["id", "email", "papel", "acesso_todas_empresas", "empresas", "expira_em", "criado_em"]
        read_only_fields = ["id", "expira_em", "criado_em"]

    def validate_email(self, valor):
        return valor.strip().lower()

    def validate_papel(self, valor):
        if valor == Papel.DONO and self.context["request"].membership.papel != Papel.DONO:
            raise serializers.ValidationError("Só o dono pode convidar outro dono.")
        return valor

    def to_representation(self, instance):
        dados = super().to_representation(instance)
        dados["empresas"] = [str(pk) for pk in instance.empresas.values_list("pk", flat=True)]
        return dados


class AceitarConviteSerializer(serializers.Serializer):
    token = serializers.CharField()
    nome = serializers.CharField(max_length=150, required=False)
    senha = serializers.CharField(write_only=True, required=False, trim_whitespace=False)
