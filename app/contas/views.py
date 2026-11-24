from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.utils.decorators import method_decorator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from auditoria.servicos import registrar, snapshot
from core.permissions import PapelPermite, TemEscritorio
from core.tenancy import SESSAO_ESCRITORIO

from . import servicos
from .models import Convite, Membership, Usuario
from .papeis import Acao
from .serializers import (
    AceitarConviteSerializer,
    AlterarSenhaSerializer,
    CadastroSerializer,
    ConviteSerializer,
    EscritorioSerializer,
    EsqueciSenhaSerializer,
    LoginSerializer,
    MembroSerializer,
    RedefinirSenhaSerializer,
    SelecionarEscritorioSerializer,
    UsuarioSerializer,
    validar_senha,
)


def dados_da_sessao(request, membership=None):
    """O que o front precisa saber sobre quem está logado."""
    usuario = request.user
    ativa = membership if membership is not None else getattr(request, "membership", None)
    escritorios = [
        {"id": str(m.escritorio_id), "razao_social": m.escritorio.razao_social, "papel": m.papel}
        for m in usuario.memberships.filter(ativo=True)
        .select_related("escritorio")
        .order_by("escritorio__razao_social")
    ]
    return {
        "usuario": UsuarioSerializer(usuario).data,
        "escritorio_atual": (
            {"id": str(ativa.escritorio_id), "papel": ativa.papel, "acesso_todas_empresas": ativa.acesso_todas_empresas}
            if ativa
            else None
        ),
        "escritorios": escritorios,
    }


class AuthThrottleMixin:
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


# --- Autenticação ------------------------------------------------------------------------


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    """Entrega o cookie CSRF para o front antes do login."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response(status=status.HTTP_204_NO_CONTENT)


class CadastroView(AuthThrottleMixin, APIView):
    """Cria usuário + escritório + assinatura trial, com o usuário como dono."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CadastroSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        usuario, membership = servicos.cadastrar_escritorio(request, serializer.validated_data)
        login(request, usuario, backend="django.contrib.auth.backends.ModelBackend")
        request.session[SESSAO_ESCRITORIO] = str(membership.escritorio_id)
        return Response(dados_da_sessao(request, membership), status=status.HTTP_201_CREATED)


