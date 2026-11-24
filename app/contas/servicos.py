"""Regras de conta: cadastro de escritório, convites, papéis e e-mails."""

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.exceptions import PermissionDenied, ValidationError

from auditoria.servicos import registrar, snapshot

from .models import Assinatura, Convite, Escritorio, Membership, Usuario
from .papeis import Papel


class LimiteDeUsuarios(PermissionDenied):
    default_code = "limite_do_plano"


class Conflito(ValidationError):
    status_code = 409
    default_code = "conflito"


@transaction.atomic
def cadastrar_escritorio(request, dados):
    usuario = Usuario.objects.create_user(email=dados["email"], password=dados["senha"], nome=dados["nome"])
    escritorio = Escritorio.objects.create(
        razao_social=dados["escritorio_razao_social"], documento=dados["escritorio_documento"]
    )
    Assinatura.criar_para(escritorio)
    membership = Membership.objects.create(escritorio=escritorio, usuario=usuario, papel=Papel.DONO)
    request.user = usuario
    registrar(request, "escritorio.criar", escritorio, depois=snapshot(escritorio), escritorio=escritorio)
    return usuario, membership


# --- Membros ---------------------------------------------------------------------------


def _donos_ativos(escritorio):
    return Membership.objects.do_escritorio(escritorio).filter(papel=Papel.DONO, ativo=True)


def _exigir_dono_para_mexer_em_dono(request, alvo, novo_papel=None):
    quem = request.membership
    envolve_dono = alvo.papel == Papel.DONO or novo_papel == Papel.DONO
    if envolve_dono and quem.papel != Papel.DONO:
        raise PermissionDenied("Só o dono pode alterar ou atribuir o papel de dono.")


@transaction.atomic
def atualizar_membro(request, serializer):
    alvo = serializer.instance
    dados = serializer.validated_data
    novo_papel = dados.get("papel", alvo.papel)
    _exigir_dono_para_mexer_em_dono(request, alvo, novo_papel)
    if alvo.papel == Papel.DONO and novo_papel != Papel.DONO and _donos_ativos(alvo.escritorio).count() <= 1:
        raise ValidationError({"papel": "O escritório precisa de ao menos um dono."})

    antes = {**snapshot(alvo), "empresas": _empresas_ids(alvo)}
    empresas = dados.pop("empresas", None)
    membro = serializer.save()
    if membro.acesso_todas_empresas:
        membro.empresas_permitidas.clear()
    elif empresas is not None:
        membro.empresas_permitidas.set(empresas)
    registrar(
        request, "membro.editar", membro, antes=antes, depois={**snapshot(membro), "empresas": _empresas_ids(membro)}
    )
    return membro


@transaction.atomic
def remover_membro(request, alvo):
    _exigir_dono_para_mexer_em_dono(request, alvo)
    if alvo.papel == Papel.DONO and _donos_ativos(alvo.escritorio).count() <= 1:
        raise ValidationError({"detail": "O escritório precisa de ao menos um dono."})
    antes = snapshot(alvo)
    registrar(request, "membro.remover", alvo, antes=antes)
    alvo.delete()


def _empresas_ids(membership):
    return sorted(str(pk) for pk in membership.empresas_permitidas.values_list("pk", flat=True))


# --- Convites ----------------------------------------------------------------------------


def _checar_limite_usuarios(escritorio):
    assinatura = Assinatura.objects.select_for_update().get(escritorio=escritorio)
    membros = Membership.objects.do_escritorio(escritorio).filter(ativo=True).count()
    pendentes = (
        Convite.objects.do_escritorio(escritorio)
        .filter(aceito_em__isnull=True, cancelado_em__isnull=True, expira_em__gt=timezone.now())
        .count()
    )
    if membros + pendentes >= assinatura.limite_usuarios:
        raise LimiteDeUsuarios(f"O plano permite {assinatura.limite_usuarios} usuários (contando convites pendentes).")


@transaction.atomic
def criar_convite(request, serializer):
    escritorio = request.membership.escritorio
    email = serializer.validated_data["email"]
    if Membership.objects.do_escritorio(escritorio).filter(usuario__email__iexact=email).exists():
        raise Conflito({"email": "Esta pessoa já faz parte do escritório."})
    if (
        Convite.objects.do_escritorio(escritorio)
        .filter(email__iexact=email, aceito_em__isnull=True, cancelado_em__isnull=True, expira_em__gt=timezone.now())
        .exists()
    ):
        raise Conflito({"email": "Já existe convite pendente para este e-mail."})
    # Convites vencidos não bloqueiam um novo: são cancelados para liberar a restrição de unicidade.
    Convite.objects.do_escritorio(escritorio).filter(
        email__iexact=email, aceito_em__isnull=True, cancelado_em__isnull=True
    ).update(cancelado_em=timezone.now())
    _checar_limite_usuarios(escritorio)

    convite = Convite(escritorio=escritorio, criado_por=request.user)
    token = convite.gerar_token()
    empresas = serializer.validated_data.pop("empresas", [])
    convite = serializer.save(
        escritorio=escritorio, criado_por=request.user, token_hash=convite.token_hash, expira_em=convite.expira_em
    )
    if not convite.acesso_todas_empresas:
        convite.empresas.set(empresas)
    registrar(request, "convite.criar", convite, depois=snapshot(convite, ["email", "papel", "acesso_todas_empresas"]))
    transaction.on_commit(lambda: enviar_email_convite(convite, token))
    return convite


@transaction.atomic
def cancelar_convite(request, convite):
    convite.cancelado_em = timezone.now()
    convite.save(update_fields=["cancelado_em", "atualizado_em"])
    registrar(request, "convite.cancelar", convite)


@transaction.atomic
def aceitar_convite(request, convite, usuario):
    if usuario.email.lower() != convite.email:
        raise PermissionDenied("Este convite foi enviado para outro e-mail.")
    try:
        with transaction.atomic():
            membership = Membership.objects.create(
                escritorio=convite.escritorio,
                usuario=usuario,
                papel=convite.papel,
                acesso_todas_empresas=convite.acesso_todas_empresas,
            )
    except IntegrityError as exc:
        raise Conflito({"detail": "Você já faz parte deste escritório."}) from exc
    if not convite.acesso_todas_empresas:
        membership.empresas_permitidas.set(convite.empresas.all())
    convite.aceito_em = timezone.now()
    convite.aceito_por = usuario
    convite.save(update_fields=["aceito_em", "aceito_por", "atualizado_em"])
    request.user = usuario
    registrar(request, "convite.aceitar", membership, depois=snapshot(membership), escritorio=convite.escritorio)
    return membership


# --- E-mails ------------------------------------------------------------------------------


def enviar_email_convite(convite, token):
    link = f"{settings.FRONTEND_URL}/convite?token={token}"
    send_mail(
        subject=f"Convite para o escritório {convite.escritorio.razao_social} no Tributo360",
        message=(
            f"Você foi convidado(a) para o escritório {convite.escritorio.razao_social} no Tributo360.\n\n"
            f"Aceite o convite em: {link}\n\n"
            f"O link vale até {timezone.localtime(convite.expira_em):%d/%m/%Y %H:%M}."
        ),
        from_email=None,
        recipient_list=[convite.email],
    )


def enviar_email_redefinicao(usuario):
    uid = urlsafe_base64_encode(force_bytes(usuario.pk))
    token = default_token_generator.make_token(usuario)
    link = f"{settings.FRONTEND_URL}/redefinir-senha?uid={uid}&token={token}"
    send_mail(
        subject="Redefinição de senha do Tributo360",
        message=(
            "Recebemos um pedido para redefinir sua senha.\n\n"
            f"Crie uma nova senha em: {link}\n\n"
            "Se não foi você, ignore este e-mail. Sua senha atual continua valendo."
        ),
        from_email=None,
        recipient_list=[usuario.email],
    )