class LoginView(AuthThrottleMixin, APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        usuario = authenticate(
            request, username=serializer.validated_data["email"].lower(), password=serializer.validated_data["senha"]
        )
        if usuario is None:
            raise ValidationError({"detail": "E-mail ou senha incorretos."})
        login(request, usuario)
        request.user = usuario
        membros = list(usuario.memberships.filter(ativo=True)[:2])
        membership = membros[0] if len(membros) == 1 else None
        if membership:
            request.session[SESSAO_ESCRITORIO] = str(membership.escritorio_id)
        return Response(dados_da_sessao(request, membership))


class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EuView(APIView):
    def get(self, request):
        return Response(dados_da_sessao(request))


class SelecionarEscritorioView(APIView):
    """Troca o escritório ativo da sessão (para quem participa de mais de um)."""

    def post(self, request):
        serializer = SelecionarEscritorioSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = (
            request.user.memberships.filter(ativo=True, escritorio_id=serializer.validated_data["escritorio"])
            .select_related("escritorio")
            .first()
        )
        if membership is None:
            raise NotFound("Escritório não encontrado.")
        request.session[SESSAO_ESCRITORIO] = str(membership.escritorio_id)
        return Response(dados_da_sessao(request, membership))


class EsqueciSenhaView(AuthThrottleMixin, APIView):
    """Sempre responde 204, exista ou não a conta (não revela e-mails cadastrados)."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = EsqueciSenhaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        usuario = Usuario.objects.filter(email__iexact=serializer.validated_data["email"], is_active=True).first()
        if usuario is not None and usuario.has_usable_password():
            servicos.enviar_email_redefinicao(usuario)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RedefinirSenhaView(AuthThrottleMixin, APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RedefinirSenhaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dados = serializer.validated_data
        try:
            pk = force_str(urlsafe_base64_decode(dados["uid"]))
            usuario = Usuario.objects.get(pk=pk, is_active=True)
        except (ValueError, TypeError, OverflowError, Usuario.DoesNotExist):
            usuario = None
        if usuario is None or not default_token_generator.check_token(usuario, dados["token"]):
            raise ValidationError({"token": "Link inválido ou expirado. Peça uma nova redefinição."})
        validar_senha_ou_erro(dados["nova_senha"], usuario)
        usuario.set_password(dados["nova_senha"])
        usuario.save(update_fields=["password"])
        return Response(status=status.HTTP_204_NO_CONTENT)


def validar_senha_ou_erro(senha, usuario):
    try:
        validar_senha(senha, usuario)
    except ValidationError as exc:
        raise ValidationError({"nova_senha": exc.detail}) from exc


class AlterarSenhaView(APIView):
    def post(self, request):
        serializer = AlterarSenhaSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["nova_senha"])
        request.user.save(update_fields=["password"])
        update_session_auth_hash(request, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Escritório, membros e convites --------------------------------------------------------


class EscritorioAtualView(APIView):
    permission_classes = [IsAuthenticated, TemEscritorio, PapelPermite]
    acoes = {"get": Acao.VER, "patch": Acao.GERIR_PLANO}

    def get(self, request):
        return Response(EscritorioSerializer(request.membership.escritorio).data)

    def patch(self, request):
        escritorio = request.membership.escritorio
        antes = snapshot(escritorio)
        serializer = EscritorioSerializer(escritorio, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        registrar(request, "escritorio.editar", escritorio, antes=antes, depois=snapshot(escritorio))
        return Response(serializer.data)


class MembroViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = MembroSerializer
    permission_classes = [IsAuthenticated, TemEscritorio, PapelPermite]
    acoes = {
        "list": Acao.VER,
        "retrieve": Acao.VER,
        "update": Acao.GERIR_USUARIOS,
        "partial_update": Acao.GERIR_USUARIOS,
        "destroy": Acao.GERIR_USUARIOS,
    }

    def get_queryset(self):
        return Membership.tenant(self.request).select_related("usuario").order_by("usuario__nome")

    def perform_update(self, serializer):
        servicos.atualizar_membro(self.request, serializer)

    def perform_destroy(self, instance):
        servicos.remover_membro(self.request, instance)


class ConviteViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Convites pendentes do escritório. DELETE cancela o convite."""

    serializer_class = ConviteSerializer
    permission_classes = [IsAuthenticated, TemEscritorio, PapelPermite]
    acoes = {
        "list": Acao.GERIR_USUARIOS,
        "retrieve": Acao.GERIR_USUARIOS,
        "create": Acao.GERIR_USUARIOS,
        "destroy": Acao.GERIR_USUARIOS,
    }

    def get_queryset(self):
        return Convite.tenant(self.request).filter(aceito_em__isnull=True, cancelado_em__isnull=True)

    def perform_create(self, serializer):
        servicos.criar_convite(self.request, serializer)

    def perform_destroy(self, instance):
        servicos.cancelar_convite(self.request, instance)


class ConsultarConviteView(AuthThrottleMixin, APIView):
    """Mostra para quem é o convite antes de aceitar (tela de aceite)."""

    permission_classes = [AllowAny]

    def get(self, request):
        convite = Convite.pendente_por_token(request.query_params.get("token"))
        if convite is None:
            raise NotFound("Convite inválido ou expirado.")
        return Response(
            {
                "escritorio": convite.escritorio.razao_social,
                "email": convite.email,
                "papel": convite.papel,
                "conta_existente": Usuario.objects.filter(email__iexact=convite.email).exists(),
            }
        )


class AceitarConviteView(AuthThrottleMixin, APIView):
    """Aceita um convite.

    - Logado com o e-mail do convite: entra no escritório.
    - Sem conta: cria a conta (nome + senha) e entra.
    - Com conta, mas sem login: precisa entrar primeiro (409).
    """

    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request):
        serializer = AceitarConviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dados = serializer.validated_data
        convite = Convite.pendente_por_token(dados["token"])
        if convite is None:
            raise NotFound("Convite inválido ou expirado.")

        if request.user.is_authenticated:
            usuario = request.user
        elif Usuario.objects.filter(email__iexact=convite.email).exists():
            raise servicos.Conflito({"detail": "Já existe conta com este e-mail. Entre e aceite o convite."})
        else:
            usuario = self._criar_usuario(convite, dados)

        if usuario.email.lower() != convite.email:
            raise PermissionDenied("Este convite foi enviado para outro e-mail.")
        membership = servicos.aceitar_convite(request, convite, usuario)
        if not request.session.get("_auth_user_id"):
            login(request, usuario, backend="django.contrib.auth.backends.ModelBackend")
        request.session[SESSAO_ESCRITORIO] = str(membership.escritorio_id)
        return Response(dados_da_sessao(request, membership), status=status.HTTP_201_CREATED)

    def _criar_usuario(self, convite, dados):
        erros = {}
        if not dados.get("nome"):
            erros["nome"] = ["Informe seu nome."]
        if not dados.get("senha"):
            erros["senha"] = ["Crie uma senha."]
        if erros:
            raise ValidationError(erros)
        try:
            validar_senha(dados["senha"], Usuario(email=convite.email, nome=dados["nome"]))
        except ValidationError as exc:
            raise ValidationError({"senha": exc.detail}) from exc
        return Usuario.objects.create_user(email=convite.email, password=dados["senha"], nome=dados["nome"])
